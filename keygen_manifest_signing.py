"""
One-time setup: generates the Ed25519 keypair used to sign The Undercroft's
card-set manifest (card_sets.json). This is what actually enforces "no one
but Jonathon can push a set" now that the wallet's source -- including
set_manifest.py's fetch/verify logic and MANIFEST_URL itself -- is going to
be public. Without this, "keep manifest publishing under sole control" was
only true because nobody else happened to know the repo existed.

Run this ONCE, on your own machine, before the first real sign_manifest.py
run. Needs the `cryptography` package (same one set_manifest.py itself now
depends on) -- `pip install cryptography` first if you haven't already.

Produces two files in the current directory:

  manifest_signing_key.pem   Your PRIVATE key. Never commit this, never
                             upload it anywhere (not GitHub, not a paste
                             site, not this chat), never share it. Anyone
                             who gets it can publish fake sets that every
                             Undercroft wallet will accept as genuine.
                             Losing it (not leaking it -- losing it)
                             means you can't publish a new set at all
                             without shipping a wallet update that
                             changes the baked-in public key. Back it up
                             somewhere safe and OFFLINE -- a USB drive, an
                             encrypted archive, a password manager's file
                             storage. Not the GitHub repo, even a private
                             one.

  manifest_public_key.txt   The matching PUBLIC key, hex-encoded. This
                             one is meant to be public -- paste it into
                             set_manifest.py's MANIFEST_PUBLIC_KEY_HEX
                             constant and ship it with the open-sourced
                             wallet. Anyone can see it; that's fine, a
                             public key can't be used to forge a
                             signature, only to check one.

Refuses to overwrite an existing manifest_signing_key.pem in this
directory, so re-running this by accident can't silently orphan a key
you're already using.
"""

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PRIVATE_KEY_PATH = Path("manifest_signing_key.pem")
PUBLIC_KEY_PATH = Path("manifest_public_key.txt")


def main() -> None:
    if PRIVATE_KEY_PATH.exists():
        raise SystemExit(
            f"{PRIVATE_KEY_PATH} already exists -- refusing to overwrite an existing "
            "signing key. If you really mean to rotate it, move the old file aside "
            "yourself first -- and remember every wallet already shipped with the old "
            "public key will reject manifests signed by the new one until it's updated."
        )

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    PRIVATE_KEY_PATH.write_text(private_bytes.decode("ascii"))
    PUBLIC_KEY_PATH.write_text(public_bytes.hex())
    try:
        PRIVATE_KEY_PATH.chmod(0o600)  # best-effort -- keep other local users/processes out
    except OSError:
        pass

    print(f"Private key written to {PRIVATE_KEY_PATH} -- back this up OFFLINE, never commit it.")
    print(f"Public key written to {PUBLIC_KEY_PATH}:")
    print(f"  {public_bytes.hex()}")
    print("Paste that hex string into set_manifest.py's MANIFEST_PUBLIC_KEY_HEX constant.")


if __name__ == "__main__":
    main()
