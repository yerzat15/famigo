"""Сборка плана на день: реальные кандидаты → ИИ выбирает и объясняет → проверка → запасной план по правилам."""
import asyncio
import hashlib
import json
import logging
import re
import secrets
from datetime import date

import httpx

from . import cities, sources
from .cache import cache_get, cache_set
from .config import GEMINI_API_KEY, GEMINI_MODELS

log = logging.getLogger("famigo.planner")


class PlanError(Exception):
    def __init__(self, message: str, suggestions: list | None = None):
        super().__init__(message)
        self.suggestions = suggestions or []


# ---------- 1. Кандидаты ----------

def party_size(adults: int, kids_ages: list[int], paying_only: bool = False) -> int:
    kids = [a for a in kids_ages if a >= 3] if paying_only else kids_ages
    return adults + len(kids)


def _cost(item: dict, adults: int, kids_ages: list[int]):
    p = item.get("price_per_person")
    if p is None:
        return None
    return round(p * party_size(adults, kids_ages, paying_only=True))


def shortlist(req: dict, events: list, places: list, weather: dict | None):
    budget, max_mi = req["budget"], req["max_miles"]
    adults, kids = req["adults"], req["kids_ages"]
    vibe = req["vibe"]
    rainy = weather is not None and not weather["outdoor_ok"]

    def fits(x):
        if x["distance_mi"] > max_mi:
            return False
        c = _cost(x, adults, kids)
        return c is None or c <= budget

    def vibe_ok(x):
        if x.get("indoor") is None:
            return True
        if vibe == "indoor":
            return x["indoor"]
        if vibe == "outdoor":
            return not x["indoor"] or rainy  # в дождь всё равно оставляем запасные варианты в помещении
        return True

    evs = [e for e in events if fits(e) and not e["adult_only"]]
    evs.sort(key=lambda e: (not e["family"], e["distance_mi"]))

    acts = [p for p in places if p["kind"] == "place" and fits(p) and vibe_ok(p)]
    acts.sort(key=lambda p: (
        (not p["indoor"]) if rainy else False,  # в дождь — сначала помещения
        not p["kid_friendly"], p["distance_mi"]))
    acts = _diversify(acts, per_category=4, limit=24)

    food = [p for p in places if p["kind"] == "food" and fits(p)]
    food.sort(key=lambda p: (not p["kid_friendly"], p["distance_mi"]))
    food = _diversify(food, per_category=6, limit=14)

    return {"events": evs[:12], "activities": acts, "food": food}


def _diversify(items, per_category, limit):
    out, counts = [], {}
    for x in items:
        cat = x["category"].split(" · ")[0]
        if counts.get(cat, 0) < per_category:
            out.append(x)
            counts[cat] = counts.get(cat, 0) + 1
        if len(out) >= limit:
            break
    return out


# ---------- 2. ИИ (Gemini) ----------

PLAN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "start": {"type": "STRING"},
                    "end": {"type": "STRING"},
                    "why": {"type": "STRING"},
                },
                "required": ["id", "start", "end", "why"],
            },
        },
        "tip": {"type": "STRING"},
    },
    "required": ["title", "summary", "items"],
}


def _compact(x: dict, adults, kids) -> dict:
    """Минимум полей для ИИ — меньше токенов, меньше шансов на выдумки."""
    d = {"id": x["id"], "name": x["name"], "type": x["category"], "mi": x["distance_mi"]}
    c = _cost(x, adults, kids)
    d["family_cost_usd"] = c if c is not None else "unknown"
    if x.get("start_time"):
        d["starts"] = x["start_time"]
    if x.get("indoor") is not None:
        d["indoor"] = x["indoor"]
    if x.get("opening_hours"):
        d["hours"] = x["opening_hours"]
    if x.get("family") or x.get("kid_friendly"):
        d["kid_friendly"] = True
    return d


