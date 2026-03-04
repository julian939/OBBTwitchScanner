"""Global stats image generator — matches leaderboard design."""
from PIL import Image, ImageDraw, ImageFont
import io
import os

# ── Colors (shared with leaderboard) ──
BG_COLOR = (24, 20, 33)
HEADER_BG = (55, 48, 75)
ROW_BG_1 = (36, 31, 50)
ROW_BG_2 = (32, 27, 45)
BORDER_COLOR = (65, 58, 85)
TEXT_WHITE = (240, 240, 245)
TEXT_SECONDARY = (170, 165, 185)
TEXT_HEADER = (200, 195, 215)

# ── Font loading (bundled first, then system fallback) ──
_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")
_BOLD_PATH = os.path.join(_ASSETS_DIR, "DejaVuSans-Bold.ttf")
_REG_PATH = os.path.join(_ASSETS_DIR, "DejaVuSans.ttf")

if not os.path.exists(_BOLD_PATH):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p):
            _BOLD_PATH = p
            break

if not os.path.exists(_REG_PATH):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/TTF/DejaVuSans.ttf"]:
        if os.path.exists(p):
            _REG_PATH = p
            break


def _load(size, bold=False):
    try:
        return ImageFont.truetype(_BOLD_PATH if bold else _REG_PATH, size)
    except OSError:
        return ImageFont.load_default()


# ── Fonts ──
FONT_TITLE = _load(18, bold=True)
FONT_SUBTITLE = _load(10)
FONT_LABEL = _load(12)
FONT_VALUE = _load(12, bold=True)
FONT_SMALL = _load(10)
FONT_TIP_LABEL = _load(10, bold=True)

# ── Layout ──
W = 400
PAD = 20
TX = PAD
TW = W - 2 * PAD
ROW_H = 30
TITLE_AREA = 50
MARGIN = 12


def _ts(draw, text, font):
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0], b[3] - b[1]


def _draw_row_divider(draw, y):
    draw.line((TX + 10, y, TX + TW - 10, y), fill=BORDER_COLOR, width=1)


def _draw_table_border(draw, top, bottom):
    draw.rounded_rectangle(
        (TX, top, TX + TW, bottom),
        radius=8, fill=None, outline=BORDER_COLOR, width=1
    )


def render_stats(data, tip=""):
    """Render global stats card as PIL Image.

    data keys: streamers, live_now, total_streams, total_hours, total_points
    """
    # Height
    h = PAD + TITLE_AREA
    h += 5 * ROW_H
    if tip:
        h += 12 + 16 + 12
    else:
        h += PAD

    img = Image.new("RGB", (W, h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # ── Title ──
    y = PAD
    draw.text((PAD, y), "OBB Global Stream Stats", font=FONT_TITLE, fill=TEXT_WHITE)
    draw.text((PAD, y + 28), "Overview of all tracked streaming activity.", font=FONT_SUBTITLE, fill=TEXT_SECONDARY)
    y += TITLE_AREA

    # ── Stats rows ──
    rows = [
        ("Streamers", str(data.get("streamers", 0))),
        ("Live Now", str(data.get("live_now", 0))),
        ("Total Streams", str(data.get("total_streams", 0))),
        ("Total Hours", f"{data.get('total_hours', 0):.1f}h"),
        ("Total Points", f"{data.get('total_points', 0):,}"),
    ]
    num_rows = len(rows)
    table_top = y

    for i, (label, value) in enumerate(rows):
        bg = ROW_BG_1 if i % 2 == 0 else ROW_BG_2

        if i == 0 and num_rows > 1:
            draw.rounded_rectangle(
                (TX + 1, y, TX + TW - 1, y + ROW_H), radius=8, fill=bg
            )
            draw.rectangle((TX + 1, y + ROW_H - 8, TX + TW - 1, y + ROW_H), fill=bg)
        elif i == num_rows - 1:
            draw.rounded_rectangle(
                (TX + 1, y, TX + TW - 1, y + ROW_H), radius=8, fill=bg
            )
            draw.rectangle((TX + 1, y, TX + TW - 1, y + 10), fill=bg)
        else:
            draw.rectangle((TX + 1, y, TX + TW - 1, y + ROW_H), fill=bg)

        _, lh = _ts(draw, label, FONT_LABEL)
        draw.text((TX + MARGIN, y + (ROW_H - lh) // 2), label, font=FONT_LABEL, fill=TEXT_SECONDARY)

        vw, vh = _ts(draw, value, FONT_VALUE)
        draw.text((TX + TW - MARGIN - vw, y + (ROW_H - vh) // 2), value, font=FONT_VALUE, fill=TEXT_WHITE)

        if i < num_rows - 1:
            _draw_row_divider(draw, y + ROW_H)
        y += ROW_H

    _draw_table_border(draw, table_top, y)

    # ── Tip ──
    if tip:
        y += 12
        full_tip = f"Tip: {tip}"
        full_w, _ = _ts(draw, full_tip, FONT_SMALL)
        tip_x = (W - full_w) // 2
        draw.text((tip_x, y), "Tip:", font=FONT_TIP_LABEL, fill=TEXT_HEADER)
        tw_tip, _ = _ts(draw, "Tip: ", FONT_TIP_LABEL)
        draw.text((tip_x + tw_tip, y), tip, font=FONT_SMALL, fill=TEXT_SECONDARY)

    return img


def to_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()