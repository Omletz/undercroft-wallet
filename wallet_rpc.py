"""
WalletRPCClient: the wallet-facing half of talking to a real Core-compatible
node, sitting next to node_client.py's BitcoinRPCClient rather than
replacing it.

Split deliberately along the same line node_client.py's own docstring draws:
that file is "what the pack indexer needs" (tiny, read-only, chain/block
calls). This file is what a wallet GUI needs -- balance, send, receive,
transaction history -- which is a different, wallet-scoped set of RPCs that
live under Core's /wallet/<name> endpoint rather than the base one. Reusing
BitcoinRPCClient underneath (composition, not duplication) means both halves
share the exact same retry/backoff/auth-error handling that's already been
tested against a real node.

wallet_app.py is the only intended caller of this module.
"""

from typing import Optional

from node_client import BitcoinRPCClient, NodeConfig, NodeRPCError

DEFAULT_WALLET_NAME = "undercroft"


class WalletRPCClient:
    """Wallet-scoped RPC calls for one Core-compatible node + one wallet."""

    def __init__(self, config: NodeConfig, wallet_name: str = DEFAULT_WALLET_NAME):
        # Set config.wallet_name so BitcoinRPCClient._call's default (no
        # explicit `wallet=` passed per call) already routes to
        # /wallet/<name> -- every wallet-scoped method below relies on this.
        # A handful of calls (createwallet/loadwallet/listwallets/
        # listwalletdir/getblockchaininfo) are base-endpoint-only RPCs and
        # pass wallet="" explicitly to opt back out of that default.
        config.wallet_name = wallet_name
        self.wallet_name = wallet_name
        self._core_config = config
        self._core = BitcoinRPCClient(config)

    # --- connectivity / wallet lifecycle ------------------------------------

    def get_blockchain_info(self) -> dict:
        return self._core._call("getblockchaininfo", wallet="")

    def list_wallets(self) -> list[str]:
        return self._core._call("listwallets", wallet="")

    def list_wallet_dir(self) -> list[str]:
        result = self._core._call("listwalletdir", wallet="")
        return [w["name"] for w in result.get("wallets", [])]

    def ensure_wallet_ready(self) -> str:
        """Make sure self.wallet_name is loaded, creating it on first run.
        Safe to call every startup -- each branch is a no-op if that state
        is already true. Returns the wallet name for convenience."""
        loaded = self.list_wallets()
        if self.wallet_name in loaded:
            return self.wallet_name

        on_disk = self.list_wallet_dir()
        if self.wallet_name in on_disk:
            self._core._call("loadwallet", [self.wallet_name], wallet="")
            return self.wallet_name

        # brand new install: no wallet file on disk yet at all
        self._core._call(
            "createwallet",
            [self.wallet_name],  # wallet_name, disable_private_keys=False, blank=False (defaults)
            wallet="",
        )
        return self.wallet_name

    def get_wallet_info(self) -> dict:
        return self._core._call("getwalletinfo")

    # --- balance / addresses -------------------------------------------------

    def get_balance(self) -> float:
        """Confirmed + immature balance combined, in whole coins (not
        satoshis) -- matches what getbalance returns natively."""
        return self._core._call("getbalance")

    def get_balances(self) -> dict:
        """Full breakdown (trusted/untrusted_pending/immature) for a more
        informative Overview tab than a single number."""
        return self._core._call("getbalances")

    def get_new_address(self, label: str = "", address_type: str = "bech32") -> str:
        return self._core._call("getnewaddress", [label, address_type])

    def list_received_by_address(self, minconf: int = 0, include_empty: bool = True) -> list[dict]:
        """Every address this wallet has ever handed out, with how much
        each has received -- this is how the Binder tab finds "all
        addresses that belong to me" to look up cards against, since a
        wallet can generate many receiving addresses over time."""
        return self._core._call("listreceivedbyaddress", [minconf, include_empty])

    def get_all_owned_addresses(self) -> list[str]:
        return [entry["address"] for entry in self.list_received_by_address()]

    # --- sending -------------------------------------------------------------

    def validate_address(self, address: str) -> dict:
        return self._core._call("validateaddress", [address])

    def send_to_address(
        self,
        address: str,
        amount: float,
        comment: str = "",
        subtract_fee_from_amount: bool = False,
    ) -> str:
        """Returns the txid. Raises NodeRPCError (from node_client) if Core
        rejects it -- bad address, insufficient funds, amount below dust,
        etc. -- with Core's own message, which wallet_app.py surfaces
        directly rather than re-wording (Core's errors are already clear:
        e.g. 'Insufficient funds')."""
        validation = self.validate_address(address)
        if not validation.get("isvalid"):
            raise NodeRPCError(f"'{address}' is not a valid Undercroft address")
        return self._core._call(
            "sendtoaddress",
            [address, amount, comment, "", subtract_fee_from_amount],
        )

    # --- transaction history --------------------------------------------------

    def list_transactions(self, count: int = 50, skip: int = 0) -> list[dict]:
        """Most recent first -- Core returns oldest-first, so this reverses
        it for a wallet UI where the newest activity belongs at the top."""
        txs = self._core._call("listtransactions", ["*", count, skip])
        return list(reversed(txs))

    def get_transaction(self, txid: str) -> dict:
        return self._core._call("gettransaction", [txid])
