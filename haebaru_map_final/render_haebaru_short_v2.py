"""南風原町 YouTube Shorts v2 — choropleth build.

Motion language adapted from the HyperFrames "spain-map" block (heygen-com/hyperframes,
Apache-2.0): staggered back-out reveal from the centre, wiped headlines, gradient legend,
sequential highlight strokes. Data: every municipality is shaded by the share of its
boundary that faces the sea (国土数値情報 N03 2026). 南風原町 is the only 0 %.

Sea, camera, captions and audio are shared with render_haebaru_short.py (v1).

usage:
  python3 render_haebaru_short_v2.py --n03 N03-20260101_47.geojson \
      --boundary haebaru_boundary.geojson --bgm 20261020南風原001.mp3 --out .
"""
import argparse, json, math, os, subprocess, sys, tempfile
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from shapely.geometry import box, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_haebaru_short as v1                      # noqa: E402
from render_haebaru_short import (W, H, FPS, DUR, NF, FONT, CREAM, GOLD, INK,  # noqa: E402
                                  clamp, smooth, ease_out, lerp)

LAND_LO, LAND_MID, LAND_HI = (46, 64, 92), (112, 136, 166), (226, 232, 240)
GHOST = (24, 42, 74)                                    # land before it is assembled
STROKE = (10, 18, 34)
CYAN = (103, 232, 249)
SLATE = (148, 163, 184)
NEIGHBOURS = ['那覇市', '西原町', '与那原町', '南城市', '八重瀬町', '豊見城市']
TOLS = [2.0, 8.0, 25.0, 70.0]
G = {}


def back_out(p, c1=1.4):
    p = clamp(p) - 1
    return 1 + (c1 + 1) * p ** 3 + c1 * p ** 2


def power2_inout(p):
    p = clamp(p)
    return 2 * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 2 / 2


# ---------------------------------------------------------------- data
def rings(g, tol):
    g = g.simplify(tol)
    parts = list(g.geoms) if g.geom_type == 'MultiPolygon' else [g]
    return [np.asarray(p.exterior.coords)[:, :2] for p in parts if not p.is_empty]


def lines(g, tol):
    g = g.simplify(tol)
    parts = list(g.geoms) if hasattr(g, 'geoms') else [g]
    return [np.asarray(p.coords)[:, :2] for p in parts if p.geom_type == 'LineString' and len(p.coords) > 1]


def municipalities(n03):
    by = {}
    for f in json.load(open(n03, encoding='utf-8'))['features']:
        p = f['properties']
        c = p['N03_007']
        if not c or c == '47000':
            continue
        g = shape(f['geometry'])
        x0, y0, x1, y1 = g.bounds
        if x0 > 128.5 or x1 < 127.0 or y1 < 25.9 or y0 > 27.5:
            continue
        by.setdefault(c, [p['N03_004'], []])[1].append(g)
    codes = list(by)
    geoms = [v1.proj(unary_union(by[c][1])).simplify(2.0) for c in codes]
    tree = STRtree(geoms)
    out = []
    for i, c in enumerate(codes):
        g = geoms[i]
        nb = [geoms[j] for j in tree.query(g.buffer(20)) if j != i and geoms[j].distance(g) < 10]
        coast = g.boundary.difference(unary_union([n.boundary for n in nb]).buffer(6)) if nb else g.boundary
        share = coast.length / g.boundary.length
        rp = g.representative_point()
        out.append(dict(code=c, name=by[c][0], share=share, area=g.area, bounds=g.bounds,
                        c=(g.centroid.x, g.centroid.y), rp=(rp.x, rp.y),
                        rings={t: rings(g, t) for t in TOLS},
                        coast={t: lines(coast, t) for t in TOLS}))
    out.sort(key=lambda m: -m['area'])
    return out


def land_colour(share, lo):
    t = clamp((share - lo) / (1 - lo))
    a, b, u = (LAND_LO, LAND_MID, t / 0.5) if t < 0.5 else (LAND_MID, LAND_HI, (t - 0.5) / 0.5)
    return tuple(int(lerp(x, y, u)) for x, y in zip(a, b))


