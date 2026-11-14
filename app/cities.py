"""Нечёткий поиск города США по справочнику GeoNames: «Austn, TX» -> Austin, TX. Без внешних запросов."""
import difflib
import math
import re
from pathlib import Path

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
STATE_NAMES = {v: k.title() for k, v in STATES.items()}

_CITIES = []  # (name, state, lat, lng, population), крупные первыми
for _line in (Path(__file__).parent / "data" / "us_cities.tsv").read_text(encoding="utf-8").splitlines():
    if _line and not _line.startswith("#"):
        _n, _s, _lat, _lng, _pop = _line.split("\t")
        _CITIES.append((_n, _s, float(_lat), float(_lng), int(_pop)))
_BY_NAME = {}
for _c in _CITIES:
    _BY_NAME.setdefault(_c[0].lower(), []).append(_c)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s.lower())).strip()


def parse(query: str):
    """'Austin, TX' / 'austin texas' / 'Austin' -> ('austin', 'TX' | None)."""
    if "," in query:
        city, st = query.split(",", 1)
        st = _norm(st)
        return _norm(city), (st.upper() if st.upper() in STATE_NAMES else STATES.get(st))
    words = _norm(query).split()
    for k in (2, 1):  # «new york», «texas», «tx» в конце строки
        tail = " ".join(words[-k:])
        if len(words) > k and (tail in STATES or tail.upper() in STATE_NAMES):
            return " ".join(words[:-k]), STATES.get(tail) or tail.upper()
    return " ".join(words), None


def _result(c):
    return {"lat": c[2], "lng": c[3], "label": f"{c[0]}, {STATE_NAMES[c[1]]}", "query": f"{c[0]}, {c[1]}"}


def exact(query: str):
    """Точное совпадение названия (без опечатки), с учётом штата, если он указан."""
    city, state = parse(query)
    found = [c for c in _BY_NAME.get(city, []) if state in (None, c[1])]
    return _result(found[0]) if found else None


def suggest(query: str, n: int = 3):
    """Похожие города: сначала совпадение штата, потом похожесть написания с поправкой на размер города."""
    city, state = parse(query)
    if len(city) < 3:
        return []
    names = difflib.get_close_matches(city, list(_BY_NAME), n=12, cutoff=0.78)  # 0.78: «Salt Lake» ещё находит, «Nowhereville» уже нет
    scored = []
    for name in names:
        ratio = difflib.SequenceMatcher(None, city, name).ratio()
        for c in _BY_NAME[name]:
            # крупный город чуть вероятнее: «Miama» -> Miami, а не Miramar
            scored.append((state is not None and c[1] == state, ratio + 0.1 * math.log10(c[4] + 1), c))
    scored.sort(key=lambda x: x[:2], reverse=True)
    return [_result(x[2]) for x in scored[:n]]