def build_prompt(req, cands, weather, label):
    adults, kids = req["adults"], req["kids_ages"]
    payload = {k: [_compact(x, adults, kids) for x in v] for k, v in cands.items()}
    wx = (f'{weather["summary"]}, high {weather["high_f"]}°F, low {weather["low_f"]}°F, '
          f'{weather["rain_chance"]}% chance of rain') if weather else "forecast unavailable"
    return f"""You plan family days for busy working parents in the US.
Build ONE realistic plan for {req["date"]} in {label}.

Family: {adults} adult(s), kids aged {", ".join(map(str, kids)) or "none"}.
Total budget: ${req["budget"]} for the whole family.
Max travel: {req["max_miles"]} miles. Preference: {req["vibe"]}.
Weather: {wx}.
Parent's note: {req.get("notes") or "none"}

Rules:
- Use ONLY ids from the candidates below. Never invent places.
- 3 to 5 items between 09:00 and 19:00, in time order, times as HH:MM.
- Include exactly one meal from "food" around lunchtime. Optionally one ice cream/cafe treat.
- Events with "starts" must be scheduled at that exact start time.
- Keep the sum of family_cost_usd within budget (unknown counts as a risk; mention it).
- Fit the kids' ages: short attention spans for under 6, more active for 8+.
- If rain is likely, prefer indoor options.
- Keep consecutive stops close to each other.
- "why": one short sentence, concrete and specific to THIS family (ages, weather, budget, note).
- "title": catchy, max 6 words. "summary": 1 sentence. "tip": one practical tip for parents.

Candidates (JSON):
{json.dumps(payload, ensure_ascii=False)}"""


async def ask_gemini(prompt: str):
    if not GEMINI_API_KEY:
        return None, None
    async with httpx.AsyncClient(timeout=40) as c:
        for model in GEMINI_MODELS:
            for use_schema in (True, False):
                gen = {"responseMimeType": "application/json", "temperature": 0.7}
                if use_schema:
                    gen["responseSchema"] = PLAN_SCHEMA
                try:
                    r = await c.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        headers={"x-goog-api-key": GEMINI_API_KEY},
                        json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                              "generationConfig": gen},
                    )
                except httpx.HTTPError as e:
                    log.warning("gemini %s network error: %s", model, e)
                    break
                if r.status_code == 400 and use_schema:
                    log.warning("gemini %s rejected schema, retrying without", model)
                    continue
                if r.status_code != 200:
                    log.warning("gemini %s -> %s %s", model, r.status_code, r.text[:200])
                    break  # 429/404/5xx — следующая модель
                try:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
                    return json.loads(text), model
                except (KeyError, IndexError, ValueError) as e:
                    log.warning("gemini %s bad output: %s", model, e)
                    break
    return None, None


TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _plus_2h(t: str) -> str:
    h, m = map(int, t.split(":"))
    return "21:00" if h + 2 >= 21 else f"{h + 2:02d}:{m:02d}"


def validate_ai(ai: dict, by_id: dict):
    """Не доверяем ИИ вслепую: только существующие id, корректное время, без повторов, есть еда."""
    if not isinstance(ai, dict) or not isinstance(ai.get("items"), list):
        return None
    items, seen = [], set()
    for it in ai["items"]:
        if not isinstance(it, dict):
            continue
        iid = it.get("id")
        if iid not in by_id or iid in seen:
            continue
        start, end = str(it.get("start", "")), str(it.get("end", ""))
        if not TIME_RE.match(start) or not TIME_RE.match(end):
            continue
        start, end = start.zfill(5), end.zfill(5)
        x = by_id[iid]
        if x["kind"] == "event" and x.get("start_time"):  # у события время фиксировано — ИИ не может его сдвинуть
            start = x["start_time"]
            if end <= start:
                end = _plus_2h(start)
        if end <= start:
            continue
        seen.add(iid)
        items.append({"id": iid, "start": start, "end": end,
                      "why": str(it.get("why", ""))[:240]})
    items.sort(key=lambda x: x["start"])
    items = items[:5]
    if len(items) < 2 or not any(by_id[i["id"]]["kind"] == "food" for i in items):
        return None
    return {"title": str(ai.get("title") or "Your family day")[:80],
            "summary": str(ai.get("summary") or "")[:300],
            "tip": str(ai.get("tip") or "")[:300], "items": items}


