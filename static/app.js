// Weekly Nutrition Tracker — front end. Talks to the Flask JSON API only.

const state = {
  config: null,
  today: null,           // local date (YYYY-MM-DD); refreshed if the page stays open past midnight
  estimate: null,        // meal under review: {description, items, nutrients, source, warnings, itemsEditable}
  estimateStale: false,  // description edited after calculating
  editing: null,         // saved meal being edited (null = logging a new meal)
  meals: new Map(),      // id -> meal, for edit / log-again buttons
  weekDate: null, weekly: null, catFilter: "Key", sort: "category",
  mealsWeekDate: null,
  dayDate: null, daily: null, dayFilter: "Key", daySort: "category",
  insightsFor: null,     // week_start the weekly insights on screen belong to
  dayInsightsFor: null,  // date the daily insights on screen belong to
  goals: null,
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cls = (status) => String(status || "").replace(/\s+/g, "-");
const SAMPLE_MARKER = " (sample data)";

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

// Only the newest response for each view is rendered, so clicking ◀ ▶ quickly can't show an older week.
const STALE = new Error("stale response");
const requestSeq = {};
async function fetchLatest(view, path) {
  const seq = (requestSeq[view] = (requestSeq[view] || 0) + 1);
  const data = await api(path);
  if (seq !== requestSeq[view]) throw STALE;
  return data;
}

function fmt(v) {
  const n = Number(v) || 0;
  const a = Math.abs(n);
  if (a >= 100) return Math.round(n).toLocaleString();
  if (a >= 10) return n.toFixed(1).replace(/\.0$/, "");
  return n.toFixed(2).replace(/\.?0+$/, "") || "0";
}

function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 3500);
}

function msg(el, kind, html) {
  el.innerHTML = html ? `<div class="msg ${kind}">${html}</div>` : "";
}

const fmtDate = (iso, opts = { weekday: "short", day: "numeric", month: "short" }) =>
  new Date(iso + "T00:00:00").toLocaleDateString(undefined, opts);
const todayISO = () => new Date().toLocaleDateString("en-CA");

function shiftDate(iso, days) {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + days);
  return d.toLocaleDateString("en-CA"); // YYYY-MM-DD
}

function weekStartOf(iso) {
  const d = new Date(iso + "T00:00:00");
  return shiftDate(iso, -((d.getDay() + 6) % 7));
}

const isActive = (tab) => $(`#tab-${tab}`).classList.contains("active");
const nutrientMap = () => Object.fromEntries(state.config.nutrients.map((n) => [n.key, n]));

function rememberMeals(list) {
  for (const m of list) if (m.id) state.meals.set(m.id, m);
}

// ------------------------------------------------------------------ tabs & refresh
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  window.scrollTo({ top: 0 });
  refreshTab(name);
}

function refreshTab(name) {
  if (name === "log") { loadMeals(); loadRecent(); }
  if (name === "meals") loadAllMeals();
  if (name === "daily") loadDaily();
  if (name === "dashboard") { loadWeekly(); loadTrends(); }
  if (name === "insights") updateInsightsHeader();
  if (name === "goals") loadGoals();
}

function refreshActive() {
  const active = document.querySelector(".tab.active");
  if (active) refreshTab(active.dataset.tab);
}

/** Meals changed: anything computed from them is out of date. */
function dataChanged() {
  state.insightsFor = null;
  state.dayInsightsFor = null;
  $("#insights-body").innerHTML = "";
  $("#day-insights-body").innerHTML = "";
  loadMeals();
  loadRecent();
  refreshActive();
}

function checkNewDay() {
  if (!state.config) return;
  const t = todayISO();
  if (t === state.today) return;
  const old = state.today;
  state.today = t;
  for (const k of ["weekDate", "mealsWeekDate", "dayDate"]) if (state[k] === old) state[k] = t;
  if ($("#meal-date").value === old) $("#meal-date").value = t;
  $("#meal-date").max = t;
  refreshActive();
}

// ------------------------------------------------------------------ nutrient display helpers
function nutrientsByCategory() {
  const groups = {};
  for (const n of state.config.nutrients) (groups[n.category] ||= []).push(n);
  return groups;
}

/** Key nutrients up front, the full list behind a "show all" toggle. */
function nutrientGrid(values) {
  const cell = (n) => `<div><span>${esc(n.name)}</span><b>${fmt(values[n.key])} <span class="muted">${esc(n.unit)}</span></b></div>`;
  const key = state.config.nutrients.filter((n) => n.is_key);
  const all = Object.entries(nutrientsByCategory()).map(([cat, list]) =>
    `<div class="ncat"><h4>${esc(cat)}</h4><div class="ngrid">${list.map(cell).join("")}</div></div>`).join("");
  return `${key.length ? `<div class="ncat"><h4>Key nutrients</h4><div class="ngrid">${key.map(cell).join("")}</div></div>` : ""}
    <details class="all-nutrients" ${key.length ? "" : "open"}><summary>Show all ${state.config.nutrients.length} nutrients</summary>${all}</details>`;
}

function filterButtons(active, attr) {
  const cats = ["Key", "All", ...new Set(state.config.nutrients.map((n) => n.category))];
  return cats.map((c) => `<button data-${attr}="${esc(c)}" class="${c === active ? "active" : ""}">${c === "Key" ? "Key nutrients" : esc(c)}</button>`).join("");
}

function filterRows(rows, filter) {
  if (filter === "Key") {
    const key = rows.filter((r) => r.is_key);
    return key.length ? key : rows;
  }
  return filter === "All" ? rows : rows.filter((r) => r.category === filter);
}

function sortRows(rows, sort) {
  if (sort === "pct-asc") return [...rows].sort((a, b) => a.pct - b.pct);
  if (sort === "pct-desc") return [...rows].sort((a, b) => b.pct - a.pct);
  return rows;
}

