"""
The Stage 4 continuous sync process: polls a real node via NodeClient,
persists block/pack state into SQLite via db.py, and reveals matured packs
using the same reveal.py/cards.py logic the PoC's in-memory indexer used.

Reorg handling, precisely:

  Most poll cycles see no reorg. So on every cycle we do exactly ONE cheap
  check first: does the node's hash at our last-synced height still match
  what we have stored as active? If yes, nothing behind us changed --
  just sync forward from there to the new tip. Only when that single check
  fails do we pay for a backward walk to find the fork point, and even then
  we bound how far back we're willing to walk (REORG_MAX_DEPTH). This is
  strictly cheaper than the ~200-block bounded *rescan-every-cycle* approach
  floated during design -- same safety bound, but the common (no-reorg) case
  costs one RPC call instead of up to two hundred.

  If the backward walk exhausts REORG_MAX_DEPTH without finding a matching
  height, that's a reorg deeper than this chain should ever plausibly
  produce (it's bounded well past any realistic depth for a low-hashrate
  SHA256d chain) -- we raise rather than guess, so it surfaces as an alert
  instead of silently corrupting state.
"""

import logging
import time
from typing import Optional

import db
import set_registry
from node_client import AmbiguousPayoutError, NodeClient
from reveal import SaltedFutureBlockReveal, classify_seed, pick_card_index

log = logging.getLogger("sync_daemon")

REORG_MAX_DEPTH = 200  # generous multiple of the 30-block maturity window


class DeepReorgError(RuntimeError):
    """Reorg fork point not found within REORG_MAX_DEPTH -- needs a human look."""


def find_fork_point(conn, node: NodeClient, from_height: int, max_depth: int = REORG_MAX_DEPTH) -> int:
    """Walk backward from from_height until the node's hash matches our
    stored active hash at that height. Returns the first height ABOVE that
    match point (i.e. the lowest height that actually needs to be retracted
    and re-synced). Assumes from_height is already known to mismatch."""
    height = from_height
    checked = 0
    while checked < max_depth and height >= 1:
        node_hash = node.get_block_hash(height)
        db_hash = db.get_active_block_hash_at_height(conn, height)
        if db_hash is not None and db_hash == node_hash:
            return height + 1
        height -= 1
        checked += 1
    raise DeepReorgError(
        f"No common ancestor found within {max_depth} blocks of height {from_height}. "
        "This is far deeper than this chain should plausibly reorg -- stopping rather "
        "than guessing. Investigate manually (compare node's chain tip against the DB) "
        "before restarting the daemon."
    )


def handle_reorg(conn, node: NodeClient, fork_height: int, old_tip: int, new_tip: int) -> None:
    deactivated, retracted = [], []
    for height in range(fork_height, old_tip + 1):
        block_hash = db.get_active_block_hash_at_height(conn, height)
        if block_hash is None:
            continue  # nothing was ever recorded at this height (shouldn't happen, but be safe)
        db.deactivate_block(conn, block_hash)
        deactivated.append(block_hash)
        pack_row = conn.execute(
            "SELECT status FROM packs WHERE block_hash = ?", (block_hash,)
        ).fetchone()
        if pack_row is not None:
            db.retract_pack(conn, block_hash)
            retracted.append(block_hash)
    db.log_reorg(conn, fork_height, old_tip, new_tip, deactivated, retracted)
    log.warning(
        "reorg handled: fork_height=%d old_tip=%d new_tip=%d deactivated=%d retracted=%d",
        fork_height, old_tip, new_tip, len(deactivated), len(retracted),
    )


def sync_forward(conn, node: NodeClient, scheme: SaltedFutureBlockReveal, from_height: int, tip: int) -> None:
    for height in range(from_height, tip + 1):
        block_hash = node.get_block_hash(height)
        try:
            payout_script = node.get_block_payout_script(height)
        except AmbiguousPayoutError:
            # Structural, not transient -- this block's coinbase will never
            # resolve to exactly one miner payout address no matter how many
            # times we ask. Index it as a clean (no-card) block: recorded for
            # reorg tracking, no pending pack, and move on -- never halt the
            # whole sync pass or spin retrying the same height forever.
            log.warning(
                "height %d: ambiguous/unresolvable coinbase payout -- indexing "
                "as a clean block (no card possible), see exception for detail",
                height, exc_info=True,
            )
            db.insert_block(conn, block_hash, height, None)
            db.set_last_synced_height(conn, height)
            continue
        db.insert_block(conn, block_hash, height, payout_script)
        db.insert_pending_pack(
            conn,
            block_hash=block_hash,
            mint_height=height,
            matures_at=scheme.matures_at(height),
            required_heights=scheme.required_heights(height),
        )
        db.set_last_synced_height(conn, height)


