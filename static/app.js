/* Helios dashboard front-end */
"use strict";

const FMT = {
  money: (v) => (v == null ? "—" : "$" + v.toLocaleString(undefined, { maximumFractionDigits: 2 })),
  pct: (v) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%"),
  num: (v, d = 2) => (v == null ? "—" : v.toFixed(d)),
};

// Escape untrusted strings before interpolating into innerHTML. Instrument
// symbol/name (uploaded by users) and news headlines (from external feeds) are
// attacker-influenceable and rendered into the shared dashboard — never trust them.
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { mode: "instrument", ticker: null, modelId: null, horizon: 21, charts: {} };

Chart.defaults.color = "#8b98ad";
Chart.defaults.font.family = "-apple-system, system-ui, sans-serif";
Chart.defaults.borderColor = "rgba(255,255,255,0.05)";

/* ----------------------------------------------------------------------- */
/* Boot                                                                    */
/* ----------------------------------------------------------------------- */
window.addEventListener("DOMContentLoaded", async () => {
  if (typeof Chart === "undefined") {
    document.getElementById("loading").textContent =
      "Charting library failed to load (offline CDN). Data still loads via the API.";
  }
  bindControls();
  await loadMandates();
  await loadModels();
  await loadTickers();
});

function bindControls() {
  const h = document.getElementById("horizon");
  h.addEventListener("input", () => {
    state.horizon = +h.value;
    document.getElementById("horizonVal").textContent = h.value;
    setActivePreset(null);
  });
  h.addEventListener("change", refresh);

  document.querySelectorAll(".hbtn").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      state.horizon = btn.dataset.h;
      setActivePreset(btn.dataset.h);
      refresh();
    }));

  document.getElementById("uploadBtn").addEventListener("click", uploadCsv);
  document.getElementById("liveBtn").addEventListener("click", fetchLive);
  document.getElementById("modelUploadBtn").addEventListener("click", uploadModel);
}

function setActivePreset(h) {
  document.querySelectorAll(".hbtn").forEach((b) => b.classList.toggle("active", b.dataset.h === h));
}

/* Re-run whatever is currently selected at the current horizon. */
function refresh() {
  if (state.mode === "model" && state.modelId) analyzeModel(state.modelId);
  else if (state.ticker) analyze(state.ticker);
}

/* ----------------------------------------------------------------------- */
/* Data loading                                                            */
/* ----------------------------------------------------------------------- */
async function api(url, opts) {
  const r = await fetch(url, opts);
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || "Request failed");
  return j;
}

async function loadTickers(selectSymbol) {
  const { tickers, live_available } = await api("/api/tickers");
  if (live_available) {
    document.getElementById("livePanel").hidden = false;
    document.getElementById("dataMode").textContent = "live data available";
    document.getElementById("dataMode").className = "badge badge-live";
  }
  const list = document.getElementById("tickerList");
  list.innerHTML = "";
  tickers.forEach((t) => {
    const li = document.createElement("li");
    li.dataset.symbol = t.symbol;
    const dir = t.change_pct >= 0 ? "up" : "down";
    const dot = t.source !== "sample" ? `<span class="src-dot">●</span>` : "";
    li.innerHTML = `
      <div><div class="sym">${esc(t.symbol)}${dot}</div><div class="nm">${esc(t.name)}</div></div>
      <div class="px"><div class="p">${FMT.money(t.last_price)}</div>
        <div class="c ${dir}">${FMT.pct(t.change_pct)}</div></div>`;
    li.addEventListener("click", () => analyze(t.symbol));
    list.appendChild(li);
  });
  const first = selectSymbol || (tickers[0] && tickers[0].symbol);
  if (first) analyze(first);
}

async function analyze(symbol) {
  state.mode = "instrument";
  state.ticker = symbol;
  state.modelId = null;
  document.querySelectorAll("#modelList li").forEach((li) => li.classList.remove("active"));
  document.querySelectorAll("#tickerList li").forEach((li) =>
    li.classList.toggle("active", li.dataset.symbol === symbol));
  setPresetsEnabled(false); // long-horizon presets are for models
  const hz = typeof state.horizon === "number" ? state.horizon : 21;
  document.getElementById("loading").hidden = false;
  document.getElementById("dashboard").hidden = true;
  try {
    const d = await api(`/api/analyze?ticker=${encodeURIComponent(symbol)}&horizon=${hz}`);
    render(d);
    document.getElementById("loading").hidden = true;
    document.getElementById("dashboard").hidden = false;
  } catch (e) {
    document.getElementById("loading").textContent = "Error: " + e.message;
  }
}

