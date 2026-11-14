import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from .cache import plan_get, plan_save
from .config import GEMINI_API_KEY, STRIPE_PAYMENT_LINK, TICKETMASTER_API_KEY
from .planner import PlanError, make_plan, swap_item

logging.basicConfig(level=logging.INFO)
BASE = Path(__file__).parent
app = FastAPI(title="Famigo")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


class PlanRequest(BaseModel):
    location: str = Field(min_length=2, max_length=80)
    date: date
    budget: int = Field(ge=0, le=2000)
    adults: int = Field(default=2, ge=1, le=6)
    kids_ages: list[int] = Field(default_factory=list, max_length=6)
    vibe: Literal["any", "outdoor", "indoor"] = "any"
    max_miles: int = Field(default=15, ge=1, le=50)
    notes: str = Field(default="", max_length=200)

    @field_validator("kids_ages")
    @classmethod
    def ages_ok(cls, v):
        if any(a < 0 or a > 17 for a in v):
            raise ValueError("Kids' ages must be between 0 and 17")
        return v

    @field_validator("date")
    @classmethod
    def date_ok(cls, v):
        today = date.today()
        if not today - timedelta(days=1) <= v <= today + timedelta(days=15):
            raise ValueError("Pick a date within the next 2 weeks")
        return v


def _public(plan: dict) -> dict:
    """Отдаём фронту план без служебного пула кандидатов."""
    return {k: v for k, v in plan.items() if k not in ("pool", "alternatives", "swapped")}


def _page_ctx(request: Request, **extra):
    return {"request": request, "stripe_link": STRIPE_PAYMENT_LINK, **extra}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", _page_ctx(request))


@app.get("/p/{plan_id}", response_class=HTMLResponse)
def shared_plan(request: Request, plan_id: str):
    plan = plan_get(plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return templates.TemplateResponse(request, "plan.html", _page_ctx(request, plan=_public(plan)))


@app.post("/api/plan")
async def api_plan(body: PlanRequest):
    req = body.model_dump(mode="json")
    try:
        plan = await make_plan(req)
    except PlanError as e:
        raise HTTPException(422, str(e))
    plan_save(plan["id"], plan)
    return _public(plan)


@app.get("/api/plan/{plan_id}")
def api_get_plan(plan_id: str):
    plan = plan_get(plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return _public(plan)


@app.post("/api/plan/{plan_id}/swap/{index}")
def api_swap(plan_id: str, index: int):
    plan = plan_get(plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    try:
        plan = swap_item(plan, index)
    except PlanError as e:
        raise HTTPException(422, str(e))
    plan_save(plan_id, plan)
    return _public(plan)


@app.get("/debug")
async def debug(location: str = "Austin, TX", miles: int = 15):
    """Диагностика: что вернул каждый источник данных."""
    from . import sources
    geo = await sources.geocode(location)
    if not geo:
        return {"geocode": None}
    day = date.today()
    wx = await sources.weather(geo["lat"], geo["lng"], day)
    plc = await sources.places(geo["lat"], geo["lng"], miles)
    evs = await sources.events(geo["lat"], geo["lng"], day, miles)
    return {
        "geocode": geo, "weather": wx,
        "places": sum(p["kind"] == "place" for p in plc),
        "food": sum(p["kind"] == "food" for p in plc),
        "events_today": len(evs),
        "sample": [p["name"] for p in plc[:8]],
    }


@app.get("/health")
def health():
    return {"ok": True, "gemini": bool(GEMINI_API_KEY), "ticketmaster": bool(TICKETMASTER_API_KEY)}
