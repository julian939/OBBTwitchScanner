"""Leaderboard image generator - OBB website style table layout."""
from PIL import Image, ImageDraw, ImageFont
import io
import os

# ── Colors ──
BG_COLOR = (24, 20, 33)
HEADER_BG = (55, 48, 75)
ROW_BG_1 = (36, 31, 50)
ROW_BG_2 = (32, 27, 45)
BORDER_COLOR = (65, 58, 85)
GOLD = (230, 190, 70)
SILVER = (192, 200, 215)
BRONZE = (205, 140, 75)
TEXT_WHITE = (240, 240, 245)
TEXT_SECONDARY = (170, 165, 185)
TEXT_HEADER = (200, 195, 215)
LIVE_RED = (233, 25, 22)
STREAK_ORANGE = (255, 160, 60)

RANK_COLORS = {1: GOLD, 2: SILVER, 3: BRONZE}


# ── Font loading (bundled first, then system fallback) ──
_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")
_BOLD_PATH = os.path.join(_ASSETS_DIR, "DejaVuSans-Bold.ttf")
_REG_PATH = os.path.join(_ASSETS_DIR, "DejaVuSans.ttf")

# Fallback to system fonts if bundled not found
if not os.path.exists(_BOLD_PATH):
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(p):
            _BOLD_PATH = p
            break

if not os.path.exists(_REG_PATH):
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]:
        if os.path.exists(p):
            _REG_PATH = p
            break


def _load(size, bold=False):
    path = _BOLD_PATH if bold else _REG_PATH
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


# ── Fonts ──
FONT_HEADER = _load(13, bold=True)
FONT_RANK_SINGLE = _load(18, bold=True)
FONT_RANK_DOUBLE = _load(15, bold=True)
FONT_RANK_SUP_SINGLE = _load(10)
FONT_RANK_SUP_DOUBLE = _load(9)
FONT_NAME = _load(15, bold=True)
FONT_DATA = _load(14)
FONT_LIVE = _load(9, bold=True)
FONT_STREAK = _load(11, bold=True)
FONT_TITLE = _load(22, bold=True)
FONT_SUBTITLE = _load(12)

# ── Layout ──
IMG_WIDTH = 480
PADDING = 24
TABLE_X = PADDING
TABLE_WIDTH = IMG_WIDTH - 2 * PADDING
HEADER_HEIGHT = 38
TITLE_AREA = 58

ROW_H_NORMAL = 44
ROW_H_STREAK = 56

COL_RANK_X = 0
COL_RANK_W = 58
COL_NAME_X = COL_RANK_W
COL_NAME_W = TABLE_WIDTH - COL_RANK_W - 100
COL_POINTS_X = COL_NAME_X + COL_NAME_W
COL_POINTS_W = 100


def rank_suffix(n):
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _row_height(entry):
    return ROW_H_STREAK if entry.get("streak", 0) >= 2 else ROW_H_NORMAL


def _text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _center_y(area_y, area_h, text_h):
    return area_y + (area_h - text_h) // 2


