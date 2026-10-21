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
SHALLOW = (40, 178, 196)          # soft coastal glow (style only; geometry untouched)
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
    halo = Image.new('L', img.size, 0)                  # turquoise shallows glow hugging the coast
    dh = ImageDraw.Draw(halo)
    for g in sel:
        dh.line(f(g.exterior.coords), fill=255, width=int(ss * 12))
    halo = halo.filter(ImageFilter.GaussianBlur(ss * 6)).point(lambda v: int(min(255, v * 1.6) * 0.62))
    img.paste(Image.new('RGBA', img.size, SHALLOW + (255,)), (0, 0), halo)
    for g in sel:                                       # coastline: stroke left outside the land
        d.line(f(g.exterior.coords), fill=COAST + (220,), width=4)
    for g in sel:
        d.polygon(f(g.exterior.coords), fill=LAND + (255,))
    for g in sel:                                       # faint municipal edges
        d.line(f(g.exterior.coords), fill=LAND_EDGE + (255,), width=2)
    return img.resize((w, h), Image.LANCZOS)


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


def background():
    bg = Image.new('RGB', (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=tuple(int(lerp(a, b, y / H)) for a, b in zip(SEA_TOP, SEA_BOT)))
    return bg


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
    if 6.0 < t < 8.2:                                   # boundary moment: a touch more glow
        glow_a *= 1 + 0.45 * math.sin(math.pi * clamp((t - 6.0) / 2.2))
    return fill_a, lw, glow_a, glow_r


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
    frame = G['bg'].copy().convert('RGBA')
    x0, y0, x1, y1 = frame_extent(cam, pad=0)
    layer = None
    for L, w in level_weights(s):
        img, (rx0, ry0, rx1, ry1) = G['levels'][L]
        bx = ((x0 - rx0) / L, (ry1 - y1) / L, (x1 - rx0) / L, (ry1 - y0) / L)
        crop = img.resize((W, H), Image.LANCZOS if s / L > 1.05 else Image.BICUBIC, box=bx)
        layer = crop if layer is None else Image.blend(layer, crop, w)
    frame.alpha_composite(layer)

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
        for L, _ in level_weights(c[2]):
            r = regions.get(L)
            regions[L] = ex if r is None else (min(r[0], ex[0]), min(r[1], ex[1]),
                                               max(r[2], ex[2]), max(r[3], ex[3]))
    levels = {}
    for L, r in sorted(regions.items()):
        levels[L] = (render_level(L, r, geoms), r)
        print(f'  level {L:>4} m/px  {levels[L][0].size}', flush=True)
    rp = hb.representative_point()
    G.update(cam=cam, hb=hb, levels=levels, bg=background(), vig=vignette(), caps=captions(),
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
    mp4 = os.path.join(a.out, 'haebaru_short_final.mp4')
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
    contact_sheet(mp4, os.path.join(a.out, 'haebaru_short_qa.jpg'))
    print('done', mp4)


if __name__ == '__main__':
    main()
