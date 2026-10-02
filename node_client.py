"""
NodeClient: the interface the pack indexer needs from a blockchain node.

Deliberately tiny -- Bitcoin-Core-compatible nodes (and every coin already
running on hashnomletz, since they're all Core-family forks) expose exactly
this over JSON-RPC:

    getblockcount()          -> current tip height
    getblockhash(height)     -> hex block hash at that height
    getblock(hash, 2)        -> full block, used to pull the coinbase payout

Two implementations exist:

  BitcoinRPCClient  -- talks to a REAL Core-compatible node's JSON-RPC (no
                       external dependencies, stdlib urllib only). Stage 4:
                       now takes a NodeConfig (host/port/rpcuser/rpcpassword),
                       retries transient connection failures, and raises
                       clear, distinct errors for auth failure vs. the node
                       being unreachable vs. an RPC-level error.

  (mock_node.MockRegtestNode implements the same interface in-process, for
  running the indexer/sync_daemon end-to-end without a real node.)

Swapping which one callers use is a one-line change; nothing in reveal.py,
indexer.py, or sync_daemon.py knows or cares which implementation it's
talking to -- they only depend on the NodeClient Protocol below.
"""

import base64
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol


class NodeClient(Protocol):
    def get_block_count(self) -> int: ...
    def get_block_hash(self, height: int) -> str: ...
    def get_block_payout_script(self, height: int) -> str: ...


class NodeConnectionError(RuntimeError):
    """Node unreachable after retries (connection refused, DNS failure, timeout)."""


class NodeAuthError(RuntimeError):
    """RPC call rejected on credentials (HTTP 401) -- check rpc_user/rpc_password."""


class NodeRPCError(RuntimeError):
    """Node reached and authenticated, but the RPC call itself returned an error
    (e.g. bad height, node still warming up / not fully synced)."""


@dataclass
class NodeConfig:
    """Everything needed to reach one Core-compatible daemon's RPC interface.

    Defaults to this project's regtest RPC port (23866, set in the
    chainparamsbase.cpp patch) -- override host/rpc_port for mainnet (23856)
    or a remote hashnomletz node.

    dev_fee_address: only meaningful if/when Undercroft's hashnomletz pool
    config is ever given a dev fee address (confirmed against the pool's
    actual coinbase-construction code: StratumV1Client.ts/MiningJob.ts).
    When set, get_block_payout_script() excludes this address from
    consideration so a dev-fee output never gets mistaken for the miner's
    payout. When None (the default, matching Undercroft's planned 0%-fee
    launch), the pool's own code never emits a dev-fee output at all, so
    there's nothing to exclude. Left as None until/unless a fee is turned on.
    """

    rpc_user: str
    rpc_password: str
    host: str = "127.0.0.1"
    rpc_port: int = 23866
    timeout: float = 10.0
    max_retries: int = 3
    retry_backoff: float = 1.5  # seconds, doubles each retry
    dev_fee_address: Optional[str] = None

    # Stage 5 (wallet_rpc.py): Core's multi-wallet RPC routes wallet-specific
    # calls (getbalance, sendtoaddress, etc.) to /wallet/<name> rather than
    # the base "/" endpoint the indexer's block/chain calls use. Left None
    # here (indexer never sets or needs it) and threaded through _call()'s
    # optional `wallet` argument below -- purely additive, existing callers
    # that never pass `wallet` see the exact same URL/behavior as before.
    wallet_name: Optional[str] = None

    @property
    def url(self) -> str:
        return self.url_for(None)

    def url_for(self, wallet: Optional[str]) -> str:
        base = f"http://{self.host}:{self.rpc_port}/"
        name = wallet if wallet is not None else self.wallet_name
        return f"{base}wallet/{name}" if name else base


