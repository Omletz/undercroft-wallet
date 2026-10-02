"""
Pluggable reveal schemes for pack rarity.

SPARSE SINGLE-DROP MODEL (current design). Most mined blocks yield coins
only -- no card at all. Only 15% of mature blocks mint a single card, split
across three tiers:

    secret_rare   0.2%   (~2/day  at 960 blocks/day)
    holo_rare     2.8%   (~27/day)
    uncommon      12.0%  (~115/day)
    NO DROP       85.0%  (~816/day) -- coins only, classify_seed() -> None

This replaces the earlier design's "common" tier: the 85% fallback case is
no longer a low-rarity card, it is *no card*. There is no "common" tier
anymore.

Thresholds are computed as EXACT integers (via Fraction, not float division)
against the FULL 256-bit SHA256 digest interpreted as a big-endian integer --
unchanged from before, and deliberately NOT diluted by splitting the seed's
bits between the tier decision and card selection (see below). This matters
more than it looks: tier classification has to be bit-for-bit reproducible
across every independent indexer implementation (that's the whole trust
model here -- no consensus enforcement, just "anyone can re-derive it and
get the same answer"). Floating-point division has platform- and language-
dependent rounding at the bit level; two indexers written in different
languages could disagree right at a tier boundary. Integer comparison
against a fixed hex constant can't drift.

CARD SELECTION WITHIN A TIER. The spec asked for "the remaining bytes of
the 32-byte seed" to deterministically pick a card from the winning tier's
pool. Taken literally that conflicts with "use exact 256-bit thresholds for
the tier decision" -- the tier decision already treats the full 32 bytes as
one 256-bit integer, so there ARE no leftover/unused bytes to split off
without shrinking the tier check's precision (e.g. a 128/128 bit split
would quietly turn the tier decision into a 128-bit comparison, which is
not what "exact 256-bit thresholds" asked for).

The fix: derive a SECOND, independent 256-bit value from the same seed via
domain-separated hashing --

    CardIndexSeed_H = SHA256(b"pow-tcg-card-index" || Seed_H)

-- and use THAT (mod pool_size) to pick the card index. This keeps the tier
decision at full, undiluted 256-bit precision exactly as specified, while
still giving card selection its own uniformly-distributed, deterministic,
independently-reproducible source of randomness derived from the same
underlying seed. It costs one extra SHA256 call (cheap, and every indexer
re-derives it identically -- same trust model as everything else here).

Reveal scheme (finalized): SaltedFutureBlockReveal --

    Seed_H = SHA256(PayoutScript_H || BlockHash_H || BlockHash_{H+30})

Folding in the pack's own payout script and mint-block hash means a single
grind attempt on block H+30 no longer produces a correlated result across
every pack maturing in the same window (that was the batch-amplification
flaw in the plain multi-block version). It does NOT defend a single
targeted pack against whoever ends up mining H+30 -- see project notes.

SingleFutureBlockReveal is kept as the simpler baseline for comparison.
"""

import hashlib
from fractions import Fraction
from typing import Optional, Protocol

BITS = 256
_TWO_POW = 1 << BITS

_CARD_INDEX_DOMAIN_TAG = b"pow-tcg-card-index"


def _threshold(cumulative_fraction: Fraction) -> int:
    """Exact floor(fraction * 2**256) via integer arithmetic -- no float involved."""
    return (cumulative_fraction.numerator * _TWO_POW) // cumulative_fraction.denominator


# (tier name, cumulative fraction "at least this rare"), rarest first.
# Exclusive rates: secret_rare 0.2%, holo_rare 2.8%, uncommon 12.0%, no-drop 85.0%.
TIER_CUMULATIVE = [
    ("secret_rare", Fraction(2, 1000)),
    ("holo_rare", Fraction(30, 1000)),
    ("uncommon", Fraction(150, 1000)),
]
TIERS = [(name, _threshold(frac)) for name, frac in TIER_CUMULATIVE]


def classify_seed(seed: bytes) -> Optional[str]:
    """Map a 32-byte SHA256 digest to a rarity tier via exact integer comparison,
    or None if the block falls in the 85% no-drop (coins-only) case."""
    assert len(seed) == 32, "expected a 32-byte SHA256 digest"
    value = int.from_bytes(seed, "big")
    for name, threshold in TIERS:
        if value < threshold:
            return name
    return None


def derive_card_index_seed(seed: bytes) -> bytes:
    """Independent 256-bit value for card selection, domain-separated from the
    tier-classification use of the same seed (see module docstring)."""
    assert len(seed) == 32, "expected a 32-byte SHA256 digest"
    return hashlib.sha256(_CARD_INDEX_DOMAIN_TAG + seed).digest()


def pick_card_index(seed: bytes, pool_size: int) -> int:
    """Deterministically pick a card index in [0, pool_size) from the seed.
    Only meaningful when classify_seed(seed) returned a tier (not None)."""
    assert pool_size > 0, "tier card pool must be non-empty"
    card_seed = derive_card_index_seed(seed)
    return int.from_bytes(card_seed, "big") % pool_size


class RevealScheme(Protocol):
    def matures_at(self, height_h: int) -> int: ...
    def required_heights(self, height_h: int) -> list[int]: ...
    def compute_seed(self, node, height_h: int) -> bytes: ...


class SingleFutureBlockReveal:
    """Baseline / comparison scheme: seed = hash of block H+maturity alone."""

    def __init__(self, maturity: int = 30):
        self.maturity = maturity

    def matures_at(self, height_h: int) -> int:
        return height_h + self.maturity

    def required_heights(self, height_h: int) -> list[int]:
        return [height_h + self.maturity]

    def compute_seed(self, node, height_h: int) -> bytes:
        h_reveal = node.get_block_hash(height_h + self.maturity)
        return hashlib.sha256(bytes.fromhex(h_reveal)).digest()


class SaltedFutureBlockReveal:
    """Finalized scheme: Seed_H = SHA256(PayoutScript_H || BlockHash_H || BlockHash_{H+maturity})"""

    def __init__(self, maturity: int = 30):
        self.maturity = maturity

    def matures_at(self, height_h: int) -> int:
        return height_h + self.maturity

    def required_heights(self, height_h: int) -> list[int]:
        return [height_h, height_h + self.maturity]

    def compute_seed(self, node, height_h: int) -> bytes:
        payout_script = node.get_block_payout_script(height_h)
        hash_h = bytes.fromhex(node.get_block_hash(height_h))
        hash_reveal = bytes.fromhex(node.get_block_hash(height_h + self.maturity))
        return hashlib.sha256(payout_script.encode("utf-8") + hash_h + hash_reveal).digest()