# ---------------------------------------------------------------- sea (v1 sea without land drawing)
def render_sea_level(L, region, geoms, ss=2):
    x0, y0, x1, y1 = region
    w, h = int(math.ceil((x1 - x0) / L)), int(math.ceil((y1 - y0) / L))
    f = lambda c: [((x - x0) / L * ss, (y1 - y) / L * ss) for x, y in c]
    rb = box(*region)
    land = Image.new('L', (w * ss, h * ss), 0)
    dl = ImageDraw.Draw(land)
    for g in geoms:
        if g.intersects(rb):
            dl.polygon(f(g.exterior.coords), fill=255)
    land = land.resize((w, h), Image.LANCZOS)
    near = lambda r_m: np.asarray(land.filter(ImageFilter.GaussianBlur(max(0.5, r_m / L))), np.float32) / 255
    sea = np.empty((h, w, 3), np.float32)
    sea[:] = v1.DEEP
    for col, r_m, gain, k in ((v1.OPEN, 6000, 2.5, 1.0), (v1.TEAL, 1200, 2.2, 0.85), (v1.TURQ, 250, 1.8, 0.75)):
        a = np.clip(near(r_m) * gain, 0, 1)[..., None] * k
        sea += (np.array(col, np.float32) - sea) * a
    m = (np.asarray(land, np.float32) / 255)[..., None]
    sea = sea * (1 - m) + np.array(GHOST, np.float32) * m
    return Image.fromarray(sea.astype(np.uint8), 'RGB'), Image.eval(land, lambda v: 255 - v)