class BitcoinRPCClient:
    """Minimal JSON-RPC client for a real Bitcoin-Core-compatible node."""

    def __init__(self, config: NodeConfig):
        self.config = config
        auth = base64.b64encode(f"{config.rpc_user}:{config.rpc_password}".encode()).decode()
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}",
        }

    def _call(self, method: str, params: Optional[list] = None, wallet: Optional[str] = None):
        """wallet: routes to Core's /wallet/<name> multi-wallet RPC endpoint
        instead of the base endpoint. None (the default) means "use
        config.wallet_name if set, else the base endpoint" -- see
        NodeConfig.url_for. The indexer never passes this; wallet_rpc.py's
        WalletRPCClient always does."""
        url = self.config.url_for(wallet)
        payload = json.dumps({
            "jsonrpc": "1.0",
            "id": "pow-tcg-indexer",
            "method": method,
            "params": params or [],
        }).encode()
        req = urllib.request.Request(url, data=payload, headers=self._headers, method="POST")

        last_error: Optional[Exception] = None
        delay = self.config.retry_backoff
        for attempt in range(1, self.config.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    body = json.loads(resp.read())
                if body.get("error"):
                    raise NodeRPCError(f"RPC error calling {method}{params or []}: {body['error']}")
                return body["result"]
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise NodeAuthError(
                        f"RPC auth rejected calling {method} at {url} -- "
                        "check rpc_user/rpc_password"
                    ) from e
                # non-auth HTTP errors (e.g. 500 during node warmup) are worth retrying
                last_error = e
            except (urllib.error.URLError, socket.timeout, ConnectionRefusedError, TimeoutError) as e:
                # node unreachable / not up yet -- retry, it may be mid-restart
                last_error = e

            if attempt < self.config.max_retries:
                time.sleep(delay)
                delay *= 2

        raise NodeConnectionError(
            f"Could not reach node at {url} after {self.config.max_retries} attempts "
            f"calling {method}: {last_error}"
        ) from last_error

    def get_blockchain_info(self) -> dict:
        """Handy for a startup sanity check (chain name, verification progress, tip)."""
        return self._call("getblockchaininfo")

    def get_block_count(self) -> int:
        return self._call("getblockcount")

    def get_block_hash(self, height: int) -> str:
        return self._call("getblockhash", [height])

    @staticmethod
    def _vout_address(script_pub_key: dict) -> Optional[str]:
        """Modern Core exposes a decoded address list directly; fall back to
        the raw script hex for older nodes / nonstandard outputs."""
        addresses = script_pub_key.get("address") or script_pub_key.get("addresses")
        if addresses:
            return addresses if isinstance(addresses, str) else addresses[0]
        return script_pub_key.get("hex")

    def get_block_payout_script(self, height: int) -> str:
        """The finding miner's coinbase payout address for the block at this
        height. Uses getblock verbosity=2 (full tx decode) -- standard on any
        Core-compatible node, no custom RPC needed.

        Deliberately does NOT assume the miner is always vout[0]. Confirmed
        directly against hashnomletz's actual coinbase-construction code
        (StratumV1Client.ts / MiningJob.ts): the coinbase can carry 1-3
        outputs -- an optional dev-fee output (present only when that coin's
        DEV_FEE_ADDRESS is configured; ordered before the miner's when it
        is), the miner's own payout, and an optional trailing OP_RETURN
        witness-commitment output that's never a payout at all. Vout order
        is config-driven, not fixed, so hardcoding an index silently breaks
        the moment a dev fee is turned on (or off) for this coin. Instead:
        drop the witness-commitment output (type == 'nulldata'), drop the
        configured dev_fee_address if one's set, and whatever single output
        is left is the miner's -- matching by address rather than position."""
        block_hash = self.get_block_hash(height)
        block = self._call("getblock", [block_hash, 2])
        coinbase_tx = block["tx"][0]

        candidates = []
        for vout in coinbase_tx["vout"]:
            script_pub_key = vout["scriptPubKey"]
            if script_pub_key.get("type") == "nulldata":
                continue  # OP_RETURN witness commitment -- never a payout
            addr = self._vout_address(script_pub_key)
            if self.config.dev_fee_address and addr == self.config.dev_fee_address:
                continue  # this pool's dev fee output, not the miner
            candidates.append(addr)

        if len(candidates) != 1:
            raise NodeRPCError(
                f"Expected exactly one non-dev-fee, non-witness-commitment coinbase "
                f"output at height {height}, found {len(candidates)}: {candidates}. "
                f"Check dev_fee_address config against this coin's actual pool setup."
            )
        return candidates[0]
