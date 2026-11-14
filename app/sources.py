"""Источники реальных данных: геокодинг, погода, события (Ticketmaster), места и еда (OpenStreetMap)."""
import asyncio
import hashlib
import logging
import math
from datetime import date

import httpx

from .cache import cache_get, cache_set
from .config import GEOAPIFY_API_KEY, HTTP_TIMEOUT, OVERPASS_URLS, TICKETMASTER_API_KEY, USER_AGENT

log = logging.getLogger("famigo.sources")

DAY = 24 * 3600


def miles_between(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})


# ---------- Геокодинг: город или ZIP -> координаты ----------

async def geocode(query: str):
    query = query.strip()
    key = f"geo:{query.lower()}"
    if (hit := cache_get(key)) is not None:
        return hit
    result = None
    async with _client() as c:
        if not query.isdigit():
            # Open-Meteo: города по названию, без ключа
            try:
                name = query.split(",")[0].strip()
                r = await c.get("https://geocoding-api.open-meteo.com/v1/search",
                                params={"name": name, "count": 10, "countryCode": "US"})
                results = r.json().get("results") or []
                # если указан штат ("Austin, TX"), предпочитаем совпадение по штату
                state = query.split(",")[1].strip().lower() if "," in query else ""
                if state:
                    results = sorted(results, key=lambda x: state not in (
                        (x.get("admin1") or "").lower() + " " + (x.get("admin1_code") or "").lower()))
                if results:
                    x = results[0]
                    result = {"lat": x["latitude"], "lng": x["longitude"],
                              "label": f'{x["name"]}, {x.get("admin1", "")}'.strip(", ")}
            except Exception as e:  # noqa: BLE001
                log.warning("open-meteo geocode failed: %s", e)
        if result is None:
            # Nominatim: понимает ZIP-коды США
            try:
                r = await c.get("https://nominatim.openstreetmap.org/search",
                                params={"q": query, "countrycodes": "us", "format": "json", "limit": 1})
                data = r.json()
                if data:
                    result = {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"]),
                              "label": data[0]["display_name"].split(",")[0] + (f" ({query})" if query.isdigit() else "")}
            except Exception as e:  # noqa: BLE001
                log.warning("nominatim geocode failed: %s", e)
    if result:
        cache_set(key, result, 30 * DAY)
    return result


# ---------- Погода (Open-Meteo, без ключа) ----------

WEATHER_CODES = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Fog",
    51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 61: "Rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain", 71: "Snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow", 80: "Showers", 81: "Showers", 82: "Heavy showers", 85: "Snow showers",
    86: "Snow showers", 95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}


async def weather(lat: float, lng: float, day: date):
    key = f"wx:{lat:.2f}:{lng:.2f}:{day.isoformat()}"
    if (hit := cache_get(key)) is not None:
        return hit
    try:
        async with _client() as c:
            r = await c.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lng, "timezone": "auto",
                "temperature_unit": "fahrenheit",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "start_date": day.isoformat(), "end_date": day.isoformat(),
            })
            d = r.json()["daily"]
        code = d["weather_code"][0]
        rain = d["precipitation_probability_max"][0] or 0
        hi, lo = d["temperature_2m_max"][0], d["temperature_2m_min"][0]
        result = {
            "summary": WEATHER_CODES.get(code, "Mixed"),
            "high_f": round(hi), "low_f": round(lo), "rain_chance": rain,
            # простое правило «хороший день для улицы»
            "outdoor_ok": rain < 50 and code < 61 and 45 <= hi <= 95,
        }
    except Exception as e:  # noqa: BLE001
        log.warning("weather failed: %s", e)
        return None  # прогноза нет (например, дата дальше 16 дней) — планируем без него
    cache_set(key, result, 3 * 3600)
    return result


# ---------- События (Ticketmaster Discovery API) ----------

