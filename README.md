# Famigo ☀️

**Your family weekend, planned in 30 seconds.**
Busy working parents enter their city, budget and kids' ages — Famigo checks the forecast, real events and places nearby, and builds a ready-to-go day.

## How it works
1. **Real data:** Open-Meteo (weather), Ticketmaster Discovery API (events), OpenStreetMap / Overpass (parks, museums, restaurants).
2. **Shortlist:** filters by distance, budget, kids' ages, adult-only events, weather.
3. **AI planner:** Gemini picks 3–5 stops and explains *why* each fits this family. Every AI answer is validated — unknown IDs are dropped, costs are computed from real data, not by the AI.
4. **Fallback:** if the AI is unavailable, a rule-based planner builds the day, so the product always works.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # и впиши ключи
uvicorn app.main:app --reload
```
Open http://localhost:8000 · health check: http://localhost:8000/health

## Deploy (Render)
- New → Web Service → connect this repo
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment: `GEMINI_API_KEY`, `TICKETMASTER_API_KEY`, (optional) `STRIPE_PAYMENT_LINK`

## Business model
Free: 3 plans / month, no sign-up. **Famigo Family — $6.99/mo:** unlimited plans, a ready plan every Friday at 6pm, school-break week planners.

## Tests
```bash
pip install respx
python test_e2e.py
```
All external APIs are mocked, so tests run offline.
