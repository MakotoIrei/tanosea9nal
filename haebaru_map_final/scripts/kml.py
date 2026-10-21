import json, sys
fc = json.load(open(sys.argv[1], encoding='utf-8')); f = fc['features'][0]; g = f['geometry']
polys = [g['coordinates']] if g['type'] == 'Polygon' else g['coordinates']
def ring(r): return ' '.join(f'{x:.9f},{y:.9f},0' for x, y in r)
pm = []
for p in polys:
    inner = ''.join(f'<innerBoundaryIs><LinearRing><coordinates>{ring(r)}</coordinates></LinearRing></innerBoundaryIs>' for r in p[1:])
    pm.append(f'<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(p[0])}</coordinates></LinearRing></outerBoundaryIs>{inner}</Polygon>')
geom = pm[0] if len(pm) == 1 else '<MultiGeometry>' + ''.join(pm) + '</MultiGeometry>'
ext = ''.join(f'<Data name="{k}"><value>{v if v is not None else ""}</value></Data>' for k, v in f['properties'].items())
kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>南風原町 行政区域</name>
<description>出典：国土数値情報（行政区域データ N03-20260101_47）国土交通省</description>
<Style id="gold"><LineStyle><color>ff5cc4ec</color><width>2</width></LineStyle><PolyStyle><color>335cc4ec</color></PolyStyle></Style>
<Placemark><name>南風原町</name><styleUrl>#gold</styleUrl><ExtendedData>{ext}</ExtendedData>{geom}</Placemark>
</Document></kml>'''
open(sys.argv[2], 'w', encoding='utf-8').write(kml)
print('kml ok', len(polys), 'polygon(s)', sum(len(p[0]) for p in polys), 'vertices')
