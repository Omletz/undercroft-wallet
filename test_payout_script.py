"""
Tests for get_block_payout_script()'s coinbase-output resolution and for
sync_daemon's handling of an ambiguous (2+ output) coinbase.

Run: python3 test_payout_script.py

No real node needed -- BitcoinRPCClient._call is monkeypatched with a stub
that returns a canned getblock(height, 2) response, so these exercise the
real production code path (BitcoinRPCClient.get_block_payout_script,
sync_daemon.sync_forward) end to end, just without an actual daemon.
"""
import sqlite3
import unittest
from unittest.mock import patch

import db
import sync_daemon
from node_client import AmbiguousPayoutError, BitcoinRPCClient, NodeConfig
from reveal import SaltedFutureBlockReveal


def make_client(dev_fee_address=None):
    config = NodeConfig(rpc_user="u", rpc_password="p", dev_fee_address=dev_fee_address)
    return BitcoinRPCClient(config)


def nulldata_vout():
    return {"scriptPubKey": {"type": "nulldata", "hex": "6a0000"}}


def payout_vout(address, script_type="pubkeyhash"):
    return {"scriptPubKey": {"type": script_type, "address": address}}


def unresolvable_vout():
    """No decoded address AND no 'hex' fallback either -- the genuinely
    'output has no address at all' case."""
    return {"scriptPubKey": {"type": "nonstandard"}}


def block_with_vouts(vouts):
    return {"tx": [{"vout": vouts}]}


class GetBlockPayoutScriptTests(unittest.TestCase):
    def test_single_clean_output_resolves(self):
        client = make_client()
        block = block_with_vouts([payout_vout("Pminer111"), nulldata_vout()])
        with patch.object(client, "_call", side_effect=["deadbeef", block]):
            self.assertEqual(client.get_block_payout_script(100), "Pminer111")

    def test_two_payout_outputs_is_ambiguous(self):
        """The case this round is actually about: a 2-output coinbase (e.g.
        a PPLNS/pool-fee split with no dev_fee_address configured to tell
        them apart) must raise AmbiguousPayoutError, not silently pick one
        or crash on something downstream."""
        client = make_client()  # dev_fee_address unset, matching UCFT's real config
        block = block_with_vouts([
            payout_vout("Ppoolwallet"),
            payout_vout("Pminer222"),
            nulldata_vout(),
        ])
        with patch.object(client, "_call", side_effect=["deadbeef", block]):
            with self.assertRaises(AmbiguousPayoutError):
                client.get_block_payout_script(101)

    def test_two_payout_outputs_resolved_by_dev_fee_address(self):
        """Same 2-output shape, but when dev_fee_address IS configured and
        matches one of them, that one output is excluded and the miner's
        is still resolved cleanly -- confirms the dev-fee exclusion path
        still works now that the error path has changed."""
        client = make_client(dev_fee_address="Pdevfee999")
        block = block_with_vouts([
            payout_vout("Pdevfee999"),
            payout_vout("Pminer333"),
            nulldata_vout(),
        ])
        with patch.object(client, "_call", side_effect=["deadbeef", block]):
            self.assertEqual(client.get_block_payout_script(102), "Pminer333")

    def test_zero_resolvable_outputs_is_ambiguous(self):
        """The sole non-nulldata output has no resolvable address at all --
        must raise AmbiguousPayoutError (found 0), not return None as if it
        were a valid payout script."""
        client = make_client()
        block = block_with_vouts([unresolvable_vout(), nulldata_vout()])
        with patch.object(client, "_call", side_effect=["deadbeef", block]):
            with self.assertRaises(AmbiguousPayoutError):
                client.get_block_payout_script(103)


class FakeNode:
    """Minimal NodeClient stand-in for sync_daemon tests: fixed tip, and a
    per-height payout script table where one height is rigged to raise
    AmbiguousPayoutError, exactly as the real client would for a 2-output
    coinbase."""

    def __init__(self, tip, payouts, ambiguous_heights):
        self.tip = tip
        self.payouts = payouts
        self.ambiguous_heights = set(ambiguous_heights)

    def get_block_count(self):
        return self.tip

    def get_block_hash(self, height):
        return f"{height:064x}"  # valid 32-byte hex, distinct per height

    def get_block_payout_script(self, height):
        if height in self.ambiguous_heights:
            raise AmbiguousPayoutError(f"found 2 candidates at height {height}")
        return self.payouts[height]


class SyncForwardAmbiguousBlockTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db.init_db(self.conn)
        self.scheme = SaltedFutureBlockReveal(maturity=30)

    def tearDown(self):
        self.conn.close()

    def test_ambiguous_height_indexed_clean_and_sync_continues(self):
        node = FakeNode(
            tip=3,
            payouts={1: "Pminer_h1", 3: "Pminer_h3"},
            ambiguous_heights=[2],
        )

        # Must not raise -- the whole point of the fix.
        sync_daemon.sync_forward(self.conn, node, self.scheme, from_height=1, tip=3)

        # Every height still got indexed (sync did not halt at height 2).
        self.assertEqual(db.get_last_synced_height(self.conn), 3)

        row2 = self.conn.execute(
            "SELECT payout_script, is_active FROM blocks WHERE height = 2"
        ).fetchone()
        self.assertIsNotNone(row2, "ambiguous block must still be recorded for reorg tracking")
        self.assertIsNone(row2["payout_script"])
        self.assertEqual(row2["is_active"], 1)

        # No pending pack for the ambiguous block -- it can never mint a card.
        pack2 = self.conn.execute(
            "SELECT * FROM packs WHERE block_hash = ?", (f"{2:064x}",)
        ).fetchone()
        self.assertIsNone(pack2)

        # The clean heights on either side got their normal pending packs.
        for h in (1, 3):
            pack = self.conn.execute(
                "SELECT status FROM packs WHERE block_hash = ?", (f"{h:064x}",)
            ).fetchone()
            self.assertIsNotNone(pack)
            self.assertEqual(pack["status"], "pending")

    def test_ambiguous_block_never_surfaces_in_reveal(self):
        payouts = {h: f"Pminer_h{h}" for h in range(1, 32) if h != 2}
        node = FakeNode(tip=31, payouts=payouts, ambiguous_heights=[2])
        sync_daemon.sync_forward(self.conn, node, self.scheme, from_height=1, tip=31)
        # reveal_ready_packs needs get_block_payout_script again (via compute_seed)
        # for height 1's pack, but must NEVER call it for height 2, since no
        # pending pack exists there -- if it did, FakeNode would raise again
        # and this would blow up instead of completing cleanly.
        revealed = sync_daemon.reveal_ready_packs(self.conn, node, self.scheme, tip=31)
        self.assertEqual(revealed, 1)  # only height 1's pack was ever eligible


if __name__ == "__main__":
    unittest.main()