# ---------------------------------------------------------------- sprites
def plate(text, border, size=36):
    f = ImageFont.truetype(FONT, size)
    tw = int(f.getlength(text)) + 44
    spr = Image.new('RGBA', (tw + 24, size + 26 + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(spr)
    d.rectangle([12, 12, 12 + tw, 12 + size + 26], fill=(5, 9, 14, 215), outline=border + (230,), width=2)
    d.text((12 + tw / 2, 12 + (size + 26) / 2), text, font=f, anchor='mm', fill=CREAM + (255,),
           stroke_width=1, stroke_fill=CREAM + (255,))
    return spr


def legend(lo):
    """Gradient legend; returns (static parts, bar) so the bar can be wiped in."""
    wdt, bar_w, bar_h = 760, 560, 16
    spr = Image.new('RGBA', (wdt, 170), (0, 0, 0, 0))
    d = ImageDraw.Draw(spr)
    ft, fs = ImageFont.truetype(FONT, 30), ImageFont.truetype(FONT, 24)
    d.text((wdt / 2, 22), '海に面している割合', font=ft, anchor='mm', fill=(203, 213, 225, 255),
           stroke_width=1, stroke_fill=(203, 213, 225, 255))
    bx0 = (wdt - bar_w) // 2
    d.text((bx0 - 14, 78), '0%', font=fs, anchor='rm', fill=SLATE + (255,))
    d.text((bx0 + bar_w + 14, 78), '100%', font=fs, anchor='lm', fill=SLATE + (255,))
    bar = Image.new('RGBA', (bar_w, bar_h), (0, 0, 0, 0))
    grad = np.zeros((bar_h, bar_w, 4), np.uint8)
    for x in range(bar_w):
        grad[:, x, :3] = land_colour(lo + (1 - lo) * x / (bar_w - 1), lo)
    grad[..., 3] = 255
    bar.paste(Image.fromarray(grad, 'RGBA'))
    mask = Image.new('L', (bar_w, bar_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bar_w - 1, bar_h - 1], radius=bar_h // 2, fill=255)
    bar.putalpha(mask)
    marker = Image.new('RGBA', (wdt, 170), (0, 0, 0, 0))
    dm = ImageDraw.Draw(marker)
    mx, my = bx0, 70 + bar_h // 2
    dm.ellipse([mx - 13, my - 13, mx + 13, my + 13], fill=GOLD + (255,), outline=INK + (255,), width=3)
    dm.text((mx, my + 44), '南風原町', font=fs, anchor='mm', fill=GOLD + (255,),
            stroke_width=1, stroke_fill=GOLD + (255,))
    return spr, bar, (bx0, 70), marker


def credit():
    f = ImageFont.truetype(FONT, 22)
    t = '出典：国土数値情報（国土交通省）'
    spr = Image.new('RGBA', (int(f.getlength(t)) + 8, 34), (0, 0, 0, 0))
    ImageDraw.Draw(spr).text((4, 4), t, font=f, fill=(120, 136, 160, 255))
    return spr


def paste(frame, spr, x, y, a, anchor='c'):
    if a <= 0.003:
        return
    if a < 0.997:
        spr = spr.copy()
        spr.putalpha(spr.getchannel('A').point(lambda v: int(v * a)))
    if anchor == 'c':
        x, y = x - spr.width / 2, y - spr.height / 2
    frame.alpha_composite(spr, (int(x), int(y)))


def wipe(spr, e, feather=90):
    """Reveal a sprite from left to right (headline wipe)."""
    if e >= 1:
        return spr
    w = spr.width
    xr = e * (w + feather)
    ramp = np.clip((xr - np.arange(w, dtype=np.float32)) / feather, 0, 1)
    a = np.asarray(spr.getchannel('A'), np.float32) * ramp[None, :]
    out = spr.copy()
    out.putalpha(Image.fromarray(a.astype(np.uint8)))
    return out


# ---------------------------------------------------------------- frame
def tol_for(s):
    return max([t for t in TOLS if t <= 0.6 * s] or [TOLS[0]])


def reveal_time(m):
    return -0.6 + 1.9 * clamp(math.dist(m['c'], G['hc']) / G['dmax']) ** 0.8


def to_screen(arr, cam, k=1.0, about=None, ss=1):
    cx, cy, s = cam
    if about is not None and k != 1.0:
        arr = about + (arr - about) * k
    x = (540 + (arr[:, 0] - cx) / s) * ss
    y = (960 - (arr[:, 1] - cy) / s) * ss
    return np.stack([x, y], 1).ravel().tolist()


def draw_regions(frame, cam, t, ss=2):
    cx, cy, s = cam
    tol = tol_for(s)
    ex = v1.frame_extent(cam, pad=40)
    lay = Image.new('RGBA', (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, 'RGBA')
    naha_k = 0.0
    if t > 13.4:                                        # spain-map style highlight on 那覇市
        naha_k = ease_out((t - 13.4) / 0.5) * (1 - 0.45 * smooth((t - 14.4) / 0.8))
    for m in G['munis']:
        if m['code'] == '47350':
            continue
        b = m['bounds']
        if b[2] < ex[0] or b[0] > ex[2] or b[3] < ex[1] or b[1] > ex[3]:
            continue
        p = (t - reveal_time(m)) / 0.4
        if p <= 0:
            continue
        k = 0.55 + 0.45 * back_out(p)
        a = clamp(p * 3)
        col = land_colour(m['share'], G['lo'])
        if m['name'] == '那覇市' and naha_k > 0:
            col = tuple(int(lerp(c, 255, 0.28 * naha_k)) for c in col)
        about = np.asarray(m['c'])
        for r in m['rings'][tol]:
            pts = to_screen(r, cam, k, about, ss)
            if len(pts) >= 6:
                d.polygon(pts, fill=col + (int(255 * a),), outline=STROKE + (int(255 * a),), width=2)
    frame.alpha_composite(lay.resize((W, H), Image.LANCZOS))
    if naha_k > 0:
        m = G['by_name']['那覇市']
        hl = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        dh = ImageDraw.Draw(hl)
        for r in m['rings'][tol]:
            dh.line(to_screen(r, cam), fill=(248, 250, 252, int(235 * naha_k)), width=3, joint='curve')
        glow = hl.filter(ImageFilter.GaussianBlur(6))
        frame.alpha_composite(glow)
        frame.alpha_composite(hl)


def draw_haebaru(frame, cam, t):
    s = cam[2]
    hb = np.asarray(G['hb'].exterior.coords)[:, :2]
    if t < 1.8:                                         # the missing piece
        pts = to_screen(hb, cam)
        lay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(lay).line(pts + pts[:2], fill=SLATE + (int(140 * clamp((t + 0.2) / 0.6)),),
                                 width=2, joint='curve')
        frame.alpha_composite(lay)
        return
    p = (t - 1.8) / 0.55
    k = 1.6 - 0.6 * back_out(p)
    flat = to_screen(hb, cam, k, np.asarray(G['hc']))
    pts = list(zip(flat[0::2], flat[1::2]))
    white = 1 - smooth((t - 1.9) / 0.9)
    piece = Image.new('RGBA', (W * 2, H * 2), (0, 0, 0, 0))
    ImageDraw.Draw(piece).polygon([(x * 2, y * 2) for x, y in pts], fill=(214, 168, 72, int(235 * clamp(p * 3))))
    frame.alpha_composite(piece.resize((W, H), Image.LANCZOS))
    v1.draw_haebaru(frame, pts, t, s)
    if white > 0.01:                                    # white flash stroke on arrival
        lay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(lay).line(pts + pts[:1], fill=(255, 255, 255, int(230 * white)), width=3, joint='curve')
        frame.alpha_composite(lay.filter(ImageFilter.GaussianBlur(4)))
        frame.alpha_composite(lay)


def draw_coasts(frame, cam, t):
    """10.0–13.6 s: the six neighbours' coastlines light up one after another."""
    if not 9.9 < t < 13.7:
        return
    tol = tol_for(cam[2])
    lay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    out = 1 - smooth((t - 13.0) / 0.5)
    for i, name in enumerate(NEIGHBOURS):
        t0 = 10.0 + i * 0.38
        if t < t0:
            continue
        inten = ease_out((t - t0) / 0.25) * (1 - 0.25 * smooth((t - t0 - 0.25) / 0.8)) * out
        for ln in G['by_name'][name]['coast'][tol]:
            d.line(to_screen(ln, cam), fill=CYAN + (int(255 * inten),), width=5, joint='curve')
    glow = lay.filter(ImageFilter.GaussianBlur(10))
    for _ in range(2):
        frame.alpha_composite(glow)
    frame.alpha_composite(lay.filter(ImageFilter.GaussianBlur(3)))
    frame.alpha_composite(lay)
    for i, name in enumerate(NEIGHBOURS):               # neighbour name plates
        t0 = 10.1 + i * 0.38
        if t < t0:
            continue
        x, y = v1.Camera.screen(G['by_name'][name]['rp'], cam)
        x, y = clamp(x, 130, W - 130), clamp(y, 600, 1440)
        paste(frame, G['plates'][name], x, y, ease_out((t - t0) / 0.3) * out)


def render_frame(i):
    t = i / FPS
    cam = G['cam'](t)
    cx, cy, s = cam
    x0, y0, x1, y1 = v1.frame_extent(cam, pad=0)
    layer = mask = None
    for L, w in v1.level_weights(s):
        img, sea, (rx0, ry0, rx1, ry1) = G['levels'][L]
        bx = ((x0 - rx0) / L, (ry1 - y1) / L, (x1 - rx0) / L, (ry1 - y0) / L)
        rs = Image.LANCZOS if s / L > 1.05 else Image.BICUBIC
        crop, mc = img.resize((W, H), rs, box=bx), sea.resize((W, H), Image.BILINEAR, box=bx)
        layer = crop if layer is None else Image.blend(layer, crop, w)
        mask = mc if mask is None else Image.blend(mask, mc, w)
    frame = Image.alpha_composite(layer.convert('RGBA'), G['sky'])
    hi = Image.fromarray((v1.swell(cam, t) * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR)
    a = np.asarray(hi, np.float32) * np.asarray(mask, np.float32) / 255 * G['glint']
    sw = Image.new('RGBA', (W, H), v1.SWELL + (0,))
    sw.putalpha(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)))
    frame.alpha_composite(sw)

    draw_regions(frame, cam, t)
    draw_haebaru(frame, cam, t)

    if 3.8 < t < 6.2:                                   # inset frame the camera flies into
        a = ease_out((t - 3.9) / 0.5) * (1 - smooth((t - 5.5) / 0.6))
        tx0, ty0, tx1, ty1 = v1.frame_extent(G['cam'].anchored(G['cam'].pt, 8.6), pad=0)
        (ax, ay), (bx_, by_) = v1.Camera.screen((tx0, ty1), cam), v1.Camera.screen((tx1, ty0), cam)
        ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(ov).rectangle([ax, ay, bx_, by_], outline=GOLD + (int(200 * a),), width=2)
        frame.alpha_composite(ov)

    draw_coasts(frame, cam, t)

    if t > 13.5:                                        # place labels in the last shot
        a = ease_out((t - 13.6) / 0.6)
        x, y = v1.Camera.screen(G['by_name']['那覇市']['rp'], cam)
        paste(frame, G['plates']['那覇市_end'], x, y, a)
        x, y = v1.Camera.screen((G['hb'].bounds[2], G['hb_rp'][1]), cam)
        paste(frame, G['plates']['南風原町'], x + G['plates']['南風原町'].width / 2 - 4, y, a)

    frame.alpha_composite(G['vig'])

    if 2.3 < t < 8.9:                                   # legend: fade, bar wipe, marker pop
        a = ease_out((t - 2.3) / 0.4) * (1 - smooth((t - 8.4) / 0.45))
        spr, bar, (bx, by), marker = G['legend']
        lx, ly = (W - spr.width) // 2, 1385
        paste(frame, spr, lx, ly, a, anchor='tl')
        paste(frame, wipe(bar, power2_inout((t - 2.4) / 0.7), feather=40), lx + bx, ly + by, a, anchor='tl')
        if t > 3.0:
            paste(frame, marker, lx, ly, a * ease_out((t - 3.0) / 0.35), anchor='tl')
    paste(frame, G['credit'], 56, 1572, ease_out((t - 0.3) / 0.6), anchor='tl')

    for st, en, y, spr in G['caps']:
        if not (st - 0.45 <= t < en + 0.001):
            continue
        ws = -0.45 if st == 0 else st
        e = power2_inout((t - ws) / 0.5)
        a_out = 1.0 if en >= DUR else 1 - smooth((t - (en - 0.18)) / 0.18)
        paste(frame, wipe(spr, e), W / 2, y, a_out)

    out = frame.convert('RGB')
    if t > DUR - 0.45:
        k = 1 - smooth((t - (DUR - 0.45)) / 0.45)
        out = Image.eval(out, lambda v: int(v * k))
    return out.tobytes()


class Camera(v1.Camera):
    """v1 camera, but S5/S6 pull back further so the neighbours' coasts are on screen."""

    def __call__(self, t):
        if t < 9.0:
            return super().__call__(t)
        if t < 13.2:
            return self.anchored(self.pt, v1.loglerp(7.8, 17.0, smooth((t - 9.0) / 4.2)))
        e = smooth((t - 13.2) / 3.3)
        return self.anchored((lerp(540, 700, e), self.pt[1]), v1.loglerp(17.0, 22.0, e))


def setup(n03, boundary):
    geoms, hb = v1.load(n03, boundary)
    print('  municipalities…', flush=True)
    munis = municipalities(n03)
    cam = Camera(hb)
    regions = {}
    for i in range(NF):
        c = cam(i / FPS)
        ex = v1.frame_extent(c, pad=8)
        ex = (ex[0] - 6000, ex[1] - 6000, ex[2] + 6000, ex[3] + 6000)
        for L, _ in v1.level_weights(c[2]):
            r = regions.get(L)
            regions[L] = ex if r is None else (min(r[0], ex[0]), min(r[1], ex[1]),
                                               max(r[2], ex[2]), max(r[3], ex[3]))
    levels = {}
    for L, r in sorted(regions.items()):
        levels[L] = (*render_sea_level(L, r, geoms), r)
        print(f'  sea level {L:>4} m/px  {levels[L][0].size}', flush=True)
    hc = (hb.centroid.x, hb.centroid.y)
    ov = box(*v1.frame_extent(cam(0), pad=0))
    dmax = max(math.dist(m['c'], hc) for m in munis if box(*m['bounds']).intersects(ov))
    lo = min(m['share'] for m in munis if m['code'] != '47350')
    by_name = {m['name']: m for m in munis}
    rp = hb.representative_point()
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    glint = 0.35 + 0.9 * np.exp(-(((xx - 860) / 520) ** 2 + ((yy - 240) / 700) ** 2))
    v1.G['noise'] = v1.make_noise()
    plates = {n: plate(n, CYAN, 32) for n in NEIGHBOURS}
    plates['那覇市_end'] = plate('那覇市', GOLD, 38)
    plates['南風原町'] = plate('南風原町', GOLD, 38)
    G.update(cam=cam, hb=hb, hc=hc, hb_rp=(rp.x, rp.y), munis=munis, by_name=by_name, dmax=dmax, lo=lo,
             levels=levels, sky=v1.sky(), vig=v1.vignette(), caps=v1.captions(), glint=glint * 0.2,
             plates=plates, legend=legend(lo), credit=credit())
    print(f'  {len(munis)} municipalities, lowest non-Haebaru sea share {lo * 100:.1f}%', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n03', required=True)
    ap.add_argument('--boundary', required=True)
    ap.add_argument('--bgm')
    ap.add_argument('--out', default='.')
    a = ap.parse_args()
    mp4 = os.path.join(a.out, 'haebaru_short_v2.mp4')
    print('setup', flush=True)
    setup(a.n03, a.boundary)
    with tempfile.TemporaryDirectory() as td:
        audio = []
        if a.bgm:
            wav = os.path.join(td, 'bgm.wav')
            lufs, gain = v1.make_audio(a.bgm, wav)
            print(f'bgm segment {v1.BGM_START}s +{DUR}s: {lufs:.1f} LUFS -> gain {gain:+.1f} dB', flush=True)
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
    v1.contact_sheet(mp4, os.path.join(a.out, 'haebaru_short_v2_qa.jpg'),
                     times=(0.0, 1.0, 2.2, 3.3, 7.6, 10.8))
    print('done', mp4)


if __name__ == '__main__':
    main()
