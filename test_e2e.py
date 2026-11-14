import json, os, sys, tempfile
DB = os.path.join(tempfile.gettempdir(), "wk_test.db")
from datetime import date, timedelta
os.environ.update(GEMINI_API_KEY="test", TICKETMASTER_API_KEY="test", GEOAPIFY_API_KEY="", DB_PATH=DB)
if os.path.exists(DB): os.remove(DB)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import httpx, respx
from fastapi.testclient import TestClient
from app.main import app

DAY = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 or 7)
LAT, LNG = 30.2672, -97.7431

def geo(): return httpx.Response(200, json={"results":[{"name":"Austin","admin1":"Texas","admin1_code":"TX","latitude":LAT,"longitude":LNG}]})
def wx(rain=10, code=1): return httpx.Response(200, json={"daily":{"weather_code":[code],"temperature_2m_max":[72],"temperature_2m_min":[55],"precipitation_probability_max":[rain]}})
def tm(req):
    fam = "classificationName" in str(req.url)
    evs = [{"id":"E1" if fam else "E2","name":"Kids Science Show" if fam else "Comedy Night 21+","url":"https://tm/e",
            "dates":{"start":{"localDate":DAY.isoformat(),"localTime":"14:00:00"}},
            "priceRanges":[{"min":12.0,"max":30}],
            "classifications":[{"segment":{"name":"Family"},"genre":{"name":"Children's Theatre"}}],
            "_embedded":{"venues":[{"name":"Long Center","address":{"line1":"701 W Riverside"},"city":{"name":"Austin"},"location":{"latitude":"30.259","longitude":"-97.751"}}]}}]
    return httpx.Response(200, json={"_embedded":{"events":evs}})
def osm(): return httpx.Response(200, json={"elements":[
    {"type":"way","id":1,"center":{"lat":30.266,"lon":-97.77},"tags":{"leisure":"park","name":"Zilker Park"}},
    {"type":"node","id":2,"lat":30.27,"lon":-97.74,"tags":{"tourism":"museum","name":"Thinkery","fee":"yes"}},
    {"type":"node","id":3,"lat":30.28,"lon":-97.73,"tags":{"amenity":"library","name":"Central Library"}},
    {"type":"node","id":4,"lat":30.265,"lon":-97.76,"tags":{"amenity":"restaurant","name":"Taco Joint","cuisine":"mexican","highchair":"yes"}},
    {"type":"node","id":5,"lat":30.268,"lon":-97.75,"tags":{"amenity":"ice_cream","name":"Lick"}},
    {"type":"node","id":6,"lat":30.27,"lon":-97.745,"tags":{"amenity":"fast_food","name":"Burger Place"}},
    {"type":"node","id":7,"lat":30.27,"lon":-97.745,"tags":{"leisure":"playground"}},  # unnamed -> skipped
]})
def gemini_ok(req):
    body = json.loads(req.content)
    assert "Zilker Park" in body["contents"][0]["parts"][0]["text"]
    out = {"title":"Science & Sunshine Saturday","summary":"Park, tacos, then a kids show.","tip":"Bring water.",
           "items":[{"id":"osm_w1","start":"09:30","end":"11:30","why":"Big open space for an 8-year-old."},
                    {"id":"osm_FAKE","start":"11:00","end":"12:00","why":"hallucinated"},
                    {"id":"osm_n4","start":"12:00","end":"13:00","why":"Highchairs for the 4-year-old."},
                    {"id":"ev_E1","start":"14:00","end":"16:00","why":"Science show for both kids."}]}
    return httpx.Response(200, json={"candidates":[{"content":{"parts":[{"text":json.dumps(out)}]}}]})

def setup(router, gemini):
    router.get(url__startswith="https://geocoding-api.open-meteo.com").mock(return_value=geo())
    router.get(url__startswith="https://api.open-meteo.com").mock(return_value=wx())
    router.get(url__startswith="https://app.ticketmaster.com").mock(side_effect=tm)
    router.post(url__startswith="https://overpass-api.de").mock(return_value=osm())
    return router.post(url__startswith="https://generativelanguage.googleapis.com").mock(side_effect=gemini)

body = {"location":"Austin, TX","date":DAY.isoformat(),"budget":120,"adults":2,"kids_ages":[4,8],"vibe":"any","max_miles":15,"notes":""}
c = TestClient(app)

# 1) AI path
with respx.mock(assert_all_called=False) as r:
    g = setup(r, gemini_ok)
    res = c.post("/api/plan", json=body); assert res.status_code == 200, res.text
    p = res.json()
    names = [i["place"]["name"] for i in p["items"]]
    print("AI plan:", p["title"], names, "total", p["total_cost"], "ai", p["ai_used"])
    assert p["ai_used"] and "osm_FAKE" not in [i["id"] for i in p["items"]]
    assert "Comedy Night 21+" not in json.dumps(p)  # adult event filtered out
    assert p["total_cost"] == 4*12 + 4*15 + 0, p["total_cost"]  # event 12pp*4 + restaurant est 15*4
    assert "pool" not in p
    # cached: second identical call must not hit gemini again
    calls = g.call_count; c.post("/api/plan", json=body); assert g.call_count == calls, "AI cache miss"
    pid = p["id"]

# 2) swap + shared page + get
with respx.mock(assert_all_called=False):
    s = c.post(f"/api/plan/{pid}/swap/0"); assert s.status_code == 200, s.text
    print("after swap:", [i["place"]["name"] for i in s.json()["items"]])
    assert s.json()["items"][0]["place"]["name"] != "Zilker Park"
    page = c.get(f"/p/{pid}"); assert page.status_code == 200 and "Famigo.renderPlan" in page.text
    assert c.get("/p/nope").status_code == 404
    assert c.get("/").status_code == 200