/* ----------------------------------------------------------------------- */
/* Models                                                                  */
/* ----------------------------------------------------------------------- */
async function loadMandates() {
  try {
    const { mandates } = await api("/api/mandates");
    const sel = document.getElementById("modelMandate");
    sel.innerHTML = mandates
      .map((m) => `<option value="${esc(m.key)}">${esc(m.label)} · vol≤${m.target_vol_pct}%</option>`)
      .join("");
    const bal = mandates.find((m) => m.key === "balanced");
    if (bal) sel.value = "balanced";
    state.mandates = Object.fromEntries(mandates.map((m) => [m.key, m]));
  } catch (e) { /* non-fatal */ }
}

async function loadModels(selectId) {
  const { models } = await api("/api/models");
  const list = document.getElementById("modelList");
  document.getElementById("noModels").hidden = models.length > 0;
  list.innerHTML = "";
  models.forEach((m) => {
    const li = document.createElement("li");
    li.dataset.id = m.id;
    li.innerHTML = `
      <div><div class="sym">${esc(m.name)}</div>
        <div class="nm">${esc(m.mandate_label)} · ${m.n_holdings} holdings</div></div>
      <div class="px"><div class="c subtle">${esc(m.top || "")}</div></div>`;
    li.addEventListener("click", () => analyzeModel(m.id));
    list.appendChild(li);
  });
  if (selectId) analyzeModel(selectId);
}

async function analyzeModel(id) {
  state.mode = "model";
  state.modelId = id;
  state.ticker = null;
  document.querySelectorAll("#tickerList li").forEach((li) => li.classList.remove("active"));
  document.querySelectorAll("#modelList li").forEach((li) =>
    li.classList.toggle("active", li.dataset.id === id));
  setPresetsEnabled(true);
  document.getElementById("loading").hidden = false;
  document.getElementById("dashboard").hidden = true;
  try {
    const d = await api(`/api/model/analyze?id=${encodeURIComponent(id)}&horizon=${state.horizon}`);
    renderModel(d);
    document.getElementById("loading").hidden = true;
    document.getElementById("dashboard").hidden = false;
  } catch (e) {
    document.getElementById("loading").textContent = "Error: " + e.message;
  }
}

function setPresetsEnabled(on) {
  document.querySelectorAll(".hbtn").forEach((b) => { if (!on) b.disabled = true; });
  if (!on) setActivePreset(typeof state.horizon === "string" ? state.horizon : null);
}

/* ----------------------------------------------------------------------- */
/* Render                                                                  */
/* ----------------------------------------------------------------------- */
/* Show/hide the model-only panels. */
function toggleModelPanels(isModel) {
  ["mandateRow", "provenanceBanner", "mandatePanel", "holdingsPanel",
   "insightsPanel", "horizonTag"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.hidden = !isModel;
  });
  document.getElementById("newsCard").hidden = isModel;       // news is per-instrument
  document.getElementById("featuresWrap").hidden = isModel;   // feature weights are instrument-level
}

function render(d) {
  toggleModelPanels(false);
  document.getElementById("instName").textContent = `${d.symbol} · ${d.name}`;
  document.getElementById("instMeta").textContent =
    `source: ${d.source} · ${d.series.dates.length} sessions shown`;
  document.getElementById("priceTitle").textContent = "Price, trend & signal markers";

  renderSignal(d.signal);
  renderMetrics(d.metrics);
  renderPriceChart(d.series);
  renderForecast(d.series, d.forecast);
  renderRsi(d.series);
  renderMacd(d.series);
  renderBreakdown(d.signal, d.forecast);
  renderNews(d.sentiment);
  renderBacktest(d.backtest);
}

