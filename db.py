"""
Stage 4 persistent storage: SQLite schema, connection management, and the
narrow set of write/read primitives that sync_daemon.py and queries.py build
on. All SQL for the persistent layer lives here -- sync_daemon.py and
queries.py never write raw SQL against these tables themselves, so the
schema has exactly one place that understands its own shape.

Design notes (see project history for the full discussion):

  - blocks/packs are keyed by block_hash, not height. A reorged-out block's
    row is never overwritten or deleted -- it's soft-deleted (is_active=0 /
    status='retracted') so the audit trail survives. This is a deliberate
    upgrade over the in-memory PoC's indexer.py, which hard-deletes on
    reorg because it never needed to survive a restart.
  - card_catalog mirrors cards.py so SQL can join/aggregate without calling
    back into Python. cards.py stays the source of truth for *selection*
    (pick_card_index); this table is a synced read cache, reseeded from
    cards.py on every init_db() call (INSERT OR REPLACE, keyed by card_id --
    safe because cards.py's own docstring guarantees pool order/identity
    never changes once minted).
  - miner_stats is a VIEW, not a table -- no second source of truth to drift
    out of sync during reorgs.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

import cards

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS card_catalog (
    card_id     TEXT PRIMARY KEY,
    set_id      TEXT NOT NULL,
    tier        TEXT NOT NULL,
    pool_index  INTEGER NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    UNIQUE (set_id, tier, pool_index)
);

CREATE TABLE IF NOT EXISTS blocks (
    block_hash     TEXT PRIMARY KEY,
    height         INTEGER NOT NULL,
    payout_script  TEXT,            -- NULL means this block's coinbase was
                                     -- ambiguous/unresolvable (see
                                     -- node_client.AmbiguousPayoutError) --
                                     -- indexed for reorg tracking, but it
                                     -- never gets a pending pack row, so it
                                     -- can never mint a card.
    is_active      INTEGER NOT NULL DEFAULT 1,
    first_seen_at  TEXT NOT NULL,
    deactivated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_blocks_active_height
    ON blocks(height) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_blocks_payout_script ON blocks(payout_script);
CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks(height);

CREATE TABLE IF NOT EXISTS packs (
    block_hash        TEXT PRIMARY KEY REFERENCES blocks(block_hash),
    mint_height       INTEGER NOT NULL,
    matures_at        INTEGER NOT NULL,
    required_heights  TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    tier              TEXT,
    card_id           TEXT REFERENCES card_catalog(card_id),
    seed_hex          TEXT,
    revealed_at       TEXT,
    retracted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_packs_status ON packs(status);
CREATE INDEX IF NOT EXISTS idx_packs_matures_at ON packs(matures_at);
CREATE INDEX IF NOT EXISTS idx_packs_card_id ON packs(card_id);

CREATE TABLE IF NOT EXISTS reorg_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at         TEXT NOT NULL,
    fork_height         INTEGER NOT NULL,
    old_tip_height      INTEGER NOT NULL,
    new_tip_height      INTEGER NOT NULL,
    deactivated_blocks  TEXT NOT NULL,
    retracted_packs     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    last_synced_height  INTEGER NOT NULL DEFAULT 0,
    last_synced_at      TEXT
);

CREATE VIEW IF NOT EXISTS miner_stats AS
SELECT b.payout_script,
       COUNT(*)                                                AS blocks_mined,
       SUM(CASE WHEN p.tier IS NOT NULL THEN 1 ELSE 0 END)     AS cards_won,
       SUM(CASE WHEN p.tier = 'secret_rare' THEN 1 ELSE 0 END) AS secret_rares,
       SUM(CASE WHEN p.tier = 'holo_rare'   THEN 1 ELSE 0 END) AS holo_rares,
       SUM(CASE WHEN p.tier = 'uncommon'    THEN 1 ELSE 0 END) AS uncommons
FROM blocks b
JOIN packs p ON p.block_hash = b.block_hash
WHERE b.is_active = 1
GROUP BY b.payout_script;
"""


# PRAGMA user_version. v1.0.0 files are 0; 2 means the one-time recheck of
# cards revealed inside a past reorg's maturity window has run (see
# sync_daemon.recheck_reorg_window_reveals).
SCHEMA_VERSION = 2

