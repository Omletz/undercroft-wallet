#!/usr/bin/env python3
"""
Undercroft Wallet -- the downloadable GUI wallet.

Architecture in one paragraph: this process launches (or attaches to) your
compiled Undercroft bitcoind via node_manager.NodeManager, talks to it over
RPC via wallet_rpc.WalletRPCClient (balance/send/receive/history), and runs
the Stage 4 card indexer (sync_daemon.run_once) itself on every refresh tick,
writing to its own per-datadir SQLite file -- no separate process to start,
nothing to point Settings at. BinderTab then reads that same file read-only
(see BinderTab's docstring) for the Binder tab. Every blocking call (starting
the node, sending a transaction, refreshing balances, the indexer pass)
runs on a background QThread so the window never freezes.

Run directly for development: `python3 wallet_app.py`
The actual double-click entry point is run_undercroft_wallet.py.
"""

import io
import json
import platform
import sqlite3
import sys
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import cards
import db
import queries
import render_card
import set_manifest
import set_registry
import sync_daemon
import theme
from node_client import BitcoinRPCClient, NodeConfig
from node_manager import BitcoindConfig, NodeLaunchError, NodeManager
from reveal import SaltedFutureBlockReveal
from wallet_rpc import DEFAULT_WALLET_NAME, WalletRPCClient

APP_NAME = "Undercroft Wallet"
ASSETS_DIR = Path(__file__).parent / "assets"
BIN_DIR = Path(__file__).parent / "bin"
ART_DIR = Path(__file__).parent / "art"
SETTINGS_PATH = Path.home() / ".undercroft-wallet" / "settings.json"
REFRESH_INTERVAL_MS = 8000
BINDER_THUMB_SIZE = (150, 210)  # 5:7, matching render_card.py's 750x1050 card canvas
BINDER_FULL_SIZE = (500, 700)  # click-to-enlarge size -- same 5:7 ratio, big enough to actually read
BINDER_COLUMNS = 4


def find_bundled_daemon() -> Optional[str]:
    """Every other desktop wallet either ships the node binary inside the
    app or finds it without asking -- making Jonathon hunt down a file path
    by hand defeats the entire "one icon, one double-click" point of this
    app. So: drop your compiled bitcoind (or bitcoind.exe) into a `bin/`
    folder next to this file ONCE, and every future launch finds it here
    automatically -- the Daemon path field in Settings becomes a rarely-
    touched override instead of a required manual step."""
    for name in ("bitcoind.exe", "bitcoind"):
        candidate = BIN_DIR / name
        if candidate.exists():
            return str(candidate)
    return None


# =============================================================================
# Settings: persisted to a small JSON file. Nothing here is secret in a way
# that matters -- rpc_password only guards localhost RPC on your own machine
# -- so plain JSON is fine, no need for anything fancier.
# =============================================================================

@dataclass
class WalletSettings:
    exe_path: str = ""
    datadir: str = ""
    network: str = "regtest"  # "regtest" while testing, "main" once you're ready for real mainnet
    rpc_port: Optional[int] = None
    rpc_user: str = "undercroft"
    rpc_password: str = "undercroft"
    connect_peer: str = ""  # optional "host:port" (P2P port, not RPC) -- see to_bitcoind_config()

    @staticmethod
    def default_datadir(network: str) -> str:
        base = Path.home() / ".undercroft-wallet-data"
        return str(base / network)

    def indexer_db_path(self) -> str:
        # Lives next to this wallet's own chain data, same as
        # receive_history.json -- one card index per network/datadir,
        # maintained automatically (see MainWindow._run_indexer_pass).
        # Nothing to configure: a real user who only downloaded the .exe
        # has no separate process to run or file to go find.
        return str(Path(self.datadir) / "card_index.sqlite3")

    def is_complete(self) -> bool:
        return bool(self.exe_path) and bool(self.datadir)

    @classmethod
    def load(cls) -> "WalletSettings":
        settings = cls()
        if SETTINGS_PATH.exists():
            try:
                data = json.loads(SETTINGS_PATH.read_text())
                settings = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError):
                pass

        # If there's no exe_path set, or the one we had no longer exists
        # (moved/reinstalled), prefer a bundled binary over making the user
        # type a path -- see find_bundled_daemon()'s docstring.
        if not settings.exe_path or not Path(settings.exe_path).exists():
            bundled = find_bundled_daemon()
            if bundled:
                settings.exe_path = bundled

        return settings

    def save(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(asdict(self), indent=2))

    def to_bitcoind_config(self) -> BitcoindConfig:
        # addnode (not connect=) so this adds one known peer without
        # disabling normal outbound peer discovery -- matters once there's
        # an actual wider network to also find, not just this one peer.
        extra_args = [f"-addnode={self.connect_peer.strip()}"] if self.connect_peer.strip() else []
        return BitcoindConfig(
            exe_path=self.exe_path,
            datadir=self.datadir,
            network=self.network,
            rpc_user=self.rpc_user,
            rpc_password=self.rpc_password,
            rpc_port=self.rpc_port,
            extra_args=extra_args,
        )


