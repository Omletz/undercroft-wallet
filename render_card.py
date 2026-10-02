import colorsys, hashlib, os, random, textwrap
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import cards

WIDTH, HEIGHT = 750, 1050
COLOR_BG = (10, 14, 12)
COLOR_PANEL = (16, 22, 18)
COLOR_GREEN = (57, 255, 20)
COLOR_AMBER = (255, 176, 0)
COLOR_CYAN = (70, 220, 255)      # holo_rare's own accent -- was sharing amber with secret_rare
COLOR_MUTED = (40, 90, 55)
COLOR_TEXT = (180, 225, 190)
COLOR_WHITE = (240, 240, 240)

# Three-way tier -> accent color. Previously holo_rare and secret_rare both
# fell into one 'secret_rare' in ('secret_rare','holo_rare') bucket and
# shared amber -- that's the collision Jonathon flagged. uncommon is
# deliberately left plain (no foil/glitch treatment below) so the rare tiers
# still read as special by contrast.
TIER_ACCENT = {
    'uncommon': COLOR_GREEN,
    'holo_rare': COLOR_CYAN,
    'secret_rare': COLOR_AMBER,
}

# Viewport for artwork (or the schematic placeholder) -- exactly 550x550,
# so art_path images are resized to fit it exactly with no letterboxing.
VIEWPORT_BOX = (100, 130, 650, 680)
VIEWPORT_SIZE = (550, 550)


def _card_seed(card_id: str) -> int:
    """Deterministic per-card integer seed -- so holo foil angle and secret
    glitch placement are stable across re-renders of the same card (a
    reprint should look identical) but vary card to card instead of every
    holo looking like a stamped copy of the same effect."""
    return int(hashlib.sha256(card_id.encode('utf-8')).hexdigest()[:8], 16)


def _holo_foil_overlay(size, seed):
    """Diagonal rainbow foil sheen, the way a real holo card catches light at
    an angle. Built as a translucent RGBA layer with numpy (fast enough for
    550x550) and alpha-composited over the viewport -- works identically over
    real artwork or the schematic placeholder, since a foil layer is a
    physical property of the card stock, not the art printed on it."""
    w, h = size
    xx, yy = np.meshgrid(np.linspace(0, 1, w), np.linspace(0, 1, h))
    phase = (seed % 1000) / 1000.0
    band_freq = 1 + (seed % 3)  # 1-3 diagonal rainbow repeats across the card
    diag = ((xx + yy) / 2.0 * band_freq + phase) % 1.0

    lut_n = 256
    hue_lut = np.array(
        [colorsys.hsv_to_rgb(h_, 0.95, 1.0) for h_ in np.linspace(0, 1, lut_n)],
        dtype=np.float32,
    )
    idx = np.clip((diag * (lut_n - 1)).astype(np.int32), 0, lut_n - 1)
    rgb = (hue_lut[idx] * 255).astype(np.uint8)

    # sheen is strongest along a bright diagonal band, fading elsewhere --
    # a flat-opacity rainbow wash would just look tinted, not "foil". First
    # pass (165 peak / 25 floor) read too dark/subtle; second pass at these
    # levels was flagged as too intense over real artwork -- settled here.
    band_center = 0.5
    band_dist = np.abs(((diag - band_center + 0.5) % 1.0) - 0.5)
    alpha = np.clip(100 - band_dist * 320, 12, 100).astype(np.uint8)

    rgba = np.dstack([rgb, alpha])
    return Image.fromarray(rgba, mode='RGBA')


