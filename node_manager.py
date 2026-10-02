"""
NodeManager: makes the wallet app a real single-icon double-click experience
by auto-starting the Undercroft daemon (your compiled bitcoind) instead of
requiring you to launch it yourself first.

Behavior, in order, every time the app starts:
  1. Try the RPC port first. If something's already answering (e.g. you're
     running a node manually for testing, same as your multi-node regtest
     work), we just use it and never touch it -- we did not start it, so we
     will not stop it on exit either. This matters: the app must never kill
     a node you're mid-experiment with.
  2. Nothing answering -> launch bitcoind ourselves as a child process with
     the right datadir/network/RPC-credential flags, then poll until it
     responds (a fresh datadir has to create a genesis block / open its
     wallet backend first, which takes a few seconds).
  3. On app exit, if (and only if) we were the ones who started it, ask it
     to shut down cleanly via the `stop` RPC (same as `bitcoin-cli stop`)
     rather than killing the process, so the chainstate/wallet always closes
     cleanly.

BitcoindConfig.exe_path has no sensible default -- it depends entirely on
where you put your compiled binary -- so the app is expected to ask for it
once (see wallet_app.py's first-run settings prompt) and remember it.
"""

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from node_client import BitcoinRPCClient, NodeAuthError, NodeConfig, NodeConnectionError


class NodeLaunchError(RuntimeError):
    """bitcoind either failed to start or never became responsive in time."""


class PortInUseByOtherNodeError(NodeLaunchError):
    """Something is already listening on the configured RPC port, but it
    rejected our RPC credentials -- so it's not "our" node (or at least not
    configured with the username/password in Settings). Launching our own
    bitcoind here would just collide with whatever that is and fail with a
    confusing "Unable to start HTTP server" from bitcoind itself, so this is
    caught and reported clearly instead of attempting that launch at all."""


@dataclass
class BitcoindConfig:
    exe_path: str                       # full path to your compiled bitcoind (or bitcoind.exe)
    datadir: str                        # where this node keeps its chain data + wallet
    network: str = "main"               # "main" or "regtest" -- picks default RPC port + flag
    rpc_user: str = "undercroft"
    rpc_password: str = "undercroft"    # local-only RPC auth; see wallet_app.py settings for changing this
    rpc_port: Optional[int] = None      # None -> project default for `network` (23856 / 23866)
    extra_args: list[str] = field(default_factory=list)
    startup_timeout: float = 60.0       # first launch can be slow (genesis creation, wallet load)
    poll_interval: float = 1.0

    def __post_init__(self):
        if self.rpc_port is None:
            self.rpc_port = 23856 if self.network == "main" else 23866

    def to_node_config(self) -> NodeConfig:
        return NodeConfig(
            rpc_user=self.rpc_user,
            rpc_password=self.rpc_password,
            host="127.0.0.1",
            rpc_port=self.rpc_port,
        )