def enforce_budget(plan: dict, req: dict, cands: dict, by_id: dict):
    """ИИ может превысить бюджет — тогда заменяем самые дорогие пункты на более дешёвые рядом."""
    adults, kids, budget = req["adults"], req["kids_ages"], req["budget"]
    items = [dict(i) for i in plan["items"]]

    def cost(iid):
        return _cost(by_id[iid], adults, kids) or 0

    for _ in range(6):
        total = sum(cost(i["id"]) for i in items)
        if total <= budget:
            break
        used = {i["id"] for i in items}
        # самый дорогой пункт, который можно заменить (события с фиксированным временем — тоже)
        worst = max(items, key=lambda i: cost(i["id"]))
        cur = by_id[worst["id"]]
        group = "food" if cur["kind"] == "food" else "activities"
        need = total - budget
        cheaper = [x for x in cands[group] if x["id"] not in used
                   and (_cost(x, adults, kids) or 0) <= cost(worst["id"]) - need]
        if not cheaper:  # хотя бы просто дешевле
            cheaper = [x for x in cands[group] if x["id"] not in used
                       and (_cost(x, adults, kids) or 0) < cost(worst["id"])]
        if cur["kind"] == "food":  # обед меняем на обед, а не на мороженое
            meals = [x for x in cheaper if not x["category"].startswith(("Ice cream", "Cafe"))]
            cheaper = meals or cheaper
        new = _nearest(cheaper, cur, set())
        if new:
            worst.update(id=new["id"], why=f"Swapped for a cheaper {new['category'].split(' · ')[0].lower()} "
                                            f"nearby to keep the day within your ${budget} budget.")
        elif cur["kind"] != "food" or sum(by_id[i["id"]]["kind"] == "food" for i in items) > 1:
            items.remove(worst)  # заменить нечем — убираем пункт
        else:
            break
    if len(items) < 2 or sum(cost(i["id"]) for i in items) > budget:
        return None  # не уложились — пусть строит запасной план
    return {**plan, "items": items}


# ---------- 3. Запасной план по правилам (если ИИ недоступен) ----------

def _nearest(items, anchor, exclude):
    pool = [x for x in items if x["id"] not in exclude]
    if not pool:
        return None
    if anchor is None:
        return pool[0]
    return min(pool, key=lambda x: sources.miles_between(anchor["lat"], anchor["lng"], x["lat"], x["lng"]))


def rule_plan(req, cands, weather):
    acts, food, evs = cands["activities"], cands["food"], cands["events"]
    adults, kids = req["adults"], req["kids_ages"]
    budget_left = req["budget"]
    used, items = set(), []
    rainy = weather is not None and not weather["outdoor_ok"]

    def add(x, start, end, why):
        nonlocal budget_left
        used.add(x["id"])
        budget_left -= _cost(x, adults, kids) or 0
        items.append({"id": x["id"], "start": start, "end": end, "why": why})

    def affordable(pool):
        return [x for x in pool if (_cost(x, adults, kids) or 0) <= budget_left]

    free_day = not affordable(food)
    meals = [f for f in affordable(food) if not f["category"].startswith(("Ice cream", "Cafe"))]
    # оставляем деньги на обед, чтобы утро не съело весь бюджет
    reserve = min((_cost(f, adults, kids) or 0 for f in meals), default=0)
    morning = _nearest([a for a in affordable(acts) if (_cost(a, adults, kids) or 0) <= budget_left - reserve],
                       None, used)
    if morning:
        add(morning, "09:30", "11:30", "Indoor start since rain is likely." if rainy and morning["indoor"]
            else "Close to home and a good way to burn morning energy.")
    lunch_pool = [f for f in affordable(food) if not f["category"].startswith(("Ice cream", "Cafe"))] or affordable(food)
    lunch = _nearest(lunch_pool, morning, used)
    if lunch:
        add(lunch, "12:00", "13:00", "Kid-friendly lunch nearby." if lunch["kid_friendly"] else "Quick lunch close to your morning stop.")
    ev = next((e for e in affordable(evs) if e.get("start_time") and "13:00" <= e["start_time"] < "19:00"), None)
    if ev:
        h, m = map(int, ev["start_time"].split(":"))
        add(ev, ev["start_time"], f"{min(h + 2, 21):02d}:{m:02d}", "A live event happening today that fits your budget.")
    else:
        afternoon = _nearest(affordable(acts), lunch or morning, used)
        if afternoon:
            add(afternoon, "13:45", "16:00", "A change of pace after lunch, a short drive away.")
    treat = _nearest([f for f in affordable(food) if f["category"].startswith(("Ice cream", "Cafe"))],
                     None, used)
    if treat and (not ev or ev["start_time"] < "15:00"):
        add(treat, "16:15", "16:45", "A small treat to end the day on a high note.")
    items.sort(key=lambda x: x["start"])
    if len(items) < 2:
        return None
    tip = "Pack a picnic — this day is free." if free_day else "Check opening hours before you leave."
    return {"title": "Your family day", "summary": "A balanced day built from what's open near you.",
            "tip": tip, "items": items}


# ---------- 4. Главная функция ----------