// ------------------------------------------------------------------ log meal: estimate & review
const EXAMPLES = [
  "2 eggs, 2 slices of whole wheat toast, 1 banana and 200 ml milk",
  "1 cup brown rice, 1 cup dal and a bowl of mixed vegetables",
  "150 g grilled salmon, 1 sweet potato and 1 cup spinach",
  "1 cup greek yogurt with blueberries and a handful of almonds",
];

function fromApiEstimate(e, description) {
  const items = e.items.map((it) => ({ ...it, origGrams: it.grams, removed: false }));
  return {
    description, source: e.source, warnings: e.warnings, nutrients: e.nutrients, items,
    itemsEditable: items.length > 0 && items.every((it) => it.nutrients && it.grams > 0),
  };
}

function fromSavedMeal(m, { relog }) {
  const source = relog ? `${m.source.replace(SAMPLE_MARKER, "")}`.trim() : m.source;
  return {
    description: m.description, source, nutrients: { ...m.nutrients }, items: [], itemsEditable: false,
    savedItemsText: m.items_text,
    warnings: [`Using the nutrition saved with this meal on ${fmtDate(m.date)}. Change the description to recalculate.`],
  };
}

function currentTotals(est) {
  if (!est.itemsEditable) return est.nutrients;
  const totals = Object.fromEntries(state.config.nutrients.map((n) => [n.key, 0]));
  for (const it of est.items) {
    if (it.removed) continue;
    const scale = it.origGrams ? it.grams / it.origGrams : 1;
    for (const k in totals) totals[k] += (it.nutrients[k] || 0) * scale;
  }
  return totals;
}

function itemsText(est) {
  if (!est.items.length) return est.savedItemsText || "";
  return est.items.filter((it) => !it.removed).map((it) => {
    const adjusted = it.origGrams && Math.round(it.grams) !== Math.round(it.origGrams) ? ", adjusted" : "";
    const grams = it.grams ? ` (~${Math.round(it.grams)} g${adjusted})` : "";
    return `${it.quantity ? `${it.quantity} → ` : ""}${it.food}${grams}`;
  }).join("; ");
}

