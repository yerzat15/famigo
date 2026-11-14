# Famigo ☀️

**Your family weekend, planned in 30 seconds.**

Busy working parents in the US enter their city or ZIP, the day, a budget and their kids' ages. Famigo checks the forecast and what is really nearby, and hands back a ready-to-go day: a 3–5 stop timeline with times, costs, directions and a one-line *why* for each stop.

**Live:** https://famigo-4zg8.onrender.com — no sign-up needed. (Free hosting: the first load after a quiet period can take ~1 minute.)

## How it works
1. **Real data, no hard-coded lists**
   - Weather: [Open-Meteo](https://open-meteo.com)
   - Parks, museums, playgrounds and places to eat: OpenStreetMap via [Geoapify Places](https://www.geoapify.com), with the public Overpass API as a backup
   - Events: Ticketmaster Discovery API, used when a key is configured
2. **Shortlist** — candidates are filtered by distance, budget and weather (indoor first when rain is likely). Adult-only events are dropped, and categories are kept diverse.
3. **AI planner** — Gemini picks the stops and explains why each one fits *this* family.
4. **The AI is never trusted blindly**
   - `validate_ai()` drops unknown ids, invalid times and duplicates, pins events to their real start time, and requires a meal.
   - Costs are computed by the backend from the data, not by the AI.
   - `enforce_budget()` swaps or removes stops until the day fits the budget.
5. **Always works** — if the AI is rate-limited or down, a second model is tried, then a rule-based planner builds the day.
6. **Typo-tolerant city search** — "Austn, TX" gets *Did you mean: Austin, TX?* One tap builds the plan. It uses a bundled list of 7,500 US cities from [GeoNames](https://www.geonames.org) (CC BY 4.0) and fuzzy matching, with no extra API calls.
7. **Swap & share** — replace any stop instantly (without the AI, still within budget), and share a read-only link with your partner.

## Business model
- **Free:** 3 plans a month, no account.
- **Famigo Family — $6.99/mo:** unlimited plans, a ready plan every Friday at 6pm, school-break week planners.

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # then fill in the keys
uvicorn app.main:app --reload
```
Open http://localhost:8000. Checks:
- `/health` shows which keys are configured.
- `/debug?location=Austin, TX` shows what every data source returned.

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | AI planner (Google AI Studio, free tier) |
| `GEOAPIFY_API_KEY` | Places and food (OpenStreetMap data) |
| `TICKETMASTER_API_KEY` | Events (optional) |
| `STRIPE_PAYMENT_LINK` | Paywall checkout link (optional) |
| `GEMINI_MODELS` | Optional model list, default `gemini-3.5-flash-lite,gemini-3.1-flash-lite` |

## Tests
```bash
pip install respx
python test_e2e.py
python test_geoapify.py
python test_cities.py
```
All external APIs are mocked, so the tests run offline. They cover:
- the AI path, including a hallucinated id that must be dropped
- the AI cache
- swap and share
- the rule-based fallback
- rainy days
- validation errors
- 40 misspelled city names and the *Did you mean* flow
- budget enforcement

## Stack
FastAPI, Jinja2 templates, vanilla JS and Tailwind (CDN), SQLite for the cache and saved plans, httpx. Deployed on Render.