def reveal_ready_packs(conn, node: NodeClient, scheme: SaltedFutureBlockReveal, tip: int) -> int:
    ready = db.get_pending_ready_packs(conn, tip)
    for block_hash, mint_height in ready:
        seed = scheme.compute_seed(node, mint_height)
        tier = classify_seed(seed)
        card_id: Optional[str] = None
        if tier is not None:
            # Which SET is active at this mint height -- always Set 1 today,
            # but once a future set's activation height is reached (per the
            # card-set manifest, see set_manifest.py/set_registry.py) this
            # picks that set's pool instead, automatically and identically
            # across every wallet re-deriving the same block's reveal.
            era = set_registry.active_era_for_height(mint_height)
            idx = pick_card_index(seed, era.pool_size(tier))
            card_id = era.list_cards(tier)[idx].card_id
        db.reveal_pack(conn, block_hash, tier, card_id, seed.hex())
        log.info(
            "height %d revealed: %s",
            mint_height, card_id if card_id else "no drop (coins only)",
        )
    return len(ready)


def run_once(conn, node: NodeClient, scheme: SaltedFutureBlockReveal) -> dict:
    """One sync pass. Returns a small summary dict, handy for logging/CLI output."""
    tip = node.get_block_count()
    last_synced = db.get_last_synced_height(conn)

    reorg_handled = False
    if last_synced > tip:
        # The node's chain is now shorter than what we'd already synced --
        # e.g. a deliberate genesis wipe/rewind, or a very deep reorg. Every
        # height above the new tip is gone for certain (the node provably
        # doesn't have a block there), so there's no point asking for it.
        # Only the new tip itself (if above genesis) needs a hash check to
        # tell a clean truncation apart from a reorg that also changed
        # history at or below the old tip.
        if tip > 0 and db.get_active_block_hash_at_height(conn, tip) == node.get_block_hash(tip):
            fork_height = tip + 1
        else:
            fork_height = find_fork_point(conn, node, tip) if tip > 0 else 1
        handle_reorg(conn, node, fork_height, last_synced, tip)
        db.set_last_synced_height(conn, fork_height - 1)
        last_synced = fork_height - 1
        reorg_handled = True
    elif last_synced > 0:
        db_hash = db.get_active_block_hash_at_height(conn, last_synced)
        node_hash = node.get_block_hash(last_synced)
        if db_hash != node_hash:
            fork_height = find_fork_point(conn, node, last_synced)
            handle_reorg(conn, node, fork_height, last_synced, tip)
            db.set_last_synced_height(conn, fork_height - 1)
            last_synced = fork_height - 1
            reorg_handled = True

    if tip > last_synced:
        sync_forward(conn, node, scheme, last_synced + 1, tip)

    revealed_count = reveal_ready_packs(conn, node, scheme, tip)

    return {
        "tip": tip,
        "synced_to": max(last_synced, tip),
        "reorg_handled": reorg_handled,
        "newly_revealed": revealed_count,
    }


def run_forever(
    db_path: str,
    node: NodeClient,
    scheme: Optional[SaltedFutureBlockReveal] = None,
    poll_interval: float = 15.0,
) -> None:
    """Long-running loop, suitable as a systemd service / screen session on
    the same box as the daemon. Ctrl-C to stop; each pass is committed
    independently so an interruption never loses more than the in-flight pass."""
    scheme = scheme or SaltedFutureBlockReveal(maturity=30)
    log.info("sync_daemon starting: db=%s poll_interval=%.1fs", db_path, poll_interval)
    while True:
        try:
            with db.open_db(db_path) as conn:
                summary = run_once(conn, node, scheme)
            if summary["reorg_handled"] or summary["newly_revealed"]:
                log.info("sync pass: %s", summary)
        except DeepReorgError:
            log.exception("deep reorg beyond configured bound -- daemon stopping, needs manual review")
            raise
        except Exception:
            # transient errors (node briefly unreachable, etc.) -- log and keep polling.
            # node_client.py already retries transport-level failures internally;
            # anything that reaches here survived those retries and failed anyway.
            log.exception("sync pass failed, will retry next interval")
        time.sleep(poll_interval)