async def make_plan(req: dict) -> dict:
    geo = await sources.geocode(req["location"])
    if not geo:
        hints = [] if req["location"].strip().isdigit() else [s["query"] for s in cities.suggest(req["location"])]
        if hints:
            raise PlanError(f"We couldn't find \"{req['location'].strip()}\". Did you mean:", hints)
        raise PlanError("We couldn't find that city or ZIP in the US. Try e.g. 'Austin, TX' or '78701'.")
    day = date.fromisoformat(req["date"])
    weather, evs, plc = await asyncio.gather(
        sources.weather(geo["lat"], geo["lng"], day),
        sources.events(geo["lat"], geo["lng"], day, req["max_miles"]),
        sources.places(geo["lat"], geo["lng"], req["max_miles"]),
    )
    cands = shortlist(req, evs, plc, weather)
    if not cands["activities"] and not cands["events"]:
        raise PlanError("We couldn't find activities in that area. Try a bigger distance or a nearby larger city.")
    by_id = {x["id"]: x for v in cands.values() for x in v}

    # ИИ-кеш: одинаковый запрос + одинаковые кандидаты → тот же ответ без лишнего вызова
    ai_key = "ai:" + hashlib.sha256(json.dumps([req, sorted(by_id)], sort_keys=True).encode()).hexdigest()
    plan, model = cache_get(ai_key), None
    if plan is None:
        raw, model = await ask_gemini(build_prompt(req, cands, weather, geo["label"]))
        plan = validate_ai(raw, by_id) if raw else None
        if plan:
            plan["model"] = model
            cache_set(ai_key, plan, 6 * 3600)
    ai_used = plan is not None
    if plan:
        plan = enforce_budget(plan, req, cands, by_id)
    if not plan:
        plan, ai_used = rule_plan(req, cands, weather), False
    if not plan:
        raise PlanError("Not enough options within your budget and distance. Try loosening the filters.")

    return finalize(plan, req, geo, weather, by_id, cands, ai_used)


def finalize(plan, req, geo, weather, by_id, cands, ai_used):
    adults, kids = req["adults"], req["kids_ages"]
    items, total, unknown = [], 0, False
    for it in plan["items"]:
        x = by_id[it["id"]]
        cost = _cost(x, adults, kids)
        if cost is None:
            unknown = True
        else:
            total += cost
        items.append({**it, "place": x, "family_cost": cost})
    result = {
        "id": secrets.token_urlsafe(6),
        "request": req,
        "location": geo,
        "weather": weather,
        "title": plan["title"], "summary": plan["summary"], "tip": plan.get("tip", ""),
        "items": items,
        "total_cost": total, "cost_has_unknowns": unknown,
        "ai_used": ai_used, "model": plan.get("model"),
        # запасные варианты для кнопки «заменить»
        "alternatives": {k: [x["id"] for x in v] for k, v in cands.items()},
        "pool": by_id,
    }
    return result


def swap_item(plan: dict, index: int) -> dict:
    """Заменить пункт плана на ближайшую альтернативу того же типа (без ИИ — мгновенно)."""
    items = plan["items"]
    if not 0 <= index < len(items):
        raise PlanError("No such item.")
    cur = items[index]["place"]
    group = "food" if cur["kind"] == "food" else "activities"
    used = {i["place"]["id"] for i in items}
    tried = set(plan.get("swapped", []))
    req = plan["request"]
    others = sum(i["family_cost"] or 0 for j, i in enumerate(items) if j != index)
    pool = [plan["pool"][i] for i in plan["alternatives"][group]
            if i not in used and i not in tried and i in plan["pool"]
            and others + (_cost(plan["pool"][i], req["adults"], req["kids_ages"]) or 0) <= req["budget"]]
    if cur["kind"] == "food" and not cur["category"].startswith(("Ice cream", "Cafe")):  # обед меняем на обед
        pool = [x for x in pool if not x["category"].startswith(("Ice cream", "Cafe"))] or pool
    new = _nearest(pool, cur, set())
    if not new:
        raise PlanError("No more alternatives nearby. Try increasing the distance.")
    cost = _cost(new, req["adults"], req["kids_ages"])
    items[index] = {**items[index], "place": new, "family_cost": cost, "id": new["id"],
                    "why": f"Swapped in: {new['category'].lower()} {new['distance_mi']} mi from home."}
    plan["swapped"] = list(tried | {cur["id"]})
    plan["total_cost"] = sum(i["family_cost"] or 0 for i in items)
    plan["cost_has_unknowns"] = any(i["family_cost"] is None for i in items)
    return plan
