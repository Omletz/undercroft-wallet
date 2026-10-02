"""
Fetches and locally caches The Undercroft's card-set manifest, so new sets
(Set 2, Set 3, ...) can be pushed out and old ones retired from future
minting WITHOUT every user having to reinstall the wallet. See
set_registry.py for how the cached data actually gets used by the reveal
logic and the Binder tab.

Design constraints this has to satisfy:
  - Every indexer (every user's wallet) has to end up with the exact same
    card data for a given set before the chain reaches that set's
    activation height, or two wallets could disagree about which card a
    block revealed. So a set's manifest entry, once its activation height
    has passed on the real chain, must never be edited -- only NEW sets
    with a future activation height get appended. This module can't
    enforce that against a file it doesn't control the editing of; it's
    the operating rule for whoever edits the hosted manifest (Jonathon).
  - Must never block or crash wallet startup if GitHub (or the network)
    is unreachable. A failed fetch just means "no new sets to check this
    launch, try again next time" -- the wallet still works fine on
    whatever is already cached, which always includes Set 1 (it ships
    bundled with the app, never fetched).
  - Already-cached sets are never deleted or re-fetched wholesale once
    downloaded -- a user's already-owned cards from an older set must
    keep rendering correctly forever, even if a future manifest edit
    drops that set's entry entirely.

MANIFEST_URL is a placeholder. Point it at wherever card_sets.json ends up
actually hosted -- a public GitHub repo's raw.githubusercontent.com link
is the simplest option and what Jonathon already has an account for.

SIGNING: now that this module (including MANIFEST_URL and this file's own
source) is going to be public along with the rest of the wallet, "only
Jonathon can publish a set" can no longer rest on the repo being obscure --
anyone can read the URL straight out of the open-sourced code. So the
hosted file isn't the plain manifest anymore; it's a signed envelope, and
this module refuses to trust anything that doesn't verify against
MANIFEST_PUBLIC_KEY_HEX below. See keygen_manifest_signing.py (one-time
keypair generation) and sign_manifest.py (run before every publish) --
both live alongside this file but are never bundled into the wallet
itself, since they need the PRIVATE key and only Jonathon should ever run
them.

Expected hosted card_sets.json shape (the signed envelope):
{
  "manifest": {
    "sets": [
      {
        "set_id": "S1",
        "name": "The Silicon Strata: 1st Edition",
        "active_from_height": 0,
        "cards_url": null,
        "art_base_url": null
      },
      {
        "set_id": "S2",
        "name": "<future set name>",
        "active_from_height": 700000,
        "cards_url": "https://raw.githubusercontent.com/.../set2_cards.json",
        "art_base_url": "https://raw.githubusercontent.com/.../set2_art",
        "cards_sha256": "<sha256 of the exact bytes served at cards_url, filled in by sign_manifest.py>"
      }
    ]
  },
  "signature": "<hex Ed25519 signature over the canonical JSON bytes of the 'manifest' object above>"
}

Set 1 is always treated as bundled (cards_url/art_base_url null and
ignored even if present) -- it ships with the app itself via cards.py and
the local art/ folder, never fetched. A set's own cards_url should point
to a JSON file shaped like: {"cards": [{"card_id", "name", "tier",
"description", "depth", "location"}, ...]}. Its bytes are checked against
cards_sha256 (itself covered by the manifest's own signature) before
anything from it is cached or used -- a set entry with a cards_url but no
cards_sha256 is trusted as-is (sign_manifest.py will refuse to produce one
in that state, so seeing it in the wild means the manifest was hand-edited
after signing, or signed by an older version of that script -- worth
noticing, but not a reason to brick an otherwise-signature-valid set).

Note: art files fetched via art_base_url are NOT individually hash-checked
-- lower stakes than card data (wrong/missing art is a visual glitch, not
a correctness or consensus issue, since card identity/tier/ownership never
depends on the art itself), and per-file signing would meaningfully
complicate the publish workflow for little real benefit. Worth revisiting
if that judgment call ever stops feeling right.
"""

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MANIFEST_URL = "https://raw.githubusercontent.com/Omletz/undercroft-card-sets/main/card_sets.json"
FETCH_TIMEOUT = 6.0  # seconds -- fast-fail so a slow/dead connection never noticeably delays startup

# Placeholder -- replace with the real hex string keygen_manifest_signing.py
# prints out, generated once on Jonathon's own machine. Until it's a real
# key, every fetched manifest will correctly fail verification and the
# wallet just runs on whatever's bundled/already cached (Set 1), same as
# being offline -- this is a safe default, not a silent bypass.
MANIFEST_PUBLIC_KEY_HEX = "31a9869d35587b845a2049010b313b2050ce36ebff73bf6843481b302eb0fc45"

# Deliberately NOT Path(__file__).parent -- in the packaged .exe, __file__ lives
# inside PyInstaller's onefile self-extraction folder (a fresh Temp\_MEI... dir
# every single launch, wiped on exit). Downloaded sets/art cached there would
# vanish and re-download every time the app opens -- silently breaking the
# whole point of caching, and worse, making an offline launch unable to find
# art for a set it already "has." Same persistent-data convention wallet_app.py
# already uses for settings.json/datadir: a real folder in the user's home
# directory that survives across launches and across app updates.
CACHE_DIR = Path.home() / ".undercroft-wallet" / "set_cache"
MANIFEST_CACHE_PATH = CACHE_DIR / "manifest.json"

