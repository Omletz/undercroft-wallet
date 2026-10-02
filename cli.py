#!/usr/bin/env python3
"""
Stage 4 CLI. Talks to a real node over RPC and a local SQLite store.

Examples:

    # one-off sync pass against a local regtest node, then exit
    python cli.py sync --once --rpc-user foo --rpc-password bar --network regtest

    # long-running sync daemon (Ctrl-C to stop)
    python cli.py sync --daemon --rpc-user foo --rpc-password bar --network regtest

    # query the local DB (no node/RPC needed for any of these)
    python cli.py stats
    python cli.py owner P1a2b3c...
    python cli.py block 12345
    python cli.py block 00000c7e9a3a...
    python cli.py miners
    python cli.py reorgs

Credentials can also come from environment variables instead of flags:
POW_TCG_RPC_USER, POW_TCG_RPC_PASSWORD, POW_TCG_RPC_HOST, POW_TCG_RPC_PORT.

--network picks the project's default RPC port when --rpc-port isn't given
explicitly: 23856 for main, 23866 for regtest (see chainparamsbase.cpp).
"""

import argparse
import json
import logging
import os
import sys

import db
import queries
import sync_daemon
from node_client import BitcoinRPCClient, NodeConfig
from reveal import SaltedFutureBlockReveal

DEFAULT_RPC_PORTS = {"main": 23856, "regtest": 23866}
DEFAULT_DB_PATH = "pow_tcg.sqlite3"


def build_node_config(args: argparse.Namespace) -> NodeConfig:
    rpc_user = args.rpc_user or os.environ.get("POW_TCG_RPC_USER")
    rpc_password = args.rpc_password or os.environ.get("POW_TCG_RPC_PASSWORD")
    if not rpc_user or not rpc_password:
        sys.exit(
            "RPC credentials required: pass --rpc-user/--rpc-password or set "
            "POW_TCG_RPC_USER / POW_TCG_RPC_PASSWORD"
        )
    host = args.rpc_host or os.environ.get("POW_TCG_RPC_HOST", "127.0.0.1")
    port = args.rpc_port or int(os.environ.get("POW_TCG_RPC_PORT", 0)) or DEFAULT_RPC_PORTS[args.network]
    return NodeConfig(rpc_user=rpc_user, rpc_password=rpc_password, host=host, rpc_port=port)


def cmd_sync(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = build_node_config(args)
    node = BitcoinRPCClient(config)
    scheme = SaltedFutureBlockReveal(maturity=args.maturity)

    # fail fast with a clear message if the node isn't reachable at all,
    # rather than the first sync pass's error looking like a mystery
    info = node.get_blockchain_info()
    print(f"connected: chain={info.get('chain')} blocks={info.get('blocks')}")

    if args.once:
        with db.open_db(args.db) as conn:
            summary = sync_daemon.run_once(conn, node, scheme)
        print(json.dumps(summary, indent=2))
    else:
        sync_daemon.run_forever(args.db, node, scheme, poll_interval=args.interval)


def cmd_stats(args: argparse.Namespace) -> None:
    with db.open_db(args.db) as conn:
        print(json.dumps(queries.get_supply_stats(conn), indent=2))


def cmd_owner(args: argparse.Namespace) -> None:
    with db.open_db(args.db) as conn:
        print(json.dumps(queries.get_cards_by_owner(conn, args.address), indent=2))


def cmd_block(args: argparse.Namespace) -> None:
    with db.open_db(args.db) as conn:
        if args.height_or_hash.isdigit():
            result = queries.get_drop_by_height(conn, int(args.height_or_hash))
        else:
            result = queries.get_drop_by_hash(conn, args.height_or_hash)
    if result is None:
        sys.exit(f"no record found for {args.height_or_hash!r}")
    print(json.dumps(result, indent=2))


def cmd_miners(args: argparse.Namespace) -> None:
    with db.open_db(args.db) as conn:
        print(json.dumps(queries.get_miner_stats(conn, args.address), indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    with db.open_db(args.db) as conn:
        print(json.dumps(queries.get_sync_status(conn), indent=2))


def cmd_reorgs(args: argparse.Namespace) -> None:
    with db.open_db(args.db) as conn:
        print(json.dumps(queries.get_recent_reorgs(conn, args.limit), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="pow-tcg-coin Stage 4 indexer/CLI")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite file (default: {DEFAULT_DB_PATH})")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="sync the local DB against a live node")
    p_sync.add_argument("--once", action="store_true", help="run a single pass and exit (default: daemon loop)")
    p_sync.add_argument("--daemon", action="store_true", help="explicit alias for the default loop behavior")
    p_sync.add_argument("--interval", type=float, default=15.0, help="poll interval in seconds (daemon mode)")
    p_sync.add_argument("--maturity", type=int, default=30, help="reveal maturity window in blocks")
    p_sync.add_argument("--network", choices=["main", "regtest"], default="regtest")
    p_sync.add_argument("--rpc-host", dest="rpc_host", default=None)
    p_sync.add_argument("--rpc-port", dest="rpc_port", type=int, default=None)
    p_sync.add_argument("--rpc-user", dest="rpc_user", default=None)
    p_sync.add_argument("--rpc-password", dest="rpc_password", default=None)
    p_sync.set_defaults(func=cmd_sync)

    p_stats = sub.add_parser("stats", help="collection-wide supply stats")
    p_stats.set_defaults(func=cmd_stats)

    p_owner = sub.add_parser("owner", help="cards currently held by a payout address")
    p_owner.add_argument("address")
    p_owner.set_defaults(func=cmd_owner)

    p_block = sub.add_parser("block", help="drop details by height or block hash")
    p_block.add_argument("height_or_hash")
    p_block.set_defaults(func=cmd_block)

    p_miners = sub.add_parser("miners", help="per-address mining/drop stats")
    p_miners.add_argument("--address", default=None, help="restrict to one address (default: all)")
    p_miners.set_defaults(func=cmd_miners)

    p_status = sub.add_parser("status", help="sync progress and reorg count")
    p_status.set_defaults(func=cmd_status)

    p_reorgs = sub.add_parser("reorgs", help="recent reorg_log entries")
    p_reorgs.add_argument("--limit", type=int, default=20)
    p_reorgs.set_defaults(func=cmd_reorgs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