async def events(lat: float, lng: float, day: date, radius_miles: int):
    if not TICKETMASTER_API_KEY:
        return []
    key = f"tm:{lat:.2f}:{lng:.2f}:{day.isoformat()}:{radius_miles}"
    if (hit := cache_get(key)) is not None:
        return hit
    base = {
        "apikey": TICKETMASTER_API_KEY, "latlong": f"{lat},{lng}", "radius": radius_miles,
        "unit": "miles", "size": 50, "sort": "date,asc", "countryCode": "US",
        # широкое окно в UTC, точный день фильтруем по localDate ниже
        "startDateTime": f"{day.isoformat()}T00:00:00Z",
        "endDateTime": f"{date.fromordinal(day.toordinal() + 1).isoformat()}T12:00:00Z",
    }
    out, seen = [], set()
    async with _client() as c:
        for extra in ({"classificationName": "family"}, {}):
            try:
                r = await c.get("https://app.ticketmaster.com/discovery/v2/events.json",
                                params={**base, **extra})
                r.raise_for_status()
                items = (r.json().get("_embedded") or {}).get("events") or []
            except Exception as e:  # noqa: BLE001
                log.warning("ticketmaster failed: %s", e)
                continue
            for ev in items:
                parsed = _parse_tm_event(ev, lat, lng, day, family=bool(extra))
                if parsed and parsed["id"] not in seen:
                    seen.add(parsed["id"])
                    out.append(parsed)
            await asyncio.sleep(0.25)  # лимит Ticketmaster — 5 запросов/сек
    cache_set(key, out, 6 * 3600)
    return out


def _parse_tm_event(ev: dict, lat: float, lng: float, day: date, family: bool):
    start = (ev.get("dates") or {}).get("start") or {}
    if start.get("localDate") != day.isoformat():
        return None
    venues = (ev.get("_embedded") or {}).get("venues") or [{}]
    v = venues[0]
    loc = v.get("location") or {}
    try:
        vlat, vlng = float(loc["latitude"]), float(loc["longitude"])
    except (KeyError, TypeError, ValueError):
        vlat, vlng = lat, lng
    prices = ev.get("priceRanges") or []
    pmin = min((p.get("min") for p in prices if p.get("min") is not None), default=None)
    cls = (ev.get("classifications") or [{}])[0]
    genre = " / ".join(x for x in [
        (cls.get("segment") or {}).get("name"), (cls.get("genre") or {}).get("name")] if x and x != "Undefined")
    text = f'{ev.get("name", "")} {genre}'.lower()
    adult_only = bool((ev.get("ageRestrictions") or {}).get("legalAgeEnforced")) or any(
        w in text for w in ("21+", "18+", "comedy", "burlesque"))
    images = sorted(ev.get("images") or [], key=lambda i: i.get("width", 0), reverse=True)
    return {
        "id": f'ev_{ev.get("id")}',
        "kind": "event",
        "name": ev.get("name", "Event"),
        "category": genre or "Event",
        "start_time": (start.get("localTime") or "")[:5] or None,
        "price_per_person": pmin,
        "place": v.get("name", ""),
        "address": ", ".join(x for x in [(v.get("address") or {}).get("line1"),
                                          (v.get("city") or {}).get("name")] if x),
        "lat": vlat, "lng": vlng,
        "distance_mi": round(miles_between(lat, lng, vlat, vlng), 1),
        "indoor": None,
        "family": family or "family" in text or "kids" in text,
        "adult_only": adult_only,
        "url": ev.get("url"),
        "image": images[0]["url"] if images else None,
    }


# ---------- Места и еда (OpenStreetMap через Overpass) ----------

PLACE_RULES = [
    # (тег, значение, категория, indoor, примерная цена на человека)
    ("leisure", "park", "Park", False, 0),
    ("leisure", "playground", "Playground", False, 0),
    ("leisure", "nature_reserve", "Nature area", False, 0),
    ("tourism", "zoo", "Zoo", False, 20),
    ("tourism", "aquarium", "Aquarium", True, 25),
    ("tourism", "museum", "Museum", True, 12),
    ("tourism", "theme_park", "Theme park", False, 45),
    ("amenity", "library", "Library", True, 0),
    ("leisure", "trampoline_park", "Trampoline park", True, 20),
    ("leisure", "bowling_alley", "Bowling", True, 10),
    ("leisure", "ice_rink", "Ice rink", True, 12),
    ("leisure", "miniature_golf", "Mini golf", False, 10),
    ("leisure", "swimming_pool", "Pool", None, 5),
]
FOOD_RULES = [
    ("amenity", "restaurant", "Restaurant", 15),
    ("amenity", "fast_food", "Fast food", 9),
    ("amenity", "cafe", "Cafe", 8),
    ("amenity", "ice_cream", "Ice cream", 5),
]