# 3) Gemini 429 on all models -> rule-based fallback
body2 = {**body, "budget": 119}  # different key -> no AI cache
with respx.mock(assert_all_called=False) as r:
    g = setup(r, lambda req: httpx.Response(429, json={"error":"quota"}))
    res = c.post("/api/plan", json=body2); assert res.status_code == 200, res.text
    p = res.json(); print("Fallback:", [(i["start"], i["place"]["name"]) for i in p["items"]], "total", p["total_cost"])
    assert not p["ai_used"] and any(i["place"]["kind"]=="food" for i in p["items"])
    assert g.call_count == 2  # tried both models

# 4) rain -> indoor first in fallback
body3 = {**body, "budget": 118}
with respx.mock(assert_all_called=False) as r:
    setup(r, lambda req: httpx.Response(500))
    r.get(url__startswith="https://api.open-meteo.com").mock(return_value=wx(rain=90, code=63))
    p = c.post("/api/plan", json=body3).json()
    print("Rainy:", [(i["place"]["name"], i["place"]["indoor"]) for i in p["items"]])
    assert p["items"][0]["place"]["indoor"] is True

# 5) validation + unknown city
assert c.post("/api/plan", json={**body, "date":"2020-01-01"}).status_code == 422
assert c.post("/api/plan", json={**body, "kids_ages":[40]}).status_code == 422
with respx.mock(assert_all_called=False) as r:
    r.get(url__startswith="https://geocoding-api.open-meteo.com").mock(return_value=httpx.Response(200, json={}))
    r.get(url__startswith="https://nominatim").mock(return_value=httpx.Response(200, json=[]))
    res = c.post("/api/plan", json={**body, "location":"Nowhereville"}); print("unknown city:", res.status_code, res.json()["detail"])
    assert res.status_code == 422
print("ALL OK")

# 6) AI ignores budget -> backend enforces it
def gemini_over(req):
    out = {"title":"Pricey day","summary":"s","tip":"t","items":[
        {"id":"osm_n2","start":"10:00","end":"11:30","why":"museum"},   # Thinkery 12pp -> 48
        {"id":"osm_n4","start":"12:00","end":"13:00","why":"lunch"},    # Taco 15pp -> 60
        {"id":"osm_n5","start":"14:00","end":"14:30","why":"treat"}]}  # Lick 5pp -> 20  => 128
    return httpx.Response(200, json={"candidates":[{"content":{"parts":[{"text":json.dumps(out)}]}}]})
body4 = {**body, "budget": 60}
with respx.mock(assert_all_called=False) as r:
    setup(r, gemini_over)
    p = c.post("/api/plan", json=body4).json()
    print("Budget-enforced:", [(i["place"]["name"], i["family_cost"]) for i in p["items"]], "total", p["total_cost"], "of", 60)
    assert p["ai_used"] and p["total_cost"] <= 60, p["total_cost"]
    assert any(i["place"]["kind"] == "food" for i in p["items"])

# 7) swap never pushes total over budget
with respx.mock(assert_all_called=False):
    for n in range(len(p["items"])):
        for _ in range(4):
            s = c.post(f"/api/plan/{p['id']}/swap/{n}")
            if s.status_code != 200: break
            assert s.json()["total_cost"] <= 60, s.json()["total_cost"]
    print("swaps within budget:", [(i["place"]["name"], i["family_cost"]) for i in s.json().get("items", [])] if s.status_code == 200 else s.status_code)

# 8) fallback: museum+lunch doesn't fit 61 -> lunch still included
with respx.mock(assert_all_called=False) as r:
    setup(r, lambda req: httpx.Response(429))
    p = c.post("/api/plan", json={**body, "budget": 61}).json()
    print("Tight fallback:", [(i["place"]["name"], i["family_cost"]) for i in p["items"]], "total", p["total_cost"])
    assert any(i["place"]["kind"] == "food" for i in p["items"]) and p["total_cost"] <= 61

# 9) budget 0 -> free day, no failure
with respx.mock(assert_all_called=False) as r:
    setup(r, lambda req: httpx.Response(429))
    res = c.post("/api/plan", json={**body, "budget": 0}); assert res.status_code == 200, res.text
    p = res.json(); print("Free day:", [i["place"]["name"] for i in p["items"]], p["tip"])
    assert p["total_cost"] == 0 and all(i["family_cost"] == 0 for i in p["items"])

# 10) AI plan can't fit the budget -> rule plan, ai_used False
def gemini_two_meals(req):
    out = {"title":"Food day","summary":"s","tip":"t","items":[
        {"id":"osm_n6","start":"12:00","end":"13:00","why":"lunch"},   # Burger 36
        {"id":"osm_n5","start":"13:30","end":"14:00","why":"treat"}]}  # Lick 20 => 56 > 40
    return httpx.Response(200, json={"candidates":[{"content":{"parts":[{"text":json.dumps(out)}]}}]})
with respx.mock(assert_all_called=False) as r:
    setup(r, gemini_two_meals)
    p = c.post("/api/plan", json={**body, "budget": 40}).json()
    print("AI replaced:", [i["place"]["name"] for i in p["items"]], "ai", p["ai_used"], p["model"])
    assert p["ai_used"] is False and p["model"] is None and p["total_cost"] <= 40
print("ALL OK 2")
