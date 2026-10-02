"""
Unifies Set 1 (bundled with the app, defined in cards.py) with any
additional sets fetched via set_manifest.py's card-set manifest, and
answers the two questions every other module actually needs:

  1. "Which set is active at height H, and what's its card pool for a
     given tier?" -- used by sync_daemon.py's reveal_ready_packs() when
     picking a brand-new card for a freshly matured block.
  2. "What are this card_id's name/tier/description/location?" -- used
     by render_card.py (to draw it) and the Binder tab (to show it),
     for a card_id that's already been picked and just needs displaying.

Deliberately does NOT touch cards.py at all -- Set 1 keeps working exactly
as it always has, including support for both the depth/location and
sector Card schema variants already floating around this project (see
cards.py's own history for why). A cached set's cards are represented
with a small self-contained CachedCard object instead, defined here,
using depth+location as the standard for anything generated going
forward -- no dependency on whatever shape a future editor's local
cards.py happens to use.

Every card_id in this project is prefixed with its set ("S1-...",
"S2-...", ...), so get_card_by_id() can go straight to the right set
instead of scanning every era's pool -- cheap, and correct as long as
the S<N> prefix convention keeps being followed for future sets, which
render_card.py and cli.py's own S1-<TIER>-NNN scheme already establishes.
"""

from dataclasses import dataclass
from typing import Optional

import cards
import set_manifest

TIERS = ("uncommon", "holo_rare", "secret_rare")


@dataclass
class CachedCard:
    card_id: str
    name: str
    tier: str
    description: str
    depth: int = 0
    location: str = ""


class SetEra:
    """One set's card pools plus the height it became active at."""

    def __init__(self, set_id: str, active_from_height: int, pools: dict[str, list]):
        self.set_id = set_id
        self.active_from_height = active_from_height
        self.pools = pools  # tier -> list of card objects

    def pool_size(self, tier: str) -> int:
        return len(self.pools.get(tier, []))

    def list_cards(self, tier: str) -> list:
        return self.pools.get(tier, [])


def _set1_era() -> SetEra:
    pools = {tier: cards.list_cards(tier) for tier in TIERS}
    return SetEra(set_id="S1", active_from_height=0, pools=pools)


def _cached_era(entry: dict) -> Optional[SetEra]:
    data = set_manifest.load_cached_set(entry["set_id"])
    if data is None:
        return None
    pools: dict[str, list] = {tier: [] for tier in TIERS}
    for row in data.get("cards", []):
        card = CachedCard(
            card_id=row["card_id"],
            name=row["name"],
            tier=row["tier"],
            description=row.get("description", ""),
            depth=row.get("depth", 0),
            location=row.get("location", ""),
        )
        pools.setdefault(card.tier, []).append(card)
    return SetEra(set_id=entry["set_id"], active_from_height=entry["active_from_height"], pools=pools)


def load_eras() -> list[SetEra]:
    """All known eras -- Set 1 always included, plus any additional set
    that's actually been downloaded and cached. A set listed in the
    manifest but not yet successfully cached (offline, or this is the
    first launch since it was announced) is silently skipped: callers
    just see the highest era that IS cached, exactly as if the new set
    didn't exist yet, rather than crashing or guessing at its contents."""
    eras = [_set1_era()]
    for entry in set_manifest.list_known_set_entries():
        if entry.get("set_id") == "S1":
            continue  # S1 is always the bundled one above, never a cached override
        era = _cached_era(entry)
        if era is not None:
            eras.append(era)
    eras.sort(key=lambda e: e.active_from_height)
    return eras


def active_era_for_height(height: int, eras: Optional[list[SetEra]] = None) -> SetEra:
    """The era whose activation height is the highest one <= height.
    Set 1's activation height is always 0, so this always returns
    something -- there's no height with no active era."""
    eras = eras if eras is not None else load_eras()
    current = eras[0]
    for era in eras:
        if era.active_from_height <= height:
            current = era
        else:
            break
    return current


def get_card_by_id(card_id: str):
    """Looks up an already-selected card_id for display purposes,
    regardless of which set it came from. Uses the S<N> prefix to go
    straight to the right set (S1 -> cards.py's own catalog, unchanged;
    anything else -> that set's cache) instead of scanning every era."""
    set_id = card_id.split("-", 1)[0]

    if set_id == "S1":
        # Deliberately cards.list_cards(tier) here, not cards.CARD_SETS directly --
        # list_cards/pool_size are the two functions proven identical between
        # this project's cards.py and the other cards.py variant floating around
        # (see sync_daemon.py's reveal_ready_packs for the same reasoning), so
        # this works regardless of which cards.py is actually sitting in the
        # project folder.
        for tier in TIERS:
            for c in cards.list_cards(tier):
                if c.card_id == card_id:
                    return c
        return None

    data = set_manifest.load_cached_set(set_id)
    if data is None:
        return None
    for row in data.get("cards", []):
        if row["card_id"] == card_id:
            return CachedCard(
                card_id=row["card_id"],
                name=row["name"],
                tier=row["tier"],
                description=row.get("description", ""),
                depth=row.get("depth", 0),
                location=row.get("location", ""),
            )
    return None


def art_path_for(card_id: str):
    """Path to a card's art file, wherever it actually lives -- bundled
    art/ for Set 1, the set_cache/<set_id>/art/ download folder for
    anything else. Returns None if not found (render_card.render_card_image
    already handles a missing art_path by drawing the schematic
    placeholder instead of crashing)."""
    from pathlib import Path

    set_id = card_id.split("-", 1)[0]
    if set_id == "S1":
        path = Path(__file__).parent / "art" / f"{card_id}.png"
        return path if path.exists() else None
    return set_manifest.art_path_for(set_id, card_id)