function renderSignal(s) {
  const a = document.getElementById("signalAction");
  a.textContent = s.action;
  a.className = "signal-action " + s.action.toLowerCase();
  const fill = document.getElementById("gaugeFill");
  fill.style.width = s.conviction_pct + "%";
  fill.style.background =
    s.action === "BUY" ? "var(--buy)" : s.action === "SELL" ? "var(--sell)" : "var(--hold)";
  document.getElementById("convictionVal").textContent = s.conviction_pct;
  document.getElementById("convictionBand").textContent = s.conviction_band ? `· ${s.conviction_band}` : "";
  document.getElementById("rationale").textContent = s.headline_rationale || s.rationale || "";
  const cav = document.getElementById("caveats");
  cav.innerHTML = (s.caveats || []).map((c) => `<li>${esc(c)}</li>`).join("");
}

function renderMetrics(m) {
  const cards = [
    ["Last price", FMT.money(m.last_price)],
    ["Daily move", FMT.pct(m.daily_change_pct), m.daily_change_pct],
    ["Annual return", FMT.pct(m.annual_return_pct), m.annual_return_pct],
    ["Volatility", FMT.num(m.annual_vol_pct, 1) + "%"],
    ["Sharpe", FMT.num(m.sharpe, 2), m.sharpe],
    ["Sortino", FMT.num(m.sortino, 2), m.sortino],
    ["Max drawdown", FMT.num(m.max_drawdown_pct, 1) + "%", m.max_drawdown_pct],
    ["52w range", FMT.money(m["52w_low"]) + " – " + FMT.money(m["52w_high"])],
  ];
  document.getElementById("metricsGrid").innerHTML = cards
    .map(([label, value, color]) => {
      const cls = color == null ? "" : color >= 0 ? "up" : "down";
      return `<div class="metric"><div class="label">${label}</div>
        <div class="value ${cls}">${value}</div></div>`;
    })
    .join("");
}

function destroy(id) { if (state.charts[id]) { state.charts[id].destroy(); delete state.charts[id]; } }

function renderPriceChart(s) {
  destroy("priceChart");
  if (typeof Chart === "undefined") return;
  const buy = s.dates.map(() => null), sell = s.dates.map(() => null);
  s.markers.forEach((mk) => {
    const i = s.dates.indexOf(mk.date);
    if (i >= 0) (mk.type === "buy" ? buy : sell)[i] = mk.price;
  });
  state.charts.priceChart = new Chart(document.getElementById("priceChart"), {
    type: "line",
    data: {
      labels: s.dates,
      datasets: [
        { label: "Bollinger upper", data: s.bb_upper, borderColor: "rgba(79,140,255,0.18)",
          borderWidth: 1, pointRadius: 0, fill: false },
        { label: "Bollinger lower", data: s.bb_lower, borderColor: "rgba(79,140,255,0.18)",
          borderWidth: 1, pointRadius: 0, fill: "-1", backgroundColor: "rgba(79,140,255,0.05)" },
        { label: "Close", data: s.close, borderColor: "#e6edf6", borderWidth: 1.6,
          pointRadius: 0, fill: false, tension: 0.05 },
        { label: "SMA50", data: s.sma50, borderColor: "#4f8cff", borderWidth: 1.2,
          pointRadius: 0, fill: false },
        { label: "SMA200", data: s.sma200, borderColor: "#e9b949", borderWidth: 1.2,
          pointRadius: 0, fill: false },
        { label: "Buy", data: buy, borderColor: "#22c98a", backgroundColor: "#22c98a",
          pointStyle: "triangle", pointRadius: 7, showLine: false },
        { label: "Sell", data: sell, borderColor: "#f0566d", backgroundColor: "#f0566d",
          pointStyle: "triangle", rotation: 180, pointRadius: 7, showLine: false },
      ],
    },
    options: baseOpts({ legendFilter: ["Close", "SMA50", "SMA200"] }),
  });
}

