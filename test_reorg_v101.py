"""
v1.0.1 indexer hardening tests: reorgs that bring a block back, reveals that
span a reorg's fork, commit cadence, per-pass cap, locking, and the one-time
recheck for databases written by v1.0.0.

Run: python test_reorg_v101.py

Every test uses a throwaway SQLite file in a temp dir -- never a real datadir.
The key check throughout: after any sequence of reorgs, this wallet's cards
must match a wallet that synced the final chain fresh, because every wallet
has to agree on who owns which card.
"""
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

import db
import sync_daemon
from reveal import SaltedFutureBlockReveal

MATURITY = 30


class ChainNode:
    """Fake node whose chain can be rewritten from any height (a reorg).
    Hashes are derived from (branch, height), so the same branch always
    produces the same block hash -- which is what lets a block come back."""

    def __init__(self, tip, branch="A"):
        self.branches = {h: branch for h in range(1, tip + 1)}
        self.tip = tip
        self.on_get_hash = None  # optional hook(height) for timing tests

    def reorg(self, from_height, new_tip, branch):
        for h in list(self.branches):
            if h >= from_height:
                del self.branches[h]
        for h in range(from_height, new_tip + 1):
            self.branches[h] = branch
        self.tip = new_tip

    def get_block_count(self):
        return self.tip

    def get_block_hash(self, height):
        if self.on_get_hash:
            self.on_get_hash(height)
        branch = self.branches[height]
        return f"{ord(branch):08x}{height:056x}"

    def get_block_payout_script(self, height):
        return f"Pminer{height % 3}"  # three miners taking turns


def cards_by_hash(conn):
    rows = conn.execute(
        """SELECT p.block_hash, p.status, p.card_id, p.seed_hex
           FROM packs p JOIN blocks b ON b.block_hash = p.block_hash
           WHERE b.is_active = 1 ORDER BY p.mint_height"""
    ).fetchall()
    return {r["block_hash"]: (r["status"], r["card_id"], r["seed_hex"]) for r in rows}


def sync_until_caught_up(path, node, scheme):
    for _ in range(50):
        with db.open_db(path) as conn:
            summary = sync_daemon.run_once(conn, node, scheme)
        if summary["caught_up"]:
            return summary
    raise AssertionError("never caught up")


class TempDbCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ucft-test-")
        self.path = os.path.join(self.dir, "card_index.sqlite3")
        self.scheme = SaltedFutureBlockReveal(maturity=MATURITY)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fresh_wallet_cards(self, node):
        """What a brand-new wallet syncing this exact chain would show."""
        path = os.path.join(self.dir, f"fresh-{time.time_ns()}.sqlite3")
        sync_until_caught_up(path, node, self.scheme)
        conn = db.connect(path)
        try:
            return cards_by_hash(conn)
        finally:
            conn.close()

    def my_cards(self):
        conn = db.connect(self.path)
        try:
            return cards_by_hash(conn)
        finally:
            conn.close()


class UpsertTests(TempDbCase):
    def test_reinserted_block_is_reactivated_not_a_crash(self):
        with db.open_db(self.path) as conn:
            db.insert_block(conn, "aa" * 32, 5, "Pminer1")
            first_seen = conn.execute("SELECT first_seen_at FROM blocks").fetchone()[0]
            db.deactivate_block(conn, "aa" * 32)
            db.insert_block(conn, "aa" * 32, 5, "Pminer1")  # v1.0.0: UNIQUE constraint failed
            row = conn.execute("SELECT * FROM blocks WHERE block_hash = ?", ("aa" * 32,)).fetchone()
        self.assertEqual(row["is_active"], 1)
        self.assertIsNone(row["deactivated_at"])
        self.assertEqual(row["first_seen_at"], first_seen)

    def test_retracted_pack_comes_back_pending_and_clean(self):
        h = "bb" * 32
        with db.open_db(self.path) as conn:
            db.insert_block(conn, h, 5, "Pminer1")
            db.insert_pending_pack(conn, h, 5, 35, [5, 35])
            db.reveal_pack(conn, h, "uncommon", None, "ff" * 32)
            db.retract_pack(conn, h)
            db.insert_pending_pack(conn, h, 5, 35, [5, 35])
            p = conn.execute("SELECT * FROM packs WHERE block_hash = ?", (h,)).fetchone()
        self.assertEqual(p["status"], "pending")
        for col in ("tier", "card_id", "seed_hex", "revealed_at", "retracted_at"):
            self.assertIsNone(p[col], col)

    def test_reinsert_leaves_live_pack_alone(self):
        """Only retracted packs are reset; a revealed one is never wiped by a
        duplicate insert."""
        h = "cc" * 32
        with db.open_db(self.path) as conn:
            db.insert_block(conn, h, 5, "Pminer1")
            db.insert_pending_pack(conn, h, 5, 35, [5, 35])
            db.reveal_pack(conn, h, None, None, "ee" * 32)
            db.insert_pending_pack(conn, h, 5, 35, [5, 35])
            p = conn.execute("SELECT status, seed_hex FROM packs").fetchone()
        self.assertEqual((p["status"], p["seed_hex"]), ("revealed", "ee" * 32))


