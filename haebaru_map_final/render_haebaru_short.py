"""南風原町 YouTube Shorts (1080x1920, 16.5 s) renderer.

Every frame is drawn from the same GIS data as haebaru_map_1080x1920.png
(国土数値情報 N03 2026, JGD2011 / UTM 52N), so zooms stay sharp and the boundary
stays exactly registered.

usage:
  python3 render_haebaru_short.py --n03 N03-20260101_47.geojson \
      --boundary haebaru_boundary.geojson --bgm 20261020南風原001.mp3 --out .
"""
import argparse, json, math, os, subprocess, sys, tempfile
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pyproj import Transformer
from shapely import transform as stransform
from shapely.geometry import box, shape

W, H = 1080, 1920
FPS = 30
DUR = 16.5
NF = int(round(DUR * FPS))
BGM_START = 54.8          # section hit at 56.6 s lands on 1.8 s (「海がない町がある。」)
FONT = '/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf'

SEA_TOP, SEA_BOT = (11, 36, 76), (4, 17, 42)
LAND = (50, 64, 56)
LAND_EDGE = (92, 110, 98)
COAST = (150, 200, 205)
# Sea tint by distance from the real coastline (style only, not bathymetry; land geometry untouched)
DEEP, OPEN, TEAL, TURQ = (5, 20, 54), (10, 46, 98), (18, 96, 140), (46, 172, 188)
SWELL = (176, 224, 238)
GOLD = (240, 198, 104)
GOLD_LINE = (250, 214, 128)
CREAM = (248, 242, 228)
INK = (6, 10, 16)

tr = Transformer.from_crs(6668, 6689, always_xy=True)   # JGD2011 -> UTM 52N


def proj(g):
    return stransform(g, lambda c: list(zip(*tr.transform(c[:, 0], c[:, 1]))))


def P(lon, lat):
    return tr.transform(lon, lat)


# ---------------------------------------------------------------- easing
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def smooth(x):
    x = clamp(x)
    return x * x * x * (x * (6 * x - 15) + 10)


def ease_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def lerp(a, b, e):
    return a + (b - a) * e


def loglerp(a, b, e):
    return math.exp(lerp(math.log(a), math.log(b), e))


# ---------------------------------------------------------------- data / camera
def load(n03, boundary):
    feats = json.load(open(n03, encoding='utf-8'))['features']
    hb = proj(shape(json.load(open(boundary, encoding='utf-8'))['features'][0]['geometry']))
    geoms = []
    for f in feats:
        g = shape(f['geometry'])
        x0, y0, x1, y1 = g.bounds
        if x1 < 127.2 or x0 > 128.7 or y1 < 25.6 or y0 > 27.4:
            continue
        g = proj(g)
        geoms += list(g.geoms) if g.geom_type == 'MultiPolygon' else [g]
    return geoms, hb


class Camera:
    """Camera = (world centre x, y, metres per pixel). Haebaru is the anchor."""

    def __init__(self, hb):
        self.h = (hb.centroid.x, hb.centroid.y)
        top = P(128.33, 26.87)[1]                       # Cape Hedo latitude
        mid_x = (P(127.65, 26.5)[0] + P(128.33, 26.5)[0]) / 2
        s0 = 72.0
        c0 = (mid_x, top - (960 - 560) * s0)            # island top at y=560
        self.p0 = self.screen(self.h, (c0[0], c0[1], s0))
        self.pt = (540, 1060)                           # Haebaru screen spot in close-ups

    @staticmethod
    def screen(pt, cam):
        cx, cy, s = cam
        return 540 + (pt[0] - cx) / s, 960 - (pt[1] - cy) / s

    def anchored(self, spot, s):
        return self.h[0] - (spot[0] - 540) * s, self.h[1] + (spot[1] - 960) * s, s

    def __call__(self, t):
        if t < 3.8:                                     # S1-2: slow push on the island
            return self.anchored(self.p0, loglerp(72, 62, t / 3.8))
        if t < 6.2:                                     # S3: fly south into the inset frame
            e = smooth((t - 3.8) / 2.4)
            spot = (lerp(self.p0[0], self.pt[0], e), lerp(self.p0[1], self.pt[1], e))
            return self.anchored(spot, loglerp(62, 8.6, e))
        if t < 9.0:                                     # S4: gentle push on 南風原町
            return self.anchored(self.pt, loglerp(8.6, 7.8, (t - 6.2) / 2.8))
        if t < 13.2:                                    # S5: pull back until both coasts show
            return self.anchored(self.pt, loglerp(7.8, 15.0, smooth((t - 9.0) / 4.2)))
        e = smooth((t - 13.2) / 3.3)                    # S6: ease back, bring 那覇 in
        return self.anchored((lerp(540, 700, e), self.pt[1]), loglerp(15.0, 21.0, e))


