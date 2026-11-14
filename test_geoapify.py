import os, sys, json
os.environ.update(GEOAPIFY_API_KEY="k", DB_PATH="/tmp/wk_geo.db")
if os.path.exists("/tmp/wk_geo.db"): os.remove("/tmp/wk_geo.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asyncio, httpx, respx
from app import sources
def feat(name, cats, lat, lon, raw=None, pid="x"):
    return {"type":"Feature","properties":{"name":name,"categories":cats,"lat":lat,"lon":lon,"street":"Main St","housenumber":"1","city":"Austin","place_id":pid,"datasource":{"sourcename":"openstreetmap","raw":raw or {}}}}
def handler(req):
    from urllib.parse import unquote; q = unquote(str(req.url))
    if "catering" in q:
        fs=[feat("Taco Joint",["catering","catering.restaurant","catering.restaurant.mexican"],30.265,-97.76,{"cuisine":"mexican","highchair":"yes","osm_id":1},"a1"),
            feat("Lick",["catering","catering.ice_cream"],30.268,-97.75,{},"a2")]
    else:
        fs=[feat("Zilker Park",["leisure","leisure.park"],30.266,-97.77,{},"b1"),
            feat("Pease Playground",["leisure","leisure.playground","leisure.park"],30.28,-97.75,{},"b2"),
            feat("Thinkery",["entertainment","entertainment.museum"],30.27,-97.74,{"fee":"yes"},"b3"),
            feat("No cat",["building"],30.27,-97.74,{},"b4")]
    assert "circle:-97.74,30.27," in q, q
    return httpx.Response(200, json={"type":"FeatureCollection","features":fs})
with respx.mock(assert_all_called=False) as r:
    r.get(url__startswith="https://api.geoapify.com").mock(side_effect=handler)
    ov = r.post(url__startswith="https://overpass").mock(return_value=httpx.Response(502))
    res = asyncio.run(sources.places(30.2672,-97.7431,15))
    for x in res: print(x["kind"], x["name"], x["category"], x["indoor"], x["kid_friendly"], x["price_per_person"], x["address"], x["id"])
    assert ov.call_count == 0 and len(res) == 5
    assert {x["name"]:x["category"] for x in res}["Pease Playground"] == "Playground"
    # cached second call -> no new http
    n = r.calls.call_count; asyncio.run(sources.places(30.2672,-97.7431,15)); assert r.calls.call_count == n
# geoapify fails -> overpass fallback; empty -> not cached
os.remove("/tmp/wk_geo.db") if False else None
with respx.mock(assert_all_called=False) as r:
    r.get(url__startswith="https://api.geoapify.com").mock(return_value=httpx.Response(401, json={"message":"Invalid apiKey"}))
    ov = r.post(url__startswith="https://overpass").mock(return_value=httpx.Response(502, text="busy"))
    res = asyncio.run(sources.places(40.0,-100.0,15)); assert res == [] and ov.call_count == 2
    print("fallback tried overpass on both servers, empty not cached:", sources.cache_get("osm:40.0:-100.0:24135"))
print("GEO OK")