function renderForecast(s, fc) {
  destroy("forecastChart");
  document.getElementById("forecastNote").textContent =
    `${fc.horizon_days}d horizon · p(up) ${(fc.prob_up * 100).toFixed(0)}%`;
  // Stats row
  const q = fc.quality || {};
  const stats = [
    ["Expected return", FMT.pct(fc.expected_return_pct), fc.expected_return_pct],
    ["Annualized drift", FMT.pct(fc.annualized_drift_pct), fc.annualized_drift_pct],
    ["Expected vol", FMT.num(fc.expected_vol_pct, 1) + "%"],
    ["Dir. accuracy", q.directional_accuracy == null ? "—" : (q.directional_accuracy * 100).toFixed(1) + "%"],
  ];
  document.getElementById("forecastStats").innerHTML = stats
    .map(([k, v, c]) => `<div class="stat"><div class="k">${k}</div>
      <div class="v ${c == null ? "" : c >= 0 ? "up" : "down"}">${v}</div></div>`).join("");

  if (typeof Chart === "undefined") return;
  const tail = 60;
  const histDates = s.dates.slice(-tail);
  const histClose = s.close.slice(-tail);
  const anchorDate = histDates[histDates.length - 1];
  const anchorPx = histClose[histClose.length - 1];

  const labels = histDates.concat(fc.dates);
  const pad = (arr) => histDates.slice(0, -1).map(() => null).concat([anchorPx], arr);
  const histLine = histClose.concat(fc.dates.map(() => null));

  state.charts.forecastChart = new Chart(document.getElementById("forecastChart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "p95", data: pad(fc.bands.p95), borderColor: "transparent", pointRadius: 0, fill: false },
        { label: "p05", data: pad(fc.bands.p05), borderColor: "transparent", pointRadius: 0,
          fill: "-1", backgroundColor: "rgba(79,140,255,0.10)" },
        { label: "p75", data: pad(fc.bands.p75), borderColor: "transparent", pointRadius: 0, fill: false },
        { label: "p25", data: pad(fc.bands.p25), borderColor: "transparent", pointRadius: 0,
          fill: "-1", backgroundColor: "rgba(79,140,255,0.18)" },
        { label: "Forecast (median)", data: pad(fc.bands.p50), borderColor: "#4f8cff",
          borderWidth: 1.8, borderDash: [5, 3], pointRadius: 0, fill: false },
        { label: "Actual", data: histLine, borderColor: "#e6edf6", borderWidth: 1.6,
          pointRadius: 0, fill: false },
      ],
    },
    options: baseOpts({ legendFilter: ["Actual", "Forecast (median)"], sparseX: true }),
  });
}

function renderRsi(s) {
  destroy("rsiChart");
  if (typeof Chart === "undefined") return;
  state.charts.rsiChart = new Chart(document.getElementById("rsiChart"), {
    type: "line",
    data: {
      labels: s.dates,
      datasets: [
        { label: "RSI", data: s.rsi, borderColor: "#a78bfa", borderWidth: 1.4, pointRadius: 0, fill: false },
        { label: "70", data: s.dates.map(() => 70), borderColor: "rgba(240,86,109,0.4)",
          borderWidth: 1, borderDash: [4, 4], pointRadius: 0 },
        { label: "30", data: s.dates.map(() => 30), borderColor: "rgba(34,201,138,0.4)",
          borderWidth: 1, borderDash: [4, 4], pointRadius: 0 },
      ],
    },
    options: baseOpts({ hideLegend: true, yMin: 0, yMax: 100, sparseX: true }),
  });
}

function renderMacd(s) {
  destroy("macdChart");
  if (typeof Chart === "undefined") return;
  state.charts.macdChart = new Chart(document.getElementById("macdChart"), {
    data: {
      labels: s.dates,
      datasets: [
        { type: "bar", label: "Histogram", data: s.macd_hist,
          backgroundColor: s.macd_hist.map((v) => (v >= 0 ? "rgba(34,201,138,0.5)" : "rgba(240,86,109,0.5)")) },
        { type: "line", label: "MACD", data: s.macd, borderColor: "#4f8cff",
          borderWidth: 1.3, pointRadius: 0, fill: false },
        { type: "line", label: "Signal", data: s.macd_signal, borderColor: "#e9b949",
          borderWidth: 1.3, pointRadius: 0, fill: false },
      ],
    },
    options: baseOpts({ hideLegend: true, sparseX: true }),
  });
}