def _apply_secret_glitch(img, draw, box, seed):
    """Secret rares get a distinct 'the system flagged an anomaly' treatment
    instead of a shinier foil: a chromatic-aberration ghost border (classic
    glitch-art RGB channel split) plus a couple of translucent horizontal
    scan-tear bands -- fits the AI-surveillance lore (an artifact the system
    can barely classify) rather than just being 'holo but rarer'."""
    x0, y0, x1, y1 = box
    rnd = random.Random(seed)

    # scan-tear bands drawn on their own RGBA layer first, so the alpha
    # blends correctly (ImageDraw on an RGB image drops alpha entirely)
    overlay = Image.new('RGBA', (x1 - x0, y1 - y0), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for _ in range(rnd.randint(3, 5)):
        band_y = rnd.randint(20, (y1 - y0) - 20)
        band_h = rnd.randint(4, 10)
        shift = rnd.randint(-24, 24)
        color = rnd.choice([(255, 70, 70, 120), (70, 255, 255, 120), (255, 255, 255, 90)])
        odraw.rectangle([(shift, band_y), (x1 - x0 + shift, band_y + band_h)], fill=color)
    img.paste(overlay, (x0, y0), overlay)

    # chromatic-aberration ghost border: offset cyan/red copies behind the
    # true amber border, like a mis-tracked broadcast signal. Widened the
    # offset and stroke after the first pass was nearly invisible against
    # the true border at card resolution.
    offset = 6
    draw.rectangle([(x0 - offset, y0), (x1 - offset, y1)], outline=(70, 220, 255), width=3)
    draw.rectangle([(x0 + offset, y0), (x1 + offset, y1)], outline=(255, 60, 60), width=3)
    draw.rectangle([(x0, y0), (x1, y1)], outline=TIER_ACCENT['secret_rare'], width=2)

def _secret_rare_full_shimmer(size, seed):
    """Full-card diagonal rainbow shimmer for secret_rare, draped over the
    entire printed card (border, telemetry bar, sensor log, everything) --
    not just the art viewport the way holo_rare's sheen is. This is what
    Jonathon asked for after seeing a reference: a single rainbow sweep
    corner-to-corner across the whole card, like a physical foil stamp
    catching light edge to edge, layered as the very last thing before the
    scanline pass so the underlying content stays legible through it."""
    w, h = size
    xx, yy = np.meshgrid(np.linspace(0, 1, w), np.linspace(0, 1, h))
    phase = (seed % 1000) / 1000.0
    diag = ((xx + yy) / 2.0 + phase) % 1.0  # one sweep, not repeated bands

    lut_n = 256
    hue_lut = np.array(
        [colorsys.hsv_to_rgb(h_, 0.9, 1.0) for h_ in np.linspace(0, 1, lut_n)],
        dtype=np.float32,
    )
    idx = np.clip((diag * (lut_n - 1)).astype(np.int32), 0, lut_n - 1)
    rgb = (hue_lut[idx] * 255).astype(np.uint8)

    # narrow, bright band along the sweep, near-zero alpha off it -- the
    # first pass used a wide floor and read as a flat color wash across the
    # whole card instead of one rainbow streak crossing dark card stock.
    # Peak alpha lowered again after the ID/tier/edition-badge text became
    # hard to read under it at full opacity on real card renders.
    band_center = 0.5
    band_dist = np.abs(((diag - band_center + 0.5) % 1.0) - 0.5)
    alpha = np.clip(110 - band_dist * 420, 4, 110).astype(np.uint8)

    rgba = np.dstack([rgb, alpha])
    return Image.fromarray(rgba, mode='RGBA')


_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    # Windows has no DejaVu installed by default. Without a fallback here,
    # get_font() drops all the way to PIL's tiny fixed-size bitmap default
    # font on Windows -- fine for a quick WSL test render, but exactly
    # backwards for the Binder tab, whose whole point is showing off real
    # card art. Consolas ships with Windows itself and is a close visual
    # match, so it's tried before giving up to the bitmap fallback.
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "consolab.ttf"),
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "consola.ttf"),
]
_FONT_CACHE = {}
_warned_font_fallback = False


def get_font(size):
    """Crisp monospace vector typography, with a real fallback chain instead
    of a silent try/except: bold DejaVu Sans Mono, then regular, and only if
    NEITHER is present on disk does this drop to PIL's bitmap default font --
    which now prints a one-time warning instead of failing quietly, so a
    missing font package shows up immediately instead of as "the card just
    looks a little off"."""
    global _warned_font_fallback
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]

    for path in _FONT_PATHS:
        try:
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[size] = font
            return font
        except Exception:
            continue

    if not _warned_font_fallback:
        print(
            "WARNING: could not load DejaVu Sans Mono (bold or regular) from "
            f"{_FONT_PATHS} -- falling back to PIL's bitmap default font. "
            "Install the fonts-dejavu-mono (or fonts-dejavu) package to fix this."
        )
        _warned_font_fallback = True
    font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


