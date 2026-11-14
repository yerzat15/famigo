// Famigo frontend: form, loading state, timeline rendering, swap, share, paywall.
const Famigo = (() => {
  const FREE_PLANS = 3;
  const $ = (s, el = document) => el.querySelector(s);
  const ymd = (d) => d.toLocaleDateString("sv"); // локальная дата YYYY-MM-DD (toISOString — это UTC)
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- free-plan counter (stored per browser; no login needed) ----------
  const store = {
    get() { try { return JSON.parse(localStorage.getItem("wk_usage") || "{}"); } catch { return {}; } },
    set(v) { try { localStorage.setItem("wk_usage", JSON.stringify(v)); } catch {} },
  };
  function monthKey() { const d = new Date(); return `${d.getFullYear()}-${d.getMonth() + 1}`; }
  function plansUsed() { const u = store.get(); return u.month === monthKey() ? u.count || 0 : 0; }
  function countPlan() { store.set({ month: monthKey(), count: plansUsed() + 1 }); updateFreeLeft(); }
  function updateFreeLeft() {
    const el = $("#free-left"); if (!el) return;
    const left = Math.max(0, FREE_PLANS - plansUsed());
    el.textContent = left > 0 ? `${left} free plan${left === 1 ? "" : "s"} left this month` : "Free plans used";
  }
  function showPaywall() { $("#paywall").classList.remove("hidden"); $("#paywall-close").focus(); }
  function initPaywall() {
    const close = $("#paywall-close");
    if (close) close.onclick = () => $("#paywall").classList.add("hidden");
    document.onkeydown = (e) => { if (e.key === "Escape") $("#paywall").classList.add("hidden"); };
  }

  // ---------- form ----------
  function addKid(age = 6) {
    const wrap = $("#kids");
    const el = document.createElement("span");
    el.className = "kid";
    el.innerHTML = `<select aria-label="Kid age">${Array.from({ length: 18 }, (_, i) => `<option value="${i}" ${i === age ? "selected" : ""}>${i === 0 ? "<1" : i} yr</option>`).join("")}</select><button type="button" aria-label="Remove">✕</button>`;
    el.querySelector("button").onclick = () => { el.remove(); };
    wrap.insertBefore(el, $("#add-kid"));
  }

  function initForm() {
    initPaywall(); updateFreeLeft();
    const d = new Date(); const sat = new Date(d); sat.setDate(d.getDate() + ((6 - d.getDay() + 7) % 7));
    $("#date").value = ymd(sat);
    $("#date").min = ymd(new Date(Date.now() - 864e5));
    $("#date").max = ymd(new Date(Date.now() + 15 * 864e5));
    const add = document.createElement("button");
    add.type = "button"; add.id = "add-kid";
    add.className = "text-sm font-semibold text-coral-dark px-3 py-2 rounded-full hover:bg-coral/10";
    add.textContent = "+ Add kid";
    add.onclick = () => addKid(5);
    $("#kids").appendChild(add);
    addKid(4); addKid(8);

    $("#location").addEventListener("input", () => $("#location-hint").classList.add("hidden"));
    $("#plan-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (plansUsed() >= FREE_PLANS) { showPaywall(); return; }
      const f = new FormData(e.target);
      const body = {
        location: f.get("location"), date: f.get("date"), budget: Number(f.get("budget")),
        adults: Number(f.get("adults")), max_miles: Number(f.get("max_miles")),
        vibe: f.get("vibe"), notes: f.get("notes") || "",
        kids_ages: [...document.querySelectorAll("#kids select")].map((s) => Number(s.value)),
      };
      await requestPlan(body);
    });
  }

  const STEPS = ["🌦️ Checking the forecast", window.HAS_EVENTS && "🎟️ Looking for events that day", "🌳 Finding parks, museums & places to eat", "🧠 Fitting it to your kids and budget"].filter(Boolean);

  async function requestPlan(body) {
    $("#form-error").classList.add("hidden");
    $("#location-hint").classList.add("hidden");
    $("#hero").classList.add("hidden");
    $("#result").classList.add("hidden");
    $("#loading").classList.remove("hidden");
    const ul = $("#loading-steps"); ul.innerHTML = "";
    let i = 0;
    const tick = setInterval(() => { if (i < STEPS.length) { ul.insertAdjacentHTML("beforeend", `<li class="fade-in">${STEPS[i++]}</li>`); } }, 900);
    try {
      const r = await fetch("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) throw Object.assign(new Error(errorText(data)), { suggestions: data.detail?.suggestions });
      countPlan();
      history.pushState({}, "", `/p/${data.id}`);
      renderPlan(data);
    } catch (err) {
      $("#hero").classList.remove("hidden");
      // «Did you mean…?» показываем прямо под полем города; клик подставляет город и сразу строит план
      const el = err.suggestions?.length ? $("#location-hint") : $("#form-error");
      el.textContent = err.message || "Something went wrong. Please try again.";
      (err.suggestions || []).forEach((s) => {
        const b = document.createElement("button");
        b.type = "button"; b.textContent = s;
        b.className = "ml-2 mt-2 px-3 py-1.5 rounded-full bg-white text-coral-dark font-semibold shadow-sm hover:bg-coral/10";
        b.onclick = () => { $("#location").value = s; $("#plan-form").requestSubmit(); };
        el.appendChild(b);
      });
      el.classList.remove("hidden");
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    } finally {
      clearInterval(tick);
      $("#loading").classList.add("hidden");
    }
  }

  function errorText(data) {
    if (typeof data.detail === "string") return data.detail;
    if (data.detail?.message) return data.detail.message;
    if (Array.isArray(data.detail)) return data.detail.map((d) => (d.msg || "").replace("Value error, ", "")).join(". ");
    return "Something went wrong. Please try again.";
  }

  // ---------- plan rendering ----------
  const ICONS = [
    [/park|nature/i, "🌳"], [/playground/i, "🛝"], [/zoo/i, "🦒"], [/aquarium/i, "🐠"], [/museum/i, "🏛️"],
    [/library/i, "📚"], [/theme park/i, "🎢"], [/trampoline/i, "🤸"], [/bowling/i, "🎳"], [/ice rink/i, "⛸️"],
    [/mini golf/i, "⛳"], [/pool/i, "🏊"], [/ice cream/i, "🍦"], [/cafe/i, "☕"], [/fast food/i, "🍔"],
    [/restaurant/i, "🍽️"], [/music|concert/i, "🎵"], [/sport/i, "🏟️"], [/theat|arts/i, "🎭"], [/film/i, "🎬"],
  ];
  const icon = (p) => (ICONS.find(([re]) => re.test(p.category)) || [null, p.kind === "event" ? "🎟️" : "📍"])[1];
  const money = (n) => (n === 0 ? "Free" : `$${n}`);
  const t12 = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`; };
  const maps = (p) => `https://www.google.com/maps/search/?api=1&query=${p.lat},${p.lng}`;

  function costLabel(it) {
    const p = it.place;
    if (it.family_cost === null || it.family_cost === undefined) return p.kind === "event" ? "See prices" : "Price varies";
    return `${money(it.family_cost)}${p.price_is_estimate ? " est." : ""}`;
  }

  function renderPlan(plan, opts = {}) {
    initPaywall(); updateFreeLeft();
    const el = $("#result");
    const req = plan.request, wx = plan.weather;
    const dateStr = new Date(req.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
    const over = plan.total_cost > req.budget;
    el.innerHTML = `
      <div class="fade-in">
        ${opts.shared ? "" : `<button id="back" class="text-sm text-ink/70 hover:text-ink mb-4 py-2">← New plan</button>`}
        <div class="bg-white rounded-3xl p-6 md:p-8 shadow-xl shadow-ink/5 mb-6">
          <p class="text-sm text-ink/70 mb-1">${esc(dateStr)} · ${esc(plan.location.label)}</p>
          <h2 tabindex="-1" class="font-display outline-none text-3xl md:text-4xl font-bold leading-tight mb-2">${esc(plan.title)}</h2>
          <p class="text-ink/70 mb-5">${esc(plan.summary)}</p>
          <div class="flex flex-wrap gap-2 text-sm">
            ${wx ? `<span class="bg-sun/15 px-3 py-1.5 rounded-full">🌡️ ${esc(wx.summary)} · ${wx.high_f}°/${wx.low_f}°F · ${wx.rain_chance}% rain</span>` : ""}
            <span class="${over ? "bg-coral/15" : "bg-leaf/15"} px-3 py-1.5 rounded-full">💸 ~${money(plan.total_cost)}${plan.cost_has_unknowns ? "+" : ""} of $${req.budget}</span>
            <span class="bg-ink/5 px-3 py-1.5 rounded-full">👨‍👩‍👧 ${req.adults} adult${req.adults > 1 ? "s" : ""}${req.kids_ages.length ? `, kids ${req.kids_ages.join(", ")}` : ""}</span>
          </div>
        </div>

        <ol class="timeline space-y-5 mb-6">
          ${plan.items.map((it, i) => item(it, i, opts)).join("")}
        </ol>

        ${plan.tip ? `<div class="bg-sun/15 rounded-2xl p-4 mb-6 text-ink/80">💡 ${esc(plan.tip)}</div>` : ""}

        <div class="flex flex-col sm:flex-row gap-3">
          <button id="share" class="flex-1 bg-ink text-white font-semibold py-3.5 rounded-2xl hover:bg-ink/90">🔗 Share with your partner</button>
          ${opts.shared ? `<a href="/" class="flex-1 text-center bg-coral text-white font-semibold py-3.5 rounded-2xl hover:bg-coral/90">Plan your own day →</a>` : ""}
        </div>
        <p class="text-xs text-ink/70 mt-6 text-center">
          ${plan.ai_used ? "Plan composed by AI from" : "Plan built from"} live data: Open-Meteo, ${plan.items.some((i) => i.place.kind === "event") ? "Ticketmaster, " : ""}OpenStreetMap. Prices marked "est." are estimates — check before you go.
        </p>
      </div>`;
    el.classList.remove("hidden");
    window.scrollTo({ top: 0, behavior: "smooth" });
    $("#result h2").focus({ preventScroll: true });

    const back = $("#back");
    if (back) back.onclick = () => { history.pushState({}, "", "/"); el.classList.add("hidden"); $("#hero").classList.remove("hidden"); $("#location").focus(); };
    $("#share").onclick = async () => {
      const url = `${location.origin}/p/${plan.id}`;
      if (navigator.share) { try { await navigator.share({ title: plan.title, url }); return; } catch {} }
      try { await navigator.clipboard.writeText(url); $("#share").textContent = "✓ Link copied"; } catch { prompt("Copy this link:", url); }
    };
    el.querySelectorAll("[data-swap]").forEach((b) => (b.onclick = () => swap(plan, Number(b.dataset.swap), opts)));
  }

  function item(it, i, opts) {
    const p = it.place;
    const link = p.kind === "event" && p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener" class="text-coral-dark font-semibold py-2">Tickets ↗</a>` : "";
    return `
      <li class="flex gap-4 fade-in" style="animation-delay:${i * 80}ms">
        <div class="dot" aria-hidden="true">${icon(p)}</div>
        <div class="flex-1 bg-white rounded-2xl p-5 shadow-md shadow-ink/5">
          <div class="flex items-start justify-between gap-3 mb-1">
            <p class="text-sm font-semibold text-coral-dark">${t12(it.start)} – ${t12(it.end)}</p>
            <p class="text-sm font-semibold whitespace-nowrap">${esc(costLabel(it))}</p>
          </div>
          <h3 class="text-lg font-semibold leading-snug">${esc(p.name)}</h3>
          <p class="text-sm text-ink/70 mb-2">${esc(p.category)} · ${p.distance_mi} mi${p.place ? ` · ${esc(p.place)}` : ""}</p>
          <p class="text-ink/80 mb-3">${esc(it.why)}</p>
          <div class="flex flex-wrap gap-4 text-sm">
            <a href="${maps(p)}" target="_blank" rel="noopener" class="text-ink/70 hover:text-ink py-2">📍 Directions</a>
            ${link}
            ${opts.shared || p.kind === "event" ? "" : `<button data-swap="${i}" class="text-ink/70 hover:text-ink py-2">🔄 Swap</button>`}
          </div>
        </div>
      </li>`;
  }

  async function swap(plan, index, opts) {
    let r, data;
    try { r = await fetch(`/api/plan/${plan.id}/swap/${index}`, { method: "POST" }); data = await r.json(); }
    catch { alert("Network error. Please try again."); return; }
    if (!r.ok) { alert(errorText(data)); return; }
    renderPlan(data, opts);
  }

  window.addEventListener("popstate", () => location.reload());
  return { initForm, renderPlan };
})();