function renderBreakdown(sig, fc) {
  document.getElementById("breakdown").innerHTML = sig.components
    .map((c) => {
      const w = Math.min(Math.abs(c.contribution) * 200, 50); // half-width %
      const bar = c.contribution >= 0
        ? `<div class="bar-pos" style="width:${w}%"></div>`
        : `<div class="bar-neg" style="width:${w}%"></div>`;
      const wt = c.effective_weight != null ? ` <span class="subtle">${(c.effective_weight * 100).toFixed(0)}%</span>` : "";
      return `<div class="bar-row"><div class="name">${esc(c.name)}${wt}</div>
        <div class="bar-track"><div class="zero"></div>${bar}</div>
        <div class="val">${c.contribution >= 0 ? "+" : ""}${c.contribution.toFixed(2)}</div></div>`;
    })
    .join("");
  // Per-component plain-English clauses (lead with dominant driver).
  const ordered = [...sig.components].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
  document.getElementById("componentClauses").innerHTML = ordered
    .filter((c) => c.clause)
    .map((c) => `<div class="clause">${esc(c.clause)}</div>`)
    .join("");
  document.getElementById("features").innerHTML = ((fc && fc.feature_weights) || [])
    .slice(0, 6)
    .map((f) => `<span class="feat-chip">${esc(f.feature)}: ${f.weight >= 0 ? "+" : ""}${f.weight.toFixed(3)}</span>`)
    .join("");
}

function renderNews(sent) {
  const badge = document.getElementById("sentBadge");
  badge.textContent = `${sent.aggregate_label} (${sent.aggregate_score >= 0 ? "+" : ""}${sent.aggregate_score})`;
  badge.className = "badge " +
    (sent.aggregate_label === "positive" ? "badge-pos" : sent.aggregate_label === "negative" ? "badge-neg" : "badge-neu");
  const list = document.getElementById("newsList");
  if (!sent.items.length) {
    list.innerHTML = `<li><span class="subtle">No headlines available for this instrument.</span></li>`;
    return;
  }
  list.innerHTML = sent.items
    .map((i) => `<li><span>${esc(i.headline)}</span>
      <span class="sent-chip sent-${esc(i.label)}">${esc(i.label)} ${i.score >= 0 ? "+" : ""}${i.score}</span></li>`)
    .join("");
}

function renderBacktest(bt) {
  destroy("backtestChart");
  const rows = [
    ["Strategy return", FMT.pct(bt.strategy.total_return_pct), bt.strategy.total_return_pct],
    ["Buy & hold", FMT.pct(bt.benchmark.total_return_pct), bt.benchmark.total_return_pct],
    ["Strategy Sharpe", FMT.num(bt.strategy.sharpe, 2)],
    ["Strategy max DD", FMT.num(bt.strategy.max_drawdown_pct, 1) + "%"],
    ["Win rate", FMT.num(bt.win_rate_pct, 0) + "%"],
    ["Trades", String(bt.n_trades)],
    ["Time in market", FMT.num(bt.exposure_pct, 0) + "%"],
    ["Vol reduction", FMT.num(bt.benchmark.annual_vol_pct - bt.strategy.annual_vol_pct, 1) + "%"],
  ];
  document.getElementById("backtestStats").innerHTML = rows
    .map(([k, v, c]) => `<div class="stat"><div class="k">${k}</div>
      <div class="v ${c == null ? "" : c >= 0 ? "up" : "down"}">${v}</div></div>`).join("");

  if (typeof Chart === "undefined") return;
  state.charts.backtestChart = new Chart(document.getElementById("backtestChart"), {
    type: "line",
    data: {
      labels: bt.dates,
      datasets: [
        { label: "Signal strategy", data: bt.strategy_curve, borderColor: "#22c98a",
          borderWidth: 1.8, pointRadius: 0, fill: false },
        { label: "Buy & hold", data: bt.benchmark_curve, borderColor: "#8b98ad",
          borderWidth: 1.3, pointRadius: 0, fill: false, borderDash: [5, 3] },
      ],
    },
    options: baseOpts({ sparseX: true }),
  });
}

