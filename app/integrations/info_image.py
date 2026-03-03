"""Info card image generator — matches leaderboard design."""
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
FONT_TITLE = _load(22, bold=True)
FONT_SUBTITLE = _load(12)
FONT_HEADER = _load(13, bold=True)
FONT_LABEL = _load(14)
FONT_VALUE = _load(14, bold=True)
FONT_SMALL = _load(12)
FONT_TIP_LABEL = _load(12, bold=True)

# ── Layout ──
W = 560
PAD = 28
TX = PAD
TW = W - 2 * PAD
HDR_H = 38
TITLE_AREA = 58
MARGIN = 14
PILL_H = 26
PILL_PAD = 12
PILL_GAP = 8
RULE_ROW = 44
STEP_ROW = 36


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


def render_info(categories, tip="", points_per_min=1, daily_bonus=500,
                streak_multiplier=10, event_multiplier=2):
    """Render info card as PIL Image.

    categories: list of tracked category names
    """
    rules = [
        ("Stream Time", f"{points_per_min} point per minute streamed"),
        ("Daily Bonus", f"{daily_bonus:,} points for first stream of the day"),
        ("Streak Bonus", f"streak days x {streak_multiplier} pts (3+ day streak)"),
        ("Event Bonus", f"points x {event_multiplier} during Discord events"),
    ]
    steps = [
        "Use /register with your Twitch username",
        "An admin will review your request",
        "Once approved, streams are tracked automatically",
    ]

    # ── Measure category pills ──
    dummy = Image.new("RGB", (1, 1))
    dd = ImageDraw.Draw(dummy)

    pills = []
    for cat in categories:
        cw, _ = _ts(dd, cat, FONT_SMALL)
        pills.append((cat, cw + PILL_PAD * 2))

    # Lay out into rows
    pill_rows = []
    row = []
    row_w = 0
    inner_w = TW - 24
    for cat, pw in pills:
        if row_w + pw + PILL_GAP > inner_w and row:
            pill_rows.append(row)
            row = []
            row_w = 0
        row.append((cat, pw))
        row_w += pw + PILL_GAP
    if row:
        pill_rows.append(row)

    cat_box_h = len(pill_rows) * (PILL_H + 6) + 14
    num_rules = len(rules)
    num_steps = len(steps)

    # ── Height ──
    h = PAD + TITLE_AREA
    h += 20 + cat_box_h
    h += 20 + HDR_H + num_rules * RULE_ROW
    h += 20 + HDR_H + num_steps * STEP_ROW
    if tip:
        h += 12 + 18
        h += 14  # balanced bottom padding after tip
    else:
        h += PAD

    img = Image.new("RGB", (W, h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # ── Title ──
    y = PAD
    draw.text((PAD, y), "OBB Stream Tracker", font=FONT_TITLE, fill=TEXT_WHITE)
    draw.text((PAD, y + 28), "Everything you need to know about stream tracking.", font=FONT_SUBTITLE, fill=TEXT_SECONDARY)
    y += TITLE_AREA

    # ── Tracked Categories ──
    draw.text((PAD, y), "Tracked Categories", font=FONT_HEADER, fill=TEXT_HEADER)
    tw_hdr, _ = _ts(draw, "Tracked Categories", FONT_HEADER)
    draw.text((PAD + tw_hdr + 8, y + 2), "— only these count toward points", font=FONT_SMALL, fill=TEXT_SECONDARY)
    y += 20

    draw.rounded_rectangle(
        (TX, y, TX + TW, y + cat_box_h),
        radius=8, fill=ROW_BG_1, outline=BORDER_COLOR
    )

    py = y + 7
    for row in pill_rows:
        px = TX + 12
        for cat, pw in row:
            draw.rounded_rectangle(
                (px, py, px + pw, py + PILL_H),
                radius=PILL_H // 2, fill=HEADER_BG, outline=BORDER_COLOR
            )
            draw.text(
                (px + pw / 2, py + PILL_H / 2),
                cat, font=FONT_SMALL, fill=TEXT_WHITE, anchor="mm"
            )
            px += pw + PILL_GAP
        py += PILL_H + 6

    y += cat_box_h

    # ── Points System ──
    y += 20
    pts_top = y
    y = _draw_table_header(draw, y, [("Points System", 0, TW, "left")])
    draw.line((TX, y, TX + TW, y), fill=BORDER_COLOR, width=1)

    for i, (label, desc) in enumerate(rules):
        bg = ROW_BG_1 if i % 2 == 0 else ROW_BG_2
        if i == num_rules - 1:
            draw.rounded_rectangle(
                (TX + 1, y, TX + TW - 1, y + RULE_ROW), radius=8, fill=bg
            )
            draw.rectangle((TX + 1, y, TX + TW - 1, y + 10), fill=bg)
        else:
            draw.rectangle((TX + 1, y, TX + TW - 1, y + RULE_ROW), fill=bg)

        _, lh = _ts(draw, label, FONT_VALUE)
        _, dh = _ts(draw, desc, FONT_SMALL)
        block_h = lh + 3 + dh
        top_y = y + (RULE_ROW - block_h) // 2

        draw.text((TX + MARGIN, top_y), label, font=FONT_VALUE, fill=TEXT_WHITE)
        draw.text((TX + MARGIN, top_y + lh + 3), desc, font=FONT_SMALL, fill=TEXT_SECONDARY)

        if i < num_rules - 1:
            _draw_row_divider(draw, y + RULE_ROW)
        y += RULE_ROW

    _draw_table_border(draw, pts_top, y)

    # ── How to Join ──
    y += 20
    join_top = y
    y = _draw_table_header(draw, y, [("How to Join", 0, TW, "left")])
    draw.line((TX, y, TX + TW, y), fill=BORDER_COLOR, width=1)

    for i, step in enumerate(steps):
        bg = ROW_BG_1 if i % 2 == 0 else ROW_BG_2
        if i == num_steps - 1:
            draw.rounded_rectangle(
                (TX + 1, y, TX + TW - 1, y + STEP_ROW), radius=8, fill=bg
            )
            draw.rectangle((TX + 1, y, TX + TW - 1, y + 10), fill=bg)
        else:
            draw.rectangle((TX + 1, y, TX + TW - 1, y + STEP_ROW), fill=bg)

        # Step number circle
        cx = TX + MARGIN + 10
        cy = y + STEP_ROW // 2
        draw.ellipse((cx - 10, cy - 10, cx + 10, cy + 10), fill=HEADER_BG, outline=BORDER_COLOR)
        draw.text((cx, cy), str(i + 1), font=FONT_SMALL, fill=TEXT_WHITE, anchor="mm")

        _, sh = _ts(draw, step, FONT_LABEL)
        draw.text(
            (TX + MARGIN + 30, y + (STEP_ROW - sh) // 2),
            step, font=FONT_LABEL, fill=TEXT_SECONDARY
        )

        if i < num_steps - 1:
            _draw_row_divider(draw, y + STEP_ROW)
        y += STEP_ROW

    _draw_table_border(draw, join_top, y)

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