class ReorgTests(TempDbCase):
    def test_reorg_away_and_back_again(self):
        """A -> B -> A. On v1.0.0 the return to A crashed the indexer for good."""
        node = ChainNode(tip=80, branch="A")
        sync_until_caught_up(self.path, node, self.scheme)
        original = self.my_cards()

        node.reorg(70, 82, "B")
        summary = sync_until_caught_up(self.path, node, self.scheme)
        self.assertTrue(summary["reorg_handled"])

        node.reorg(70, 80, "A")
        node.reorg(81, 84, "A")  # A grows past B and wins back
        summary = sync_until_caught_up(self.path, node, self.scheme)
        self.assertTrue(summary["reorg_handled"])

        mine = self.my_cards()
        self.assertEqual(mine, self.fresh_wallet_cards(node))
        # Blocks that existed on A the first time got exactly the same cards back.
        for h, card in original.items():
            if card[0] == "revealed":
                self.assertEqual(mine[h], card)

    def test_reveal_spanning_the_fork_is_redone(self):
        """Packs minted just below the fork were revealed from a block the
        reorg replaced. They must be re-revealed from the new chain."""
        node = ChainNode(tip=60, branch="A")
        sync_until_caught_up(self.path, node, self.scheme)
        before = self.my_cards()

        node.reorg(50, 62, "B")  # replaces the reveal blocks of packs minted 20..32
        sync_until_caught_up(self.path, node, self.scheme)
        after = self.my_cards()

        self.assertEqual(after, self.fresh_wallet_cards(node))
        h25 = node.get_block_hash(25)  # minted below the fork, revealed from block 55
        self.assertNotEqual(before[h25][2], after[h25][2], "seed must come from the new block 55")
        h19 = node.get_block_hash(19)  # revealed from block 49, untouched by the reorg
        self.assertEqual(before[h19], after[h19])

    def test_chain_shorter_than_index(self):
        node = ChainNode(tip=70, branch="A")
        sync_until_caught_up(self.path, node, self.scheme)
        node.reorg(55, 58, "C")
        sync_until_caught_up(self.path, node, self.scheme)
        self.assertEqual(self.my_cards(), self.fresh_wallet_cards(node))