/* ----------------------------------------------------------------------- */
/* Model rendering                                                         */
/* ----------------------------------------------------------------------- */
function renderModel(d) {
  toggleModelPanels(true);
  const mn = d.mandate || {};
  document.getElementById("instName").textContent = d.name;
  document.getElementById("instMeta").textContent =
    `${d.holdings.length} holdings · ${d.series.dates.length} sessions${d.warnings && d.warnings.length ? " · " + d.warnings[0] : ""}`;
  document.getElementById("priceTitle").textContent = "Model NAV, trend & signal markers";

  const badge = document.getElementById("mandateBadge");
  badge.textContent = mn.label || mn.key || "—";
  badge.className = "badge badge-live";
  document.getElementById("modelContextText").textContent = d.context ? `“${d.context}”` : "";

  // Horizon tag
  const tag = document.getElementById("horizonTag");
  tag.hidden = false;
  tag.textContent = d.horizon.kind === "long"
    ? `${d.horizon.label} strategic projection`
    : `${d.horizon.value}d tactical signal`;

  renderProvenance(d.provenance);
  renderSignal(d.signal);
  renderMetricsModel(d.metrics, mn);
  renderMandateFit(d.metrics, mn, d.signal);
  renderHoldings(d.holdings, d.concentration, mn);
  renderInsights(d.insights);
  renderPriceChart(d.series);
  if (d.forecast && d.forecast.kind === "long") renderForecastLong(d.forecast, mn);
  else renderForecast(d.series, d.forecast);
  renderRsi(d.series);
  renderMacd(d.series);
  renderBreakdown(d.signal, d.forecast_short);
  renderBacktest(d.backtest);
  setPresetsAvailability(d.horizon.available_long);
}

function setPresetsAvailability(avail) {
  document.querySelectorAll(".hbtn").forEach((b) => {
    b.disabled = !avail.includes(b.dataset.h);
  });
}

function renderProvenance(p) {
  const el = document.getElementById("provenanceBanner");
  if (!p || !p.simulated_weight_pct) { el.hidden = true; return; }
  el.hidden = false;
  el.className = "banner amber";
  el.innerHTML = `⚠ <b>${p.simulated_weight_pct}% of portfolio weight uses simulated price history</b>
    (${p.n_simulated} of ${p.n_holdings} holdings had no live/sample data: ${esc((p.simulated_symbols || []).join(", "))}).
    Forecasts and metrics are illustrative, not market-calibrated.`;
}

function renderMetricsModel(m, mn) {
  const tgt = mn.target_vol_pct;
  const cards = [
    ["NAV (base 100)", FMT.num(m.last_price, 1)],
    ["Annual return", FMT.pct(m.annual_return_pct), m.annual_return_pct],
    ["Volatility", FMT.num(m.annual_vol_pct, 1) + "%", tgt ? (m.annual_vol_pct <= tgt * 1.15 ? 1 : -1) : null],
    ["Sharpe", FMT.num(m.sharpe, 2), m.sharpe],
    ["Sortino", FMT.num(m.sortino, 2), m.sortino],
    ["Max drawdown", FMT.num(m.max_drawdown_pct, 1) + "%", m.max_drawdown_pct],
    ["Mandate vol target", "≤ " + FMT.num(tgt, 0) + "%"],
    ["Drawdown tolerance", "−" + FMT.num(mn.max_drawdown_tolerance_pct, 0) + "%"],
  ];
  document.getElementById("metricsGrid").innerHTML = cards
    .map(([label, value, color]) => {
      const cls = color == null ? "" : color >= 0 ? "up" : "down";
      return `<div class="metric"><div class="label">${label}</div>
        <div class="value ${cls}">${value}</div></div>`;
    }).join("");
}

function renderMandateFit(m, mn, sig) {
  const vol = m.annual_vol_pct, tgt = mn.target_vol_pct;
  const dd = Math.abs(m.max_drawdown_pct), tol = mn.max_drawdown_tolerance_pct;
  const volOk = vol <= tgt * 1.15, ddOk = dd <= tol;
  const bar = (val, max, ok) => {
    const pct = Math.min((val / max) * 100, 100);
    return `<div class="fit-bar"><div style="width:${pct}%;background:${ok ? "var(--buy)" : "var(--sell)"}"></div></div>`;
  };
  document.getElementById("mandateFitNote").textContent =
    `signal conviction scaled ×${sig.mandate_fit} for mandate fit`;
  document.getElementById("mandateFit").innerHTML = `
    <div class="fit-item"><div class="k">Volatility vs target</div>
      <div class="v ${volOk ? "up" : "down"}">${FMT.num(vol, 1)}% / ${FMT.num(tgt, 0)}%</div>
      ${bar(vol, tgt * 2, volOk)}</div>
    <div class="fit-item"><div class="k">Max drawdown vs tolerance</div>
      <div class="v ${ddOk ? "up" : "down"}">−${FMT.num(dd, 1)}% / −${FMT.num(tol, 0)}%</div>
      ${bar(dd, tol * 2, ddOk)}</div>
    <div class="fit-item"><div class="k">Return vs mandate target</div>
      <div class="v ${m.annual_return_pct >= mn.target_return_pct ? "up" : "down"}">
        ${FMT.pct(m.annual_return_pct)} / ${FMT.num(mn.target_return_pct, 1)}%</div>
      ${bar(Math.max(m.annual_return_pct, 0), Math.max(mn.target_return_pct * 2, 1), m.annual_return_pct >= mn.target_return_pct)}</div>`;
}

