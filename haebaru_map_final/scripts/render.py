import json, sys
from shapely.geometry import shape, box
from shapely.ops import transform as stransform
from pyproj import Transformer
from PIL import Image, ImageDraw, ImageFilter, ImageFont

src, hb_path, out = sys.argv[1:4]
SS = 2                                   # supersampling
W, H = 1080, 1920
tr = Transformer.from_crs(6668, 6689, always_xy=True)   # JGD2011 -> JGD2011 / UTM zone 52N
proj = lambda g: stransform(lambda x, y, z=None: tr.transform(x, y), g)

feats = json.load(open(src, encoding='utf-8'))['features']
hb = proj(shape(json.load(open(hb_path, encoding='utf-8'))['features'][0]['geometry']))

# Map windows (lon/lat) -> projected
def win(lon0, lat0, lon1, lat1):
    return proj(box(lon0, lat0, lon1, lat1)).bounds
OV = win(127.62, 26.06, 128.36, 26.89)          # Okinawa main island
hx0, hy0, hx1, hy1 = hb.bounds
cx, cy = (hx0 + hx1) / 2, (hy0 + hy1) / 2

# Panels in output pixels
OV_PANEL = (0, 70, W, 1110)
ZM_PANEL = (60, 1190, W - 60, 1860)

def fit(bounds, panel, pad=0):
    x0, y0, x1, y1 = bounds; px0, py0, px1, py1 = panel
    s = min((px1 - px0 - 2 * pad) / (x1 - x0), (py1 - py0 - 2 * pad) / (y1 - y0))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    ox, oy = (px0 + px1) / 2, (py0 + py1) / 2
    return lambda x, y: ((ox + (x - mx) * s) * SS, (oy - (y - my) * s) * SS), s

f_ov, s_ov = fit(OV, OV_PANEL)
# Zoom extent: Haebaru height + margin, panel aspect
zw, zh = ZM_PANEL[2] - ZM_PANEL[0], ZM_PANEL[3] - ZM_PANEL[1]
half_h = (hy1 - hy0) / 2 * 1.32
half_w = half_h * zw / zh
ZM = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
f_zm, s_zm = fit(ZM, ZM_PANEL)

region = box(OV[0] - 2000, OV[1] - 2000, OV[2] + 2000, OV[3] + 2000)
geoms = []
for f in feats:
    g = shape(f['geometry'])
    lon0, lat0, lon1, lat1 = g.bounds
    if lon1 < 127.55 or lon0 > 128.45 or lat1 < 26.0 or lat0 > 26.95: continue
    geoms.append(proj(g))

def polys(g):
    return list(g.geoms) if g.geom_type == 'MultiPolygon' else [g]

SEA_TOP, SEA_BOT = (8, 22, 38), (4, 12, 24)
LAND = (38, 52, 49)
LAND_EDGE = (78, 96, 92)
COAST = (120, 160, 175)
GOLD = (236, 196, 92)