# How long a connection waits on another connection's lock before raising
# "database is locked". Python's own default is 5s.
BUSY_TIMEOUT_S = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: the Binder tab's read-only reads and the indexer's writes no longer
    # block each other. Persistent on the file, harmless to re-issue, and a
    # no-op for :memory: test databases.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


@contextmanager
def open_db(db_path: str) -> Iterator[sqlite3.Connection]:
    """Context manager: connect, ensure schema, yield, commit-or-rollback, close."""
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    seed_card_catalog(conn)
    conn.execute(
        "INSERT OR IGNORE INTO sync_state (id, last_synced_height, last_synced_at) VALUES (1, 0, NULL)"
    )


def seed_card_catalog(conn: sqlite3.Connection) -> None:
    """Reseed card_catalog from cards.py. Safe to call every startup: cards.py
    guarantees card_id/pool position never change once minted, so this can
    only add newly-appended cards (a future Set 2/3), never rewrite existing
    ones out from under already-minted packs."""
    rows = []
    for tier, pool in cards.CARD_SETS.items():
        for index, card in enumerate(pool):
            rows.append((card.card_id, cards.SET_ID, tier, index, card.name, card.description))
    # Upsert that only writes when something actually changed, so the
    # every-pass reseed doesn't rewrite the whole catalog each refresh tick.
    conn.executemany(
        """INSERT INTO card_catalog (card_id, set_id, tier, pool_index, name, description)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(card_id) DO UPDATE SET
               set_id = excluded.set_id,
               tier = excluded.tier,
               pool_index = excluded.pool_index,
               name = excluded.name,
               description = excluded.description
           WHERE card_catalog.set_id IS NOT excluded.set_id
              OR card_catalog.tier IS NOT excluded.tier
              OR card_catalog.pool_index IS NOT excluded.pool_index
              OR card_catalog.name IS NOT excluded.name
              OR card_catalog.description IS NOT excluded.description""",
        rows,
    )


# --- sync_state -----------------------------------------------------------

