"""
Undercroft Wallet visual theme.

Deliberately reuses the exact RGB values from render_card.py's palette
(COLOR_BG/COLOR_PANEL/COLOR_GREEN/COLOR_AMBER/COLOR_CYAN/COLOR_MUTED/
COLOR_TEXT) rather than redefining a separate wallet color scheme, so the
wallet app and the card renders feel like one product instead of two. This
module has zero dependency on render_card.py / PIL on purpose -- the wallet
app shouldn't need Pillow just to paint its own window, so the six colors
that matter are copied here as the single source of truth for the *app*
side, and a comment marks them as intentionally kept in sync with
render_card.py's copy by hand (they change together, but rarely, so a
shared import isn't worth the coupling).

TIER_ACCENT mirrors render_card.py's own dict (green/cyan/amber for
uncommon/holo_rare/secret_rare) so the Binder tab's card-tier labels use
the same accent color as the actual printed card.
"""

from PySide6.QtGui import QColor, QFont, QFontDatabase

# --- palette (kept byte-for-byte identical to render_card.py) --------------

COLOR_BG = (10, 14, 12)
COLOR_PANEL = (16, 22, 18)
COLOR_GREEN = (57, 255, 20)
COLOR_AMBER = (255, 176, 0)
COLOR_CYAN = (70, 220, 255)
COLOR_MUTED = (40, 90, 55)
COLOR_TEXT = (180, 225, 190)
COLOR_WHITE = (240, 240, 240)
COLOR_ERROR = (255, 90, 90)  # not used by render_card.py -- wallet-only (failed tx, RPC error)

TIER_ACCENT = {
    "uncommon": COLOR_GREEN,
    "holo_rare": COLOR_CYAN,
    "secret_rare": COLOR_AMBER,
}


def rgb(t: tuple[int, int, int]) -> str:
    """(r, g, b) -> 'rgb(r, g, b)' for QSS."""
    return f"rgb({t[0]}, {t[1]}, {t[2]})"


def hexs(t: tuple[int, int, int]) -> str:
    """(r, g, b) -> '#rrggbb'."""
    return "#%02x%02x%02x" % t


def qcolor(t: tuple[int, int, int]) -> QColor:
    return QColor(*t)


# --- font --------------------------------------------------------------

_MONO_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]

_mono_family_cache: str | None = None


def mono_family() -> str:
    """Load DejaVu Sans Mono into Qt's font database (same font the cards
    use) and return its family name. Falls back to Qt's generic 'Monospace'
    if DejaVu isn't installed on this machine -- never crashes on a missing
    font file, just looks slightly different."""
    global _mono_family_cache
    if _mono_family_cache is not None:
        return _mono_family_cache

    for path in _MONO_FONT_PATHS:
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id != -1:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                _mono_family_cache = families[0]
                return _mono_family_cache

    _mono_family_cache = "Monospace"
    return _mono_family_cache


def base_font(point_size: int = 10, bold: bool = False) -> QFont:
    font = QFont(mono_family(), point_size)
    font.setBold(bold)
    return font


# --- stylesheet --------------------------------------------------------

def stylesheet() -> str:
    """One QSS blob applied to the whole app: dark industrial/terminal look,
    green-on-black readouts, amber/cyan accents reserved for the things that
    are amber/cyan on the cards themselves (secret_rare / holo_rare), so the
    color vocabulary means the same thing everywhere in the product."""
    bg = hexs(COLOR_BG)
    panel = hexs(COLOR_PANEL)
    green = hexs(COLOR_GREEN)
    amber = hexs(COLOR_AMBER)
    cyan = hexs(COLOR_CYAN)
    muted = hexs(COLOR_MUTED)
    text = hexs(COLOR_TEXT)
    white = hexs(COLOR_WHITE)

    return f"""
    QMainWindow, QWidget {{
        background-color: {bg};
        color: {text};
    }}

    QTabWidget::pane {{
        border: 1px solid {muted};
        background-color: {bg};
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: {panel};
        color: {muted};
        border: 1px solid {muted};
        border-bottom: none;
        padding: 8px 18px;
        margin-right: 2px;
        font-weight: bold;
    }}
    QTabBar::tab:selected {{
        background-color: {bg};
        color: {green};
        border-color: {green};
    }}
    QTabBar::tab:hover:!selected {{
        color: {text};
    }}

    QLabel {{
        color: {text};
        background: transparent;
    }}
    QLabel[role="heading"] {{
        color: {green};
        font-weight: bold;
        font-size: 13pt;
    }}
    QLabel[role="value"] {{
        color: {white};
        font-size: 16pt;
        font-weight: bold;
    }}
    QLabel[role="muted"] {{
        color: {muted};
    }}
    QLabel[role="error"] {{
        color: {hexs(COLOR_ERROR)};
    }}

    QFrame[role="panel"] {{
        background-color: {panel};
        border: 1px solid {muted};
        border-radius: 2px;
    }}

    QPushButton {{
        background-color: {panel};
        color: {green};
        border: 1px solid {green};
        border-radius: 2px;
        padding: 8px 16px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background-color: {green};
        color: {bg};
    }}
    QPushButton:disabled {{
        color: {muted};
        border-color: {muted};
    }}
    QPushButton[variant="danger"] {{
        color: {hexs(COLOR_ERROR)};
        border-color: {hexs(COLOR_ERROR)};
    }}
    QPushButton[variant="danger"]:hover {{
        background-color: {hexs(COLOR_ERROR)};
        color: {bg};
    }}

    QLineEdit, QTextEdit, QPlainTextEdit {{
        background-color: {panel};
        color: {white};
        border: 1px solid {muted};
        border-radius: 2px;
        padding: 6px;
        selection-background-color: {green};
        selection-color: {bg};
    }}
    QLineEdit:focus, QTextEdit:focus {{
        border-color: {green};
    }}

    QTableWidget, QListWidget {{
        background-color: {panel};
        color: {text};
        border: 1px solid {muted};
        gridline-color: {muted};
    }}
    QHeaderView::section {{
        background-color: {bg};
        color: {green};
        border: 1px solid {muted};
        padding: 4px;
        font-weight: bold;
    }}
    QTableWidget::item:selected, QListWidget::item:selected {{
        background-color: {muted};
        color: {white};
    }}

    QScrollBar:vertical {{
        background: {bg};
        width: 12px;
    }}
    QScrollBar::handle:vertical {{
        background: {muted};
        min-height: 20px;
        border-radius: 2px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QStatusBar {{
        background-color: {panel};
        color: {muted};
        border-top: 1px solid {muted};
    }}

    QToolTip {{
        background-color: {panel};
        color: {green};
        border: 1px solid {green};
    }}
    """
