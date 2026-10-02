"""
Offline tool: signs card_sets.json before you publish/upload it, so every
wallet out there (via set_manifest.py's baked-in public key) can verify the
manifest it downloaded genuinely came from you and wasn't tampered with,
substituted, or served by someone who merely got write access to wherever
it's hosted. Run this on your own machine -- never in a place that would
expose manifest_signing_key.pem.

Workflow whenever you add/change a set:

  1. Write or edit your plain manifest (just the {"sets": [...]} content --
     no signature yet) in a local file, e.g. unsigned_manifest.json.
  2. For every set entry that has a cards_url, pass --set-file so this
     script can hash your local copy of that set's card data and record it
     as cards_sha256 in the entry -- this is what lets a wallet trust the
     cards_url response later without needing it separately signed.
  3. Run this script. It writes the final, signed card_sets.json --
     THIS is the file you upload/commit as the real hosted manifest.
     The unsigned input file is a working file, never published.

Example:

    python sign_manifest.py unsigned_manifest.json manifest_signing_key.pem \\
        -o card_sets.json \\
        --set-file S2=set2_cards.json

Reminder from set_manifest.py's own operating rule: once a set's
active_from_height has passed on the real chain, never edit that entry
again in the unsigned source either -- only append new future-height sets,
then re-sign and re-publish the whole file.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _canonical_bytes(obj) -> bytes:
    """Same canonicalization set_manifest.py verifies against -- sorted
    keys, no incidental whitespace, so signing and verifying always agree
    on exactly which bytes were signed regardless of dict ordering."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("manifest_path", help="Unsigned card_sets.json (just the {'sets': [...]} content)")
    parser.add_argument(
        "key_path", help="Path to manifest_signing_key.pem, from keygen_manifest_signing.py"
    )
    parser.add_argument("-o", "--output", required=True, help="Where to write the signed, ready-to-publish manifest")
    parser.add_argument(
        "--set-file",
        action="append",
        default=[],
        metavar="SET_ID=path/to/cards.json",
        help="Hash a local copy of SET_ID's card data and record it as cards_sha256. Repeatable.",
    )
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest_path).read_text())
    if "sets" not in manifest:
        sys.exit(f"{args.manifest_path} doesn't look like a card_sets.json -- no top-level 'sets' key")

    hashes = {}
    for spec in args.set_file:
        if "=" not in spec:
            sys.exit(f"--set-file must look like SET_ID=path, got: {spec!r}")
        set_id, path = spec.split("=", 1)
        hashes[set_id] = hashlib.sha256(Path(path).read_bytes()).hexdigest()

    for entry in manifest["sets"]:
        set_id = entry.get("set_id")
        if set_id in hashes:
            entry["cards_sha256"] = hashes[set_id]
        elif entry.get("cards_url") and not entry.get("cards_sha256"):
            sys.exit(
                f"Set {set_id!r} has a cards_url but no cards_sha256 and no --set-file was given "
                f"for it -- pass --set-file {set_id}=path/to/its/local/cards.json"
            )

    key_bytes = Path(args.key_path).read_bytes()
    try:
        private_key = serialization.load_pem_private_key(key_bytes, password=None)
    except ValueError as exc:
        sys.exit(f"Couldn't read {args.key_path} as a private key: {exc}")
    if not isinstance(private_key, Ed25519PrivateKey):
        sys.exit(f"{args.key_path} isn't an Ed25519 key -- generate one with keygen_manifest_signing.py")

    signature = private_key.sign(_canonical_bytes(manifest))

    envelope = {"manifest": manifest, "signature": signature.hex()}
    Path(args.output).write_text(json.dumps(envelope, indent=2))
    print(f"Signed manifest written to {args.output} -- this is the file to publish.")
    if not hashes and any(e.get("cards_url") for e in manifest["sets"]):
        print(
            "Note: at least one set has a cards_url but its cards_sha256 came from the "
            "unsigned input file rather than being freshly computed here -- double-check "
            "it still matches what you're about to upload."
        )


if __name__ == "__main__":
    main()
