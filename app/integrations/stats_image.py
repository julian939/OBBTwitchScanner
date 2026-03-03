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
LIVE_RED = (233, 25, 22)

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
FONT_HEADER = _load(11, bold=True)
FONT_LABEL = _load(12)
FONT_VALUE = _load(12, bold=True)
FONT_NAME = _load(13, bold=True)
FONT_SMALL = _load(10)
FONT_TIP_LABEL = _load(10, bold=True)

# ── Layout ──
W = 400
PAD = 20
TX = PAD
TW = W - 2 * PAD
HDR_H = 30
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


def _draw_table_header(draw, y, labels):
    draw.rounded_rectangle(
        (TX, y, TX + TW, y + HDR_H + 8), radius=8, fill=HEADER_BG
    )
    draw.rectangle(
        (TX, y + HDR_H - 8, TX + TW, y + HDR_H), fill=HEADER_BG
    )
    for text, x, w, align in labels:
        tw, th = _ts(draw, text, FONT_HEADER)
        ty = y + (HDR_H - th) // 2
        if align == "right":
            tx = TX + x + w - tw - MARGIN
        else:
            tx = TX + x + MARGIN
        draw.text((tx, ty), text, font=FONT_HEADER, fill=TEXT_HEADER)
    return y + HDR_H


def _draw_row_bg(draw, y, i, num_rows):
    bg = ROW_BG_1 if i % 2 == 0 else ROW_BG_2
    if i == num_rows - 1:
        draw.rounded_rectangle(
            (TX + 1, y, TX + TW - 1, y + ROW_H), radius=8, fill=bg
        )
        draw.rectangle((TX + 1, y, TX + TW - 1, y + 10), fill=bg)
    else:
        draw.rectangle((TX + 1, y, TX + TW - 1, y + ROW_H), fill=bg)


def render_stats(data, tip=""):
    """Render global stats card as PIL Image.

    data keys: streamers, live_now, total_streams, total_hours, total_points,
               live_list: list of (display_name, login)
    """
    live_list = data.get("live_list", [])
    num_live = len(live_list)

    # Height
    h = PAD + TITLE_AREA
    h += 5 * ROW_H
    if num_live > 0:
        h += 16 + HDR_H + num_live * ROW_H
    if tip:
        h += 12 + 16
        h += 12  # balanced bottom padding after tip
    else:
        h += PAD

    img = Image.new("RGB", (W, h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # ── Title ──
    y = PAD
    draw.text((PAD, y), "OBB Global Stream Stats", font=FONT_TITLE, fill=TEXT_WHITE)
    draw.text((PAD, y + 28), "Overview of all tracked streaming activity.", font=FONT_SUBTITLE, fill=TEXT_SECONDARY)
    y += TITLE_AREA

    # ── Stats rows (no header) ──
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

    # ── Currently Live table ──
    if num_live > 0:
        y += 16
        live_top = y
        y = _draw_table_header(draw, y, [
            ("Currently Live", 0, TW - 160, "left"),
            ("Channel", TW - 160, 160, "right"),
        ])
        draw.line((TX, y, TX + TW, y), fill=BORDER_COLOR, width=1)

        for i, (display, login) in enumerate(live_list):
            _draw_row_bg(draw, y, i, num_live)

            # Red dot
            dot_r = 4
            draw.ellipse(
                (TX + MARGIN + dot_r - dot_r, y + ROW_H // 2 - dot_r,
                 TX + MARGIN + dot_r + dot_r, y + ROW_H // 2 + dot_r),
                fill=LIVE_RED
            )

            # Name (truncated)
            name_x = TX + MARGIN + 18
            max_name_w = TW // 2 - MARGIN - 18
            dname = display
            nw, nh = _ts(draw, dname, FONT_NAME)
            if nw > max_name_w:
                while len(dname) > 3:
                    dname = dname[:-1]
                    nw, _ = _ts(draw, dname + "..", FONT_NAME)
                    if nw <= max_name_w:
                        dname += ".."
                        break
                _, nh = _ts(draw, dname, FONT_NAME)
            draw.text((name_x, y + (ROW_H - nh) // 2), dname, font=FONT_NAME, fill=TEXT_WHITE)

            # URL (truncated)
            url = f"twitch.tv/{login}"
            max_url_w = TW // 2 - MARGIN
            uw, uh = _ts(draw, url, FONT_SMALL)
            if uw > max_url_w:
                while len(url) > 10:
                    url = url[:-1]
                    uw, _ = _ts(draw, url + "..", FONT_SMALL)
                    if uw <= max_url_w:
                        url += ".."
                        break
                _, uh = _ts(draw, url, FONT_SMALL)
            draw.text((TX + TW - MARGIN - uw, y + (ROW_H - uh) // 2), url, font=FONT_SMALL, fill=TEXT_SECONDARY)

            if i < num_live - 1:
                _draw_row_divider(draw, y + ROW_H)
            y += ROW_H

        _draw_table_border(draw, live_top, y)

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