function renderHoldings(holdings, conc, mn) {
  document.getElementById("concNote").textContent =
    `HHI ${conc.hhi.toFixed(2)} · ${conc.n_eff.toFixed(1)} effective holdings · avg corr ${conc.corr_mean.toFixed(2)}`;
  document.getElementById("holdingsBody").innerHTML = holdings
    .map((h) => `<tr>
      <td class="tk">${esc(h.ticker)}</td>
      <td>${(h.weight * 100).toFixed(1)}%</td>
      <td><span class="src-tag src-${h.source}">${h.source}</span></td>
      <td class="${h.window_return_pct >= 0 ? "up" : "down"}">${FMT.pct(h.window_return_pct)}</td>
      <td>${h.mrc_pct != null ? h.mrc_pct.toFixed(0) + "%" : "—"}</td>
      <td><span class="sig-tag sig-${h.signal}">${h.signal}</span></td></tr>`)
    .join("");
}

function renderInsights(ins) {
  document.getElementById("insightsCount").textContent =
    ins.length ? `${ins.length} finding${ins.length > 1 ? "s" : ""}` : "no issues flagged";
  document.getElementById("insightsList").innerHTML = ins.length ? ins
    .map((i) => `<div class="insight ${i.severity}">
      <div class="ihead"><span class="sev">${i.severity}</span>
        <span class="icat">${esc(i.category)}</span></div>
      <p class="imsg">${esc(i.message)}</p>
      <div class="iact">${esc(i.suggested_action)}</div></div>`)
    .join("") : `<div class="subtle">No mandate, concentration, or risk issues flagged for this model.</div>`;
}

function renderForecastLong(fc, mn) {
  destroy("forecastChart");
  document.getElementById("forecastTitle").textContent =
    `${fc.label} strategic value projection ($10,000 base)`;
  document.getElementById("forecastNote").textContent =
    `drift ${fc.params.mu_long_pct >= 0 ? "+" : ""}${fc.params.mu_long_pct}%/yr (λ${fc.params.anchor_weight_lambda} to anchor) · vol ${fc.params.sigma_eff_pct}%`;
  const cagr = fc.cagr_pct;
  const stats = [
    ["Median value", FMT.money(fc.terminal.p50)],
    ["Median CAGR", FMT.pct(cagr.p50), cagr.p50],
    ["P05–P95 CAGR", `${FMT.num(cagr.p05, 1)}% … ${FMT.num(cagr.p95, 1)}%`],
    ["Prob. positive", (fc.prob_positive * 100).toFixed(0) + "%"],
    [`Meets ${fc.mandate_target_pct}% target`, (fc.prob_meets_mandate * 100).toFixed(0) + "%"],
    ["Median path drawdown", FMT.num(fc.drawdown_median_pct, 0) + "%", fc.drawdown_median_pct],
    [`Breach −${mn.max_drawdown_tolerance_pct}% tol`, (fc.prob_breach_maxdd * 100).toFixed(0) + "%",
      fc.prob_breach_maxdd > 0.2 ? -1 : 1],
    ["Value range", `${FMT.money(fc.terminal.p05)} – ${FMT.money(fc.terminal.p95)}`],
  ];
  document.getElementById("forecastStats").innerHTML = stats
    .map(([k, v, c]) => `<div class="stat"><div class="k">${k}</div>
      <div class="v ${c == null ? "" : c >= 0 ? "up" : "down"}">${v}</div></div>`).join("");
  const dis = document.getElementById("forecastDisclaimer");
  dis.hidden = false;
  dis.textContent = fc.disclaimer;

  if (typeof Chart === "undefined") return;
  const labels = ["now"].concat(fc.dates);
  const base = fc.base_value;
  const pad = (arr) => [base].concat(arr);
  state.charts.forecastChart = new Chart(document.getElementById("forecastChart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "p95", data: pad(fc.bands.p95), borderColor: "transparent", pointRadius: 0, fill: false },
        { label: "p05", data: pad(fc.bands.p05), borderColor: "transparent", pointRadius: 0,
          fill: "-1", backgroundColor: "rgba(79,140,255,0.10)" },
        { label: "p75", data: pad(fc.bands.p75), borderColor: "transparent", pointRadius: 0, fill: false },
        { label: "p25", data: pad(fc.bands.p25), borderColor: "transparent", pointRadius: 0,
          fill: "-1", backgroundColor: "rgba(79,140,255,0.18)" },
        { label: "Median path", data: pad(fc.bands.p50), borderColor: "#4f8cff",
          borderWidth: 1.8, pointRadius: 0, fill: false },
        { label: "Start", data: labels.map(() => base), borderColor: "rgba(139,152,173,0.5)",
          borderWidth: 1, borderDash: [4, 4], pointRadius: 0, fill: false },
      ],
    },
    options: baseOpts({ legendFilter: ["Median path", "Start"], sparseX: true }),
  });
}