def render_leaderboard(entries, page=1, total_pages=1):
    """Render a leaderboard page as a PIL Image.

    entries: list of dicts with keys:
        rank, display_name, pts, streak, tracked_live
    """
    num = len(entries)
    total_rows_h = sum(_row_height(e) for e in entries)
    img_height = PADDING + TITLE_AREA + HEADER_HEIGHT + total_rows_h + PADDING

    img = Image.new("RGB", (IMG_WIDTH, img_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # ── Title ──
    y = PADDING
    draw.text((PADDING, y), "OBB Streamer Leaderboard", font=FONT_TITLE, fill=TEXT_WHITE)
    draw.text((PADDING, y + 28), "Earn points by streaming tracked categories.", font=FONT_SUBTITLE, fill=TEXT_SECONDARY)
    y += TITLE_AREA

    table_top = y
    table_bottom = y + HEADER_HEIGHT + total_rows_h

    # ── Table border ──
    draw.rounded_rectangle(
        (TABLE_X, table_top, TABLE_X + TABLE_WIDTH, table_bottom),
        radius=8, fill=None, outline=BORDER_COLOR, width=1
    )

    # ── Header background ──
    draw.rounded_rectangle(
        (TABLE_X, table_top, TABLE_X + TABLE_WIDTH, table_top + HEADER_HEIGHT + 8),
        radius=8, fill=HEADER_BG
    )
    draw.rectangle(
        (TABLE_X, table_top + HEADER_HEIGHT - 8, TABLE_X + TABLE_WIDTH, table_top + HEADER_HEIGHT),
        fill=HEADER_BG
    )

    # ── Header labels ──
    for label, cx, cw, align in [
        ("Rank", COL_RANK_X, COL_RANK_W, "left"),
        ("Streamer", COL_NAME_X, COL_NAME_W, "left"),
        ("Points", COL_POINTS_X, COL_POINTS_W, "right"),
    ]:
        tw, th = _text_size(draw, label, FONT_HEADER)
        hy = _center_y(table_top, HEADER_HEIGHT, th)
        if align == "right":
            hx = TABLE_X + cx + cw - tw - 14
        else:
            hx = TABLE_X + cx + 14
        draw.text((hx, hy), label, font=FONT_HEADER, fill=TEXT_HEADER)

    y = table_top + HEADER_HEIGHT
    draw.line((TABLE_X, y, TABLE_X + TABLE_WIDTH, y), fill=BORDER_COLOR, width=1)

    # ── Rows ──
    for i, entry in enumerate(entries):
        rh = _row_height(entry)
        has_streak = entry.get("streak", 0) >= 2
        row_bg = ROW_BG_1 if i % 2 == 0 else ROW_BG_2

        # Background
        if i == num - 1:
            draw.rounded_rectangle(
                (TABLE_X + 1, y, TABLE_X + TABLE_WIDTH - 1, y + rh),
                radius=8, fill=row_bg
            )
            draw.rectangle(
                (TABLE_X + 1, y, TABLE_X + TABLE_WIDTH - 1, y + 10),
                fill=row_bg
            )
        else:
            draw.rectangle(
                (TABLE_X + 1, y, TABLE_X + TABLE_WIDTH - 1, y + rh),
                fill=row_bg
            )

        if i < num - 1:
            draw.line(
                (TABLE_X + 10, y + rh, TABLE_X + TABLE_WIDTH - 10, y + rh),
                fill=BORDER_COLOR, width=1
            )

        rank = entry["rank"]
        rank_color = RANK_COLORS.get(rank, TEXT_SECONDARY)

        # ── Rank ──
        rank_str = str(rank)
        sup = rank_suffix(rank)
        is_single = rank < 10
        font_rank = FONT_RANK_SINGLE if is_single else FONT_RANK_DOUBLE
        font_sup = FONT_RANK_SUP_SINGLE if is_single else FONT_RANK_SUP_DOUBLE
        sup_offset = 5 if is_single else 4

        rw, rh_t = _text_size(draw, rank_str, font_rank)
        sw, _ = _text_size(draw, sup, font_sup)
        total_rw = rw + sw + 1
        rx = TABLE_X + COL_RANK_X + (COL_RANK_W - total_rw) // 2
        ry = _center_y(y, rh, rh_t)
        draw.text((rx, ry), rank_str, font=font_rank, fill=rank_color)
        draw.text((rx + rw + 1, ry + sup_offset), sup, font=font_sup, fill=rank_color)

        # ── Name area ──
        name_x = TABLE_X + COL_NAME_X + 14
        _, name_h = _text_size(draw, "Ag", FONT_NAME)

        if has_streak:
            _, streak_h = _text_size(draw, "5d", FONT_STREAK)
            block_h = name_h + 3 + streak_h
            name_y = _center_y(y, rh, block_h)
            streak_y = name_y + name_h + 3
        else:
            name_y = _center_y(y, rh, name_h)

        # Truncate
        max_name_w = COL_NAME_W - 28
        if entry.get("tracked_live"):
            max_name_w -= 50
        display_name = entry["display_name"]
        nw, _ = _text_size(draw, display_name, FONT_NAME)
        if nw > max_name_w:
            while len(display_name) > 3:
                display_name = display_name[:-1]
                nw, _ = _text_size(draw, display_name + "..", FONT_NAME)
                if nw <= max_name_w:
                    display_name += ".."
                    break

        draw.text((name_x, name_y), display_name, font=FONT_NAME, fill=TEXT_WHITE)

        # ── LIVE badge ──
        if entry.get("tracked_live"):
            nw_actual, _ = _text_size(draw, display_name, FONT_NAME)
            badge_w, badge_h = 38, 16
            badge_x = name_x + nw_actual + 8
            badge_y = name_y + (name_h - badge_h) // 2

            draw.rounded_rectangle(
                (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
                radius=badge_h // 2, fill=LIVE_RED
            )

            mid_x = badge_x + badge_w / 2
            mid_y = badge_y + badge_h / 2
            draw.text(
                (mid_x, mid_y), "LIVE",
                font=FONT_LIVE, fill=(255, 255, 255), anchor="mm"
            )

        # ── Streak ──
        if has_streak:
            streak_text = f"{entry['streak']}d streak"
            fx, fy = name_x, streak_y + 1
            draw.polygon(
                [(fx + 3, fy), (fx + 6, fy + 4), (fx + 5, fy + 8),
                 (fx + 1, fy + 8), (fx, fy + 4)],
                fill=STREAK_ORANGE
            )
            draw.polygon(
                [(fx + 3, fy + 2), (fx + 5, fy + 5), (fx + 4, fy + 8),
                 (fx + 2, fy + 8), (fx + 1, fy + 5)],
                fill=(255, 210, 100)
            )
            draw.text((name_x + 10, streak_y), streak_text, font=FONT_STREAK, fill=STREAK_ORANGE)

        # ── Points ──
        pts_text = f"{entry['pts']:,}"
        pw, ph = _text_size(draw, pts_text, FONT_DATA)
        px = TABLE_X + COL_POINTS_X + COL_POINTS_W - pw - 14
        py = _center_y(y, rh, ph)
        draw.text((px, py), pts_text, font=FONT_DATA, fill=TEXT_WHITE)

        y += rh

    return img


def to_discord_file(img, filename="leaderboard.png"):
    """Convert PIL Image to discord.File."""
    import discord
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename=filename)