class SettingsDialog(QDialog):
    """Shown on first run (no usable settings yet) and reachable later from
    the Overview tab's "Settings" button. First-run copy is a little more
    explanatory since a non-coder is filling this in without prior context."""

    def __init__(self, settings: WalletSettings, first_run: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} -- Setup" if first_run else f"{APP_NAME} -- Settings")
        self.setMinimumWidth(520)
        self._settings = settings

        layout = QVBoxLayout(self)

        bundled = find_bundled_daemon()

        if first_run:
            if bundled:
                intro = QLabel(
                    "One-time setup -- found your Undercroft daemon automatically. "
                    "Just confirm a folder below for it to keep its chain data in."
                )
            else:
                intro = QLabel(
                    "One-time setup: no daemon found in this app's bin/ folder yet. "
                    f"Copy your compiled bitcoind (or bitcoind.exe) into "
                    f"'{BIN_DIR}' and it'll be found automatically next time -- "
                    "or point at it manually below just for now."
                )
            intro.setWordWrap(True)
            intro.setProperty("role", "muted")
            layout.addWidget(intro)

        form = QFormLayout()

        self.exe_edit = QLineEdit(settings.exe_path)
        exe_browse = QPushButton("Browse...")
        exe_browse.clicked.connect(self._browse_exe)
        exe_row = QHBoxLayout()
        exe_row.addWidget(self.exe_edit)
        exe_row.addWidget(exe_browse)
        exe_label = "Daemon path (auto-detected):" if bundled else "Daemon (bitcoind) path:"
        form.addRow(exe_label, exe_row)

        self.network_combo = QComboBox()
        self.network_combo.addItems(["regtest", "main"])
        self.network_combo.setCurrentText(settings.network)
        self.network_combo.currentTextChanged.connect(self._suggest_datadir)
        form.addRow("Network:", self.network_combo)

        self.datadir_edit = QLineEdit(settings.datadir or WalletSettings.default_datadir(settings.network))
        datadir_browse = QPushButton("Browse...")
        datadir_browse.clicked.connect(self._browse_datadir)
        datadir_row = QHBoxLayout()
        datadir_row.addWidget(self.datadir_edit)
        datadir_row.addWidget(datadir_browse)
        form.addRow("Data directory:", datadir_row)

        self.rpc_port_edit = QLineEdit(str(settings.rpc_port) if settings.rpc_port else "")
        self.rpc_port_edit.setPlaceholderText("default for network (23856 main / 23866 regtest)")
        form.addRow("RPC port (optional):", self.rpc_port_edit)

        self.rpc_user_edit = QLineEdit(settings.rpc_user)
        form.addRow("RPC username:", self.rpc_user_edit)

        self.rpc_password_edit = QLineEdit(settings.rpc_password)
        self.rpc_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("RPC password:", self.rpc_password_edit)

        self.connect_peer_edit = QLineEdit(settings.connect_peer)
        self.connect_peer_edit.setPlaceholderText("e.g. 192.168.1.50:23867 -- leave blank if you don't have one")
        form.addRow("Connect to peer (optional):", self.connect_peer_edit)

        note = QLabel(
            "\"Connect to peer\" is another Undercroft node's network address, if you know "
            "one -- use its P2P port, not its RPC port (regtest: 23867, mainnet: 23857). "
            "Without this, your node only sees blocks/transactions that happen on its own -- "
            "it won't automatically find other Undercroft nodes."
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")

        layout.addLayout(form)
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _suggest_datadir(self, network: str) -> None:
        if not self.datadir_edit.text() or self.datadir_edit.text() in (
            WalletSettings.default_datadir("main"),
            WalletSettings.default_datadir("regtest"),
        ):
            self.datadir_edit.setText(WalletSettings.default_datadir(network))

    def _browse_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select the Undercroft daemon executable")
        if path:
            self.exe_edit.setText(path)

    def _browse_datadir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select (or create) a data directory")
        if path:
            self.datadir_edit.setText(path)

    def result_settings(self) -> WalletSettings:
        port_text = self.rpc_port_edit.text().strip()
        return WalletSettings(
            exe_path=self.exe_edit.text().strip(),
            datadir=self.datadir_edit.text().strip(),
            network=self.network_combo.currentText(),
            rpc_port=int(port_text) if port_text.isdigit() else None,
            rpc_user=self.rpc_user_edit.text().strip() or "undercroft",
            rpc_password=self.rpc_password_edit.text() or "undercroft",
            connect_peer=self.connect_peer_edit.text().strip(),
        )


# =============================================================================
# Background work: nothing that touches the node or the network runs on the
# UI thread. Worker is intentionally generic (runs any callable) rather than
# one subclass per RPC call.
# =============================================================================

class Worker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as e:  # noqa: BLE001 -- surfaced to the user, not swallowed
            self.failed.emit(f"{e}")
        else:
            self.succeeded.emit(result)


# =============================================================================
# Startup screen: shown while bitcoind boots (can take a while on first run).
# =============================================================================

class StartupWidget(QWidget):
    def __init__(self, settings: WalletSettings, on_ready: Callable[[NodeManager, WalletRPCClient], None]):
        super().__init__()
        self._settings = settings
        self._on_ready = on_ready
        self._manager: Optional[NodeManager] = None
        self._worker: Optional[Worker] = None

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(APP_NAME)
        title.setProperty("role", "heading")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(theme.base_font(20, bold=True))

        self.status_label = QLabel("Starting the Undercroft node...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setFixedWidth(320)

        self.retry_button = QPushButton("Retry")
        self.retry_button.clicked.connect(self.start)
        self.retry_button.hide()

        self.settings_button = QPushButton("Edit Settings")
        self.settings_button.clicked.connect(self._edit_settings)
        self.settings_button.hide()

        button_row = QHBoxLayout()
        button_row.addWidget(self.retry_button)
        button_row.addWidget(self.settings_button)

        layout.addWidget(title)
        layout.addSpacing(20)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(button_row)

    def start(self) -> None:
        self.retry_button.hide()
        self.settings_button.hide()
        self.progress.show()
        self.status_label.setText(
            f"Starting the Undercroft node ({self._settings.network})...\n"
            "This can take up to a minute on first launch."
        )

        self._manager = NodeManager(self._settings.to_bitcoind_config())
        self._worker = Worker(self._start_node_and_wallet)
        self._worker.succeeded.connect(self._on_started)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _start_node_and_wallet(self) -> WalletRPCClient:
        # Best-effort check for new card sets before anything else -- runs
        # off the UI thread same as the node startup below, and never
        # raises (see set_manifest.sync_sets()'s own docstring) so a slow
        # or unreachable GitHub can never delay or break wallet startup.
        # Silent on failure/no-op by design: the Binder tab and reveal
        # logic both fall back to whatever's already cached (which always
        # includes Set 1) regardless of whether this found anything new.
        set_manifest.sync_sets()

        node_config: NodeConfig = self._manager.ensure_started()
        wallet = WalletRPCClient(node_config, wallet_name=DEFAULT_WALLET_NAME)
        wallet.ensure_wallet_ready()
        return wallet

    def _on_started(self, wallet: WalletRPCClient) -> None:
        self.status_label.setText("Connected.")
        self._on_ready(self._manager, wallet)

    def _on_failed(self, message: str) -> None:
        self.progress.hide()
        self.status_label.setText(f"Couldn't start the node:\n\n{message}")
        self.retry_button.show()
        self.settings_button.show()

    def _edit_settings(self) -> None:
        dialog = SettingsDialog(self._settings, first_run=False, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._settings = dialog.result_settings()
            self._settings.save()
            self.start()


# =============================================================================
# Tabs
# =============================================================================

def _panel(*widgets: QWidget) -> QFrame:
    frame = QFrame()
    frame.setProperty("role", "panel")
    v = QVBoxLayout(frame)
    for w in widgets:
        v.addWidget(w)
    return frame


class OverviewTab(QWidget):
    def __init__(self, main_window: "MainWindow"):
        super().__init__()
        self.main_window = main_window

        self.network_label = QLabel("--")
        self.height_label = QLabel("--")
        self.balance_label = QLabel("0.00000000 UCFT")
        self.balance_label.setProperty("role", "value")
        self.pending_label = QLabel("")
        self.pending_label.setProperty("role", "muted")

        heading = QLabel("Balance")
        heading.setProperty("role", "heading")

        balance_panel = _panel(heading, self.balance_label, self.pending_label)

        banner_label = QLabel()
        banner_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        banner_path = ASSETS_DIR / "logo_banner.png"
        if banner_path.exists():
            banner_pixmap = QPixmap(str(banner_path))
            if not banner_pixmap.isNull():
                # Cap the width so it never dominates the tab on a small window --
                # scaled proportionally, so it still looks right at any DPI.
                scaled = banner_pixmap.scaledToWidth(360, Qt.TransformationMode.SmoothTransformation)
                banner_label.setPixmap(scaled)

        status_form = QFormLayout()
        status_form.addRow("Network:", self.network_label)
        status_form.addRow("Block height:", self.height_label)
        status_panel = QFrame()
        status_panel.setProperty("role", "panel")
        status_panel.setLayout(status_form)

        settings_button = QPushButton("Settings")
        settings_button.clicked.connect(self.main_window.open_settings)

        layout = QVBoxLayout(self)
        layout.addWidget(banner_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(balance_panel)
        layout.addWidget(status_panel)
        layout.addStretch()
        layout.addWidget(settings_button, alignment=Qt.AlignmentFlag.AlignRight)

    def update_data(self, info: dict) -> None:
        self.network_label.setText(info.get("chain", "--"))
        self.height_label.setText(str(info.get("blocks", "--")))
        balances = info.get("balances", {})
        trusted = balances.get("mine", {}).get("trusted", 0.0)
        pending = balances.get("mine", {}).get("untrusted_pending", 0.0)
        immature = balances.get("mine", {}).get("immature", 0.0)
        self.balance_label.setText(f"{trusted:.8f} UCFT")
        extras = []
        if pending:
            extras.append(f"{pending:.8f} pending")
        if immature:
            extras.append(f"{immature:.8f} immature (coinbase maturity)")
        self.pending_label.setText(" | ".join(extras))


class SendTab(QWidget):
    def __init__(self, main_window: "MainWindow"):
        super().__init__()
        self.main_window = main_window

        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("Recipient Undercroft address")
        self.amount_edit = QLineEdit()
        self.amount_edit.setPlaceholderText("Amount (UCFT)")
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("Note to self (optional, stored only in your own wallet)")

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._on_send_clicked)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("To:", self.address_edit)
        form.addRow("Amount:", self.amount_edit)
        form.addRow("Note:", self.comment_edit)

        panel = QFrame()
        panel.setProperty("role", "panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.addLayout(form)
        panel_layout.addWidget(self.send_button, alignment=Qt.AlignmentFlag.AlignRight)

        layout = QVBoxLayout(self)
        layout.addWidget(panel)
        layout.addWidget(self.status_label)
        layout.addStretch()

    def _on_send_clicked(self) -> None:
        address = self.address_edit.text().strip()
        amount_text = self.amount_edit.text().strip()

        if not address:
            self._set_status("Enter a recipient address.", error=True)
            return
        try:
            amount = float(amount_text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            self._set_status("Enter a valid amount greater than zero.", error=True)
            return

        confirm = QMessageBox.question(
            self,
            "Confirm send",
            f"Send {amount:.8f} UCFT to:\n\n{address}\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.send_button.setEnabled(False)
        self._set_status("Sending...")

        worker = Worker(
            self.main_window.wallet.send_to_address,
            address,
            amount,
            self.comment_edit.text().strip(),
        )
        worker.succeeded.connect(self._on_sent)
        worker.failed.connect(self._on_send_failed)
        self.main_window.run_worker(worker)

    def _on_sent(self, txid: str) -> None:
        self.send_button.setEnabled(True)
        self._set_status(f"Sent. Transaction ID:\n{txid}")
        self.address_edit.clear()
        self.amount_edit.clear()
        self.comment_edit.clear()
        self.main_window.refresh_now()

    def _on_send_failed(self, message: str) -> None:
        self.send_button.setEnabled(True)
        self._set_status(message, error=True)

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_label.setProperty("role", "error" if error else "muted")
        self.status_label.setText(text)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)


class ReceiveTab(QWidget):
    """Keeps a locally-persisted history of every address this wallet has
    generated (address/label/created_at), the same way Bitcoin Core's own
    Qt wallet keeps its "recent requests" list -- that data isn't something
    the daemon tracks for you via RPC, so it's stored as a small JSON file
    next to this wallet's own datadir (one history per network/datadir,
    same as the chain data itself). Deliberately does NOT auto-generate a
    fresh address every time this tab is opened -- a normal wallet keeps
    showing you the same "current" address until you actually ask for a
    new one, rather than burning a new one on every launch.

    Also deliberately does NOT auto-generate an address on first boot
    either (a brand new datadir with no history yet): it shows an empty
    state instead and waits for an explicit "Generate New Address" click.
    A silently-created address at boot is exactly the kind of thing that
    could get displayed/copied before anyone's consciously looked at it --
    a real risk if it's then handed to a miner/pool as a payout address
    without the user having deliberately generated it for this wallet."""

    HISTORY_COLUMNS = ["Date", "Label", "Address", "Received (UCFT)"]

    def __init__(self, main_window: "MainWindow"):
        super().__init__()
        self.main_window = main_window
        self._history: list[dict] = []
        self._received_by_address: dict[str, float] = {}

        heading = QLabel("Your receiving address")
        heading.setProperty("role", "heading")

        self.address_display = QLineEdit()
        self.address_display.setReadOnly(True)
        self.address_display.setFont(theme.base_font(12, bold=True))
        self.address_display.setPlaceholderText(
            "No address yet -- click \"Generate New Address\" below"
        )

        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self._copy_address)
        self.copy_button.setEnabled(False)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Label for this address (optional)")

        new_button = QPushButton("Generate New Address")
        new_button.clicked.connect(self._generate_new)

        row = QHBoxLayout()
        row.addWidget(self.address_display)
        row.addWidget(self.copy_button)

        note = QLabel(
            "Generating a new address doesn't retire the old ones -- every address "
            "below still counts for your Binder collection and can still receive "
            "coins, even if you remove it from this list (Remove only tidies up "
            "this list; it's local to this wallet, not something on chain). "
            "Double-click a row to bring that address back up above."
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")

        panel = _panel(heading)
        panel.layout().addLayout(row)
        panel.layout().addWidget(self.label_edit)
        panel.layout().addWidget(new_button)

        history_heading = QLabel("Address history")
        history_heading.setProperty("role", "heading")

        self.history_table = QTableWidget(0, len(self.HISTORY_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(self.HISTORY_COLUMNS)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.itemDoubleClicked.connect(self._use_history_row)

        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(self._remove_selected)

        layout = QVBoxLayout(self)
        layout.addWidget(panel)
        layout.addWidget(note)
        layout.addWidget(history_heading)
        layout.addWidget(self.history_table)
        layout.addWidget(remove_button, alignment=Qt.AlignmentFlag.AlignRight)

        self._loaded = False

    # --- local history persistence -------------------------------------------

    def _history_path(self) -> Path:
        # Lives next to this wallet's own chain data -- so regtest and
        # mainnet (or two different datadirs) each get their own history,
        # same as everything else datadir-scoped.
        return Path(self.main_window.settings.datadir) / "receive_history.json"

    def _load_history(self) -> None:
        path = self._history_path()
        try:
            self._history = json.loads(path.read_text()) if path.exists() else []
        except (ValueError, OSError):
            self._history = []

    def _save_history(self) -> None:
        try:
            path = self._history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._history, indent=2))
        except OSError:
            pass  # non-critical -- worst case this session's new entries don't persist to next launch

    # --- lifecycle -------------------------------------------------------------

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._load_history()
        if self._history:
            self.address_display.setText(self._history[-1]["address"])
            self.copy_button.setEnabled(True)
            self._refresh_table()
        # else: brand new datadir, no address yet -- leave the empty state
        # showing (placeholder text, Copy disabled) rather than generating
        # one now. See the class docstring for why.

    def _generate_new(self) -> None:
        label = self.label_edit.text().strip()
        worker = Worker(self.main_window.wallet.get_new_address, label, "bech32")
        worker.succeeded.connect(lambda address: self._on_generated(address, label))
        worker.failed.connect(lambda msg: self.main_window.show_error("Couldn't generate address", msg))
        self.main_window.run_worker(worker)

    def _on_generated(self, address: str, label: str) -> None:
        self.address_display.setText(address)
        self.copy_button.setEnabled(True)
        self.label_edit.clear()
        self._history.append(
            {
                "address": address,
                "label": label,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )
        self._save_history()
        self._refresh_table()

    def update_received(self, rows: list[dict]) -> None:
        """Called every refresh cycle with live listreceivedbyaddress data
        (see MainWindow._gather_refresh_data), so the Received column
        reflects real chain activity without this tab needing its own
        polling -- purely a display update, never touches self._history."""
        self._received_by_address = {r["address"]: r.get("amount", 0.0) for r in rows}
        self._refresh_table()

    def _refresh_table(self) -> None:
        rows = list(reversed(self._history))  # newest first
        self.history_table.setRowCount(len(rows))
        for row_idx, entry in enumerate(rows):
            address = entry.get("address", "")
            values = [
                entry.get("created_at", ""),
                entry.get("label", ""),
                address,
                f"{self._received_by_address.get(address, 0.0):.8f}",
            ]
            for col, value in enumerate(values):
                self.history_table.setItem(row_idx, col, QTableWidgetItem(value))

    def _use_history_row(self, item: QTableWidgetItem) -> None:
        address_item = self.history_table.item(item.row(), 2)
        if address_item is None:
            return
        self.address_display.setText(address_item.text())
        QApplication.clipboard().setText(address_item.text())
        self.main_window.statusBar().showMessage("Address copied", 3000)

    def _remove_selected(self) -> None:
        row = self.history_table.currentRow()
        if row < 0:
            return
        address_item = self.history_table.item(row, 2)
        if address_item is None:
            return
        address = address_item.text()
        self._history = [e for e in self._history if e.get("address") != address]
        self._save_history()
        self._refresh_table()

    def _copy_address(self) -> None:
        text = self.address_display.text()
        if text:
            QApplication.clipboard().setText(text)
            self.main_window.statusBar().showMessage("Address copied", 3000)


class HistoryTab(QWidget):
    COLUMNS = ["Time", "Type", "Amount (UCFT)", "Confirmations", "Address", "Txid"]

    def __init__(self, main_window: "MainWindow"):
        super().__init__()
        self.main_window = main_window

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)

    def update_data(self, transactions: list[dict]) -> None:
        self.table.setRowCount(len(transactions))
        for row, tx in enumerate(transactions):
            when = datetime.fromtimestamp(tx.get("time", 0)).strftime("%Y-%m-%d %H:%M")
            category = tx.get("category", "?")
            amount = tx.get("amount", 0.0)
            confirmations = tx.get("confirmations", 0)
            address = tx.get("address", "")
            txid = tx.get("txid", "")

            values = [when, category, f"{amount:.8f}", str(confirmations), address, txid]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 2:
                    item.setForeground(
                        theme.qcolor(theme.COLOR_GREEN if amount >= 0 else theme.COLOR_ERROR)
                    )
                self.table.setItem(row, col, item)


class CardTile(QFrame):
    """A Binder grid tile that's actually clickable -- plain QFrame has no
    click signal of its own, so this just overrides mousePressEvent to call
    back into BinderTab rather than pulling in a heavier QPushButton-based
    tile (which would need its own restyling to stop looking like a button)."""

    def __init__(self, on_click: Callable[[], None]):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mousePressEvent(event)


class CardViewerDialog(QDialog):
    """Opened by clicking a Binder tile. The grid thumbnails are
    deliberately small (BINDER_THUMB_SIZE) so a full binder page of cards
    fits on screen without endless scrolling, which makes the card's own
    printed text (flavor text, depth/location, telemetry) too small to
    actually read -- the entire point of the 9 hours spent on card art in
    the first place. This re-renders the same card at a real readable size
    in its own window rather than just stretching the blurry thumbnail."""

    def __init__(self, card, pixmap: QPixmap, location_text: str, height_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(card.name)

        image_label = QLabel()
        image_label.setPixmap(pixmap)
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        info_text = card.description
        if location_text:
            info_text += f"\n\n{location_text}"
        info_text += f"\n{height_text}"
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setProperty("role", "muted")
        info_label.setMaximumWidth(pixmap.width())

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(image_label)
        layout.addWidget(info_label)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignHCenter)


class BinderTab(QWidget):
    """Reads the card indexer's SQLite file READ-ONLY. MainWindow runs the
    actual indexer pass itself (see MainWindow._run_indexer_pass) on the
    same background-thread refresh tick as everything else -- there's no
    separate process for this tab to wait on, and nothing in Settings to
    point at it; it's just <datadir>/card_index.sqlite3. This tab still
    deliberately never calls db.open_db()/init_db() directly (that path
    reseeds the card catalog and commits on every call) -- it only ever
    needs SELECTs, so it opens the file in SQLite's own read-only URI mode
    instead, which can't block or be blocked by the indexer pass's writes
    (those always finish and close before this tab's own read runs)."""

    TIER_LABEL = {"uncommon": "Uncommon", "holo_rare": "Holo Rare", "secret_rare": "Secret Rare"}

    @staticmethod
    def _card_location_text(card_id: str) -> str:
        """cards.py's card_catalog table (in db.py) only mirrors card_id/tier/
        name/description -- not depth/location -- so this looks the actual
        Card object up from cards.py directly for that column. Schema-
        flexible the same way render_card.py's _card_sector_text is: your
        local cards.py (built by the other LLM) uses a combined `sector`
        field instead of separate depth/location, so both shapes are
        supported rather than assuming one."""
        for pool in cards.CARD_SETS.values():
            for c in pool:
                if c.card_id != card_id:
                    continue
                if hasattr(c, "location"):
                    return f"Depth {c.depth} // {c.location}"
                if hasattr(c, "sector"):
                    return c.sector
                return ""
        return ""

    def __init__(self, main_window: "MainWindow"):
        super().__init__()
        self.main_window = main_window
        self._render_cache: dict[str, bytes] = {}
        self._pixmap_cache: dict[str, QPixmap] = {}
        self._full_pixmap_cache: dict[str, QPixmap] = {}

        # "New since you last looked" tracking for the Binder(N) tab badge --
        # identifies a revealed card instance by its block_hash (one card
        # per block, see queries.get_cards_by_owner), persisted the same way
        # receive_history.json is: a small per-datadir JSON file, so it
        # survives a restart instead of re-flagging everything as new.
        self._seen_hashes: set[str] = self._load_seen()
        self._last_all_hashes: set[str] = set()
        self.new_card_count = 0

        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "heading")

        self.reveal_note = QLabel(
            "Cards can show up in a batch even without new mining -- a card "
            "only needs its block to reach 30 blocks deep to reveal, so "
            "several earlier blocks can cross that line at once as the "
            "chain advances. (This is separate from the 100-block coin "
            "maturity rule -- a card can reveal well before its coinbase "
            "reward is actually spendable.)"
        )
        self.reveal_note.setWordWrap(True)
        self.reveal_note.setProperty("role", "muted")

        # A real binder page: a scrollable grid of the actual rendered card
        # art (via render_card.render_card_image -- the same compositing
        # pipeline the master card renders use), not a text table. Cards
        # mined more than once are grouped into a single tile with an "xN"
        # badge rather than repeating the same art tile N times.
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(14)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(self.grid_container)

        self.empty_label = QLabel(
            "No card data yet. This fills in automatically once a block you've "
            "mined has matured and revealed a pack to one of this wallet's "
            "addresses -- may take a few refresh cycles after that happens."
        )
        self.empty_label.setWordWrap(True)
        self.empty_label.setProperty("role", "muted")
        self.empty_label.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(self.reveal_note)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.empty_label)

    # --- "seen" persistence (per-datadir, same pattern as receive_history.json) --

    def _seen_path(self) -> Path:
        return Path(self.main_window.settings.datadir) / "binder_seen.json"

    def _load_seen(self) -> set:
        path = self._seen_path()
        try:
            return set(json.loads(path.read_text())) if path.exists() else set()
        except (ValueError, OSError):
            return set()

    def _save_seen(self) -> None:
        try:
            path = self._seen_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(sorted(self._seen_hashes)))
        except OSError:
            pass  # non-critical -- worst case the badge re-flags these as new next launch

    def mark_seen(self) -> None:
        """Call when the user actually looks at the Binder tab -- everything
        currently shown no longer counts as new."""
        if not self.new_card_count:
            return
        self._seen_hashes = set(self._last_all_hashes)
        self._save_seen()
        self.new_card_count = 0

    def _render_bytes(self, card) -> bytes:
        """PNG bytes of this card's real art via the same
        render_card_image() pipeline the master Silicon Strata renders use
        -- never a placeholder or text stand-in. Cached per card_id since
        re-compositing a full 750x1050 image (holo foil / secret-rare
        shimmer included) is real work, and both the grid thumbnail and
        the click-to-enlarge view just scale the same render down to
        different sizes rather than each triggering their own render.

        Resolves art via set_registry -- bundled Set 1 art for "S1-..."
        card ids, the locally-cached download folder for anything from a
        later set fetched through the card-set manifest (see
        set_manifest.py). Falls back to render_card's schematic placeholder
        if the art genuinely isn't available yet (e.g. a new set's
        manifest entry was seen but its art hasn't finished downloading)."""
        cached = self._render_cache.get(card.card_id)
        if cached is not None:
            return cached
        art_path = set_registry.art_path_for(card.card_id)
        img = render_card.render_card_image(
            card, art_path=str(art_path) if art_path is not None else None
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        self._render_cache[card.card_id] = data
        return data

    def _card_pixmap(self, card) -> QPixmap:
        """The small grid-tile thumbnail, cached per card_id -- re-scaling
        on every ~8s refresh would be pure waste once a card's art can't
        change underneath it."""
        cached = self._pixmap_cache.get(card.card_id)
        if cached is not None:
            return cached
        pixmap = QPixmap()
        pixmap.loadFromData(self._render_bytes(card))
        pixmap = pixmap.scaled(
            BINDER_THUMB_SIZE[0],
            BINDER_THUMB_SIZE[1],
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._pixmap_cache[card.card_id] = pixmap
        return pixmap

    def _full_pixmap(self, card) -> QPixmap:
        """The larger click-to-enlarge size -- same render as the thumbnail,
        just scaled up enough that the card's own printed text is actually
        legible instead of a blur."""
        cached = self._full_pixmap_cache.get(card.card_id)
        if cached is not None:
            return cached
        pixmap = QPixmap()
        pixmap.loadFromData(self._render_bytes(card))
        pixmap = pixmap.scaled(
            BINDER_FULL_SIZE[0],
            BINDER_FULL_SIZE[1],
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._full_pixmap_cache[card.card_id] = pixmap
        return pixmap

    def _open_card_viewer(self, card, height_text: str) -> None:
        dialog = CardViewerDialog(
            card,
            self._full_pixmap(card),
            self._card_location_text(card.card_id),
            height_text,
            parent=self,
        )
        dialog.exec()

    def _build_tile(self, card, count: int, heights: list) -> QWidget:
        sorted_heights = sorted(h for h in heights if h is not None)
        height_text = (
            f"Minted at height {sorted_heights[0]}"
            if len(sorted_heights) == 1
            else f"Minted at heights {', '.join(str(h) for h in sorted_heights)}"
        )

        # Clicking anywhere on the tile opens a full-size, actually-readable
        # view of the card (see CardViewerDialog) -- the thumbnail is too
        # small for the printed flavor text/telemetry to be legible.
        tile = CardTile(on_click=lambda: self._open_card_viewer(card, height_text))
        tile.setProperty("role", "panel")
        accent = theme.TIER_ACCENT.get(card.tier, theme.COLOR_TEXT)
        tile.setStyleSheet(
            f"QFrame {{ border: 2px solid {theme.hexs(accent)}; border-radius: 4px; }}"
        )

        v = QVBoxLayout(tile)
        v.setContentsMargins(8, 8, 8, 8)

        # Qt doesn't bubble an unhandled mouse press up from a child widget
        # to its parent the way a browser bubbles DOM events -- so without
        # WA_TransparentForMouseEvents on each label, clicking the art or
        # text (i.e. almost the entire tile) would swallow the click
        # instead of reaching CardTile.mousePressEvent below.
        art_label = QLabel()
        art_label.setPixmap(self._card_pixmap(card))
        art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        v.addWidget(art_label)

        name_text = card.name if count <= 1 else f"{card.name}  x{count}"
        name_label = QLabel(name_text)
        name_label.setWordWrap(True)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setProperty("role", "value")
        name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        v.addWidget(name_label)

        tier_label = QLabel(self.TIER_LABEL.get(card.tier, card.tier))
        tier_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tier_label.setStyleSheet(f"color: {theme.hexs(accent)};")
        tier_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        v.addWidget(tier_label)

        tile.setToolTip(
            f"{card.description}\n\n{self._card_location_text(card.card_id)}\n{height_text}"
            f"\n\n(click to enlarge)"
        )
        return tile

    def _clear_grid(self) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    def refresh(self, db_path: str, addresses: list[str]) -> None:
        if not db_path or not Path(db_path).exists() or not addresses:
            self.new_card_count = 0
            self._show_empty()
            return

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.OperationalError:
            self.new_card_count = 0
            self._show_empty()
            return

        try:
            cards_owned: list[dict] = []
            for address in addresses:
                cards_owned.extend(queries.get_cards_by_owner(conn, address))
        except sqlite3.OperationalError:
            # schema not created yet (indexer hasn't run a sync pass at all)
            self.new_card_count = 0
            self._show_empty()
            return
        finally:
            conn.close()

        # block_hash uniquely identifies one revealed card instance (one
        # card per block) -- diffing against what's been marked seen is what
        # drives the Binder(N) tab badge, independent of whether the grid
        # below actually has anything to show right now.
        self._last_all_hashes = {row["block_hash"] for row in cards_owned}
        self.new_card_count = len(self._last_all_hashes - self._seen_hashes)

        if not cards_owned:
            self._show_empty()
            return

        # Group by card_id -- mining the same card more than once is normal,
        # and the binder should show one tile per unique card with an "xN"
        # badge rather than the same art repeated N times in the grid.
        grouped: dict[str, dict] = {}
        for row in cards_owned:
            card_id = row.get("card_id", "")
            entry = grouped.setdefault(card_id, {"count": 0, "heights": []})
            entry["count"] += 1
            entry["heights"].append(row.get("mint_height"))

        self._clear_grid()

        shown = 0
        for card_id, info in sorted(grouped.items()):
            card_obj = render_card.get_card_by_id(card_id)
            if card_obj is None:
                continue  # revealed card_id not in the local catalog -- skip rather than crash
            tile = self._build_tile(card_obj, info["count"], info["heights"])
            r, c = divmod(shown, BINDER_COLUMNS)
            self.grid_layout.addWidget(tile, r, c)
            shown += 1

        if shown == 0:
            self._show_empty()
            return

        self.scroll_area.show()
        self.summary_label.show()
        self.empty_label.hide()
        total_cards = sum(v["count"] for v in grouped.values())
        self.summary_label.setText(f"{total_cards} card(s) in your Binder ({shown} unique)")

    def _show_empty(self) -> None:
        self._clear_grid()
        self.scroll_area.hide()
        self.summary_label.hide()
        self.empty_label.show()


# =============================================================================
# Main window
# =============================================================================

class MainWindow(QMainWindow):
    def __init__(self, settings: WalletSettings, manager: NodeManager, wallet: WalletRPCClient):
        super().__init__()
        self.settings = settings
        self.manager = manager
        self.wallet = wallet
        self._active_workers: list[Worker] = []
        self._known_addresses: list[str] = []
        # A plain (non-wallet-scoped) RPC client for the embedded card
        # indexer -- separate from self.wallet's WalletRPCClient since the
        # indexer only ever needs base-endpoint chain/block calls (see
        # node_client.py's NodeClient protocol), never the /wallet/<name>
        # endpoint. maturity=30 matches every other caller in this project
        # (run_demo.py, sync_daemon.py's own default, cli.py's default).
        self._indexer_node = BitcoinRPCClient(self.manager.config.to_node_config())
        self._indexer_scheme = SaltedFutureBlockReveal(maturity=30)

        self.setWindowTitle(APP_NAME)
        icon_path = ASSETS_DIR / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(760, 560)

        self.overview_tab = OverviewTab(self)
        self.send_tab = SendTab(self)
        self.receive_tab = ReceiveTab(self)
        self.history_tab = HistoryTab(self)
        self.binder_tab = BinderTab(self)

        tabs = QTabWidget()
        tabs.addTab(self.overview_tab, "Overview")
        tabs.addTab(self.send_tab, "Send")
        tabs.addTab(self.receive_tab, "Receive")
        tabs.addTab(self.history_tab, "History")
        tabs.addTab(self.binder_tab, "Binder")
        tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(tabs)
        self._tabs = tabs

        self.statusBar().showMessage(f"Connected -- {settings.network}")

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_now)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)

        self.receive_tab.ensure_loaded()
        self.refresh_now()

    def _on_tab_changed(self, index: int) -> None:
        if self._tabs.widget(index) is self.receive_tab:
            self.receive_tab.ensure_loaded()
        if self._tabs.widget(index) is self.binder_tab:
            self.binder_tab.mark_seen()
            self._update_binder_tab_label()

    def _update_binder_tab_label(self) -> None:
        idx = self._tabs.indexOf(self.binder_tab)
        count = self.binder_tab.new_card_count
        self._tabs.setTabText(idx, f"Binder ({count})" if count else "Binder")

    def run_worker(self, worker: Worker) -> None:
        """Keeps a reference so the QThread isn't garbage-collected mid-run,
        and cleans itself up from the tracking list when done."""
        self._active_workers.append(worker)

        def _cleanup(*_args):
            if worker in self._active_workers:
                self._active_workers.remove(worker)

        worker.succeeded.connect(_cleanup)
        worker.failed.connect(_cleanup)
        worker.start()

    def refresh_now(self) -> None:
        worker = Worker(self._gather_refresh_data)
        worker.succeeded.connect(self._apply_refresh)
        worker.failed.connect(lambda msg: self.statusBar().showMessage(f"Refresh failed: {msg}", 5000))
        self.run_worker(worker)

    def _gather_refresh_data(self) -> dict:
        info = self.wallet.get_blockchain_info()
        info["balances"] = self.wallet.get_balances()
        transactions = self.wallet.list_transactions(count=100)
        # One call serves both the Binder tab's "every address I own" list
        # and the Receive tab's per-address received-amount column, instead
        # of each independently calling listreceivedbyaddress.
        received_rows = self.wallet.list_received_by_address()
        addresses = [row["address"] for row in received_rows]
        indexer_error = self._run_indexer_pass()
        return {
            "info": info,
            "transactions": transactions,
            "addresses": addresses,
            "received_rows": received_rows,
            "indexer_error": indexer_error,
        }

    def _run_indexer_pass(self) -> Optional[str]:
        """One Stage 4 card-indexer sync pass against this wallet's own
        per-datadir SQLite file (see WalletSettings.indexer_db_path) --
        runs here, on the same background-thread refresh tick as everything
        else, so there's no separate sync_daemon.py process for a real user
        to run or point Settings at. db.open_db() creates the file/schema
        on first call, so nothing needs pre-creating either.

        Best-effort: any failure here (node briefly unreachable, etc.) is
        reported but never breaks the rest of the refresh -- balances and
        history above have already been fetched by this point regardless.
        Matches sync_daemon.run_forever()'s own error handling: a deep
        reorg is worth calling out distinctly since it means something
        needs a human look, anything else just retries next tick."""
        try:
            with db.open_db(self.settings.indexer_db_path()) as conn:
                sync_daemon.run_once(conn, self._indexer_node, self._indexer_scheme)
            return None
        except sync_daemon.DeepReorgError as e:
            return f"Card indexer stopped (needs a look): {e}"
        except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
            return f"Card indexer pass failed, will retry: {e}"

    def _apply_refresh(self, data: dict) -> None:
        self.overview_tab.update_data(data["info"])
        self.history_tab.update_data(data["transactions"])
        self._known_addresses = data["addresses"]
        self.binder_tab.refresh(self.settings.indexer_db_path(), self._known_addresses)
        # If you're already sitting on the Binder tab when new cards land,
        # there's nothing to badge -- you're looking right at them.
        if self._tabs.currentWidget() is self.binder_tab:
            self.binder_tab.mark_seen()
        self._update_binder_tab_label()
        self.receive_tab.update_received(data["received_rows"])
        if data.get("indexer_error"):
            self.statusBar().showMessage(data["indexer_error"], 5000)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, first_run=False, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_settings = dialog.result_settings()
            same_node = (
                new_settings.exe_path == self.settings.exe_path
                and new_settings.datadir == self.settings.datadir
                and new_settings.network == self.settings.network
                and new_settings.rpc_port == self.settings.rpc_port
            )
            self.settings = new_settings
            self.settings.save()
            if not same_node:
                QMessageBox.information(
                    self,
                    "Restart required",
                    "Node connection settings changed -- please restart the wallet for this to take effect.",
                )
            else:
                self.refresh_now()

    def show_error(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def closeEvent(self, event) -> None:
        self._refresh_timer.stop()
        self.manager.shutdown()
        super().closeEvent(event)


# =============================================================================
# Entry point
# =============================================================================

def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(theme.stylesheet())
    icon_path = ASSETS_DIR / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    settings = WalletSettings.load()
    first_run = not settings.is_complete()

    if first_run:
        dialog = SettingsDialog(settings, first_run=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return 0
        settings = dialog.result_settings()
        settings.save()

    holder: dict = {}

    def on_ready(manager: NodeManager, wallet: WalletRPCClient) -> None:
        window = MainWindow(settings, manager, wallet)
        holder["window"] = window
        holder["startup"].close()
        window.show()

    startup = StartupWidget(settings, on_ready)
    holder["startup"] = startup
    startup.setWindowTitle(APP_NAME)
    if icon_path.exists():
        startup.setWindowIcon(QIcon(str(icon_path)))
    startup.resize(420, 220)
    startup.show()
    startup.start()

    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
