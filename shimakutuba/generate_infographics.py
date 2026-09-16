#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
しまくとぅば動画向け図解PNGバッチ生成スクリプト。

1つの共通レイアウトシステム（layout_* 関数群）に対して、
IMAGE_DEFS の定義データを1件ずつ渡して連続生成する。
画像ごとに描画コードを書き分けない。

使い方:
    python3 generate_infographics.py [出力先フォルダ]

出力先フォルダを省略した場合は DEFAULT_OUTPUT_DIR を使用する。
フォントは複数OSの候補パスを自動探索する。見つからない場合は
FONT_REGULAR / FONT_BOLD 環境変数でパスを明示できる。
"""

import os
import sys
from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------------
# 基本設定
# ----------------------------------------------------------------------

CANVAS_W, CANVAS_H = 1920, 1080
SAFE_MARGIN = int(CANVAS_W * 0.05)          # 画面端5%セーフマージン
CAPTION_SAFE_BOTTOM = int(CANVAS_H * 0.14)  # 字幕が乗るため最下部には文字を置かない

DEFAULT_OUTPUT_DIR = "/Volumes/286 SSD/2026/しまくとぅば_20260918/06_テロップ・図解/"

BG_COLOR = (247, 248, 250)
INK_COLOR = (34, 40, 48)
SUB_COLOR = (78, 88, 98)
ACCENT_COLOR = (0, 98, 145)         # 海を思わせる青（控えめに使用）
ACCENT_LIGHT = (214, 231, 240)      # アイコン背景用の淡い青
LINE_COLOR = (190, 198, 206)

FONT_REGULAR_CANDIDATES = [
    os.environ.get("FONT_REGULAR", ""),
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Osaka.ttf",
    "C:/Windows/Fonts/meiryo.ttc",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
]
FONT_BOLD_CANDIDATES = [
    os.environ.get("FONT_BOLD", ""),
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
]


def _find_font(candidates):
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


REGULAR_FONT_PATH = _find_font(FONT_REGULAR_CANDIDATES)
BOLD_FONT_PATH = _find_font(FONT_BOLD_CANDIDATES)  # 見つからなければ regular + stroke で太字を再現

if not REGULAR_FONT_PATH:
    sys.exit(
        "日本語フォントが見つかりません。FONT_REGULAR 環境変数でttf/ttcパスを指定してください。"
    )

_FONT_CACHE = {}


def get_font(size, bold=False):
    path = BOLD_FONT_PATH if (bold and BOLD_FONT_PATH) else REGULAR_FONT_PATH
    key = (path, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(path, size)
    return _FONT_CACHE[key]


def _bold_stroke(bold):
    # stroke_width での太字再現は漢字の字形を潰すため使わない。
    # 太字が必要な場合は BOLD_FONT_PATH（実ボールドフォント）のみで対応する。
    return 0


# ----------------------------------------------------------------------
# 座標外チェック（機械的検品の一部）
# ----------------------------------------------------------------------

_OUT_OF_BOUNDS = []


def _check_bounds(image_id, label, bbox):
    x0, y0, x1, y1 = bbox
    if x0 < 0 or y0 < 0 or x1 > CANVAS_W or y1 > CANVAS_H:
        _OUT_OF_BOUNDS.append((image_id, label, bbox))


def draw_text_centered(draw, image_id, label, cx, cy, text, size, bold=False, color=INK_COLOR):
    font = get_font(size, bold=bold)
    stroke = _bold_stroke(bold)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = cx - w / 2 - bbox[0], cy - h / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=color, stroke_width=stroke, stroke_fill=color)
    _check_bounds(image_id, label, (x, y, x + w, y + h))


# ----------------------------------------------------------------------
# 共通の下地・アイコン
# ----------------------------------------------------------------------

def new_canvas():
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_COLOR)
    draw = ImageDraw.Draw(img)
    # 左上と右下に控えめな海色のアクセントライン（装飾は最小限）
    draw.line([(SAFE_MARGIN, SAFE_MARGIN - 20), (SAFE_MARGIN + 160, SAFE_MARGIN - 20)],
              fill=ACCENT_COLOR, width=6)
    return img, draw


def draw_title(draw, image_id, title):
    draw_text_centered(draw, image_id, "title", CANVAS_W // 2, SAFE_MARGIN + 40,
                        title, 58, bold=True, color=INK_COLOR)


def draw_caption(draw, image_id, caption):
    if not caption:
        return
    y = CANVAS_H - CAPTION_SAFE_BOTTOM
    draw_text_centered(draw, image_id, "caption", CANVAS_W // 2, y, caption, 40, color=SUB_COLOR)


def icon_badge(draw, cx, cy, radius, kind):
    """アイコンを丸バッジの中に統一スタイルで描く。"""
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                 fill=ACCENT_LIGHT, outline=ACCENT_COLOR, width=3)
    s = radius * 0.5
    w = 4
    if kind == "book":
        draw.rectangle([cx - s, cy - s, cx + s, cy + s], outline=ACCENT_COLOR, width=w)
        draw.line([(cx, cy - s), (cx, cy + s)], fill=ACCENT_COLOR, width=w)
    elif kind == "music":
        draw.ellipse([cx - s * 0.6, cy + s * 0.3, cx + s * 0.1, cy + s * 0.9],
                     outline=ACCENT_COLOR, width=w)
        draw.line([(cx + s * 0.05, cy + s * 0.6), (cx + s * 0.05, cy - s)], fill=ACCENT_COLOR, width=w)
        draw.line([(cx + s * 0.05, cy - s), (cx + s * 0.6, cy - s * 0.8)], fill=ACCENT_COLOR, width=w)
    elif kind == "theater":
        draw.arc([cx - s, cy - s * 0.6, cx, cy + s * 0.6], start=200, end=340, fill=ACCENT_COLOR, width=w)
        draw.arc([cx, cy - s * 0.6, cx + s, cy + s * 0.6], start=200, end=340, fill=ACCENT_COLOR, width=w)
    elif kind == "video":
        draw.rounded_rectangle([cx - s, cy - s * 0.75, cx + s, cy + s * 0.75], radius=8,
                                outline=ACCENT_COLOR, width=w)
        draw.polygon([(cx - s * 0.3, cy - s * 0.4), (cx - s * 0.3, cy + s * 0.4), (cx + s * 0.45, cy)],
                     fill=ACCENT_COLOR)
    elif kind == "sns":
        draw.rounded_rectangle([cx - s, cy - s * 0.7, cx + s, cy + s * 0.5], radius=14,
                                outline=ACCENT_COLOR, width=w)
        draw.polygon([(cx - s * 0.3, cy + s * 0.5), (cx - s * 0.3, cy + s * 0.9), (cx + s * 0.1, cy + s * 0.5)],
                     fill=ACCENT_LIGHT, outline=ACCENT_COLOR, width=w)
    elif kind == "record":
        draw.rectangle([cx - s * 0.7, cy - s, cx + s * 0.7, cy + s], outline=ACCENT_COLOR, width=w)
        for i in range(3):
            yy = cy - s * 0.4 + i * s * 0.4
            draw.line([(cx - s * 0.4, yy), (cx + s * 0.4, yy)], fill=ACCENT_COLOR, width=3)
    elif kind == "share":
        draw.rounded_rectangle([cx - s, cy - s * 0.6, cx + s * 0.4, cy + s * 0.6], radius=12,
                                outline=ACCENT_COLOR, width=w)
        draw.line([(cx + s * 0.4, cy), (cx + s, cy)], fill=ACCENT_COLOR, width=w)
        draw.polygon([(cx + s * 0.6, cy - s * 0.25), (cx + s * 0.6, cy + s * 0.25), (cx + s, cy)],
                     fill=ACCENT_COLOR)
    elif kind == "try":
        draw.line([(cx - s * 0.6, cy), (cx - s * 0.1, cy + s * 0.5)], fill=ACCENT_COLOR, width=w + 1)
        draw.line([(cx - s * 0.1, cy + s * 0.5), (cx + s * 0.7, cy - s * 0.5)], fill=ACCENT_COLOR, width=w + 1)
    elif kind == "island":
        draw.ellipse([cx - s, cy - s * 0.6, cx + s, cy + s * 0.6], fill=ACCENT_COLOR)


# ----------------------------------------------------------------------
# レイアウト関数（すべての画像はこれらの組み合わせで作られる）
# ----------------------------------------------------------------------

def _center_lines_style(text):
    """中心円に収まるよう、長い語は2行に折り、円半径とフォントサイズを調整する。"""
    if len(text) <= 6:
        return [text], 170, 52
    mid = (len(text) + 1) // 2
    return [text[:mid], text[mid:]], 210, 38


def layout_radial(image_id, title, center_text, items, caption=None):
    img, draw = new_canvas()
    if title:
        draw_title(draw, image_id, title)
    cx, cy = CANVAS_W // 2, CANVAS_H // 2 + 20
    n = len(items)
    ring_r = 330 if n <= 4 else 360
    import math
    for i, label in enumerate(items):
        angle = -math.pi / 2 + i * (2 * math.pi / n)
        x = cx + ring_r * math.cos(angle)
        y = cy + ring_r * math.sin(angle)
        draw.line([(cx, cy), (x, y)], fill=LINE_COLOR, width=4)
        draw.ellipse([x - 10, y - 10, x + 10, y + 10], fill=ACCENT_COLOR)
        draw_text_centered(draw, image_id, f"item_{label}", x, y - 55, label, 44, bold=True)

    lines, circle_r, font_size = _center_lines_style(center_text)
    draw.ellipse([cx - circle_r, cy - circle_r, cx + circle_r, cy + circle_r],
                 fill=(255, 255, 255), outline=ACCENT_COLOR, width=5)
    line_gap = font_size + 14
    start_y = cy - line_gap * (len(lines) - 1) / 2
    for i, line in enumerate(lines):
        draw_text_centered(draw, image_id, f"center_{i}", cx, start_y + i * line_gap,
                            line, font_size, bold=True, color=ACCENT_COLOR)
    draw_caption(draw, image_id, caption)
    return img


def layout_tree(image_id, title, root_text, branches):
    """branches: [(branch_label, [child_labels...]), ...] 2分岐想定。"""
    img, draw = new_canvas()
    if title:
        draw_title(draw, image_id, title)
    root_y = CANVAS_H // 2 - 220
    draw_text_centered(draw, image_id, "root", CANVAS_W // 2, root_y, root_text, 56, bold=True, color=ACCENT_COLOR)

    branch_y = CANVAS_H // 2
    n_branches = len(branches)
    branch_xs = [CANVAS_W // (n_branches + 1) * (i + 1) for i in range(n_branches)]

    for bx, (branch_label, children) in zip(branch_xs, branches):
        draw.line([(CANVAS_W // 2, root_y + 40), (bx, branch_y - 40)], fill=LINE_COLOR, width=4)
        draw_text_centered(draw, image_id, f"branch_{branch_label}", bx, branch_y, branch_label, 46, bold=True)

        n_children = len(children)
        span = 300 if n_children <= 2 else 420
        child_y = branch_y + 220
        for j, child in enumerate(children):
            cx = bx - span / 2 + span * j / max(n_children - 1, 1) if n_children > 1 else bx
            draw.line([(bx, branch_y + 30), (cx, child_y - 35)], fill=LINE_COLOR, width=3)
            draw_text_centered(draw, image_id, f"child_{child}", cx, child_y, child, 38)
    return img


def layout_islands(image_id, title, caption, positions):
    img, draw = new_canvas()
    if title:
        draw_title(draw, image_id, title)
    for (x, y, r) in positions:
        draw.ellipse([x - r, y - r * 0.65, x + r, y + r * 0.65], fill=(226, 236, 242), outline=ACCENT_COLOR, width=4)
    draw_caption(draw, image_id, caption)
    return img


def layout_flow_arrows(image_id, title, steps):
    """steps: [(label, icon_kind), ...] 横一列を矢印でつなぐ。"""
    img, draw = new_canvas()
    if title:
        draw_title(draw, image_id, title)
    n = len(steps)
    xs = [CANVAS_W // (n + 1) * (i + 1) for i in range(n)]
    y = CANVAS_H // 2
    for i, (x, (label, icon_kind)) in enumerate(zip(xs, steps)):
        icon_badge(draw, x, y - 60, 90, icon_kind)
        draw_text_centered(draw, image_id, f"step_{label}", x, y + 110, label, 44, bold=True)
        if i < n - 1:
            x2 = xs[i + 1]
            ax0, ax1 = x + 110, x2 - 110
            ay = y - 60
            draw.line([(ax0, ay), (ax1, ay)], fill=ACCENT_COLOR, width=6)
            draw.polygon([(ax1, ay - 14), (ax1, ay + 14), (ax1 + 22, ay)], fill=ACCENT_COLOR)
    return img


def layout_icon_row(image_id, title, items):
    """items: [(label, icon_kind), ...] 矢印なしの等間隔配置。"""
    img, draw = new_canvas()
    if title:
        draw_title(draw, image_id, title)
    n = len(items)
    xs = [CANVAS_W // (n + 1) * (i + 1) for i in range(n)]
    y = CANVAS_H // 2
    for x, (label, icon_kind) in zip(xs, items):
        icon_badge(draw, x, y - 40, 95, icon_kind)
        draw_text_centered(draw, image_id, f"item_{label}", x, y + 130, label, 42, bold=True)
    return img


def layout_cycle(image_id, title, items, center_text):
    import math
    img, draw = new_canvas()
    if title:
        draw_title(draw, image_id, title)
    cx, cy = CANVAS_W // 2, CANVAS_H // 2 + 20
    r = 360
    n = len(items)
    pts = []
    for i in range(n):
        angle = -math.pi / 2 + i * (2 * math.pi / n)
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = x1 - x0, y1 - y0
        length = max((dx ** 2 + dy ** 2) ** 0.5, 1)
        ux, uy = dx / length, dy / length
        ax, ay = mx - ux * 20, my - uy * 20
        bx, by = mx + ux * 20, my + uy * 20
        draw.line([(x0, y0), (x1, y1)], fill=ACCENT_COLOR, width=5)
        perp = (-uy * 16, ux * 16)
        draw.polygon([(bx, by), (ax + perp[0], ay + perp[1]), (ax - perp[0], ay - perp[1])], fill=ACCENT_COLOR)
    for (x, y), label in zip(pts, items):
        draw.ellipse([x - 95, y - 95, x + 95, y + 95], fill=(255, 255, 255), outline=ACCENT_COLOR, width=4)
        draw_text_centered(draw, image_id, f"item_{label}", x, y, label, 38, bold=True)

    lines, _, font_size = _center_lines_style(center_text)
    line_gap = font_size + 12
    start_y = cy - line_gap * (len(lines) - 1) / 2
    for i, line in enumerate(lines):
        draw_text_centered(draw, image_id, f"center_{i}", cx, start_y + i * line_gap,
                            line, font_size, bold=True, color=ACCENT_COLOR)
    return img


# ----------------------------------------------------------------------
# 定義データ（ここだけ画像ごとに変わる）
# ----------------------------------------------------------------------

IMAGE_DEFS = [
    dict(
        id="B002", filename="B002_しまくとぅば_地域のことば.png", layout="radial",
        title=None, center="しまくとぅば",
        items=["国頭", "沖縄", "宮古", "八重山", "与那国"],
        caption="地域で受け継がれてきたことば",
    ),
    dict(
        id="B003", filename="B003_琉球諸語_北琉球_南琉球.png", layout="tree",
        title=None, root="琉球諸語",
        branches=[("北琉球", ["奄美", "沖縄"]), ("南琉球", ["宮古", "八重山", "与那国"])],
    ),
    dict(
        id="B004", filename="B004_島々で育ったことば.png", layout="islands",
        title=None, caption="それぞれの島で受け継がれてきた",
        positions=[
            (420, 380, 150), (900, 300, 110), (1350, 420, 170),
            (620, 720, 130), (1120, 760, 150), (1560, 700, 100),
        ],
    ),
    dict(
        id="B005", filename="B005_ことばに残る記憶.png", layout="radial",
        title=None, center="ことば",
        items=["暮らし", "土地", "記憶", "文化"],
        caption=None,
    ),
    dict(
        id="B009", filename="B009_しまくとぅば_衰退の複数要因.png", layout="radial",
        title=None, center="世代間の継承が弱まる",
        items=["学校・教育", "社会生活の変化", "メディア", "人口移動", "家庭で使う機会の減少"],
        caption=None,
    ),
    dict(
        id="B014", filename="B014_記録_伝える_使う.png", layout="flow_arrows",
        title=None,
        steps=[("記録する", "record"), ("伝える", "share"), ("使ってみる", "try")],
    ),
    dict(
        id="B019", filename="B019_ことばの表現の場.png", layout="icon_row",
        title=None,
        items=[("小説", "book"), ("音楽", "music"), ("芝居", "theater"), ("動画", "video"), ("SNS", "sns")],
    ),
    dict(
        id="B020", filename="B020_ことばが続く循環.png", layout="cycle",
        title=None, center="ことばが続いていく",
        items=["学ぶ", "使う", "教わる", "また使う"],
    ),
    dict(
        id="B021", filename="B021_しまくとぅば_未来へ.png", layout="radial",
        title=None, center="しまくとぅば",
        items=["知る", "聞く", "使う", "伝える"],
        caption="次の世代へ",
    ),
]


def build_image(d):
    layout = d["layout"]
    if layout == "radial":
        return layout_radial(d["id"], d["title"], d["center"], d["items"], d.get("caption"))
    if layout == "tree":
        return layout_tree(d["id"], d["title"], d["root"], d["branches"])
    if layout == "islands":
        return layout_islands(d["id"], d["title"], d["caption"], d["positions"])
    if layout == "flow_arrows":
        return layout_flow_arrows(d["id"], d["title"], d["steps"])
    if layout == "icon_row":
        return layout_icon_row(d["id"], d["title"], d["items"])
    if layout == "cycle":
        return layout_cycle(d["id"], d["title"], d["items"], d["center"])
    raise ValueError(f"unknown layout: {layout}")


# ----------------------------------------------------------------------
# 実行 + 機械的検品
# ----------------------------------------------------------------------

def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    completed, skipped = [], []
    for d in IMAGE_DEFS:
        try:
            img = build_image(d)
            path = os.path.join(out_dir, d["filename"])
            img.save(path, "PNG")
            completed.append(d["id"])
        except Exception as e:
            print(f"SKIP {d['id']}: {e}")
            skipped.append(d["id"])

    # 機械的検品
    print("\n--- QC ---")
    for d in IMAGE_DEFS:
        if d["id"] in skipped:
            continue
        path = os.path.join(out_dir, d["filename"])
        ok = os.path.isfile(path) and os.path.getsize(path) > 0
        size_ok = False
        if ok:
            with Image.open(path) as im:
                size_ok = im.size == (CANVAS_W, CANVAS_H)
                im.verify()
        status = "OK" if (ok and size_ok) else "FAIL"
        print(f"{d['id']}: {status}")

    if _OUT_OF_BOUNDS:
        print("\n--- 文字がキャンバス外に出た可能性 ---")
        for image_id, label, bbox in _OUT_OF_BOUNDS:
            print(f"{image_id} [{label}]: {bbox}")

    print("\n完成:")
    for i in completed:
        print(i)
    if skipped:
        print("\nSKIP:")
        for i in skipped:
            print(i)


if __name__ == "__main__":
    main()