async function calculate(e) {
  e.preventDefault();
  const description = $("#meal-text").value.trim();
  const box = $("#log-message");
  if (!description) return msg(box, "error", "Please describe what you ate.");
  const btn = $("#calc-btn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Calculating…`;
  msg(box, "", "");
  try {
    const result = await api("/api/estimate", { method: "POST", body: JSON.stringify({ description }) });
    state.estimate = fromApiEstimate(result, description);
    state.estimateStale = false;
    renderEstimate();
  } catch (err) {
    if (err === STALE) return;
    msg(box, "error", esc(err.message));
  } finally {
    btn.disabled = false;
    updateCalcButton();
  }
}

function updateCalcButton() {
  const btn = $("#calc-btn");
  if (btn.disabled) return;
  btn.textContent = state.estimate && state.estimateStale ? "Recalculate nutrition" : "Calculate nutrition";
}

function onDescriptionInput() {
  if (!state.estimate) return;
  const stale = $("#meal-text").value.trim() !== state.estimate.description;
  if (stale !== state.estimateStale) {
    state.estimateStale = stale;
    renderEstimateStatus();
    updateCalcButton();
  }
}

function renderEstimate() {
  const est = state.estimate;
  $("#estimate-card").hidden = !est;
  $("#estimate-empty").hidden = !!est;
  if (!est) return;
  $("#estimate-source").textContent = est.source || "Saved meal";
  $("#estimate-warnings").innerHTML = est.warnings.map((w) => `<div class="msg warn">${esc(w)}</div>`).join("");

  if (est.itemsEditable) {
    $("#estimate-items").innerHTML = `
      <p class="muted small">Check how your meal was understood. Remove anything that's wrong or change an amount — totals update instantly.</p>
      <div class="item-rows">${est.items.map((it, i) => `
        <div class="item-row ${it.removed ? "removed" : ""}">
          <div><b>${esc(it.food)}</b>${it.quantity ? `<div class="muted small">${esc(it.quantity)}</div>` : ""}</div>
          <label class="grams"><input type="number" min="1" step="any" class="item-grams" data-item="${i}"
            value="${Math.round(it.grams)}" ${it.removed ? "disabled" : ""} aria-label="Grams of ${esc(it.food)}"> g</label>
          <button class="btn ghost small-btn" data-toggle-item="${i}">${it.removed ? "Undo" : "Remove"}</button>
        </div>`).join("")}</div>`;
  } else if (est.items.length) {
    $("#estimate-items").innerHTML = `<ul class="items">${est.items.map((it) =>
      `<li>${esc(it.food)}${it.quantity ? ` — ${esc(it.quantity)}` : ""}${it.grams ? ` <span class="muted">(~${fmt(it.grams)} g)</span>` : ""}</li>`).join("")}</ul>
      <p class="muted small">To change something, edit the description and recalculate.</p>`;
  } else {
    $("#estimate-items").innerHTML = est.savedItemsText ? `<p class="small"><b>Interpreted as:</b> ${esc(est.savedItemsText)}</p>` : "";
  }
  renderEstimateTotals();
  renderEstimateStatus();
}

function renderEstimateTotals() {
  const est = state.estimate;
  const totals = currentTotals(est);
  const byKey = nutrientMap();
  const macros = ["energy_kcal", "protein_g", "carbs_g", "fat_g"].filter((k) => byKey[k]);
  $("#estimate-macros").innerHTML = macros.map((k) =>
    `<div class="macro"><b>${fmt(totals[k])}</b><span>${esc(byKey[k].name)} (${esc(byKey[k].unit)})</span></div>`).join("");
  $("#estimate-nutrients").innerHTML = nutrientGrid(totals);
}

function renderEstimateStatus() {
  const est = state.estimate;
  const noItems = est.itemsEditable && est.items.every((it) => it.removed);
  const blocked = state.estimateStale || noItems;
  $("#estimate-stale").innerHTML = state.estimateStale
    ? `<div class="msg error">You changed the description after calculating. <b>Recalculate</b> before saving so the nutrition matches.</div>`
    : noItems ? `<div class="msg error">All items are removed — undo one or discard this meal.</div>` : "";
  $("#save-btn").disabled = blocked;
  $("#save-btn").textContent = state.editing ? "Save changes" : "Save meal";
  $("#estimate-card").classList.toggle("stale", blocked);
}

function onItemGrams(e) {
  const i = Number(e.target.dataset.item);
  const g = parseFloat(e.target.value);
  if (!(g > 0)) return;
  state.estimate.items[i].grams = g;
  renderEstimateTotals();
}

function toggleItem(i) {
  const it = state.estimate.items[i];
  it.removed = !it.removed;
  renderEstimate();
}

function resetLogForm() {
  state.estimate = null;
  state.estimateStale = false;
  state.editing = null;
  $("#meal-text").value = "";
  $("#edit-banner").hidden = true;
  $("#log-title").textContent = "What did you eat?";
  renderEstimate();
  updateCalcButton();
}

async function saveMeal() {
  const est = state.estimate;
  if (!est || state.estimateStale) return;
  const btn = $("#save-btn");
  btn.disabled = true;
  const payload = {
    date: $("#meal-date").value, meal_type: $("#meal-type").value, description: est.description,
    items_text: itemsText(est), source: est.source, nutrients: currentTotals(est),
  };
  const editing = state.editing;
  try {
    if (editing) {
      await api(`/api/meals/${encodeURIComponent(editing.id)}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/meals", { method: "POST", body: JSON.stringify(payload) });
    }
    resetLogForm();
    state.weekDate = state.mealsWeekDate = state.dayDate = payload.date;
    msg($("#log-message"), "success", editing ? "✓ Changes saved." : "✓ Meal saved.");
    dataChanged();
    showImpact(payload, editing);
  } catch (err) {
    msg($("#log-message"), "error", esc(err.message));
    btn.disabled = false;
  }
}

async function showImpact(payload, editing) {
  try {
    const day = await api(`/api/daily?date=${payload.date}`);
    const byKey = nutrientMap();
    const pctOf = (k) => byKey[k] ? Math.round((payload.nutrients[k] || 0) / byKey[k].daily_reference * 100) : null;
    const rows = Object.fromEntries(day.rows.map((r) => [r.key, r]));
    const over = day.over_limit.map((k) => rows[k].nutrient);
    const when = day.is_today ? "Today so far" : `On ${fmtDate(day.date)}`;
    const energy = rows.energy_kcal ? ` · calories ${Math.round(rows.energy_kcal.pct)}% of goal` : "";
    msg($("#log-message"), "success", `
      <b>${editing ? "✓ Changes saved." : "✓ Meal saved."}</b>
      ${pctOf("energy_kcal") != null ? `This meal gives you <b>${pctOf("energy_kcal")}%</b> of your daily calories` : ""}${pctOf("protein_g") != null ? ` and <b>${pctOf("protein_g")}%</b> of your protein` : ""}.<br>
      ${when}: <b>${day.met_count}/${day.nutrient_count}</b> daily goals met${energy}${over.length ? ` · <span class="over-text">⚠ over limit: ${esc(over.join(", "))}</span>` : ""}.
      <button class="linkish" data-day="${day.date}">Open daily tracker →</button>`);
  } catch { /* the save itself succeeded; the summary is a nice-to-have */ }
}

function startEdit(meal) {
  state.editing = meal;
  $("#meal-date").value = meal.date;
  $("#meal-type").value = meal.meal_type;
  $("#meal-text").value = meal.description;
  showTab("log");
  state.estimate = fromSavedMeal(meal, { relog: false });
  state.estimateStale = false;
  $("#log-title").textContent = "Edit meal";
  $("#edit-banner").hidden = false;
  $("#edit-banner").innerHTML = `✎ Editing <b>${esc(meal.meal_type)}</b> on <b>${fmtDate(meal.date)}</b>. Change the date, meal or description, then save.
    <button class="linkish" id="cancel-edit">Cancel editing</button>`;
  msg($("#log-message"), "", "");
  renderEstimate();
  updateCalcButton();
}

function logAgain(meal) {
  if (state.editing) resetLogForm();
  $("#meal-date").value = state.today;
  $("#meal-text").value = meal.description;
  showTab("log");
  state.estimate = fromSavedMeal(meal, { relog: true });
  state.estimateStale = false;
  msg($("#log-message"), "info", "Loaded a previous meal — check the date and meal type, then save.");
  renderEstimate();
  updateCalcButton();
}

// ------------------------------------------------------------------ log meal: lists
async function loadRecent() {
  await ready;
  try {
    const { meals } = await fetchLatest("recent", "/api/recent-meals");
    rememberMeals(meals);
    $("#recent").innerHTML = meals.length
      ? `<span class="small muted">Log again:</span>` + meals.map((m) =>
        `<button type="button" class="chip recent" data-again="${esc(m.id)}" title="${esc(m.description)}">↻ ${esc(m.description.length > 38 ? m.description.slice(0, 36) + "…" : m.description)}</button>`).join("")
      : "";
    $("#examples").hidden = meals.length > 0;
  } catch (err) { if (err !== STALE) $("#recent").innerHTML = ""; }
}

function mealActions(m, compact) {
  if (!m.id) return `<span class="muted small" title="Close the spreadsheet so the app can give this row an ID">added in Excel</span>`;
  if (compact) {
    return `<span class="row-actions">
      <button class="icon-btn" data-again="${esc(m.id)}" title="Log again" aria-label="Log again">↻</button>
      <button class="icon-btn" data-edit="${esc(m.id)}" title="Edit meal" aria-label="Edit meal">✎</button>
      <button class="icon-btn del" data-del="${esc(m.id)}" title="Delete meal" aria-label="Delete meal">✕</button></span>`;
  }
  return `<div class="actions">
    <button class="btn ghost small-btn" data-again="${esc(m.id)}">↻ Log again</button>
    <button class="btn ghost small-btn" data-edit="${esc(m.id)}">✎ Edit</button>
    <button class="btn danger" data-del="${esc(m.id)}">Delete</button></div>`;
}

async function loadMeals() {
  await ready;
  const date = $("#meal-date").value || state.today;
  const list = $("#meal-list");
  try {
    const data = await fetchLatest("meals", `/api/meals?date=${date}`);
    rememberMeals(data.meals);
    if (!data.meals.length) {
      list.innerHTML = `<p class="muted">No meals logged for the week of ${fmtDate(data.week_start)} yet.</p>`;
      return;
    }
    const byDay = {};
    for (const m of data.meals) (byDay[m.date] ||= []).push(m);
    list.innerHTML = Object.keys(byDay).sort().reverse().map((d) => `
      <div class="day-group"><h4>${fmtDate(d, { weekday: "long", day: "numeric", month: "short" })}</h4>
        ${byDay[d].map((m) => `
          <div class="meal">
            <span class="type">${esc(m.meal_type)}</span>
            <span title="${esc(m.items_text)}">${esc(m.description)}</span>
            <span class="kcal">${fmt(m.nutrients.energy_kcal)} kcal</span>
            ${mealActions(m, true)}
          </div>`).join("")}
      </div>`).join("");
  } catch (err) {
    if (err === STALE) return;
    msg(list, "error", esc(err.message));
  }
}

async function deleteMeal(id) {
  const m = state.meals.get(id);
  if (!confirm(`Delete this meal from the log?${m ? `\n\n${m.meal_type} · ${fmtDate(m.date)}\n${m.description}` : ""}`)) return;
  try {
    await api(`/api/meals/${encodeURIComponent(id)}`, { method: "DELETE" });
    state.meals.delete(id);
    if (state.editing && state.editing.id === id) resetLogForm();
    toast("Meal deleted");
    dataChanged();
  } catch (err) {
    toast(err.message);
  }
}

// ------------------------------------------------------------------ my meals
function mealCard(m) {
  const meta = [
    m.items_text && `<span><b>Interpreted as:</b> ${esc(m.items_text)}</span>`,
    m.source && `<span><b>Estimated by:</b> ${esc(m.source)}</span>`,
    m.logged_at && `<span><b>Logged:</b> ${esc(m.logged_at.replace("T", " ").slice(0, 16))}</span>`,
  ].filter(Boolean).join("");
  return `<details class="meal-card">
    <summary>
      <span class="type">${esc(m.meal_type)}</span>
      <span>${esc(m.description)}</span>
      <span class="kcal">${fmt(m.nutrients.energy_kcal)} kcal · ${fmt(m.nutrients.protein_g)} g protein</span>
    </summary>
    <div class="meal-body">
      <div class="meal-meta">${meta}</div>
      ${nutrientGrid(m.nutrients)}
      ${mealActions(m, false)}
    </div>
  </details>`;
}

async function loadAllMeals() {
  await ready;
  const box = $("#all-meals");
  try {
    const data = await fetchLatest("allMeals", `/api/meals?date=${state.mealsWeekDate}`);
    rememberMeals(data.meals);
    const thisWeek = data.week_start <= state.today && state.today <= data.week_end;
    $("#meals-week-label").textContent = `${thisWeek ? "This week · " : ""}${fmtDate(data.week_start, { day: "numeric", month: "short" })} – ${fmtDate(data.week_end, { day: "numeric", month: "short", year: "numeric" })}`;
    $("#meals-week-sub").textContent = `${data.meals.length} meal${data.meals.length === 1 ? "" : "s"} logged`;
    $("#meals-next").disabled = thisWeek;
    if (!data.meals.length) {
      box.innerHTML = `<p class="muted">No meals logged this week.</p>`;
      return;
    }
    const byDay = {};
    for (const m of data.meals) (byDay[m.date] ||= []).push(m);
    box.innerHTML = Object.keys(byDay).sort().reverse().map((d) => {
      const kcal = byDay[d].reduce((sum, m) => sum + (m.nutrients.energy_kcal || 0), 0);
      return `<div class="day-block">
        <div class="day-block-head">
          <h3>${fmtDate(d, { weekday: "long", day: "numeric", month: "short" })}</h3>
          <span class="muted small">${byDay[d].length} meal${byDay[d].length === 1 ? "" : "s"} · ${fmt(kcal)} kcal ·
            <button class="linkish" data-day="${d}">Daily tracker →</button></span>
        </div>
        ${byDay[d].map(mealCard).join("")}
      </div>`;
    }).join("");
  } catch (err) {
    if (err === STALE) return;
    msg(box, "error", esc(err.message));
  }
}

async function sampleData(action) {
  const text = action === "remove"
    ? "Remove all sample meals? Your own meals are kept."
    : "Replace the sample meals with a fresh set for last week and this week? Your own meals are kept.";
  if (!confirm(text)) return;
  try {
    const r = await api("/api/sample-data", { method: "POST", body: JSON.stringify({ action }) });
    toast(action === "remove" ? `Removed ${r.removed} sample meals` : `Loaded ${r.added} sample meals`);
    dataChanged();
  } catch (err) { toast(err.message); }
}

// ------------------------------------------------------------------ daily tracker
async function loadDaily() {
  await ready;
  try {
    state.daily = await fetchLatest("daily", `/api/daily?date=${state.dayDate}`);
    rememberMeals(state.daily.meals);
    renderDaily();
  } catch (err) {
    if (err === STALE) return;
    msg($("#day-gaps"), "error", esc(err.message));
  }
}

function gapChips(keys, rows, over) {
  return keys.map((k) => {
    const r = rows[k];
    return over
      ? `<span class="gap-chip over">⚠ ${esc(r.nutrient)} <small>${Math.round(r.pct)}% of goal · over the limit</small></span>`
      : `<span class="gap-chip">${esc(r.nutrient)} <small>${Math.round(r.pct)}% · ${fmt(r.remaining)} ${esc(r.unit)} to go</small></span>`;
  }).join("");
}

function renderDaily() {
  const d = state.daily;
  const rows = Object.fromEntries(d.rows.map((r) => [r.key, r]));
  $("#day-label").textContent = d.is_today ? "Today" : fmtDate(d.date, { weekday: "long", day: "numeric", month: "long" });
  $("#day-date").value = d.date;
  $("#day-date").max = state.today;
  $("#day-next").disabled = d.is_today || d.is_future;

  $("#week-strip").innerHTML = d.week.map((w) => {
    const ratio = w.met_count / d.nutrient_count;
    const tone = !w.meals_logged ? "" : ratio >= 0.75 ? "tone-good" : ratio >= 0.5 ? "tone-ok" : ratio >= 0.25 ? "tone-warn" : "tone-bad";
    return `<button class="wday ${w.date === d.date ? "active" : ""} ${w.is_future ? "future" : ""}" data-day="${w.date}"
        title="${w.meals_logged} meals · ${w.met_count}/${d.nutrient_count} daily goals met${w.over_count ? ` · ${w.over_count} over limit` : ""}">
      <div class="dname">${fmtDate(w.date, { weekday: "short" })}</div>
      <div class="dnum">${fmtDate(w.date, { day: "numeric" })}</div>
      <div class="dmet">${w.meals_logged ? `${w.met_count}/${d.nutrient_count} met${w.over_count ? " ⚠" : ""}` : "—"}</div>
      <div class="bar"><div class="fill ${tone}" style="width:${Math.round(ratio * 100)}%"></div></div>
    </button>`;
  }).join("");

  const energy = rows.energy_kcal, protein = rows.protein_g;
  $("#day-stats").innerHTML = `
    <div class="stat"><b>${d.meals_logged}</b><span>meals logged</span></div>
    <div class="stat ${d.met_count >= d.nutrient_count * 0.75 ? "good" : ""}"><b>${d.met_count}<small class="muted">/${d.nutrient_count}</small></b><span>daily goals met</span></div>
    ${energy ? `<div class="stat ${energy.over_limit ? "over" : ""}"><b>${Math.round(energy.pct)}%</b><span>calories · ${fmt(energy.consumed)} of ${fmt(energy.daily_goal)} kcal</span></div>` : ""}
    ${protein ? `<div class="stat"><b>${Math.round(protein.pct)}%</b><span>protein · ${fmt(protein.consumed)} of ${fmt(protein.daily_goal)} g</span></div>` : ""}`;

  const gaps = $("#day-gaps");
  const inProgress = d.is_today ? `<p class="muted small">Today is still in progress — these numbers will change as you log more meals.</p>` : "";
  const overHtml = d.over_limit.length ? `<h3 class="over-head">⚠ Over the daily upper limit</h3><div class="gap-chips">${gapChips(d.over_limit, rows, true)}</div>` : "";
  if (d.is_future) {
    gaps.className = "card gaps none";
    gaps.innerHTML = `<h3>This day hasn't happened yet</h3>`;
  } else if (!d.meals_logged) {
    gaps.className = "card gaps";
    gaps.innerHTML = `<h3>No meals logged on this day</h3><p class="muted">Log a meal to see how the day stacks up against your daily goals.</p>`;
  } else if (d.very_low.length) {
    gaps.className = "card gaps";
    gaps.innerHTML = `<h3>🚨 Well below the daily goal (under 50%)</h3>${inProgress}
      <div class="gap-chips">${gapChips(d.very_low, rows, false)}</div>${overHtml}`;
  } else {
    gaps.className = `card gaps ${d.over_limit.length ? "" : "none"}`;
    gaps.innerHTML = `<h3>✅ Every nutrient is at least half-way to its daily goal</h3>${inProgress}${overHtml}`;
  }

  $("#day-filter").innerHTML = filterButtons(state.dayFilter, "dayfilter");
  renderDayTable();

  $("#day-insights-title").textContent = d.is_today ? "Improve today" : "Ideas for a day like this";
  $("#day-insights-sub").textContent = d.is_today
    ? "Suggestions for your next meal or snack, based on today's gaps."
    : "What could have been added or swapped on this day.";
  $("#day-insights-btn").textContent = d.is_today ? "Suggest what to eat" : "Suggest improvements";
  $("#day-insights-btn").disabled = d.is_future || !d.meals_logged;
  if (state.dayInsightsFor !== d.date) $("#day-insights-body").innerHTML = "";

  $("#day-meals").innerHTML = d.meals.length ? d.meals.map(mealCard).join("") : `<p class="muted">No meals logged on this day.</p>`;
}

function remainingCell(r, goalKey) {
  if (r.over_limit) {
    const limit = goalKey === "daily" ? r.daily_upper_limit : r.weekly_upper_limit;
    return `<span class="over-text">+${fmt(r.consumed - limit)} ${esc(r.unit)} over limit</span>`;
  }
  const done = goalKey === "daily" ? r.met : r.reached;
  return done ? "—" : `${fmt(r.remaining)} <span class="unit">${esc(r.unit)}</span>`;
}

function renderDayTable() {
  const d = state.daily;
  if (!d) return;
  const rows = sortRows(filterRows(d.rows, state.dayFilter), state.daySort);
  let html = "", lastCat = null;
  for (const r of rows) {
    if (state.daySort === "category" && r.category !== lastCat) {
      html += `<tr class="cat"><td colspan="6">${esc(r.category)}</td></tr>`;
      lastCat = r.category;
    }
    html += `<tr>
      <td>${esc(r.nutrient)}</td>
      <td class="num">${fmt(r.daily_goal)} <span class="unit">${esc(r.unit)}</span></td>
      <td class="num">${fmt(r.consumed)} <span class="unit">${esc(r.unit)}</span></td>
      <td><div class="barcell"><div class="bar"><div class="fill tone-${r.tone}" style="width:${Math.min(r.pct, 100)}%"></div></div><span class="pct">${Math.round(r.pct)}%</span></div></td>
      <td class="num">${remainingCell(r, "daily")}</td>
      <td><span class="status tone-${r.tone}">${r.met ? "✓ " : r.over_limit ? "⚠ " : ""}${esc(r.status)}</span></td>
    </tr>`;
  }
  $("#day-rows").innerHTML = html;
}

function goToDay(iso) {
  state.dayDate = iso > state.today ? state.today : iso;
  if (isActive("daily")) loadDaily();
  else showTab("daily");
}

async function loadDayInsights() {
  const btn = $("#day-insights-btn");
  const body = $("#day-insights-body");
  const label = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Thinking…`;
  body.innerHTML = `<p class="muted">Looking at this day's gaps…</p>`;
  try {
    const d = await api("/api/insights", { method: "POST", body: JSON.stringify({ date: state.dayDate, scope: "day" }) });
    renderInsights(body, d);
    state.dayInsightsFor = state.dayDate;
  } catch (err) {
    msg(body, "error", esc(err.message));
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

// ------------------------------------------------------------------ weekly dashboard
async function loadWeekly() {
  await ready;
  try {
    state.weekly = await fetchLatest("weekly", `/api/weekly?date=${state.weekDate}`);
    renderWeekly();
  } catch (err) {
    if (err === STALE) return;
    msg($("#gaps-card"), "error", esc(err.message));
  }
}

function bar(row, expected) {
  const pace = expected > 0 && expected < 100 ? `<span class="pace" style="left:${expected}%"></span>` : "";
  return `<div class="bar"><div class="fill ${cls(row.status)}" style="width:${Math.min(row.pct, 100)}%"></div>${pace}</div>`;
}

function renderWeekly() {
  const w = state.weekly;
  const rows = Object.fromEntries(w.rows.map((r) => [r.key, r]));
  $("#week-label").textContent = `${w.is_current_week ? "This week · " : ""}${fmtDate(w.week_start, { day: "numeric", month: "short" })} – ${fmtDate(w.week_end, { day: "numeric", month: "short", year: "numeric" })}`;
  $("#week-pace").textContent = w.is_current_week
    ? `Day ${w.days_elapsed} of 7 — to be on pace you'd be at about ${Math.round(w.expected_pct)}% of each weekly target.`
    : w.days_elapsed === 0 ? "This week hasn't started yet." : "Completed week — compared against full weekly targets.";
  $("#next-week").disabled = w.is_current_week || w.days_elapsed === 0;

  const far = w.substantially_below.length;
  $("#stats").innerHTML = `
    <div class="stat"><b>${w.meals_logged}</b><span>meals logged</span></div>
    <div class="stat"><b>${w.days_logged}<small class="muted">/7</small></b><span>days with meals</span></div>
    <div class="stat good"><b>${w.reached_count}<small class="muted">/${w.nutrient_count}</small></b><span>weekly targets reached</span></div>
    <div class="stat ${far ? "bad" : "good"}"><b>${far}</b><span>nutrients far behind</span></div>`;

  const gaps = $("#gaps-card");
  const overHtml = w.over_limit.length ? `<h3 class="over-head">⚠ Over the weekly upper limit</h3><div class="gap-chips">${gapChips(w.over_limit, rows, true)}</div>` : "";
  if (!w.meals_logged) {
    gaps.className = "card gaps";
    gaps.innerHTML = `<h3>No meals logged this week</h3><p class="muted">Log a meal to start tracking.</p>`;
  } else if (far) {
    gaps.className = "card gaps";
    gaps.innerHTML = `<h3>🚨 Biggest gaps — substantially below target</h3>
      <p class="muted small">Less than half of where you'd expect to be at this point in the week.</p>
      <div class="gap-chips">${gapChips(w.substantially_below, rows, false)}</div>${overHtml}`;
  } else {
    gaps.className = `card gaps ${w.over_limit.length ? "" : "none"}`;
    gaps.innerHTML = `<h3>✅ No major gaps</h3><p class="muted">Every nutrient is at least half-way to where it should be for this point in the week.</p>${overHtml}`;
  }

  const mini = (k) => { const r = rows[k]; return `<div class="mini"><span>${esc(r.nutrient)}</span>${bar(r, w.expected_pct)}<span class="pct">${Math.round(r.pct)}%</span></div>`; };
  $("#strongest").innerHTML = w.strongest.map(mini).join("");
  $("#weakest").innerHTML = w.weakest.map(mini).join("");

  $("#cat-filter").innerHTML = filterButtons(state.catFilter, "cat");
  renderTable();
}

function renderTable() {
  const w = state.weekly;
  if (!w) return;
  const rows = sortRows(filterRows(w.rows, state.catFilter), state.sort);
  let html = "", lastCat = null;
  for (const r of rows) {
    if (state.sort === "category" && r.category !== lastCat) {
      html += `<tr class="cat"><td colspan="6">${esc(r.category)}</td></tr>`;
      lastCat = r.category;
    }
    html += `<tr>
      <td>${esc(r.nutrient)}</td>
      <td class="num">${fmt(r.weekly_target)} <span class="unit">${esc(r.unit)}</span></td>
      <td class="num">${fmt(r.consumed)} <span class="unit">${esc(r.unit)}</span></td>
      <td><div class="barcell">${bar(r, w.expected_pct)}<span class="pct">${Math.round(r.pct)}%</span></div></td>
      <td class="num">${remainingCell(r, "weekly")}</td>
      <td><span class="status ${cls(r.status)}">${r.reached && !r.over_limit ? "✓ " : r.over_limit ? "⚠ " : ""}${esc(r.status)}</span></td>
    </tr>`;
  }
  $("#nutrient-rows").innerHTML = html;
}

async function loadTrends() {
  await ready;
  const box = $("#trends");
  try {
    const t = await fetchLatest("trends", `/api/trends?date=${state.weekDate}`);
    const rows = t.rows.filter((r) => r.is_key).length ? t.rows.filter((r) => r.is_key) : t.rows;
    const tone = (c) => c.over_limit ? "tone-over" : c.pct >= 100 ? "tone-good" : c.pct >= 75 ? "tone-ok" : c.pct >= 50 ? "tone-warn" : "tone-bad";
    box.innerHTML = `<table class="ntable trends-table">
      <thead><tr><th>Nutrient</th>${t.weeks.map((w) => `<th class="num">${fmtDate(w.week_start, { day: "numeric", month: "short" })}${w.is_current_week ? `<div class="muted small">so far (day ${w.days_elapsed})</div>` : ""}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((r) => `<tr><td>${esc(r.nutrient)}</td>${r.cells.map((c, i) => t.weeks[i].meals_logged
        ? `<td class="num"><span class="status ${tone(c)}">${c.over_limit ? "⚠ " : ""}${Math.round(c.pct)}%</span></td>`
        : `<td class="num muted">—</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  } catch (err) {
    if (err === STALE) return;
    msg(box, "error", esc(err.message));
  }
}

// ------------------------------------------------------------------ insights (weekly)
function renderInsights(el, d) {
  const list = (items) => `<ul class="insight-list">${items.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>`;
  const day = d.scope === "day";
  const changesTitle = day ? (state.daily && state.daily.is_today ? "Ideas for the rest of today" : "Ideas") : "Simple changes to try";
  el.innerHTML = `
    ${d.notice ? `<div class="msg warn">${esc(d.notice)}</div>` : ""}
    <p class="insight-summary">${esc(d.summary)}</p>
    ${d.focus.length ? `<h3>Nutrients to focus on</h3><div class="focus-grid">${d.focus.map((f) => `
      <div class="focus ${cls(f.status)} ${f.tone ? `tone-${f.tone}` : ""}">
        <div class="focus-head"><b>${esc(f.nutrient)}</b>${f.pct != null ? `<span class="status ${f.tone ? `tone-${f.tone}` : cls(f.status)}">${Math.round(f.pct)}%</span>` : ""}</div>
        <p>${esc(f.why_it_matters)}</p>
        ${f.food_sources.length ? `<div class="small muted" style="margin-bottom:4px">Good sources:</div><div class="foods">${f.food_sources.map((s) => `<span>${esc(s)}</span>`).join("")}</div>` : ""}
      </div>`).join("")}</div>` : ""}
    ${d.simple_changes.length ? `<h3>${changesTitle}</h3>${list(d.simple_changes)}` : ""}
    ${d.if_gaps_persist.length ? `<h3>If these gaps persist <span class="muted small">(general information)</span></h3>${list(d.if_gaps_persist)}` : ""}
    <div class="disclaimer"><b>Not medical advice.</b> ${esc(d.disclaimer)}</div>
    ${d.source ? `<p class="muted small source-line">Generated by: ${esc(d.source)}</p>` : ""}`;
}

async function updateInsightsHeader() {
  await ready;
  const start = weekStartOf(state.weekDate);
  const end = shiftDate(start, 6);
  $("#insights-week").textContent = `Based on the week of ${fmtDate(start, { day: "numeric", month: "short" })} – ${fmtDate(end, { day: "numeric", month: "short" })}.`;
  if (state.insightsFor !== start) {
    state.insightsFor = null;
    $("#insights-body").innerHTML = `<p class="muted">Get suggestions based on the nutrient gaps for this week.</p>`;
  }
}

async function loadInsights() {
  await ready;
  const btn = $("#insights-btn");
  const body = $("#insights-body");
  const start = weekStartOf(state.weekDate);
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>Analyzing…`;
  body.innerHTML = `<p class="muted">Looking at your weekly gaps…</p>`;
  try {
    const d = await api("/api/insights", { method: "POST", body: JSON.stringify({ date: state.weekDate, scope: "week" }) });
    renderInsights(body, d);
    state.insightsFor = start;
  } catch (err) {
    msg(body, "error", esc(err.message));
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze my week";
  }
}

// ------------------------------------------------------------------ goals
async function loadGoals() {
  await ready;
  try {
    state.goals = await api("/api/goals");
    $("#goals-file").textContent = state.goals.targets_file;
    $("#preset-select").innerHTML = `<option value="">Choose a preset…</option>` +
      state.goals.presets.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("");
    renderGoals(state.goals.nutrients);
    msg($("#goals-message"), "", "");
  } catch (err) {
    msg($("#goals-message"), "error", esc(err.message));
  }
}

function renderGoals(nutrients) {
  let html = "", lastCat = null;
  for (const n of nutrients) {
    if (n.category !== lastCat) {
      html += `<tr class="cat"><td colspan="5">${esc(n.category)}</td></tr>`;
      lastCat = n.category;
    }
    html += `<tr data-goal="${esc(n.key)}">
      <td>${esc(n.name)} <span class="unit">(${esc(n.unit)})</span></td>
      <td class="num"><input type="number" min="0" step="any" class="g-daily" value="${n.daily_reference}" aria-label="${esc(n.name)} daily goal"></td>
      <td class="num"><input type="number" min="0" step="any" class="g-weekly" value="${n.weekly_target}" aria-label="${esc(n.name)} weekly target"></td>
      <td class="num"><input type="number" min="0" step="any" class="g-limit" value="${n.daily_upper_limit ?? ""}" placeholder="none" aria-label="${esc(n.name)} daily upper limit"></td>
      <td><input type="checkbox" class="g-key" ${n.is_key ? "checked" : ""} aria-label="${esc(n.name)} is a key nutrient"></td>
    </tr>`;
  }
  $("#goals-rows").innerHTML = html;
}

function applyPreset() {
  const id = $("#preset-select").value;
  const preset = state.goals && state.goals.presets.find((p) => p.id === id);
  if (!preset) return toast("Choose a preset first");
  for (const tr of document.querySelectorAll("#goals-rows tr[data-goal]")) {
    const v = preset.daily[tr.dataset.goal];
    if (v == null) continue;
    tr.querySelector(".g-daily").value = v;
    tr.querySelector(".g-weekly").value = +(v * 7).toFixed(2);
  }
  msg($("#goals-message"), "info", `Applied “${esc(preset.label)}” — review and press <b>Save goals</b> to keep it.`);
}

async function saveGoals() {
  const goals = [...document.querySelectorAll("#goals-rows tr[data-goal]")].map((tr) => ({
    key: tr.dataset.goal,
    daily_reference: tr.querySelector(".g-daily").value,
    weekly_target: tr.querySelector(".g-weekly").value,
    daily_upper_limit: tr.querySelector(".g-limit").value,
    is_key: tr.querySelector(".g-key").checked,
  }));
  const btn = $("#goals-save");
  btn.disabled = true;
  try {
    const r = await api("/api/goals", { method: "PUT", body: JSON.stringify({ goals }) });
    state.config.nutrients = r.nutrients;
    state.goals.nutrients = r.nutrients;
    msg($("#goals-message"), "success", "✓ Goals saved. The trackers now use these values.");
    state.insightsFor = state.dayInsightsFor = null;
  } catch (err) {
    msg($("#goals-message"), "error", esc(err.message));
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ init
const ready = init();

async function init() {
  state.config = await api("/api/config");
  state.today = todayISO();
  state.weekDate = state.mealsWeekDate = state.dayDate = state.today;
  const c = state.config;
  $("#engine").innerHTML = `<span class="dot ${c.llm_enabled ? "" : "demo"}"></span>Nutrition engine: ${esc(c.nutrition_engine)}${c.llm_enabled ? "" : " — demo mode"}`;
  $("#meal-date").value = state.today;
  $("#meal-date").max = state.today;
  $("#meal-type").innerHTML = c.meal_types.map((t) => `<option>${esc(t)}</option>`).join("");
  const hour = new Date().getHours();
  $("#meal-type").value = hour < 11 ? "Breakfast" : hour < 16 ? "Lunch" : hour < 21 ? "Dinner" : "Snack";
  $("#meals-file").innerHTML = `Stored in <code>${esc(c.meals_file)}</code>`;
  $("#targets-file").textContent = c.targets_file;
  $("#examples").innerHTML = `<span class="small muted">Try:</span>` +
    EXAMPLES.map((x) => `<button type="button" class="chip example">${esc(x)}</button>`).join("");
  loadMeals();
  loadRecent();
}

document.addEventListener("click", (e) => {
  const t = e.target.closest("button");
  if (!t) return;
  const d = t.dataset;
  if (t.classList.contains("tab")) showTab(d.tab);
  else if (d.goto) showTab(d.goto);
  else if (d.del) deleteMeal(d.del);
  else if (d.edit) { const m = state.meals.get(d.edit); if (m) startEdit(m); }
  else if (d.again) { const m = state.meals.get(d.again); if (m) logAgain(m); }
  else if (d.day) goToDay(d.day);
  else if (d.toggleItem) toggleItem(Number(d.toggleItem));
  else if (d.quickdate) { $("#meal-date").value = shiftDate(state.today, Number(d.quickdate)); loadMeals(); }
  else if (d.cat) { state.catFilter = d.cat; $("#cat-filter").innerHTML = filterButtons(state.catFilter, "cat"); renderTable(); }
  else if (d.dayfilter) { state.dayFilter = d.dayfilter; $("#day-filter").innerHTML = filterButtons(state.dayFilter, "dayfilter"); renderDayTable(); }
  else if (t.id === "cancel-edit") { resetLogForm(); msg($("#log-message"), "", ""); }
  else if (t.classList.contains("example")) { $("#meal-text").value = t.textContent; onDescriptionInput(); $("#meal-text").focus(); }
});
document.addEventListener("input", (e) => {
  if (e.target.classList.contains("item-grams")) onItemGrams(e);
  if (e.target.classList.contains("g-daily")) {
    const v = parseFloat(e.target.value);
    if (v > 0) e.target.closest("tr").querySelector(".g-weekly").value = +(v * 7).toFixed(2);
  }
});
$("#meal-form").addEventListener("submit", calculate);
$("#meal-text").addEventListener("input", onDescriptionInput);
$("#meal-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("#meal-form").requestSubmit(); }
});
$("#save-btn").addEventListener("click", saveMeal);
$("#discard-btn").addEventListener("click", () => { resetLogForm(); msg($("#log-message"), "", ""); });
$("#meal-date").addEventListener("change", loadMeals);
$("#sort-select").addEventListener("change", (e) => { state.sort = e.target.value; renderTable(); });
$("#prev-week").addEventListener("click", () => { state.weekDate = shiftDate(state.weekDate, -7); loadWeekly(); loadTrends(); });
$("#next-week").addEventListener("click", () => { state.weekDate = shiftDate(state.weekDate, 7); loadWeekly(); loadTrends(); });
$("#insights-btn").addEventListener("click", loadInsights);
$("#meals-prev").addEventListener("click", () => { state.mealsWeekDate = shiftDate(state.mealsWeekDate, -7); loadAllMeals(); });
$("#meals-next").addEventListener("click", () => { state.mealsWeekDate = shiftDate(state.mealsWeekDate, 7); loadAllMeals(); });
$("#sample-remove").addEventListener("click", () => sampleData("remove"));
$("#sample-reload").addEventListener("click", () => sampleData("reload"));
$("#day-prev").addEventListener("click", () => goToDay(shiftDate(state.dayDate, -1)));
$("#day-next").addEventListener("click", () => goToDay(shiftDate(state.dayDate, 1)));
$("#day-today").addEventListener("click", () => goToDay(state.today));
$("#day-date").addEventListener("change", (e) => { if (e.target.value) goToDay(e.target.value); });
$("#day-sort").addEventListener("change", (e) => { state.daySort = e.target.value; renderDayTable(); });
$("#day-insights-btn").addEventListener("click", loadDayInsights);
$("#preset-apply").addEventListener("click", applyPreset);
$("#goals-save").addEventListener("click", saveGoals);
$("#goals-revert").addEventListener("click", () => { if (state.goals) { renderGoals(state.goals.nutrients); msg($("#goals-message"), "", ""); } });
window.addEventListener("focus", checkNewDay);
document.addEventListener("visibilitychange", () => { if (!document.hidden) checkNewDay(); });
setInterval(checkNewDay, 60000);

ready.catch((err) => { $("#engine").textContent = `Could not load app: ${err.message}`; });