def _overpass_query(lat, lng, radius_m):
    # отдельные лимиты для мест и для еды, чтобы рестораны не вытеснили парки
    food_radius = min(radius_m, 8000)
    places_q = "".join(f'nwr["{k}"="{v}"]["name"](around:{radius_m},{lat},{lng});' for k, v, *_ in PLACE_RULES)
    food_q = "".join(f'nwr["{k}"="{v}"]["name"](around:{food_radius},{lat},{lng});' for k, v, *_ in FOOD_RULES)
    return (f'[out:json][timeout:25];({places_q});out center 350;'
            f'({food_q});out center 350;')


async def places(lat: float, lng: float, radius_miles: int):
    # места меняются редко — кешируем на неделю; координаты округляем, чтобы кеш срабатывал чаще
    rlat, rlng = round(lat, 2), round(lng, 2)
    radius_m = int(min(radius_miles, 15) * 1609)
    key = f"osm:{rlat}:{rlng}:{radius_m}"
    if (hit := cache_get(key)) is not None:
        return hit
    out = await _places_geoapify(rlat, rlng, radius_m, lat, lng) if GEOAPIFY_API_KEY else []
    if not out:
        out = await _places_overpass(rlat, rlng, radius_m, lat, lng)
    if out:
        cache_set(key, out, 7 * DAY)  # пустой результат НЕ кешируем
    return out


# Geoapify: данные OpenStreetMap через стабильный сервис. Категория Geoapify -> (наш тег, значение)
GEOAPIFY_MAP = [
    ("leisure.playground", ("leisure", "playground")),
    ("leisure.park", ("leisure", "park")),
    ("natural.protected_area", ("leisure", "nature_reserve")),
    ("entertainment.zoo", ("tourism", "zoo")),
    ("entertainment.aquarium", ("tourism", "aquarium")),
    ("entertainment.museum", ("tourism", "museum")),
    ("entertainment.planetarium", ("tourism", "museum")),
    ("entertainment.theme_park", ("tourism", "theme_park")),
    ("entertainment.water_park", ("tourism", "theme_park")),
    ("entertainment.activity_park.trampoline", ("leisure", "trampoline_park")),
    ("entertainment.bowling_alley", ("leisure", "bowling_alley")),
    ("entertainment.miniature_golf", ("leisure", "miniature_golf")),
    ("sport.ice_rink", ("leisure", "ice_rink")),
    ("education.library", ("amenity", "library")),
    ("catering.ice_cream", ("amenity", "ice_cream")),
    ("catering.cafe", ("amenity", "cafe")),
    ("catering.fast_food", ("amenity", "fast_food")),
    ("catering.restaurant", ("amenity", "restaurant")),
]
GEO_PLACE_CATS = [c for c, (k, _) in GEOAPIFY_MAP if not c.startswith("catering")]
GEO_FOOD_CATS = [c for c in ("catering.restaurant", "catering.fast_food", "catering.cafe", "catering.ice_cream")]


async def _places_geoapify(rlat, rlng, radius_m, lat, lng):
    out = []
    async with _client() as c:
        for cats, radius in ((GEO_PLACE_CATS, radius_m), (GEO_FOOD_CATS, min(radius_m, 8000))):
            try:
                r = await c.get("https://api.geoapify.com/v2/places", params={
                    "categories": ",".join(cats), "filter": f"circle:{rlng},{rlat},{radius}",
                    "bias": f"proximity:{rlng},{rlat}", "limit": 300, "lang": "en",
                    "apiKey": GEOAPIFY_API_KEY,
                })
                if r.status_code != 200:
                    log.warning("geoapify -> HTTP %s: %s", r.status_code, r.text[:300])
                    continue
                feats = r.json().get("features", [])
                log.info("geoapify %s -> %d features", cats[0], len(feats))
            except Exception as e:  # noqa: BLE001
                log.warning("geoapify failed: %r", e)
                continue
            for f in feats:
                parsed = _parse_geoapify(f, lat, lng)
                if parsed:
                    out.append(parsed)
    return out


