# CLAUDE.md — Famigo

Этот файл читается Claude Code автоматически. Полная история проекта, решения и контекст хакатона — в `docs/PROJECT_CONTEXT.md`. **Прочитай его перед любой серьёзной работой.**

## Что это

**Famigo** — веб-приложение для хакатона Business Cup 2.0 (дедлайн сдачи: **15 ноября 2026, 12:00 по времени Астаны**).
Работающий родитель в США вводит город/ZIP, дату, бюджет, возраст детей — получает готовый план дня (таймлайн из 3–5 пунктов): реальные места, события и еда, подобранные под погоду, бюджет и возраст детей. План собирает ИИ (Gemini) из реальных данных; бэкенд проверяет ответ ИИ.

- Репозиторий: https://github.com/yerzat15/famigo (публичный — так требуют правила)
- Продакшен: https://famigo-4zg8.onrender.com (Render, free tier)
- Автор: Erzat, участвует **соло** (изначально было двое, напарница выбыла).

## Как работать с пользователем

- Общение **на русском**, коротко и по делу.
- Пользователь хочет **сам выбирать** ключевые решения: предлагай 2–3 варианта с плюсами/минусами и рекомендацией, не принимай важные решения молча.
- Сложные технические вопросы — **по одному вопросу за раз**.
- Ничего не выдумывать: цифры и факты — только из проверенных источников (см. `docs/PROJECT_CONTEXT.md`), иначе помечать как допущение.
- Пользователь на **Windows, PowerShell, VS Code**. Давай команды для PowerShell, по одной, без «...» и сокращений (он однажды ввёл `git commit ....` буквально).

## Запуск локально (Windows / PowerShell)

Папка проекта: `D:\yerzat\programming\VScode\LocalNuGet\hacaton\Famigo`
Локальный Python: 3.15.2.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # если "running scripts is disabled": Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
uvicorn app.main:app --reload       # http://localhost:8000
```

- Ключи лежат в `.env` (см. `.env.example`). **`.env` никогда не коммитить и не выводить его содержимое.**
- Чтобы удалить `famigo.db` (кеш), сначала останови сервер (Ctrl+C) — иначе Windows выдаёт EBUSY.
- `.env` читается только при старте: после изменения `.env` перезапусти uvicorn.
- VS Code иногда сам создаёт `.venv-1` и подставляет кривые команды — игнорировать, удалять лишние venv.

## Переменные окружения

| Переменная | Назначение | Статус |
|---|---|---|
| `GEMINI_API_KEY` | ИИ-планировщик (Google AI Studio, бесплатный уровень) | есть, работает |
| `GEOAPIFY_API_KEY` | места и еда (данные OpenStreetMap) | есть, работает |
| `TICKETMASTER_API_KEY` | события | **нет** — аккаунт разработчика ждёт одобрения |
| `STRIPE_PAYMENT_LINK` | ссылка на оплату в paywall (тестовый режим) | не задана → кнопка «Subscriptions launching soon» |
| `GEMINI_MODELS` | необязательно, по умолчанию `gemini-3.5-flash-lite,gemini-3.1-flash-lite` | |
| `DB_PATH` | необязательно, по умолчанию `famigo.db` | |

На Render переменные задаются в разделе Environment сервиса (заданы `GEMINI_API_KEY`, `GEOAPIFY_API_KEY`).

## Деплой

- Render Web Service из репозитория `yerzat15/famigo`, ветка `main`.
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Python на Render: **3.13** (файл `.python-version`). Не менять на 3.15 — нет готовых wheel для части пакетов.
- Обновление сайта: `git add .` → `git commit -m "..."` → `git push`.
- Free tier: сервис засыпает после простоя (первый запрос ~1 мин, бывает 503 при пробуждении); файловая система эфемерна — при каждом деплое/рестарте SQLite-кеш и сохранённые планы стираются, старые ссылки `/p/<id>` перестают работать.

## Архитектура

Один проект: FastAPI отдаёт и API, и HTML-страницы (решение принято, т.к. разработчик один — отдельный React-фронт не успеть).

```
app/
  config.py     — переменные окружения, список Overpass-серверов, User-Agent
  cache.py      — SQLite: таблица cache (key/value/expires) и plans (id/data)
  sources.py    — внешние данные:
                    geocode()  Open-Meteo geocoding (город), fallback Nominatim (ZIP)
                    weather()  Open-Meteo forecast (°F), outdoor_ok по правилу
                    events()   Ticketmaster Discovery API (2 запроса: family + общий), фильтр 18+/21+
                    places()   Geoapify Places (основной) → Overpass (запасной); кеш 7 дней,
                               пустой результат НЕ кешируется
  cities.py     — нечёткий поиск города по app/data/us_cities.tsv (GeoNames, CC BY): suggest() для «Did you mean», exact() для «austin texas»
  planner.py    — shortlist() кандидатов → Gemini (JSON schema) → validate_ai()
                  → enforce_budget() → fallback rule_plan(); swap_item()
  main.py       — роуты и валидация запроса (Pydantic)
  templates/    — base.html (шапка, paywall), index.html (форма), plan.html (расшаренный план)
  static/       — app.js (форма, loading, рендер таймлайна, swap, share, счётчик бесплатных планов),
                  app.css