def _card_sector_text(card) -> str:
    """Different local copies of cards.py describe this differently: this
    project's cards.py splits it into depth (int) + location (str), but
    Jonathon's currently-running local copy (built independently by another
    LLM) keeps it as one combined `sector` string like 'Depth 27 // Core
    Fiber Plenum'. Support both attribute names instead of hard-requiring
    one, so this renders correctly no matter which cards.py is loaded."""
    if hasattr(card, 'location'):
        return card.location
    if hasattr(card, 'sector'):
        return card.sector
    return ''


def get_card_by_id(card_id: str):
    """Look up a card by its full ID string (e.g. 'S1-SR-000'), regardless
    of which set it's from. Delegates to set_registry, which knows how to
    find Set 1 cards (via cards.py, unchanged) as well as any later set
    fetched through the card-set manifest -- see set_registry.py."""
    import set_registry
    return set_registry.get_card_by_id(card_id)


def render_card_frame(card, out_path, art_path=None):
    """Thin wrapper kept for the existing batch-render script and anything
    else that wants a card written straight to a file -- behavior here is
    completely unchanged. The actual drawing now lives in render_card_image()
    so the wallet app's Binder tab can get the finished card back as an
    in-memory image instead, without needing to round-trip through disk."""
    img = render_card_image(card, art_path=art_path)
    img.save(out_path)
    print(f'Rendered: {out_path}')