/* ----------------------------------------------------------------------- */
/* Chart options helper                                                    */
/* ----------------------------------------------------------------------- */
function baseOpts(o = {}) {
  return {
    responsive: true, maintainAspectRatio: true, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: o.hideLegend ? { display: false } : {
        display: true, position: "top", align: "end",
        labels: {
          boxWidth: 10, boxHeight: 10, font: { size: 11 },
          filter: (item) => !o.legendFilter || o.legendFilter.includes(item.text),
        },
      },
      tooltip: { backgroundColor: "#1a2231", borderColor: "#243044", borderWidth: 1 },
    },
    scales: {
      x: { grid: { display: false }, ticks: { maxTicksLimit: o.sparseX ? 6 : 8, font: { size: 10 } } },
      y: {
        grid: { color: "rgba(255,255,255,0.05)" }, ticks: { font: { size: 10 } },
        min: o.yMin, max: o.yMax,
      },
    },
  };
}

/* ----------------------------------------------------------------------- */
/* Uploads / live fetch                                                    */
/* ----------------------------------------------------------------------- */
async function uploadModel() {
  const file = document.getElementById("modelFile").files[0];
  if (!file) return toast("Choose an Excel or CSV file first.", "error");
  const btn = document.getElementById("modelUploadBtn");
  const fd = new FormData();
  fd.append("file", file);
  fd.append("name", document.getElementById("modelName").value || "");
  fd.append("mandate", document.getElementById("modelMandate").value || "balanced");
  fd.append("context", document.getElementById("modelContext").value || "");
  btn.disabled = true; btn.textContent = "Analyzing…";
  try {
    const r = await api("/api/model/upload", { method: "POST", body: fd });
    toast(`Imported model ${r.name} (${r.n_holdings} holdings).`, "success");
    await loadModels(r.id);
  } catch (e) {
    toast(e.message, "error");
  } finally {
    btn.disabled = false; btn.textContent = "Analyze model";
  }
}

async function uploadCsv() {
  const file = document.getElementById("csvFile").files[0];
  if (!file) return toast("Choose a CSV file first.", "error");
  const fd = new FormData();
  fd.append("file", file);
  fd.append("symbol", document.getElementById("csvSymbol").value || "");
  try {
    const r = await api("/api/upload", { method: "POST", body: fd });
    toast(`Imported ${r.symbol} (${r.rows} rows).`, "success");
    loadTickers(r.symbol);
  } catch (e) {
    toast(e.message, "error");
  }
}

async function fetchLive() {
  const symbol = document.getElementById("liveSymbol").value.trim();
  if (!symbol) return toast("Enter a ticker symbol.", "error");
  const btn = document.getElementById("liveBtn");
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await api("/api/live", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    });
    toast(`Fetched ${r.symbol} (${r.rows} rows, ${r.headlines} headlines).`, "success");
    loadTickers(r.symbol);
  } catch (e) {
    toast(e.message, "error");
  } finally {
    btn.disabled = false; btn.textContent = "Fetch";
  }
}

let toastTimer;
function toast(msg, kind = "") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 4000);
}