test_e2e.py       — сквозные тесты с замоканными API (respx)
test_geoapify.py  — тесты парсинга Geoapify и fallback на Overpass
test_cities.py    — 40 опечаток в названиях городов + сценарий «Did you mean» в API
```

### Ключевые принципы (не ломать)
- **Ценность должна быть реальной, не захардкоженной** (критерий жюри «Does it work?» — 40%). Никаких фейковых мест/событий.
- **ИИ не доверяем вслепую:** `validate_ai()` отбрасывает несуществующие id, кривое время, дубли; требует хотя бы один пункт еды. Стоимость считает бэкенд из данных, не ИИ.
- **Бюджет соблюдается всегда:** `enforce_budget()` заменяет самые дорогие пункты на более дешёвые рядом (обед → обед), иначе удаляет пункт. Причина: Gemini однажды выдал план на $128 при бюджете $100.
- **Продукт не падает без ИИ:** при 429/ошибке Gemini → вторая модель → `rule_plan()` по правилам.
- **Жюри без регистрации/логина** — это требование. Paywall только после 3 бесплатных планов (счётчик в localStorage) и закрывается кнопкой «Maybe later».
- Цены мест — оценки (`price_is_estimate`), в UI помечаются «est.». Дети до 3 лет в стоимость не считаются.

### API
- `GET /` — форма; `GET /p/{id}` — страница сохранённого плана (без кнопки swap)
- `POST /api/plan` — тело: `location, date (сегодня-1…+15 дней), budget (0–2000), adults (1–6), kids_ages (0–17, до 6), vibe (any|outdoor|indoor), max_miles (1–50), notes (≤200)`
- `GET /api/plan/{id}`, `POST /api/plan/{id}/swap/{index}`
- `GET /health` — `{"ok", "gemini", "geoapify", "ticketmaster"}` (наличие ключей)
- `GET /debug?location=Austin, TX&miles=15` — что вернул каждый источник (geocode, weather, кол-во places/food/events, примеры)

## Тесты

```powershell
pip install respx
python test_e2e.py
python test_geoapify.py
python test_cities.py
```
Все внешние API замоканы — тесты работают офлайн. После изменений в `planner.py`/`sources.py` гонять оба.

## Что сделано / что дальше

Подробный статус и список задач — в `docs/PROJECT_CONTEXT.md` (разделы «Текущий статус» и «Что осталось»).
Коротко: продукт работает в продакшене (Gemini + Geoapify + погода). Не сделано: презентация (PDF ≤10 слайдов), Stripe-ссылка, события (ждём Ticketmaster), проверка на мобильном, отправка формы.
