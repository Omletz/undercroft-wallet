"""
Read-only query API over the Stage 4 SQLite store. Thin, deliberately --
every function here is a straightforward SELECT against db.py's schema, no
business logic. This is what cli.py calls, and what any future web/API
layer would call too.

Every query here restricts to blocks.is_active = 1 unless it's explicitly a
historical lookup (get_drop_by_hash can return a retracted pack on request)
-- "current collection state" should never surface something a reorg
retracted, but the audit trail is still there if you go looking for it.
"""

import sqlite3
from typing import Optional


def get_cards_by_owner(conn: sqlite3.Connection, payout_script: str) -> list[dict]:
    """Every card currently held by an address (active chain only), newest first."""
    rows = conn.execute(
        """SELECT p.block_hash, p.mint_height, p.tier, p.card_id, p.revealed_at,
                  cc.name, cc.description
           FROM packs p
           JOIN blocks b ON b.block_hash = p.block_hash
           LEFT JOIN card_catalog cc ON cc.card_id = p.card_id
           WHERE b.is_active = 1 AND p.status = 'revealed' AND p.tier IS NOT NULL
                 AND b.payout_script = ?
           ORDER BY p.mint_height DESC""",
        (payout_script,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_drop_by_height(conn: sqlite3.Connection, height: int) -> Optional[dict]:
    """Drop details for the currently-active block at a height."""
    row = conn.execute(
        """SELECT b.block_hash, b.height, b.payout_script, p.status, p.tier, p.card_id,
                  p.matures_at, p.seed_hex, p.revealed_at, cc.name, cc.description
           FROM blocks b
           JOIN packs p ON p.block_hash = b.block_hash
           LEFT JOIN card_catalog cc ON cc.card_id = p.card_id
           WHERE b.height = ? AND b.is_active = 1""",
        (height,),
    ).fetchone()
    return dict(row) if row else None


def get_drop_by_hash(conn: sqlite3.Connection, block_hash: str) -> Optional[dict]:
    """Drop details for a specific block hash, active or retracted -- historical lookup."""
    row = conn.execute(
        """SELECT b.block_hash, b.height, b.payout_script, b.is_active, p.status, p.tier,
                  p.card_id, p.matures_at, p.seed_hex, p.revealed_at, p.retracted_at,
                  cc.name, cc.description
           FROM blocks b
           JOIN packs p ON p.block_hash = b.block_hash
           LEFT JOIN card_catalog cc ON cc.card_id = p.card_id
           WHERE b.block_hash = ?""",
        (block_hash,),
    ).fetchone()
    return dict(row) if row else None


def get_supply_stats(conn: sqlite3.Connection) -> dict:
    """Collection-wide totals: blocks mined, no-drop count, per-tier counts,
    and total minted count per individual card_id (booster-pack style --
    the same card can be minted many times, this is a running total)."""
    totals = conn.execute(
        """SELECT
               COUNT(*) AS blocks_mined,
               COALESCE(SUM(CASE WHEN p.tier IS NULL THEN 1 ELSE 0 END), 0) AS no_drop,
               COALESCE(SUM(CASE WHEN p.tier = 'secret_rare' THEN 1 ELSE 0 END), 0) AS secret_rares,
               COALESCE(SUM(CASE WHEN p.tier = 'holo_rare'   THEN 1 ELSE 0 END), 0) AS holo_rares,
               COALESCE(SUM(CASE WHEN p.tier = 'uncommon'    THEN 1 ELSE 0 END), 0) AS uncommons
           FROM blocks b JOIN packs p ON p.block_hash = b.block_hash
           WHERE b.is_active = 1 AND p.status = 'revealed'""",
    ).fetchone()

    per_card = conn.execute(
        """SELECT p.card_id, cc.tier, cc.name, COUNT(*) AS minted_count
           FROM packs p
           JOIN blocks b ON b.block_hash = p.block_hash
           JOIN card_catalog cc ON cc.card_id = p.card_id
           WHERE b.is_active = 1 AND p.status = 'revealed' AND p.card_id IS NOT NULL
           GROUP BY p.card_id
           ORDER BY cc.tier, minted_count DESC""",
    ).fetchall()

    return {
        "totals": dict(totals) if totals else {},
        "per_card": [dict(r) for r in per_card],
    }


def get_miner_stats(conn: sqlite3.Connection, payout_script: Optional[str] = None) -> list[dict]:
    """Rows from the miner_stats VIEW -- one address, or all of them."""
    if payout_script:
        rows = conn.execute(
            "SELECT * FROM miner_stats WHERE payout_script = ?", (payout_script,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM miner_stats ORDER BY blocks_mined DESC").fetchall()
    return [dict(r) for r in rows]


def get_sync_status(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT last_synced_height, last_synced_at FROM sync_state WHERE id = 1"
    ).fetchone()
    reorg_count = conn.execute("SELECT COUNT(*) AS n FROM reorg_log").fetchone()["n"]
    return {
        "last_synced_height": row["last_synced_height"] if row else 0,
        "last_synced_at": row["last_synced_at"] if row else None,
        "total_reorgs_handled": reorg_count,
    }


def get_recent_reorgs(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT id, detected_at, fork_height, old_tip_height, new_tip_height,
                  deactivated_blocks, retracted_packs
           FROM reorg_log ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