class NodeManager:
    def __init__(self, config: BitcoindConfig):
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._we_started_it = False
        self._log_path: Optional[Path] = None
        self._log_file = None

    # --- probing -------------------------------------------------------------

    def _probe(self) -> bool:
        """True if something is already answering RPC on this port with OUR
        configured credentials. Raises PortInUseByOtherNodeError (rather
        than just returning False) if something answers but rejects those
        credentials -- that's a real, different node already on this port,
        not an empty port we're free to bind our own daemon to."""
        node_config = self.config.to_node_config()
        node_config.max_retries = 1  # a single quick attempt -- this is just a probe
        node_config.timeout = 3.0
        client = BitcoinRPCClient(node_config)
        try:
            client._call("getblockchaininfo")
            return True
        except NodeAuthError as e:
            raise PortInUseByOtherNodeError(
                f"Something is already running on 127.0.0.1:{self.config.rpc_port} "
                f"(regtest RPC port), but it didn't accept the rpcuser/rpcpassword "
                f"configured in Settings ('{self.config.rpc_user}'). This is most likely "
                f"one of your other manually-started regtest nodes using the same "
                f"port with different credentials. Either: change the RPC port in "
                f"Settings to one nothing else is using (e.g. 23876), or stop that "
                f"other node first, or update the RPC username/password here to "
                f"match whatever that node was started with."
            ) from e
        except Exception:
            return False

    def is_running(self) -> bool:
        return self._probe()

    # --- lifecycle -------------------------------------------------------------

    def ensure_started(self) -> NodeConfig:
        """Idempotent: safe to call once at app startup. Returns the
        NodeConfig the rest of the app should use to talk to it."""
        if self._probe():
            self._we_started_it = False
            return self.config.to_node_config()

        self._launch()
        self._wait_until_ready()
        return self.config.to_node_config()

    def _launch(self) -> None:
        exe = self.config.exe_path
        if not exe or not Path(exe).exists():
            raise NodeLaunchError(
                f"Can't find the Undercroft daemon at '{exe}'. Check the path in "
                "Settings -> Node -> Daemon location."
            )
        if not shutil.os.access(exe, shutil.os.X_OK) and not exe.lower().endswith(".exe"):
            raise NodeLaunchError(
                f"'{exe}' isn't marked as executable. On Linux/WSL: chmod +x '{exe}'"
            )

        Path(self.config.datadir).mkdir(parents=True, exist_ok=True)

        args = [
            exe,
            f"-datadir={self.config.datadir}",
            "-server=1",
            f"-rpcuser={self.config.rpc_user}",
            f"-rpcpassword={self.config.rpc_password}",
            f"-rpcport={self.config.rpc_port}",
            "-listen=1",
            "-printtoconsole=0",
        ]
        if self.config.network == "regtest":
            args.append("-regtest=1")
            # Regtest has no real mempool fee history, especially on a brand
            # new chain -- without a fallback fee, sendtoaddress fails with
            # "Fee estimation failed" until ~a few blocks of history build up.
            args.append("-fallbackfee=0.0001")
        args.extend(self.config.extra_args)

        # Capture stdout+stderr to a log file instead of throwing them away.
        # bitcoind prints its actual startup error (bad arg, port in use,
        # locked datadir, whatever it really is) to stderr on exit -- without
        # this, a failure only ever produces a generic guess instead of the
        # real reason. Truncated fresh each launch attempt so _wait_until_ready
        # can just read this one attempt's output.
        self._log_path = Path(self.config.datadir) / "wallet-launch.log"
        self._log_file = open(self._log_path, "w")

        # On Windows, bitcoind.exe is a console-subsystem program: launched
        # as a plain subprocess it pops open its own visible console window
        # even though this (--windowed) app has none of its own -- breaking
        # the "one app, one window" experience. CREATE_NO_WINDOW tells
        # Windows not to allocate a console for the child at all. This flag
        # doesn't exist on Linux/macOS, so it's only applied on win32.
        popen_kwargs = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self._process = subprocess.Popen(
                args,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                **popen_kwargs,
            )
        except OSError as e:
            raise NodeLaunchError(f"Failed to launch '{exe}': {e}") from e

        self._we_started_it = True

    def _tail_launch_log(self, max_lines: int = 25) -> str:
        try:
            self._log_file.flush()
            lines = self._log_path.read_text(errors="replace").splitlines()
            return "\n".join(lines[-max_lines:]) if lines else "(daemon produced no output at all)"
        except Exception as e:  # noqa: BLE001 -- this is already the error-reporting path
            return f"(couldn't read log: {e})"

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self.config.startup_timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise NodeLaunchError(
                    f"The Undercroft daemon exited immediately (code "
                    f"{self._process.returncode}) instead of starting up. Its own "
                    f"output:\n\n{self._tail_launch_log()}"
                )
            if self._probe():
                return
            time.sleep(self.config.poll_interval)

        raise NodeLaunchError(
            f"The Undercroft daemon didn't respond to RPC within "
            f"{self.config.startup_timeout:.0f}s of starting. Its own output so far:"
            f"\n\n{self._tail_launch_log()}"
        )

    def shutdown(self, timeout: float = 30.0) -> None:
        """Only stops the node if THIS manager started it. If you already
        had a node running (e.g. from your own manual testing) before the
        app launched, this is a no-op -- the app never touches a node it
        didn't start, so it never interrupts something you're doing outside
        the wallet."""
        if not self._we_started_it or self._process is None:
            return

        try:
            client = BitcoinRPCClient(self.config.to_node_config())
            client._call("stop")
        except Exception:
            pass  # fall through to waiting on the process anyway

        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        finally:
            if self._log_file is not None:
                self._log_file.close()
