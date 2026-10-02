# Undercroft Wallet — Setup Guide

This covers everything from downloading `Undercroft Wallet.exe` to seeing
your first balance and cards. No Python, no command line, nothing else to
install — it's one file.

## 1. Download and run

Save `Undercroft Wallet.exe` anywhere you like (Desktop is fine) and
double-click it. That's the whole install.

### About the Windows warning

The first time you run it, Windows will very likely show a blue
"Windows protected your PC" screen. This happens because the file isn't
signed with a paid certificate from a commercial publisher — it does **not**
mean anything is actually wrong with it. To continue:

1. Click **More info**.
2. Click **Run anyway**.

If you want to double-check the file yourself before doing that: the
wallet's full source code is public, so you (or anyone technical you trust)
can read exactly what it does rather than taking that on faith.

## 2. First-time setup

A small setup window appears the first time you run it. For most people,
the defaults are already correct and you can just click **OK**:

- **Daemon path** — found automatically. You shouldn't need to touch this.
- **Network** — leave on **regtest** only if you were specifically asked to
  test on the pre-launch test network. Otherwise choose **main** — this is
  the real Undercroft network your coins and cards will actually count on.
- **Data directory** — where this wallet keeps its own copy of the
  blockchain and your wallet file. The suggested folder is fine; you don't
  need to create it yourself.
- **RPC port / username / password** — these only matter if you're running
  more than one Undercroft node on the same computer. Leave them as-is
  otherwise.
- **Connect to peer (optional)** — leave this blank in the normal case. Only
  fill this in if you've been given a specific Undercroft node address to
  connect to (see the note on that below).

Click **OK**, and the app will start its own node in the background. First
launch can take up to a minute; after that it opens the wallet window
directly.

### A note on "Connect to peer"

Your wallet needs to find other Undercroft nodes to see the real state of
the network — new blocks, other people's transactions, etc. Normally this
happens automatically. If you're ever told to enter a specific address here
(for example, while the network is still small), use the format
`host:port` — and make sure it's the **P2P port**, not the RPC port
(mainnet P2P is 23857, regtest P2P is 23867 — different from the RPC ports
mentioned above). Using the wrong port here just means it won't connect;
it won't break anything else.

## 3. Back up your wallet — do this before you rely on this wallet for real funds

Your coins are controlled entirely by one file: `wallet.dat`, inside the
data directory folder you set up in step 2 (specifically at
`<data directory>\wallets\undercroft\wallet.dat`). If that file is lost and
you have no backup, any coins in it are gone permanently — nobody, including
Jonathon, can recover them.

What to do:

- Copy `wallet.dat` (or, simplest, the entire data directory folder) to a
  second location — a USB drive, an external disk, cloud storage you trust.
  Not just another folder on the same computer.
- Do this again periodically, especially after you've generated several new
  receiving addresses — a very old backup may not know about addresses you
  created more recently.
- Close the wallet app first if you can before copying, so nothing's
  actively being written to the file at that moment.

This is exactly the same responsibility as any other cryptocurrency wallet
— there's no company or exchange holding your coins for you here.

## 4. What to check once it's running

- **Overview** tab shows the network you picked and a block height that
  increases over time.
- **Receive** tab starts empty the very first time — click **Generate New
  Address** to create your first one. After that it's remembered here even
  after you close and reopen the app, and won't change on its own.
- **Send** a small test amount to make sure it shows up in **History**.
- **Binder** shows any cards you've received, with real card art — it fills
  in automatically as blocks you've mined mature, nothing to set up. Click
  any card to see it full-size with its description.

## 5. Known rough edges (fine for now, worth knowing)

- No QR code on the Receive tab yet — copy/paste only.
- History shows the most recent 100 transactions; no pagination yet.
- Closing the wallet window stops the node it started. There's no "keep
  running in the background" option yet.
- Changing the node connection settings (daemon path, data directory,
  network, RPC port, connect-to-peer) requires restarting the app to take
  effect. RPC username/password take effect on the next refresh without a
  restart.

## 6. If something goes wrong

The startup screen shows the daemon's own error message directly (not a
generic guess) if it fails to start — read what it says first. Common
causes: a data directory that's already in use by a different node with
different RPC credentials (change the RPC port or matching credentials in
Settings), or the daemon couldn't find enough disk space.

If you're stuck, the app's Settings button (available even on a failed
startup screen) lets you review or fix anything from step 2 without
starting over.
