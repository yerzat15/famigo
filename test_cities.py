"""Опечатки в названии города: справочник + «Did you mean…?» в API. Внешние API замоканы."""
import json, os, sys, tempfile
from datetime import date, timedelta
DB = os.path.join(tempfile.gettempdir(), "wk_cities.db")
os.environ.update(GEMINI_API_KEY="", TICKETMASTER_API_KEY="", GEOAPIFY_API_KEY="", DB_PATH=DB)
if os.path.exists(DB): os.remove(DB)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import httpx, respx
from fastapi.testclient import TestClient
from app import cities
from app.main import app

# 1) Справочник: опечатка -> правильный город первым вариантом
TOP1 = {
    "Austn, TX": "Austin, TX", "Austn": "Austin, TX", "Seatle, WA": "Seattle, WA", "Phoenx, AZ": "Phoenix, AZ",
    "Miama, FL": "Miami, FL", "Atlnta GA": "Atlanta, GA", "Chicgo": "Chicago, IL", "Sant Francisco": "San Francisco, CA",
    "Los Angelos": "Los Angeles, CA", "New Yrok": "New York, NY", "Nashvile": "Nashville, TN", "Denvr": "Denver, CO",
    "Bostn, MA": "Boston, MA", "Pittsburg, PA": "Pittsburgh, PA", "Sacremento": "Sacramento, CA",
    "Albequerque": "Albuquerque, NM", "Cincinatti": "Cincinnati, OH", "Mineapolis": "Minneapolis, MN",
    "Tuscon": "Tucson, AZ", "Las Vega": "Las Vegas, NV", "san deigo": "San Diego, CA", "Philadelpia": "Philadelphia, PA",
    "Houstan": "Houston, TX", "Dalas, TX": "Dallas, TX", "Orlnado": "Orlando, FL", "Portlnd, OR": "Portland, OR",
    "Portlnd, ME": "Portland, ME", "Springfeld, IL": "Springfield, IL", "Kansas Cty, MO": "Kansas City, MO",
    "Salt Lake": "Salt Lake City, UT", "Charlote, NC": "Charlotte, NC", "Raliegh": "Raleigh, NC",
    "Milwakee": "Milwaukee, WI", "Indianapols": "Indianapolis, IN", "Jacksonvile": "Jacksonville, FL",
    "Oklahoma Cty": "Oklahoma City, OK", "Brooklin": "Brooklyn, NY", "austn texas": "Austin, TX",
    "Austin, Texs": "Austin, TX",  # опечатка в штате: штат не распознан -> ищем по городу
    "  AUSTN ,  tx ": "Austin, TX",
}
bad = {q: (cities.suggest(q) or [{"query": None}])[0]["query"] for q in TOP1}
bad = {q: got for q, got in bad.items() if got != TOP1[q]}
print(f"typos: {len(TOP1) - len(bad)}/{len(TOP1)} top-1 correct", bad or "")
assert not bad, bad
for q in ("xqzzy", "Nowhereville", "Ny", "", "12", "!!!"):
    assert cities.suggest(q) == [], (q, cities.suggest(q))
assert cities.parse("austin texas") == ("austin", "TX") and cities.parse("Austin, tx") == ("austin", "TX")
assert cities.parse("New York") == ("new york", None) and cities.parse("Albany new york") == ("albany", "NY")
assert cities.exact("austin texas")["label"] == "Austin, Texas" and cities.exact("Austn, TX") is None
assert cities.exact("Portland, ME")["label"] == "Portland, Maine"

# 2) API: опечатка -> 422 с вариантами; выбранный вариант строит план
DAY = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 or 7)
body = {"location": "Austn, TX", "date": DAY.isoformat(), "budget": 100, "adults": 2, "kids_ages": [5],
        "vibe": "any", "max_miles": 10, "notes": ""}
OM = [{"name": "Portland", "admin1": "Oregon", "latitude": 45.52, "longitude": -122.68},
      {"name": "Portland", "admin1": "Maine", "latitude": 43.66, "longitude": -70.26},
      {"name": "Austin", "admin1": "Texas", "latitude": 30.27, "longitude": -97.74}]

def om(req):  # имитация Open-Meteo: точное совпадение имени, как в реальном API
    name = req.url.params["name"].strip().lower()
    return httpx.Response(200, json={"results": [x for x in OM if x["name"].lower() == name]})

osm = httpx.Response(200, json={"elements": [
    {"type": "node", "id": 1, "lat": 30.27, "lon": -97.75, "tags": {"leisure": "park", "name": "Zilker Park"}},
    {"type": "node", "id": 2, "lat": 30.27, "lon": -97.74, "tags": {"amenity": "library", "name": "Central Library"}},
    {"type": "node", "id": 3, "lat": 30.26, "lon": -97.74, "tags": {"amenity": "restaurant", "name": "Taco Joint"}}]})
wx = httpx.Response(200, json={"daily": {"weather_code": [1], "temperature_2m_max": [70], "temperature_2m_min": [50],
                                         "precipitation_probability_max": [0]}})
c = TestClient(app)
with respx.mock(assert_all_called=False) as r:
    r.get(url__startswith="https://geocoding-api.open-meteo.com").mock(side_effect=om)
    r.get(url__startswith="https://api.open-meteo.com").mock(return_value=wx)
    r.post(url__startswith="https://overpass").mock(return_value=osm)
    nom = r.get(url__startswith="https://nominatim").mock(return_value=httpx.Response(200, json=[]))

    res = c.post("/api/plan", json=body)
    d = res.json()["detail"]
    print("typo ->", res.status_code, d)
    assert res.status_code == 422 and d["suggestions"][0] == "Austin, TX" and "Did you mean" in d["message"]
    assert nom.call_count == 0  # Nominatim только для ZIP

    res = c.post("/api/plan", json={**body, "location": d["suggestions"][0]})
    assert res.status_code == 200, res.text
    assert res.json()["location"]["label"] == "Austin, Texas"

    # без запятой и с полным названием штата — находит сразу, без вопроса
    res = c.post("/api/plan", json={**body, "location": "austin texas"})
    assert res.status_code == 200 and res.json()["location"]["label"] == "Austin, Texas", res.text

    # штат решает, какой Portland: Open-Meteo вернул Oregon первым, но спросили Maine
    from app import sources
    import asyncio
    assert asyncio.run(sources.geocode("Portland, ME"))["label"] == "Portland, Maine"
    assert asyncio.run(sources.geocode("Portland, OR"))["label"] == "Portland, Oregon"

    # совсем не город — понятная ошибка без вариантов
    res = c.post("/api/plan", json={**body, "location": "xqzzy"})
    assert res.status_code == 422 and isinstance(res.json()["detail"], str), res.text
print("CITIES OK")
