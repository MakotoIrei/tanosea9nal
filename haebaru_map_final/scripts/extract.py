import json, sys
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
from pyproj import Geod
src, out = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding='utf-8'))
print('CRS:', d.get('crs')); print('keys:', list(d['features'][0]['properties'].keys()))
print('sample:', d['features'][0]['properties'])
hits = [f for f in d['features'] if f['properties'].get('N03_007') == '47350']
print('hits by code:', len(hits))
for f in hits: print(f['properties'])
ok = [f for f in hits if f['properties']['N03_001']=='沖縄県' and f['properties']['N03_004']=='南風原町']
assert ok and len(ok)==len(hits), 'attribute mismatch'
geom = unary_union([shape(f['geometry']) for f in ok])
print('type:', geom.geom_type, 'valid:', geom.is_valid, 'bounds:', geom.bounds)
g = Geod(ellps='GRS80')
area, perim = g.geometry_area_perimeter(geom)
print(f'geodesic area: {abs(area)/1e6:.4f} km2, perimeter {perim/1000:.2f} km')
props = {k: ok[0]['properties'][k] for k in ok[0]['properties']}
fc = {'type':'FeatureCollection','name':'haebaru_boundary',
      'crs':{'type':'name','properties':{'name':'urn:ogc:def:crs:OGC:1.3:CRS84'}},
      'features':[{'type':'Feature','properties':props,'geometry':mapping(geom)}]}
json.dump(fc, open(out,'w',encoding='utf-8'), ensure_ascii=False)
# coast check: union of all Okinawa polygons -> exterior of island vs Haebaru
allg = [shape(f['geometry']) for f in d['features']]
others = unary_union([shape(f['geometry']) for f in d['features'] if f['properties'].get('N03_007')!='47350'])
island = unary_union([geom] + [a for a in allg if a.intersects(geom.buffer(0.05))])
coast = island.boundary
print('distance Haebaru boundary to coastline (deg):', geom.distance(unary_union([c for c in ([coast] )]).difference(geom.boundary.buffer(1e-7))) if False else '')
from shapely.geometry import Polygon
# Simpler: Haebaru boundary shared length with neighbouring municipalities vs total
shared = geom.boundary.intersection(others.buffer(1e-6))
print(f'boundary shared with neighbours: {shared.length/geom.boundary.length*100:.3f}%')
