# Undercroft Wallet

Desktop GUI wallet for **The Undercroft (UCFT)** — a Bitcoin Core v25.0
SHA256d fork where mining a block can also mint a collectible trading card.

## Download (Windows, recommended for most people)

Grab the latest `Undercroft Wallet.exe` from this repo's
[Releases](../../releases) page — one file, double-click to run, no Python
or command line needed. Full setup walkthrough: [WALLET_SETUP.md](WALLET_SETUP.md).

## Running from source (Linux / developers)

This repo deliberately does **not** include a compiled `bitcoind` — `bin/`
is gitignored, same as the Windows build. The easiest path:

```bash
git clone https://github.com/Omletz/undercroft-wallet.git
cd undercroft-wallet
bash setup_wallet.sh
```

`setup_wallet.sh` installs the Python dependencies, looks for a compiled
`bitcoind`/`bitcoind.exe` already on your machine (or asks you for the path
once if it can't find one), and launches the wallet — nothing to configure
by hand. If you don't have a compiled daemon yet, build one first using
[undercroft-core](https://github.com/Omletz/undercroft-core)'s
[LINUX_BUILD.md](https://github.com/Omletz/undercroft-core/blob/main/LINUX_BUILD.md).

Prefer to drive it manually instead of the setup script:

```bash
python3 -m pip install -r requirements-wallet.txt --break-system-packages
python3 run_undercroft_wallet.py
```

Either way, this is a desktop GUI app (PySide6/Qt) — it needs a normal
Linux desktop environment to run, not a headless server. The daemon it
manages (`bitcoind`) is the headless piece.

## What's in this repo

- `wallet_app.py` / `run_undercroft_wallet.py` — the wallet GUI
- `node_manager.py` / `node_client.py` / `wallet_rpc.py` — launches and
  talks to `bitcoind` over RPC
- `cards.py` / `render_card.py` / `reveal.py` — the card-collecting mechanic
- `set_manifest.py` / `set_registry.py` / `sign_manifest.py` /
  `keygen_manifest_signing.py` — the signed card-set manifest system (lets
  new card sets roll out later without an app reinstall; publishing stays
  restricted to a single Ed25519 keypair kept offline)
- `sync_daemon.py` / `db.py` / `queries.py` / `cli.py` — the card and
  transaction indexer

## Verifying this code

This source is what the packaged `.exe` is actually built from (PyInstaller,
bundling the daemon via `--add-data "bin;bin"`). If you'd rather not trust
an unsigned `.exe`, this is how you check it yourself or build your own copy.