# ---------------------------------------------------------------- base map levels
LEVELS = [5.0, 10.0, 22.0, 48.0]


def level_weights(s):
    if s >= LEVELS[-1]:
        return [(LEVELS[-1], 1.0)]
    if s <= LEVELS[0]:
        return [(LEVELS[0], 1.0)]
    for a, b in zip(LEVELS, LEVELS[1:]):
        if a <= s < b:
            w = math.log(s / a) / math.log(b / a)
            return [(l, x) for l, x in ((a, 1 - w), (b, w)) if x > 0.02]


def frame_extent(cam, pad=4):
    cx, cy, s = cam
    return cx - (540 + pad) * s, cy - (960 + pad) * s, cx + (540 + pad) * s, cy + (960 + pad) * s


def render_level(L, region, geoms, ss=2):
    x0, y0, x1, y1 = region
    w, h = int(math.ceil((x1 - x0) / L)), int(math.ceil((y1 - y0) / L))
    img = Image.new('RGBA', (w * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = lambda c: [((x - x0) / L * ss, (y1 - y) / L * ss) for x, y in c]
    rb = box(*region)
    sel = [g for g in geoms if g.intersects(rb)]
    land = Image.new('L', img.size, 0)
    dl = ImageDraw.Draw(land)
    for g in sel:
        dl.polygon(f(g.exterior.coords), fill=255)
    land = land.resize((w, h), Image.LANCZOS)
    near = lambda r_m: np.asarray(land.filter(ImageFilter.GaussianBlur(max(0.5, r_m / L))), np.float32) / 255
    sea = np.empty((h, w, 3), np.float32)
    sea[:] = DEEP
    for col, r_m, gain, k in ((OPEN, 6000, 2.5, 1.0), (TEAL, 1200, 2.2, 0.85), (TURQ, 250, 1.8, 0.75)):
        a = np.clip(near(r_m) * gain, 0, 1)[..., None] * k
        sea += (np.array(col, np.float32) - sea) * a
    base = Image.fromarray(sea.astype(np.uint8), 'RGB').convert('RGBA')
    for g in sel:                                       # coastline: stroke left outside the land
        d.line(f(g.exterior.coords), fill=COAST + (220,), width=4)
    for g in sel:
        d.polygon(f(g.exterior.coords), fill=LAND + (255,))
    for g in sel:                                       # faint municipal edges
        d.line(f(g.exterior.coords), fill=LAND_EDGE + (255,), width=2)
    base.alpha_composite(img.resize((w, h), Image.LANCZOS))
    return base.convert('RGB'), Image.eval(land, lambda v: 255 - v)


# ---------------------------------------------------------------- sea swell
def make_noise(n=256, cutoff=10, seed=7):
    """Tileable smooth noise in [0, 1]."""
    f = np.fft.fft2(np.random.default_rng(seed).standard_normal((n, n)))
    k = np.hypot(*np.meshgrid(np.fft.fftfreq(n) * n, np.fft.fftfreq(n) * n))
    t = np.real(np.fft.ifft2(f * np.exp(-(k / cutoff) ** 2)))
    return ((t - t.min()) / (t.max() - t.min())).astype(np.float32)


def sample(T, u, v):
    n = T.shape[0]
    u0, v0 = np.floor(u), np.floor(v)
    fu, fv = u - u0, v - v0
    i0, j0 = u0.astype(int) % n, v0.astype(int) % n
    i1, j1 = (i0 + 1) % n, (j0 + 1) % n
    return ((T[j0, i0] * (1 - fu) + T[j0, i1] * fu) * (1 - fv)
            + (T[j1, i0] * (1 - fu) + T[j1, i1] * fu) * fv)


def swell(cam, t, step=4):
    """Soft moving highlights anchored to the map; octave blend keeps the scale steady while zooming."""
    cx, cy, s = cam
    X = cx + (np.arange(W // step) * step + step / 2 - 540)[None, :] * s
    Y = cy - (np.arange(H // step) * step + step / 2 - 960)[:, None] * s
    n = math.log2(s)
    a, w = 2 ** math.floor(n), n - math.floor(n)
    acc = 0
    for sc, ww in ((a, 1 - w), (2 * a, w)):
        u, v = X / (sc * 8), Y / (sc * 2.5)            # stretched east-west: long, low swells
        acc = acc + ww * (0.6 * sample(G['noise'], u + t * 1.2, v + t * 0.5)
                          + 0.4 * sample(G['noise'], u * 1.9 - t * 0.9 + 37, v * 1.9 + t * 0.7 + 11))
    return np.clip((acc - 0.56) * 4, 0, 1) ** 1.8


# ---------------------------------------------------------------- captions
def text_sprite(lines, pad=70):
    """lines: [(size, [(text, rgb), ...], gap_after)] -> RGBA sprite centred on its middle."""
    fonts = [ImageFont.truetype(FONT, size) for size, _, _ in lines]
    widths = [sum(f.getlength(t) for t, _ in segs) for f, (_, segs, _) in zip(fonts, lines)]
    hts = [size for size, _, _ in lines]
    tw = int(max(widths)) + pad * 2
    th = int(sum(hts) + sum(g for _, _, g in lines)) + pad * 2
    fill = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
    edge = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
    df, de = ImageDraw.Draw(fill), ImageDraw.Draw(edge)
    y = pad
    for f, (size, segs, gap), wdt in zip(fonts, lines, widths):
        x = (tw - wdt) / 2
        bold = max(1, round(size * 0.022))
        for t, col in segs:
            df.text((x, y), t, font=f, fill=col + (255,), stroke_width=bold, stroke_fill=col + (255,))
            de.text((x, y), t, font=f, fill=INK + (255,), stroke_width=bold + max(3, size // 26),
                    stroke_fill=INK + (255,))
            x += f.getlength(t)
        y += size + gap
    a = edge.getchannel('A')
    shadow = Image.new('RGBA', (tw, th), INK + (0,))
    shadow.putalpha(a.filter(ImageFilter.GaussianBlur(16)).point(lambda v: int(v * 0.75)))
    shadow = shadow.transform(shadow.size, Image.AFFINE, (1, 0, 0, 0, 1, -6))
    edge.putalpha(a.filter(ImageFilter.GaussianBlur(1.2)).point(lambda v: int(v * 0.85)))
    out = Image.alpha_composite(shadow, edge)
    return Image.alpha_composite(out, fill)


def haebaru_title():
    s = text_sprite([(176, [('南風原町', CREAM)], 34), (40, [('は　え　ば　る', (226, 214, 186))], 0)])
    d = ImageDraw.Draw(s)
    cy = 70 + 176 + 17
    d.line([(s.width / 2 - 150, cy), (s.width / 2 + 150, cy)], fill=GOLD + (230,), width=2)
    return s


def captions():
    G = GOLD
    return [
        # (start, end, centre y, sprite)
        (0.0, 1.8, 370, text_sprite([(128, [('沖縄なのに、', CREAM)], 0)])),
        (1.8, 3.8, 370, text_sprite([(106, [('海がない', G), ('町がある。', CREAM)], 0)])),
        (3.8, 6.2, 370, text_sprite([(96, [('その場所が…', CREAM)], 0)])),
        (6.2, 9.0, 400, haebaru_title()),
        (9.0, 13.2, 330, text_sprite([(92, [('沖縄で唯一、', CREAM)], 0)])),
        (10.4, 13.2, 452, text_sprite([(100, [('海に面していない', G), ('町', CREAM)], 0)])),
        (13.2, 16.5, 395, text_sprite([(92, [('那覇のすぐ隣に', CREAM)], 26),
                                        (92, [('あります。', CREAM)], 0)])),
    ]


def label_sprite(text):
    """Small map label: dark plate with a thin gold rule (style taken from the reference art)."""
    f = ImageFont.truetype(FONT, 38)
    tw = int(f.getlength(text)) + 44
    spr = Image.new('RGBA', (tw + 24, 62 + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(spr)
    d.rectangle([12, 12, 12 + tw, 12 + 62], fill=(5, 9, 14, 215), outline=GOLD + (230,), width=2)
    d.text((12 + tw / 2, 12 + 31), text, font=f, anchor='mm', fill=CREAM + (255,),
           stroke_width=1, stroke_fill=CREAM + (255,))
    return spr


# ---------------------------------------------------------------- frame
G = {}


def sky():
    """Soft light from the upper right, deeper shade toward the bottom (screen space)."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    light = np.exp(-(((xx - 880) / 700) ** 2 + ((yy - 200) / 900) ** 2))
    shade = np.clip((yy - 900) / 1020, 0, 1) ** 1.4
    out = Image.new('RGBA', (W, H), SEA_BOT + (0,))
    out.putalpha(Image.fromarray((shade * 110).astype(np.uint8)))
    lit = Image.new('RGBA', (W, H), (190, 225, 255, 0))
    lit.putalpha(Image.fromarray((light * 30).astype(np.uint8)))
    out.alpha_composite(lit)
    return out


def vignette():
    m = Image.new('L', (W // 4, H // 4), 0)
    d = ImageDraw.Draw(m)
    for i in range(60):
        e = i / 60
        d.rectangle([e * W / 8 * 0.9, e * H / 8 * 0.9, W / 4 - e * W / 8 * 0.9, H / 4 - e * H / 8 * 0.9],
                    fill=int(150 * (1 - e) ** 2.2))
    m = m.filter(ImageFilter.GaussianBlur(12)).resize((W, H), Image.BILINEAR)
    v = Image.new('RGBA', (W, H), INK + (0,))
    v.putalpha(m)
    return v


def gold_style(t, s):
    """fill alpha, line width, glow alpha, glow radius for the Haebaru highlight."""
    k = clamp((s - 12) / (40 - 12))                     # 1 on the island view, 0 in close-up
    fill_a = lerp(42, 175, k)
    lw = lerp(3.2, 2.2, k)
    glow_a, glow_r = lerp(80, 120, k), lerp(9, 5, k)
    if t < 1.8:                                         # subdued until the reveal
        fill_a, glow_a = 70, 25
    elif t < 3.8:
        e = ease_out((t - 1.8) / 0.6)
        fill_a, glow_a = lerp(70, 205, e), lerp(25, 170, e)
    if 3.8 <= t < 6.2:                                  # glow builds while the camera flies in
        e = smooth((t - 3.8) / 2.4)
        glow_a, glow_r = glow_a * (1 + 0.8 * e), glow_r * (1 + 0.5 * e)
    elif 6.2 <= t < 8.6:                                # arrival: soft bloom that settles
        e = 1 - smooth((t - 6.2) / 2.4)
        glow_a, glow_r = glow_a * (1 + 0.8 * e), glow_r * (1 + 0.5 * e)
    return fill_a, lw, glow_a, glow_r


SWEEP = (5.8, 7.6)                                      # a light runs once around the boundary


def sweep_layer(loc, size, t, lw):
    p = smooth((t - SWEEP[0]) / (SWEEP[1] - SWEEP[0]))
    seg = np.hypot(*np.diff(np.array(loc), axis=0).T)
    cum = np.concatenate([[0], np.cumsum(seg)])
    tail = 0.22 * cum[-1]
    head = p * (cum[-1] + tail)
    fade = math.sin(math.pi * p)
    out = Image.new('RGBA', size, (255, 238, 196, 0))
    m = Image.new('L', size, 0)
    dm = ImageDraw.Draw(m)
    for k in range(12):                                 # tapered tail, brightest at the head
        lo, hi_ = head - tail * (k + 1) / 12, head - tail * k / 12
        pts = [loc[i] for i in range(len(loc)) if lo <= cum[i] <= hi_]
        if len(pts) > 1:
            dm.line(pts, fill=int(255 * fade * (1 - k / 12)), width=int(lw * 2.2), joint='curve')
    out.putalpha(Image.eval(m.filter(ImageFilter.GaussianBlur(5)), lambda v: min(255, v * 2)))
    core = Image.new('RGBA', size, (255, 246, 222, 0))
    core.putalpha(m.filter(ImageFilter.GaussianBlur(0.8)))
    out.alpha_composite(core)
    return out


def draw_haebaru(frame, pts, t, s, ss=3):
    fill_a, lw, glow_a, glow_r = gold_style(t, s)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    m = int(glow_r * 3 + lw * 4 + 4)
    bx0, by0 = int(min(xs)) - m, int(min(ys)) - m
    bw, bh = int(max(xs)) - bx0 + m, int(max(ys)) - by0 + m
    loc = [(x - bx0, y - by0) for x, y in pts]
    glow = Image.new('RGBA', (bw, bh), GOLD + (0,))
    gm = Image.new('L', (bw, bh), 0)
    ImageDraw.Draw(gm).line(loc, fill=int(clamp(glow_a / 255) * 255), width=int(lw * 3), joint='curve')
    glow.putalpha(gm.filter(ImageFilter.GaussianBlur(glow_r)))
    hi = Image.new('RGBA', (bw * ss, bh * ss), GOLD + (0,))
    dh = ImageDraw.Draw(hi)
    sl = [(x * ss, y * ss) for x, y in loc]
    dh.polygon(sl, fill=GOLD + (int(fill_a),))
    dh.line(sl + sl[:1], fill=GOLD_LINE + (255,), width=max(1, int(round(lw * ss))), joint='curve')
    hi = hi.resize((bw, bh), Image.LANCZOS)
    frame.alpha_composite(glow, (bx0, by0))
    frame.alpha_composite(hi, (bx0, by0))
    if SWEEP[0] < t < SWEEP[1]:
        frame.alpha_composite(sweep_layer(loc, (bw, bh), t, lw), (bx0, by0))


def paste_sprite(frame, spr, cx, cy, a):
    if a <= 0.003:
        return
    if a < 0.997:
        spr = spr.copy()
        spr.putalpha(spr.getchannel('A').point(lambda v: int(v * a)))
    frame.alpha_composite(spr, (int(cx - spr.width / 2), int(cy - spr.height / 2)))


def render_frame(i):
    t = i / FPS
    cam = G['cam'](t)
    cx, cy, s = cam
    x0, y0, x1, y1 = frame_extent(cam, pad=0)
    layer = mask = None
    for L, w in level_weights(s):
        img, sea, (rx0, ry0, rx1, ry1) = G['levels'][L]
        bx = ((x0 - rx0) / L, (ry1 - y1) / L, (x1 - rx0) / L, (ry1 - y0) / L)
        rs = Image.LANCZOS if s / L > 1.05 else Image.BICUBIC
        crop, mc = img.resize((W, H), rs, box=bx), sea.resize((W, H), Image.BILINEAR, box=bx)
        layer = crop if layer is None else Image.blend(layer, crop, w)
        mask = mc if mask is None else Image.blend(mask, mc, w)
    frame = Image.alpha_composite(layer.convert('RGBA'), G['sky'])
    hi = Image.fromarray((swell(cam, t) * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR)
    a = np.asarray(hi, np.float32) * np.asarray(mask, np.float32) / 255 * G['glint']
    sw = Image.new('RGBA', (W, H), SWELL + (0,))
    sw.putalpha(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)))
    frame.alpha_composite(sw)

    scr = lambda p: Camera.screen(p, cam)
    draw_haebaru(frame, [scr(p) for p in G['hb'].exterior.coords], t, s)

    if 3.8 < t < 6.2:                                   # inset frame the camera flies into
        a = ease_out((t - 3.9) / 0.5) * (1 - smooth((t - 5.5) / 0.6))
        tx0, ty0, tx1, ty1 = frame_extent(G['cam'].anchored(G['cam'].pt, 8.6), pad=0)
        (ax, ay), (bx_, by_) = scr((tx0, ty1)), scr((tx1, ty0))
        ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(ov).rectangle([ax, ay, bx_, by_], outline=GOLD + (int(200 * a),), width=2)
        frame.alpha_composite(ov)

    if t > 13.5:                                        # small place labels in the last shot
        a = ease_out((t - 13.6) / 0.7)
        for key, pos in (('naha', G['naha']), ('hb', G['hb_label'])):
            x, y = scr(pos)
            dx = G['labels'][key].width / 2 - 4 if key == 'hb' else 0    # beside the boundary, not on it
            paste_sprite(frame, G['labels'][key], x + dx, y, a)

    frame.alpha_composite(G['vig'])

    for st, en, y, spr in G['caps']:
        if not (st <= t < en + 0.001):
            continue
        a_in = 1.0 if st == 0 else ease_out((t - st) / 0.32)
        a_out = 1.0 if en >= DUR else 1 - smooth((t - (en - 0.18)) / 0.18)
        dy = 0 if st == 0 else (1 - ease_out((t - st) / 0.45)) * 22
        paste_sprite(frame, spr, W / 2, y + dy, a_in * a_out)

    out = frame.convert('RGB')
    if t > DUR - 0.45:                                  # quiet fade at the very end
        k = 1 - smooth((t - (DUR - 0.45)) / 0.45)
        out = Image.eval(out, lambda v: int(v * k))
    return out.tobytes()


def setup(n03, boundary):
    geoms, hb = load(n03, boundary)
    cam = Camera(hb)
    regions = {}
    for i in range(NF):
        c = cam(i / FPS)
        ex = frame_extent(c, pad=8)
        ex = (ex[0] - 6000, ex[1] - 6000, ex[2] + 6000, ex[3] + 6000)   # room for the coastal tint
        for L, _ in level_weights(c[2]):
            r = regions.get(L)
            regions[L] = ex if r is None else (min(r[0], ex[0]), min(r[1], ex[1]),
                                               max(r[2], ex[2]), max(r[3], ex[3]))
    levels = {}
    for L, r in sorted(regions.items()):
        levels[L] = (*render_level(L, r, geoms), r)
        print(f'  level {L:>4} m/px  {levels[L][0].size}', flush=True)
    rp = hb.representative_point()
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    glint = 0.35 + 0.9 * np.exp(-(((xx - 860) / 520) ** 2 + ((yy - 240) / 700) ** 2))
    G.update(cam=cam, hb=hb, levels=levels, sky=sky(), vig=vignette(), caps=captions(),
             noise=make_noise(), glint=glint * 0.2,
             labels={'naha': label_sprite('那覇市'), 'hb': label_sprite('南風原町')},
             naha=P(127.6813, 26.2124), hb_label=(hb.bounds[2], rp.y))


# ---------------------------------------------------------------- audio
def make_audio(bgm, out_wav):
    seg = ['-ss', str(BGM_START), '-t', str(DUR), '-i', bgm]
    r = subprocess.run(['ffmpeg', '-hide_banner', *seg, '-af', 'ebur128', '-f', 'null', '-'],
                       capture_output=True, text=True)
    lufs = float([l for l in r.stderr.splitlines() if l.strip().startswith('I:')][-1].split()[1])
    gain = -19.0 - lufs                                 # keep the BGM well behind the captions
    af = (f'volume={gain:.2f}dB,afade=t=in:st=0:d=0.25,'
          f'afade=t=out:st={DUR - 2.0}:d=2.0:curve=qsin,alimiter=limit=0.85')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *seg, '-af', af, '-ar', '48000', '-ac', '2', out_wav],
                   check=True)
    return lufs, gain


def contact_sheet(mp4, out_jpg, times=(0.3, 2.6, 5.0, 7.6, 12.2, 15.3)):
    tw, th = 360, 640
    sheet = Image.new('RGB', (tw * 3, th * 2), (0, 0, 0))
    with tempfile.TemporaryDirectory() as td:
        for k, t in enumerate(times):
            p = os.path.join(td, f'{k}.png')
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(t), '-i', mp4, '-frames:v', '1', p],
                           check=True)
            im = Image.open(p).convert('RGB').resize((tw, th), Image.LANCZOS)
            ImageDraw.Draw(im).text((10, 8), f'{t:.1f}s', fill=(255, 255, 255),
                                    font=ImageFont.truetype(FONT, 22))
            sheet.paste(im, ((k % 3) * tw, (k // 3) * th))
    sheet.save(out_jpg, quality=90)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n03', required=True)
    ap.add_argument('--boundary', required=True)
    ap.add_argument('--bgm')
    ap.add_argument('--out', default='.')
    a = ap.parse_args()
    mp4 = os.path.join(a.out, 'haebaru_short_v1.mp4')
    print('setup', flush=True)
    setup(a.n03, a.boundary)
    with tempfile.TemporaryDirectory() as td:
        audio = []
        if a.bgm:
            wav = os.path.join(td, 'bgm.wav')
            lufs, gain = make_audio(a.bgm, wav)
            print(f'bgm segment {BGM_START}s +{DUR}s: {lufs:.1f} LUFS -> gain {gain:+.1f} dB', flush=True)
            audio = ['-i', wav, '-c:a', 'aac', '-b:a', '192k']
        enc = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                                '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', *audio[:2],
                                '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-pix_fmt', 'yuv420p',
                                '-profile:v', 'high', *audio[2:], '-t', str(DUR),
                                '-movflags', '+faststart', mp4], stdin=subprocess.PIPE)
        with Pool(os.cpu_count()) as pool:
            for k, buf in enumerate(pool.imap(render_frame, range(NF), chunksize=4)):
                enc.stdin.write(buf)
                if k % 60 == 0:
                    print(f'  frame {k}/{NF}', flush=True)
        enc.stdin.close()
        if enc.wait():
            sys.exit('ffmpeg failed')
    contact_sheet(mp4, os.path.join(a.out, 'haebaru_short_v1_qa.jpg'))
    print('done', mp4)


if __name__ == '__main__':
    main()