_NETWORK_ERRORS = (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError)


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _canonical_bytes(obj) -> bytes:
    """Must match sign_manifest.py's canonicalization exactly, or a
    genuinely-signed manifest would fail to verify here."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify_and_unwrap(envelope: dict) -> Optional[dict]:
    """Checks the {"manifest", "signature"} envelope against
    MANIFEST_PUBLIC_KEY_HEX. Returns the inner manifest dict only if the
    signature genuinely verifies; None for anything else -- missing
    fields, malformed hex, or a signature that doesn't match (wrong key,
    tampered content, or someone who isn't Jonathon). Never raises: every
    failure mode here is treated exactly like the manifest being
    unreachable, not surfaced as an error."""
    try:
        manifest = envelope["manifest"]
        signature = bytes.fromhex(envelope["signature"])
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(MANIFEST_PUBLIC_KEY_HEX))
        public_key.verify(signature, _canonical_bytes(manifest))
        return manifest
    except (KeyError, ValueError, TypeError, InvalidSignature):
        return None


def fetch_manifest() -> Optional[dict]:
    """Best-effort fetch of the hosted, signed manifest. Returns None
    (never raises) on any failure -- no network, DNS failure, bad JSON,
    HTTP error, or a signature that doesn't verify. A None here is a
    completely normal, expected condition (offline, nothing hosted yet,
    signature check failed, etc.), not an error to surface to the user --
    the wallet just keeps running on whatever's already cached/bundled."""
    try:
        with urllib.request.urlopen(MANIFEST_URL, timeout=FETCH_TIMEOUT) as resp:
            envelope = json.loads(resp.read().decode("utf-8"))
        manifest = _verify_and_unwrap(envelope)
        if manifest is None:
            return None  # unsigned, badly signed, or signed by the wrong key
        _ensure_cache_dir()
        MANIFEST_CACHE_PATH.write_text(json.dumps(manifest, indent=2))
        return manifest
    except _NETWORK_ERRORS:
        return None


def _load_cached_manifest() -> Optional[dict]:
    if not MANIFEST_CACHE_PATH.exists():
        return None
    try:
        return json.loads(MANIFEST_CACHE_PATH.read_text())
    except (ValueError, OSError):
        return None


def list_known_set_entries() -> list[dict]:
    """Set entries from the manifest -- freshly fetched if possible, else
    whatever was cached from the last successful fetch, else an empty
    list. An empty list just means "only Set 1 exists as far as this
    wallet knows", which is always true regardless of manifest state."""
    manifest = fetch_manifest()
    if manifest is None:
        manifest = _load_cached_manifest()
    if manifest is None:
        return []
    return manifest.get("sets", [])


def _set_cache_dir(set_id: str) -> Path:
    return CACHE_DIR / set_id


def is_set_cached(set_id: str) -> bool:
    return (_set_cache_dir(set_id) / "cards.json").exists()


def download_set(entry: dict) -> bool:
    """Downloads one set's card data (and art, if a base URL is given)
    into the local cache. Returns False on any failure without raising --
    same best-effort philosophy as fetch_manifest(). Safe to call again
    for an already-cached set: re-fetches cards.json (cheap) and only
    downloads art files that aren't already on disk, so a partial/failed
    art download from a previous attempt gets retried without
    re-downloading everything."""
    cards_url = entry.get("cards_url")
    if not cards_url:
        return False  # no cards_url means "bundled with the app" (Set 1) -- nothing to download

    try:
        with urllib.request.urlopen(cards_url, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read()
    except _NETWORK_ERRORS:
        return False

    # entry itself came from a manifest that already passed signature
    # verification, so a cards_sha256 recorded on it is trustworthy --
    # checking the just-downloaded bytes against it is what stops a
    # compromised/mirrored cards_url host from serving different card
    # data than what Jonathon actually signed off on.
    expected_hash = entry.get("cards_sha256")
    if expected_hash and hashlib.sha256(raw).hexdigest() != expected_hash:
        return False  # tampered, corrupted, or wrong file -- do not cache or use it

    try:
        card_data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False

    set_dir = _set_cache_dir(entry["set_id"])
    art_dir = set_dir / "art"
    art_dir.mkdir(parents=True, exist_ok=True)
    (set_dir / "cards.json").write_text(json.dumps(card_data, indent=2))

    art_base_url = entry.get("art_base_url")
    if art_base_url:
        for card in card_data.get("cards", []):
            art_path = art_dir / f"{card['card_id']}.png"
            if art_path.exists():
                continue  # already have it -- a minted card's art never changes
            try:
                url = art_base_url.rstrip("/") + f"/{card['card_id']}.png"
                urllib.request.urlretrieve(url, art_path)
            except _NETWORK_ERRORS:
                continue  # this one card's art failed -- keep going rather than abort the whole set

    return True


def load_cached_set(set_id: str) -> Optional[dict]:
    path = _set_cache_dir(set_id) / "cards.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


def art_path_for(set_id: str, card_id: str) -> Optional[Path]:
    path = _set_cache_dir(set_id) / "art" / f"{card_id}.png"
    return path if path.exists() else None


def sync_sets() -> list[str]:
    """The one function the wallet actually calls on startup: fetches the
    manifest and downloads any set it doesn't already have cached. Wrapped
    so nothing here can ever raise out to the caller -- worst case is an
    empty list, meaning nothing new this launch, which is a normal
    outcome, not a failure. Returns the set_ids newly downloaded (for
    logging/status purposes only)."""
    newly_cached: list[str] = []
    try:
        for entry in list_known_set_entries():
            if entry.get("set_id") == "S1":
                continue  # bundled, never fetched
            if is_set_cached(entry["set_id"]):
                continue
            if download_set(entry):
                newly_cached.append(entry["set_id"])
    except Exception:  # noqa: BLE001 -- startup must never fail because of this
        return newly_cached
    return newly_cached