def get_last_synced_height(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT last_synced_height FROM sync_state WHERE id = 1").fetchone()
    return row["last_synced_height"] if row else 0


def set_last_synced_height(conn: sqlite3.Connection, height: int) -> None:
    conn.execute(
        "UPDATE sync_state SET last_synced_height = ?, last_synced_at = ? WHERE id = 1",
        (height, now_iso()),
    )


# --- blocks -----------------------------------------------------------------

def get_active_block_hash_at_height(conn: sqlite3.Connection, height: int) -> Optional[str]:
    row = conn.execute(
        "SELECT block_hash FROM blocks WHERE height = ? AND is_active = 1", (height,)
    ).fetchone()
    return row["block_hash"] if row else None


def insert_block(conn: sqlite3.Connection, block_hash: str, height: int, payout_script: Optional[str]) -> None:
    """payout_script is None for a block whose coinbase was ambiguous/unresolvable
    (AmbiguousPayoutError at sync time) -- stored for reorg tracking only; no
    pending pack is ever created for it, so it never becomes a card candidate.

    A block that was reorged out (is_active=0) and then comes back onto the
    best chain arrives here again with the same block_hash: reactivate the
    existing row instead of failing on the primary key. first_seen_at keeps
    its original value."""
    conn.execute(
        """INSERT INTO blocks (block_hash, height, payout_script, is_active, first_seen_at)
           VALUES (?, ?, ?, 1, ?)
           ON CONFLICT(block_hash) DO UPDATE SET
               height = excluded.height,
               payout_script = excluded.payout_script,
               is_active = 1,
               deactivated_at = NULL""",
        (block_hash, height, payout_script, now_iso()),
    )


def deactivate_block(conn: sqlite3.Connection, block_hash: str) -> None:
    conn.execute(
        "UPDATE blocks SET is_active = 0, deactivated_at = ? WHERE block_hash = ?",
        (now_iso(), block_hash),
    )


# --- packs --------------------------------------------------------------

def insert_pending_pack(
    conn: sqlite3.Connection,
    block_hash: str,
    mint_height: int,
    matures_at: int,
    required_heights: list,
) -> None:
    """A retracted pack whose block comes back onto the best chain is reset to
    pending so it reveals again (deterministically, so the same card). Any
    other existing row is left alone."""
    conn.execute(
        """INSERT INTO packs (block_hash, mint_height, matures_at, required_heights, status)
           VALUES (?, ?, ?, ?, 'pending')
           ON CONFLICT(block_hash) DO UPDATE SET
               mint_height = excluded.mint_height,
               matures_at = excluded.matures_at,
               required_heights = excluded.required_heights,
               status = 'pending',
               tier = NULL,
               card_id = NULL,
               seed_hex = NULL,
               revealed_at = NULL,
               retracted_at = NULL
           WHERE packs.status = 'retracted'""",
        (block_hash, mint_height, matures_at, json.dumps(required_heights)),
    )


def get_pending_ready_packs(conn: sqlite3.Connection, tip: int) -> list:
    """Pending packs whose maturity height is on-chain, restricted to packs
    whose own mint block is still active (a pack on an already-retracted
    block has nothing to reveal until forward-sync re-registers it)."""
    rows = conn.execute(
        """SELECT p.block_hash, p.mint_height
           FROM packs p
           JOIN blocks b ON b.block_hash = p.block_hash
           WHERE p.status = 'pending' AND p.matures_at <= ? AND b.is_active = 1
           ORDER BY p.mint_height""",
        (tip,),
    ).fetchall()
    return [(r["block_hash"], r["mint_height"]) for r in rows]


def reveal_pack(
    conn: sqlite3.Connection,
    block_hash: str,
    tier: Optional[str],
    card_id: Optional[str],
    seed_hex: str,
) -> None:
    conn.execute(
        """UPDATE packs SET status = 'revealed', tier = ?, card_id = ?, seed_hex = ?, revealed_at = ?
           WHERE block_hash = ?""",
        (tier, card_id, seed_hex, now_iso(), block_hash),
    )


def retract_pack(conn: sqlite3.Connection, block_hash: str) -> None:
    conn.execute(
        "UPDATE packs SET status = 'retracted', retracted_at = ? WHERE block_hash = ?",
        (now_iso(), block_hash),
    )


def reset_reveals_spanning_fork(conn: sqlite3.Connection, fork_height: int) -> list:
    """A pack's reveal hashes the block `maturity` blocks after it. A reorg
    from fork_height replaces those later blocks, so any already-revealed pack
    minted below the fork but maturing at or above it was revealed against a
    block that's no longer on the chain. Put it back to pending so it reveals
    again from the new chain. (Packs minted at or above the fork are retracted
    separately by the reorg handler.) Returns the affected block hashes."""
    rows = conn.execute(
        """SELECT block_hash FROM packs
           WHERE status = 'revealed' AND mint_height < ? AND matures_at >= ?""",
        (fork_height, fork_height),
    ).fetchall()
    hashes = [r["block_hash"] for r in rows]
    conn.executemany(
        """UPDATE packs SET status = 'pending', tier = NULL, card_id = NULL,
               seed_hex = NULL, revealed_at = NULL
           WHERE block_hash = ?""",
        [(h,) for h in hashes],
    )
    return hashes


def revealed_packs_in_past_reorg_windows(conn: sqlite3.Connection) -> list:
    """For the one-time v1.0.1 recheck: revealed packs on active blocks that
    were minted below a logged reorg's fork height but matured at or above it,
    i.e. the ones v1.0.0 could have revealed against an orphaned block."""
    rows = conn.execute(
        """SELECT DISTINCT p.block_hash, p.mint_height
           FROM packs p
           JOIN blocks b ON b.block_hash = p.block_hash
           JOIN reorg_log r ON p.mint_height < r.fork_height AND p.matures_at >= r.fork_height
           WHERE p.status = 'revealed' AND b.is_active = 1
           ORDER BY p.mint_height"""
    ).fetchall()
    return [(r["block_hash"], r["mint_height"]) for r in rows]


# --- reorg_log ------------------------------------------------------------

def log_reorg(
    conn: sqlite3.Connection,
    fork_height: int,
    old_tip_height: int,
    new_tip_height: int,
    deactivated_blocks: list,
    retracted_packs: list,
) -> None:
    conn.execute(
        """INSERT INTO reorg_log
           (detected_at, fork_height, old_tip_height, new_tip_height, deactivated_blocks, retracted_packs)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            now_iso(),
            fork_height,
            old_tip_height,
            new_tip_height,
            json.dumps(deactivated_blocks),
            json.dumps(retracted_packs),
        ),
    )