img = Image.new('RGB', (W * SS, H * SS))
d = ImageDraw.Draw(img)
for y in range(H * SS):                          # sea gradient
    t = y / (H * SS)
    d.line([(0, y), (W * SS, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(SEA_TOP, SEA_BOT)))

def draw_map(f, clip_panel, land_w, coast_w):
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for g in geoms:                              # coastline: pale stroke left visible outside land
        for p in polys(g):
            ld.line([f(*c) for c in p.exterior.coords], fill=COAST + (255,), width=coast_w * 2)
    for g in geoms:
        for p in polys(g):
            ld.polygon([f(*c) for c in p.exterior.coords], fill=LAND + (255,))
    # municipal edges (faint) then coastline from outer land edges
    for g in geoms:
        for p in polys(g):
            ld.line([f(*c) for c in p.exterior.coords], fill=LAND_EDGE + (255,), width=land_w)
    mask = Image.new('L', img.size, 0)
    ImageDraw.Draw(mask).rectangle([v * SS for v in clip_panel], fill=255)
    img.paste(layer, (0, 0), Image.composite(layer.getchannel('A'), Image.new('L', img.size, 0), mask))
    return mask

def gold(f, clip_mask, line_w, fill_a, glow_r, glow_a):
    pts = [f(*c) for c in hb.exterior.coords]
    fill = Image.new('RGBA', img.size, (0, 0, 0, 0))
    ImageDraw.Draw(fill).polygon(pts, fill=GOLD + (fill_a,))
    glow = Image.new('RGBA', img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts, fill=GOLD + (glow_a,), width=line_w * 3, joint='curve')
    glow = glow.filter(ImageFilter.GaussianBlur(glow_r))
    line = Image.new('RGBA', img.size, (0, 0, 0, 0))
    ImageDraw.Draw(line).line(pts, fill=GOLD + (255,), width=line_w, joint='curve')
    for lay in (fill, glow, line):
        a = Image.composite(lay.getchannel('A'), Image.new('L', img.size, 0), clip_mask)
        lay.putalpha(a); img.paste(lay, (0, 0), lay)

# Overview
m_ov = draw_map(f_ov, OV_PANEL, 1, 1)
gold(f_ov, m_ov, 2 * SS // 2 + 1, 200, 6, 120)
# Zoom
m_zm = draw_map(f_zm, ZM_PANEL, 2, 2)
gold(f_zm, m_zm, 3 * SS // 2 + 1, 38, 10, 90)

# Zoom-extent box on overview + connector lines + zoom frame
ov_box = [f_ov(ZM[0], ZM[3]), f_ov(ZM[2], ZM[1])]
ov_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
od = ImageDraw.Draw(ov_layer)
od.rectangle(ov_box, outline=GOLD + (170,), width=2 * SS // 2 + 1)
zx0, zy0, zx1, zy1 = [v * SS for v in ZM_PANEL]
od.line([(ov_box[0][0], ov_box[1][1]), (zx0, zy0)], fill=GOLD + (90,), width=2)
od.line([(ov_box[1][0], ov_box[1][1]), (zx1, zy0)], fill=GOLD + (90,), width=2)
od.rectangle([zx0, zy0, zx1, zy1], outline=GOLD + (140,), width=2)
img.paste(ov_layer, (0, 0), ov_layer)

# Label (only text): 南風原町, centred on Haebaru in zoom
from shapely.geometry import Point
FS = 56
font = ImageFont.truetype('/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf', FS * SS)
l, t, r, b = font.getbbox('南風原町', anchor='mm')
lw, lh = (r - l) / SS / s_zm, (b - t) / SS / s_zm          # label size in metres
inner = hb.buffer(-12 / s_zm)                              # 12 px margin from boundary
rp = hb.representative_point(); best = None
x0_, y0_, x1_, y1_ = hb.bounds; step = 20
for i in range(int((x1_ - x0_) / step)):
    for j in range(int((y1_ - y0_) / step)):
        x, y = x0_ + i * step, y0_ + j * step
        if inner.contains(box(x - lw / 2, y - lh / 2, x + lw / 2, y + lh / 2)):
            dd = Point(x, y).distance(hb.centroid)
            if best is None or dd < best[0]: best = (dd, x, y)
assert best, 'label does not fit inside Haebaru'
lx, ly = f_zm(best[1], best[2])
lab = Image.new('RGBA', img.size, (0, 0, 0, 0))
ImageDraw.Draw(lab).text((lx, ly), '南風原町', font=font, anchor='mm', fill=(0, 0, 0, 200))
lab = lab.filter(ImageFilter.GaussianBlur(6))
img.paste(lab, (0, 0), lab)
ImageDraw.Draw(img).text((lx, ly), '南風原町', font=font, anchor='mm', fill=(250, 238, 205))

img = img.resize((W, H), Image.LANCZOS)
img.save(out, optimize=True)
print('saved', out, img.size, 'overview m/px %.1f zoom m/px %.2f' % (1 / s_ov, 1 / s_zm))
print('zoom extent (UTM52):', [round(v) for v in ZM])