class PassShapeTests(TempDbCase):
    def test_pass_is_capped_and_catches_up_over_ticks(self):
        node = ChainNode(tip=sync_daemon.MAX_BLOCKS_PER_PASS * 2 + 40)
        with db.open_db(self.path) as conn:
            s = sync_daemon.run_once(conn, node, self.scheme)
        self.assertEqual(s["synced_to"], sync_daemon.MAX_BLOCKS_PER_PASS)
        self.assertFalse(s["caught_up"])
        # Nothing reveals from a block the index hasn't synced yet.
        with db.open_db(self.path) as conn:
            top = conn.execute("SELECT MAX(matures_at) FROM packs WHERE status = 'revealed'").fetchone()[0]
        self.assertLessEqual(top, sync_daemon.MAX_BLOCKS_PER_PASS)
        sync_until_caught_up(self.path, node, self.scheme)
        self.assertEqual(self.my_cards(), self.fresh_wallet_cards(node))

    def test_progress_committed_mid_pass(self):
        """Another connection can see progress before the pass ends."""
        node = ChainNode(tip=400)
        seen = {}

        def peek(height):
            if height == sync_daemon.COMMIT_EVERY + 10 and "mid" not in seen:
                ro = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
                seen["mid"] = ro.execute("SELECT last_synced_height FROM sync_state").fetchone()[0]
                ro.close()

        node.on_get_hash = peek
        with db.open_db(self.path) as conn:
            sync_daemon.run_once(conn, node, self.scheme)
        self.assertEqual(seen["mid"], sync_daemon.COMMIT_EVERY)

    def test_waits_for_another_writer_instead_of_failing(self):
        node = ChainNode(tip=40)
        with db.open_db(self.path) as conn:
            sync_daemon.run_once(conn, node, self.scheme)
        node.reorg(41, 60, "A")

        holder = sqlite3.connect(self.path, check_same_thread=False)
        holder.execute("BEGIN IMMEDIATE")  # another writer holds the lock...

        def release():
            time.sleep(1.5)  # ...for a moment, then lets go
            holder.rollback()
            holder.close()

        threading.Thread(target=release).start()
        with db.open_db(self.path) as conn:  # waits for the lock rather than erroring
            s = sync_daemon.run_once(conn, node, self.scheme)
        self.assertEqual(s["synced_to"], 60)

    def test_reader_never_blocked_by_open_write(self):
        node = ChainNode(tip=40)
        with db.open_db(self.path) as conn:
            sync_daemon.run_once(conn, node, self.scheme)
        writer = db.connect(self.path)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE sync_state SET last_synced_at = 'x'")
        ro = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=0.1)
        t0 = time.time()
        n = ro.execute("SELECT COUNT(*) FROM packs").fetchone()[0]
        self.assertLess(time.time() - t0, 0.1)
        self.assertEqual(n, 40)
        ro.close()
        writer.rollback()
        writer.close()


class V100UpgradeTests(TempDbCase):
    def make_v100_db_with_stale_reveals(self):
        """Reproduce what v1.0.0 left behind after a reorg: packs minted below
        the fork still revealed from blocks that are no longer on the chain."""
        node = ChainNode(tip=60, branch="A")
        sync_until_caught_up(self.path, node, self.scheme)
        node.reorg(50, 62, "B")
        conn = db.connect(self.path)
        with conn:
            # v1.0.0's reorg handling: deactivate + retract at/above the fork, log it,
            # but never touch the reveals of packs minted below it.
            dead = []
            for h in range(50, 61):
                bh = db.get_active_block_hash_at_height(conn, h)
                db.deactivate_block(conn, bh)
                db.retract_pack(conn, bh)
                dead.append(bh)
            db.log_reorg(conn, 50, 60, 62, dead, dead)
            db.set_last_synced_height(conn, 49)
            db.set_schema_version(conn, 0)
        conn.close()
        return node

    def test_one_time_recheck_fixes_stale_reveals(self):
        node = self.make_v100_db_with_stale_reveals()
        stale = self.my_cards()
        h25 = node.get_block_hash(25)
        with db.open_db(self.path) as conn:
            s = sync_daemon.run_once(conn, node, self.scheme)
            version = db.get_schema_version(conn)
        self.assertGreater(s["reveals_corrected"], 0)
        self.assertEqual(version, db.SCHEMA_VERSION)
        self.assertNotEqual(stale[h25][2], self.my_cards()[h25][2])
        sync_until_caught_up(self.path, node, self.scheme)
        self.assertEqual(self.my_cards(), self.fresh_wallet_cards(node))

    def test_recheck_runs_once(self):
        node = self.make_v100_db_with_stale_reveals()
        sync_until_caught_up(self.path, node, self.scheme)
        with db.open_db(self.path) as conn:
            s = sync_daemon.run_once(conn, node, self.scheme)
        self.assertEqual(s["reveals_corrected"], 0)

    def test_new_database_starts_at_current_version(self):
        sync_until_caught_up(self.path, ChainNode(tip=35), self.scheme)
        conn = db.connect(self.path)
        try:
            self.assertEqual(db.get_schema_version(conn), db.SCHEMA_VERSION)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
