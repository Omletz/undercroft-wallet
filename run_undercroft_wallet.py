#!/usr/bin/env python3
"""
Undercroft Wallet -- double-click entry point.

This is the file the eventual desktop shortcut / packaged .exe points at.
It exists as its own tiny file (rather than just running wallet_app.py
directly) so that once this gets packaged with PyInstaller, the built
executable's name and entry point stay obviously separate from the library
code (wallet_app.py, wallet_rpc.py, node_manager.py, theme.py) that a
packager bundles alongside it.
"""

import sys

from wallet_app import main

if __name__ == "__main__":
    sys.exit(main())