def _parse_geoapify(feature: dict, lat: float, lng: float):
    p = feature.get("properties") or {}
    cats = set(p.get("categories") or [])
    for gcat, (k, v) in GEOAPIFY_MAP:  # порядок важен: playground раньше park
        if gcat in cats:
            break
    else:
        return None
    raw = dict((p.get("datasource") or {}).get("raw") or {})
    raw[k] = v  # приводим к формату OSM-тегов и используем общий парсер
    raw["name"] = p.get("name") or raw.get("name")
    raw.setdefault("addr:housenumber", p.get("housenumber"))
    raw.setdefault("addr:street", p.get("street"))
    raw.setdefault("addr:city", p.get("city"))
    if p.get("website"):
        raw.setdefault("website", p["website"])
    if p.get("opening_hours"):
        raw.setdefault("opening_hours", p["opening_hours"])
    el = {"type": "geo", "id": hashlib.md5(str(p.get("place_id") or p.get("name")).encode()).hexdigest()[:12], "lat": p.get("lat"), "lon": p.get("lon"),
          "tags": {kk: vv for kk, vv in raw.items() if isinstance(vv, str)}}
    # ресторан, который одновременно отмечен как площадка, не должен стать «площадкой»
    if k == "leisure" and v == "playground" and any(c.startswith("catering") for c in cats):
        return None
    return _parse_osm(el, lat, lng)


async def _places_overpass(rlat, rlng, radius_m, lat, lng):
    query = _overpass_query(rlat, rlng, radius_m)
    elements = None
    async with _client() as c:
        for url in OVERPASS_URLS:
            try:
                r = await c.post(url, data={"data": query})
                if r.status_code != 200:
                    log.warning("overpass %s -> HTTP %s: %s", url, r.status_code, r.text[:300])
                    continue
                data = r.json()
                if data.get("remark"):
                    log.warning("overpass %s remark: %s", url, data["remark"][:300])
                found = data.get("elements", [])
                log.info("overpass %s -> %d elements", url, len(found))
                if found:
                    elements = found
                    break
            except Exception as e:  # noqa: BLE001
                log.warning("overpass %s failed: %r", url, e)
    out = []
    for el in elements or []:
        parsed = _parse_osm(el, lat, lng)
        if parsed:
            out.append(parsed)
    return out


def _parse_osm(el: dict, lat: float, lng: float):
    tags = el.get("tags") or {}
    name = (tags.get("name") or "").split(";")[0].strip()
    elat = el.get("lat") or (el.get("center") or {}).get("lat")
    elng = el.get("lon") or (el.get("center") or {}).get("lon")
    if not name or elat is None or elng is None:
        return None
    for k, v, cat, indoor, price in PLACE_RULES:
        if tags.get(k) == v:
            if v == "swimming_pool" and tags.get("access") in ("private", "customers"):
                return None
            kind, food = "place", False
            break
    else:
        for k, v, cat, price in FOOD_RULES:
            if tags.get(k) == v:
                kind, indoor, food = "food", True, True
                break
        else:
            return None
    fee = tags.get("fee")
    if kind == "place" and fee == "no":
        price = 0
    addr = " ".join(x for x in [tags.get("addr:housenumber"), tags.get("addr:street")] if x)
    return {
        "id": f'osm_{el.get("type", "n")[0]}{el.get("id")}',
        "kind": kind,
        "name": name,
        "category": cat + (f' · {tags["cuisine"].replace("_", " ").split(";")[0]}' if food and tags.get("cuisine") else ""),
        "price_per_person": price,
        "price_is_estimate": price > 0 and fee != "no",
        "address": addr or tags.get("addr:city", ""),
        "lat": elat, "lng": elng,
        "distance_mi": round(miles_between(lat, lng, elat, elng), 1),
        "indoor": indoor,
        "kid_friendly": tags.get("kids_area") == "yes" or tags.get("highchair") == "yes"
                        or tags.get("changing_table") == "yes" or cat in ("Playground", "Zoo", "Aquarium", "Ice cream"),
        "opening_hours": tags.get("opening_hours"),
        "url": tags.get("website") or tags.get("contact:website"),
    }