def render_card_image(card, art_path=None):
    """Builds and returns the fully-composited card (border, telemetry bar,
    sensor log text, and the holo_rare/secret_rare effects) as a PIL Image,
    without writing anything to disk. This is the same logic
    render_card_frame() has always used -- pulled out on its own so both the
    batch-render script and the wallet's Binder tab call one shared,
    already-tested implementation instead of two copies drifting apart."""
    img = Image.new('RGB', (WIDTH, HEIGHT), color=COLOR_BG)
    draw = ImageDraw.Draw(img)
    accent = TIER_ACCENT.get(card.tier, COLOR_GREEN)
    seed = _card_seed(card.card_id)

    draw.rectangle([(15, 15), (WIDTH - 16, HEIGHT - 16)], outline=COLOR_MUTED, width=2)
    draw.rectangle([(25, 25), (WIDTH - 26, HEIGHT - 26)], outline=accent, width=3)

    b = 30
    draw.line([(20, 20), (20 + b, 20)], fill=accent, width=4)
    draw.line([(20, 20), (20, 20 + b)], fill=accent, width=4)
    draw.line([(WIDTH - 20, 20), (WIDTH - 20 - b, 20)], fill=accent, width=4)
    draw.line([(WIDTH - 20, 20), (WIDTH - 20, 20 + b)], fill=accent, width=4)
    draw.line([(20, HEIGHT - 20), (20 + b, HEIGHT - 20)], fill=accent, width=4)
    draw.line([(20, HEIGHT - 20), (20, HEIGHT - 20 - b)], fill=accent, width=4)
    draw.line([(WIDTH - 20, HEIGHT - 20), (WIDTH - 20 - b, HEIGHT - 20)], fill=accent, width=4)
    draw.line([(WIDTH - 20, HEIGHT - 20), (WIDTH - 20, HEIGHT - 20 + b)], fill=accent, width=4)

    draw.rectangle([(40, 45), (WIDTH - 40, 110)], fill=COLOR_PANEL, outline=COLOR_MUTED, width=1)
    draw.text((55, 60), card.name.upper(), fill=COLOR_WHITE, font=get_font(28))

    draw.rectangle([(WIDTH - 180, 58), (WIDTH - 55, 95)], fill=(30, 20, 5), outline=COLOR_AMBER, width=2)
    draw.text((WIDTH - 168, 68), '1ST EDITION', fill=COLOR_AMBER, font=get_font(14))

    # --- center viewport: real artwork if given, otherwise the schematic
    # placeholder. Fill the panel first with no outline, so a pasted image
    # can't clip the frame, then redraw the accent border on top -- that way
    # the border is always crisp regardless of what's underneath.
    vx0, vy0, vx1, vy1 = VIEWPORT_BOX
    draw.rectangle([(vx0, vy0), (vx1, vy1)], fill=(5, 8, 6))
    if art_path and os.path.exists(art_path):
        art = Image.open(art_path).convert('RGBA')
        art = art.resize(VIEWPORT_SIZE, Image.Resampling.LANCZOS)
        img.paste(art, (vx0, vy0), art)  # art's own alpha channel as the paste mask
    else:
        draw.text((275, 390), '[ ARTWORK BAY ]', fill=COLOR_MUTED, font=get_font(18))
        draw.text((245, 420), '550 x 550 RESOLUTION', fill=(25, 55, 35), font=get_font(16))

    # holo_rare: lay the foil sheen over whatever's in the viewport (real art
    # or the placeholder) before the border goes back on top -- foil is a
    # property of the card stock, not the artwork underneath it
    if card.tier == 'holo_rare':
        foil = _holo_foil_overlay(VIEWPORT_SIZE, seed)
        img.paste(foil, (vx0, vy0), foil)

    # secret_rare gets its own glitch treatment (scan-tears + chromatic-
    # aberration ghost border) in place of the plain single-line border;
    # every other tier just gets the plain accent-colored viewport border
    if card.tier == 'secret_rare':
        _apply_secret_glitch(img, draw, (vx0, vy0, vx1, vy1), seed)
    else:
        draw.rectangle([(vx0, vy0), (vx1, vy1)], outline=accent, width=2)

    # --- telemetry bar: ID and TIER only now -- sector moved down into the
    # Sensor Log box below, where a long location string has room to breathe
    # instead of fighting ID/TIER for space on one line.
    draw.rectangle([(40, 700), (WIDTH - 40, 745)], fill=COLOR_PANEL, outline=COLOR_MUTED, width=1)
    draw.text((55, 715), f'ID: {card.card_id}', fill=accent, font=get_font(16))
    draw.text((380, 715), f'TIER: {card.tier.upper()}', fill=COLOR_TEXT, font=get_font(16))

    draw.rectangle([(40, 760), (WIDTH - 40, 990)], fill=COLOR_PANEL, outline=COLOR_MUTED, width=1)
    draw.text((55, 775), '// SENSOR LOG ENTRY:', fill=accent, font=get_font(16))
    draw.text((55, 798), f'[// SECTOR: {_card_sector_text(card).upper()}]', fill=COLOR_TEXT, font=get_font(13))
    wrapped = textwrap.fill(card.description, width=44)
    draw.text((55, 822), wrapped, fill=COLOR_TEXT, font=get_font(16), spacing=8)

    # secret_rare's full-card shimmer goes on last, over the whole printed
    # card (not just the viewport), so border/telemetry/sensor-log all catch
    # the rainbow sweep too -- then scanlines cut through it same as always
    if card.tier == 'secret_rare':
        shimmer = _secret_rare_full_shimmer((WIDTH, HEIGHT), seed)
        img.paste(shimmer, (0, 0), shimmer)

    for y in range(0, HEIGHT, 4):
        draw.line([(0, y), (WIDTH, y)], fill=(0, 0, 0), width=1)

    return img


if __name__ == '__main__':
    os.makedirs('output', exist_ok=True)
    sr = get_card_by_id('S1-SR-000')
    if sr:
        render_card_frame(sr, 'output/test_S1-SR-000.png')
    h = get_card_by_id('S1-H-000')
    if h:
        render_card_frame(h, 'output/test_S1-H-000.png')
    u = get_card_by_id('S1-U-000')
    if u:
        render_card_frame(u, 'output/test_S1-U-000.png')
