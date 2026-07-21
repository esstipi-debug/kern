/* Inventory Planner — single-page dashboard over the real engine (/api/portfolio).
   No build step: vanilla JS, fonts + theme tokens in index.html. All numbers come
   from the FastAPI backend, which runs src/ (forecasting -> policy -> constraints).

   Theme: the dashboard and the agent console (/console) share one token vocabulary,
   defined as CSS variables in index.html (:root). HTML inline styles below reference
   var(--token). The SVG chart can't use var() in presentation attributes, so it reads
   the few chart colors from the T object — keep T in sync with :root. */
(function () {
  "use strict";

  // ---- state ----------------------------------------------------------------
  var state = {
    tab: "overview",
    activeSkuId: "SKU-A",
    serviceLevel: 0.95,
    orderCost: 80,
    holdingRate: 0.22,
    budget: 125000,
    leadOverride: {},
    exported: false,
    data: null,
    loading: true,
    error: null,
    // Snapshot of the first successful load ("what you had in mind"), captured
    // once so the Budget Planner can contrast any later slider move against it.
    baseline: null,
  };

  var root = document.getElementById("root");

  // ---- formatting (matches the design) --------------------------------------
  function fmt0(n) { return Math.round(n).toLocaleString("en-US"); }
  function fmt1(n) {
    return (Math.round(n * 10) / 10).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }
  function money(n) { return "$" + Math.round(n).toLocaleString("en-US"); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  // ---- theme: chart-only colors (mirror of index.html :root) ----------------
  var T = {
    accent: "#4fd1c5",
    accentBand: "rgba(79,209,197,.16)",
    forecast: "#d4a017",   // amber  (--warn)
    reorder: "#f0564a",    // red    (--bad)
    grid: "#1e2733",       // --line
    gridDash: "#283341",   // --line-2
    axis: "#9aa7b6",       // --muted
  };

  var STATUS = {
    ok: { bg: "var(--ok-soft)", fg: "var(--ok)" },
    risk: { bg: "var(--bad-soft)", fg: "var(--bad)" },
    review: { bg: "var(--warn-soft)", fg: "var(--warn)" },
  };

  var CARD = "background:linear-gradient(180deg,var(--panel),var(--panel-2));border:1px solid var(--line);border-radius:var(--r-card);box-shadow:var(--shadow)";
  var TILE = "background:var(--panel);border:1px solid var(--line);border-radius:var(--r-tile);box-shadow:var(--shadow-sm)";
  var TH = "font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.08em;font-family:var(--sans)";
  var MONO = "font-family:var(--mono);font-variant-numeric:tabular-nums";

  // ---- data fetching --------------------------------------------------------
  function query() {
    var p = new URLSearchParams({
      service_level: state.serviceLevel,
      order_cost: state.orderCost,
      holding_rate: state.holdingRate,
      budget: state.budget,
    });
    if (Object.keys(state.leadOverride).length) {
      p.set("lead_overrides", JSON.stringify(state.leadOverride));
    }
    return p.toString();
  }

  function fetchData() {
    state.loading = state.data === null;
    if (state.loading) render();
    return fetch("/api/portfolio?" + query())
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        state.data = d; state.error = null; state.loading = false;
        if (!state.baseline) {
          state.baseline = {
            budget: state.budget,
            serviceLevel: state.serviceLevel,
            requested: d.totals.requested,
            cycle_floor: d.totals.cycle_floor,
            scale: d.totals.scale,
          };
        }
        render();
      })
      .catch(function (e) { state.error = e.message; state.loading = false; render(); });
  }

  function activeSku() {
    var d = state.data;
    if (!d) return null;
    var found = d.skus.filter(function (s) { return s.id === state.activeSkuId; })[0];
    return found || d.skus[0];
  }

  // ---- chart (faithful port of the design's SVG builder) --------------------
  function chartSVG(sku) {
    var hist = sku.history, n = hist.length;
    var W = 720, H = 240, padL = 46, padR = 110, padT = 16, padB = 26;
    var fc = sku.forecast, errStd = sku.error_std, rop = sku.reorder_point;
    var mean = sku.demand_mean, std = sku.demand_std;
    var maxRaw = Math.max(Math.max.apply(null, hist), rop, fc + errStd, mean + 2 * std);
    var maxY = maxRaw * 1.12, minY = 0;
    var x = function (i) { return padL + (i / (n - 1)) * (W - padL - padR); };
    var y = function (v) { return H - padB - ((v - minY) / (maxY - minY)) * (H - padT - padB); };
    var xf = W - padR, fx2 = W - 12;
    var p = [];
    var ticks = 4;
    for (var t = 0; t <= ticks; t++) {
      var v = minY + (maxY - minY) * t / ticks, yy = y(v);
      p.push('<line x1="' + padL + '" x2="' + fx2 + '" y1="' + yy + '" y2="' + yy + '" stroke="' + T.grid + '" stroke-width="1"/>');
      p.push('<text x="' + (padL - 8) + '" y="' + (yy + 3) + '" text-anchor="end" font-size="10" fill="' + T.axis + '" font-family="JetBrains Mono, monospace">' + fmt0(v) + "</text>");
    }
    var d = "";
    for (var i = 0; i < n; i++) { d += (i === 0 ? "M" : "L") + x(i).toFixed(1) + " " + y(hist[i]).toFixed(1) + " "; }
    p.push('<path d="M ' + xf + " " + y(fc + errStd) + " L " + fx2 + " " + y(fc + errStd) + " L " + fx2 + " " + y(Math.max(0, fc - errStd)) + " L " + xf + " " + y(Math.max(0, fc - errStd)) + ' Z" fill="' + T.accentBand + '"/>');
    p.push('<line x1="' + xf + '" x2="' + xf + '" y1="' + padT + '" y2="' + (H - padB) + '" stroke="' + T.gridDash + '" stroke-width="1" stroke-dasharray="2 3"/>');
    p.push('<line x1="' + padL + '" x2="' + fx2 + '" y1="' + y(rop) + '" y2="' + y(rop) + '" stroke="' + T.reorder + '" stroke-width="1.5" stroke-dasharray="5 4"/>');
    p.push('<text x="' + fx2 + '" y="' + (y(rop) - 5) + '" text-anchor="end" font-size="10" fill="' + T.reorder + '" font-family="JetBrains Mono, monospace">s=' + fmt0(rop) + "</text>");
    p.push('<line x1="' + padL + '" x2="' + fx2 + '" y1="' + y(fc) + '" y2="' + y(fc) + '" stroke="' + T.forecast + '" stroke-width="2"/>');
    p.push('<line x1="' + x(n - 1) + '" x2="' + xf + '" y1="' + y(hist[n - 1]) + '" y2="' + y(fc) + '" stroke="' + T.forecast + '" stroke-width="2"/>');
    p.push('<path d="' + d.trim() + '" fill="none" stroke="' + T.accent + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>');
    p.push('<circle cx="' + x(n - 1) + '" cy="' + y(hist[n - 1]) + '" r="3" fill="' + T.accent + '"/>');
    [0, 13, 26, 39, 51].forEach(function (i) {
      p.push('<text x="' + x(i) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="10" fill="' + T.axis + '" font-family="JetBrains Mono, monospace">w' + (i + 1) + "</text>");
    });
    return '<svg viewBox="0 0 ' + W + " " + H + '" width="100%" style="display:block;max-width:100%" role="img" aria-label="Demand history and forecast for ' + esc(sku.id) + '">' + p.join("") + "</svg>";
  }

  // ---- small view helpers ---------------------------------------------------
  function chip(status) {
    var c = STATUS[status.key];
    return '<span style="display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:var(--r-chip);font-size:11px;font-weight:500;font-family:var(--sans);background:' + c.bg + ";color:" + c.fg + '">' +
      '<span style="width:6px;height:6px;border-radius:50%;background:' + c.fg + '"></span>' + esc(status.label) + "</span>";
  }

  function header() {
    var d = state.data, tot = d.totals;
    var slPct = (state.serviceLevel * 100).toFixed(0) + "%";
    var planColor = !tot.feasible ? "var(--bad)" : (tot.scale >= 1 ? "var(--ok)" : "var(--warn)");
    return '<header style="display:flex;align-items:center;justify-content:space-between;gap:24px;padding:16px 28px;border-bottom:1px solid var(--line);background:rgba(13,18,25,.82);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);position:sticky;top:0;z-index:20">' +
      '<div style="display:flex;align-items:center;gap:14px">' +
      '<div style="width:34px;height:34px;border-radius:10px;background:linear-gradient(150deg,var(--accent-bright),var(--accent));display:flex;align-items:center;justify-content:center;color:var(--ink);font-weight:800;font-size:17px;box-shadow:0 8px 20px -8px rgba(79,209,197,.6)">σ</div>' +
      '<div style="display:flex;flex-direction:column">' +
      '<span style="font-weight:800;font-size:15px;letter-spacing:-0.02em">Inventory Planner</span>' +
      '<span style="font-size:11px;color:var(--muted);' + MONO + '">sample-portfolio · ' + tot.n_skus + " SKUs · (s,Q)/(R,S)</span>" +
      "</div></div>" +
      '<div style="display:flex;align-items:center;gap:26px;' + MONO + '">' +
      '<div style="display:flex;flex-direction:column;align-items:flex-end">' +
      '<span style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.08em;font-family:var(--sans)">Service level</span>' +
      '<span style="font-size:14px;font-weight:600">' + slPct + "</span></div>" +
      '<div style="width:1px;height:26px;background:var(--line-2)"></div>' +
      '<div style="display:flex;flex-direction:column;align-items:flex-end">' +
      '<span style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.08em;font-family:var(--sans)">Plan investment</span>' +
      '<span style="font-size:14px;font-weight:600;color:' + planColor + '">' + money(tot.final) + "</span></div>" +
      "</div></header>";
  }

  function nav() {
    var tabs = [["overview", "Portfolio"], ["detail", "SKU Detail"], ["budget", "Budget Planner"], ["forecast", "Forecast Quality"]];
    var btns = tabs.map(function (tb) {
      var on = state.tab === tb[0];
      return '<button data-action="tab" data-tab="' + tb[0] + '" role="tab" aria-selected="' + on + '" aria-controls="main-panel" style="background:none;border:none;border-bottom:2px solid ' + (on ? "var(--accent)" : "transparent") +
        ";color:" + (on ? "var(--txt)" : "var(--muted)") + ";font-family:var(--sans);font-size:13px;font-weight:" + (on ? 600 : 500) +
        ';padding:13px 16px;cursor:pointer;letter-spacing:-0.005em;transition:color .15s">' + tb[1] + "</button>";
    }).join("");
    return '<nav role="tablist" aria-label="Views" style="display:flex;gap:2px;padding:0 24px;border-bottom:1px solid var(--line);background:rgba(13,18,25,.82);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)">' + btns + "</nav>";
  }

  // ---- tabs -----------------------------------------------------------------
  function overview() {
    var d = state.data, tot = d.totals, cap = state.budget, req = tot.requested;
    var over = req > cap;
    var head = tot.headroom;
    var kpis = [
      { label: "Plan investment", value: money(req), color: over ? "var(--bad)" : "var(--ok)", sub: over ? "over budget" : "within budget" },
      { label: "Budget headroom", value: (head >= 0 ? "+" : "−") + "$" + fmt0(Math.abs(head)), color: head >= 0 ? "var(--ok)" : "var(--bad)", sub: "cap " + money(cap) },
      { label: "SKUs at risk", value: String(tot.n_risk), color: tot.n_risk ? "var(--bad)" : "var(--txt)", sub: "high bias / infeasible" },
      { label: "Intermittent", value: String(tot.n_intermittent), color: "var(--warn)", sub: "auto-routed to Croston" },
    ];
    var kpiHtml = kpis.map(function (k) {
      return '<div style="' + TILE + ';padding:16px 18px;display:flex;flex-direction:column;gap:8px">' +
        '<span style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.07em;font-weight:500">' + k.label + "</span>" +
        '<span style="font-size:30px;font-weight:600;' + MONO + ";letter-spacing:-0.02em;color:" + k.color + ';line-height:1">' + k.value + "</span>" +
        '<span style="font-size:11px;color:var(--muted);' + MONO + '">' + k.sub + "</span></div>";
    }).join("");

    var pct = req / cap * 100;
    var gColor = over ? "var(--bad)" : (pct > 90 ? "var(--warn)" : "var(--accent)");
    var gauge = '<div style="' + CARD + ';padding:18px 20px;margin-bottom:18px">' +
      '<div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:12px">' +
      '<span style="font-size:13px;font-weight:600">Budget utilization</span>' +
      '<span style="' + MONO + ';font-size:12px;color:var(--muted)">' + pct.toFixed(0) + "% of cap</span></div>" +
      '<div class="bar-track" style="height:10px;position:relative">' +
      '<div class="bar-fill-gloss" style="height:100%;width:' + Math.min(100, pct).toFixed(1) + "%;background:" + gColor + ';transition:width .5s cubic-bezier(0.16,1,0.3,1),background .3s"></div></div>' +
      '<div style="display:flex;justify-content:space-between;margin-top:7px;' + MONO + ';font-size:11px;color:var(--muted)"><span>requested ' + money(req) + "</span><span>cap " + money(cap) + "</span></div></div>";

    var cols = ["SKU", "Method", "Fcst/wk", "Q*", "Reorder", "Safety", "Inv value", "Status"];
    var ths = cols.map(function (c, i) {
      var align = i >= 2 && i <= 6 ? "right" : "left";
      var pad = i === 0 || i === 7 ? "11px 18px" : "11px 12px";
      return '<th style="text-align:' + align + ";padding:" + pad + ";" + TH + '">' + c + "</th>";
    }).join("");
    var rows = d.skus.map(function (s) {
      var c = s.intermittent ? s.order_up_to : s.order_quantity;
      return '<tr data-action="open-sku" data-sku="' + s.id + '" data-row tabindex="0" aria-label="Open ' + s.id + '" style="border-bottom:1px solid var(--line);cursor:pointer;transition:background .12s">' +
        '<td style="padding:12px 18px;font-weight:600;color:var(--txt)">' + s.id + "</td>" +
        '<td style="padding:12px 12px;color:var(--muted);font-size:12px">' + s.method + "</td>" +
        '<td style="padding:12px 12px;text-align:right;color:var(--txt-2)">' + fmt1(s.forecast) + "</td>" +
        '<td style="padding:12px 12px;text-align:right;color:var(--txt-2)">' + fmt1(c) + "</td>" +
        '<td style="padding:12px 12px;text-align:right;color:var(--txt-2)">' + fmt0(s.reorder_point) + "</td>" +
        '<td style="padding:12px 12px;text-align:right;color:var(--txt-2)">' + fmt1(s.safety_stock) + "</td>" +
        '<td style="padding:12px 12px;text-align:right;color:var(--txt);font-weight:500">' + money(s.investment) + "</td>" +
        '<td style="padding:12px 18px">' + chip(s.status) + "</td></tr>";
    }).join("");
    var table = '<div style="' + CARD + ';overflow:hidden"><table style="width:100%;border-collapse:collapse;' + MONO + '">' +
      '<thead><tr style="border-bottom:1px solid var(--line)">' + ths + "</tr></thead><tbody>" + rows + "</tbody></table></div>";

    return '<div class="fade-up"><div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px">' + kpiHtml + "</div>" + gauge + table + "</div>";
  }

  function detail() {
    var d = state.data, a = activeSku();
    var leadW = state.leadOverride[a.id] != null ? state.leadOverride[a.id] : Math.round(a.lead_periods);
    var slPct = (state.serviceLevel * 100).toFixed(0);
    var stats = [
      { label: a.intermittent ? "Order-up-to S" : "Order qty Q*", value: fmt1(a.intermittent ? a.order_up_to : a.order_quantity), color: "var(--txt)", note: a.intermittent ? "periodic review R=1" : "EOQ √(2·D·K/H)", formula: a.intermittent ? "S = μ·(L+R) + safety stock" : "Q* = √(2·D·K/H)" },
      { label: a.intermittent ? "Reorder s" : "Reorder point s", value: fmt0(a.reorder_point), color: "var(--txt)", note: "μ·L + safety", formula: "s = μ·L + z·σ·√L  (cycle service " + slPct + "%)" },
      { label: "Safety stock", value: fmt1(a.safety_stock), color: "var(--accent-bright)", note: "z=" + fmt1(a.z_factor) + " · σ=" + fmt1(a.error_std), formula: "SS = z · σₑ · √L   z=" + fmt1(a.z_factor) + " at SL " + slPct + "%" },
      { label: "Forecast", value: fmt1(a.forecast), color: "var(--txt)", note: "method " + a.method, formula: "point forecast for next period" },
      { label: "Bias", value: (a.bias >= 0 ? "+" : "") + fmt1(a.bias), color: Math.abs(a.bias) >= 2 ? "var(--bad)" : "var(--ok)", note: "mean error", formula: "bias = mean(forecast − actual). |bias|≥2 flags review" },
      { label: "MAE", value: fmt1(a.mae), color: "var(--txt)", note: "σₑ=" + fmt1(a.error_std), formula: "MAE = mean|forecast − actual|" },
    ];
    var statHtml = stats.map(function (s) {
      return '<div title="' + esc(s.formula) + '" style="' + TILE + ';padding:14px 16px;display:flex;flex-direction:column;gap:5px;cursor:help">' +
        '<span style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;font-weight:500;display:flex;align-items:center;gap:5px">' + s.label + '<span style="opacity:0.5;font-size:10px">ⓘ</span></span>' +
        '<span style="font-size:24px;font-weight:600;' + MONO + ";letter-spacing:-0.02em;color:" + s.color + ';line-height:1.05">' + s.value + "</span>" +
        '<span style="font-size:10.5px;color:var(--muted);' + MONO + '">' + esc(s.note) + "</span></div>";
    }).join("");

    var pills = d.skus.map(function (s) {
      var on = s.id === a.id;
      return '<button data-action="pill" data-sku="' + s.id + '" style="' + MONO + ";font-size:11px;font-weight:500;padding:5px 9px;border-radius:7px;cursor:pointer;border:1px solid " +
        (on ? "var(--accent-bd)" : "var(--line-2)") + ";background:" + (on ? "var(--accent-soft)" : "var(--ink-2)") +
        ";color:" + (on ? "var(--accent-bright)" : "var(--muted)") + ';transition:all .12s">' + s.id.replace("SKU-", "") + "</button>";
    }).join("");

    var legend = ['<span style="display:flex;align-items:center;gap:6px"><span style="width:14px;height:2px;background:var(--accent);border-radius:2px"></span>actual</span>',
      '<span style="display:flex;align-items:center;gap:6px"><span style="width:14px;height:2px;background:var(--warn);border-radius:2px"></span>forecast</span>',
      '<span style="display:flex;align-items:center;gap:6px"><span style="width:14px;height:9px;background:var(--accent-soft);border-radius:2px"></span>±σₑ</span>',
      '<span style="display:flex;align-items:center;gap:6px"><span style="width:14px;height:0;border-top:1.5px dashed var(--bad)"></span>reorder pt</span>'].join("");

    var metaLine = a.method + " · μ=" + fmt1(a.demand_mean) + " σ=" + fmt1(a.demand_std) + " · unit " + money(a.unit_cost) + " · lead " + leadW + "w · " + a.policy_kind;

    var whatif = [
      { key: "serviceLevel", label: "Cycle service level", display: (state.serviceLevel * 100).toFixed(1) + "%", min: 0.8, max: 0.999, step: 0.005, value: state.serviceLevel, sku: "" },
      { key: "lead", label: "Lead time (weeks)", display: String(leadW), min: 1, max: 6, step: 1, value: leadW, sku: a.id },
      { key: "orderCost", label: "Order cost K ($)", display: "$" + fmt0(state.orderCost), min: 20, max: 240, step: 5, value: state.orderCost, sku: "" },
    ];
    var whatifHtml = whatif.map(function (w) {
      var live = w.key === "lead" ? "lead-" + w.sku : w.key;
      return '<div style="display:flex;flex-direction:column;gap:8px">' +
        '<div style="display:flex;justify-content:space-between;align-items:baseline">' +
        '<span style="font-size:12px;font-weight:500;color:var(--txt-2)">' + w.label + "</span>" +
        '<span data-live="' + live + '" style="' + MONO + ';font-size:13px;font-weight:600;color:var(--accent)">' + w.display + "</span></div>" +
        '<input type="range" data-slider="' + w.key + '" data-sku="' + w.sku + '" min="' + w.min + '" max="' + w.max + '" step="' + w.step + '" value="' + w.value + '" aria-label="' + w.label + '" style="width:100%"></div>';
    }).join("");

    var left = '<div style="display:flex;flex-direction:column;gap:18px;min-width:0">' +
      '<div style="display:flex;align-items:flex-end;justify-content:space-between;gap:16px"><div>' +
      '<div style="display:flex;align-items:center;gap:10px"><h2 style="margin:0;font-size:22px;font-weight:800;letter-spacing:-0.02em">' + a.id + "</h2>" + chip(a.status) + "</div>" +
      '<span style="font-size:12px;color:var(--muted);' + MONO + '">' + esc(metaLine) + "</span></div>" +
      '<div style="display:flex;gap:8px;flex-wrap:wrap">' + pills + "</div></div>" +
      '<div style="' + CARD + ';padding:16px 18px 10px">' +
      '<div style="display:flex;align-items:center;gap:16px;margin-bottom:10px;flex-wrap:wrap"><span style="font-size:13px;font-weight:600">Demand history &amp; forecast</span>' +
      '<div style="display:flex;gap:14px;font-size:11px;color:var(--muted)">' + legend + "</div></div>" +
      '<div style="width:100%;overflow:hidden">' + chartSVG(a) + "</div></div>" +
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px">' + statHtml + "</div></div>";

    var aside = '<aside style="' + CARD + ';padding:18px;position:sticky;top:96px;display:flex;flex-direction:column;gap:20px">' +
      '<div><span style="font-size:13px;font-weight:600">What-if</span><p style="margin:4px 0 0;font-size:11.5px;color:var(--muted);line-height:1.45">Adjust assumptions — the engine recomputes the policy.</p></div>' +
      whatifHtml +
      '<div style="border-top:1px solid var(--line);padding-top:14px;display:flex;flex-direction:column;gap:9px">' +
      '<div style="display:flex;justify-content:space-between;font-size:12px"><span style="color:var(--muted)">z-factor</span><span style="' + MONO + ';font-weight:500;color:var(--txt-2)">' + fmt1(a.z_factor) + " σ</span></div>" +
      '<div style="display:flex;justify-content:space-between;font-size:12px"><span style="color:var(--muted)">Inv. at this SKU</span><span style="' + MONO + ';font-weight:500;color:var(--txt-2)">' + money(a.investment) + "</span></div></aside>";

    return '<div class="fade-up" style="display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:20px;align-items:start">' + left + aside + "</div>";
  }

  function budget() {
    var d = state.data, tot = d.totals;
    var cycleFloor = tot.cycle_floor, req = tot.requested, scale = tot.scale, feasible = tot.feasible;
    var floor = Math.round(cycleFloor / 250) * 250, ceil = Math.ceil(req * 1.25 / 250) * 250;
    var maxInv = Math.max.apply(null, d.skus.map(function (s) { return s.investment; }));

    var bars = d.skus.map(function (s) {
      var cycleW = (s.cycle_investment / maxInv * 100).toFixed(1);
      var ssW = (s.ss_investment * scale / maxInv * 100).toFixed(1);
      var inv = s.cycle_investment + s.ss_investment * scale;
      return '<div style="display:grid;grid-template-columns:64px 1fr 96px;gap:14px;align-items:center">' +
        '<span style="' + MONO + ';font-size:12px;font-weight:600;color:var(--txt-2)">' + s.id + "</span>" +
        '<div class="bar-track" style="height:18px;display:flex">' +
        '<div class="bar-fill-gloss bar-fill-shimmer" style="height:100%;width:' + cycleW + '%;background-color:var(--accent-bar);transition:width .55s cubic-bezier(0.16,1,0.3,1)"></div>' +
        '<div class="bar-fill-gloss bar-fill-safety" style="height:100%;width:' + ssW + '%;transition:width .55s cubic-bezier(0.16,1,0.3,1)"></div></div>' +
        '<span style="' + MONO + ';font-size:12px;text-align:right;color:var(--txt-2)">' + money(inv) + "</span></div>";
    }).join("");

    var banner = (function () {
      var icon = feasible ? (scale >= 1 ? "✓" : "▲") : "✕";
      var title = feasible ? (scale >= 1 ? "Feasible — full safety stock funded" : "Feasible — safety stock scaled to fit") : "Infeasible — cycle stock alone exceeds the cap";
      var sub = feasible ? (scale >= 1 ? "all " + tot.n_skus + " SKUs at requested service level" : "safety stock at " + (scale * 100).toFixed(0) + "% · raise cap to " + money(req) + " for full") : "minimum cycle-stock floor is " + money(cycleFloor) + " · raise the cap";
      var fg = feasible ? (scale >= 1 ? "var(--ok)" : "var(--warn)") : "var(--bad)";
      var bg = feasible ? (scale >= 1 ? "var(--ok-soft)" : "var(--warn-soft)") : "var(--bad-soft)";
      var bd = feasible ? (scale >= 1 ? "var(--ok-bd)" : "var(--warn-bd)") : "var(--bad-bd)";
      return '<div style="border-radius:var(--r-card);padding:14px 18px;display:flex;align-items:center;gap:12px;border:1px solid ' + bd + ";background:" + bg + '">' +
        '<span style="font-size:18px;color:' + fg + '">' + icon + "</span>" +
        '<div style="display:flex;flex-direction:column;gap:2px"><span style="font-size:13px;font-weight:600;color:' + fg + '">' + title + "</span>" +
        '<span style="font-size:11.5px;color:var(--muted);' + MONO + '">' + sub + "</span></div></div>";
    })();

    var stat = function (label, value, color) {
      return '<div style="display:flex;justify-content:space-between;font-size:12px"><span style="color:var(--muted)">' + label + '</span><span style="' + MONO + ";font-weight:600" + (color ? ";color:" + color : "") + '">' + value + "</span></div>";
    };

    // ---- consequence panel: contrast "what you had in mind" (baseline) against
    // the current slider position, in plain language, before committing to it ----
    var consequence = (function () {
      var b = state.baseline;
      if (!b) return "";
      var headroom = state.budget - req;
      var baseHeadroom = b.budget - b.requested;
      var moneySigned = function (n) { return (n < 0 ? "−" : "+") + "$" + Math.abs(Math.round(n)).toLocaleString("en-US"); };
      var pct = function (s) { return s >= 1 ? "100%" : Math.round(s * 100) + "%"; };
      // Dollar value of the safety-stock buffer NOT funded at the current scale
      // (n_risk is a forecast-bias flag, independent of the budget slider --
      // using it here would show "0 productos" even while the buffer visibly
      // shrinks, which is what happened in testing: misleading, not a real
      // consequence of this decision).
      var uncovered = Math.max(0, (1 - Math.min(1, scale)) * Math.max(0, req - cycleFloor));
      var baseUncovered = Math.max(0, (1 - Math.min(1, b.scale)) * Math.max(0, b.requested - b.cycle_floor));

      var moodClass = !feasible ? "bad" : (scale >= 1 ? "good" : "warn");
      var moodFg = { good: "var(--ok)", warn: "var(--warn)", bad: "var(--bad)" }[moodClass];
      var moodBg = { good: "var(--ok-soft)", warn: "var(--warn-soft)", bad: "var(--bad-soft)" }[moodClass];
      var moodBd = { good: "var(--ok-bd)", warn: "var(--warn-bd)", bad: "var(--bad-bd)" }[moodClass];
      var moodIcon = { good: "✓", warn: "▲", bad: "✗" }[moodClass];
      var moodTitle = moodClass === "good" ? "Con esto cubrís todo sin quedarte sin stock"
        : moodClass === "warn" ? "Se cubre casi todo, pero no del todo"
        : "Con esta plata no alcanza";
      var moodSub = moodClass === "good" ? (headroom > 0 ? "Y te sobra " + moneySigned(headroom).replace("+", "") + " del presupuesto que tenías pensado." : "Justo lo que hacía falta — sin margen extra.")
        : moodClass === "warn" ? "El colchón de seguridad queda en " + pct(scale) + " en vez de completo — subí el presupuesto a " + money(req) + " para cubrirlo del todo."
        : "Te faltan " + money(cycleFloor - state.budget) + " para cubrir ni el mínimo de stock.";

      var card = function (label, nowVal, nowColor, wasVal) {
        return '<div style="background:var(--ink-2);border:1px solid var(--line);border-radius:11px;padding:12px 13px">' +
          '<div style="font-size:11px;color:var(--muted);line-height:1.4;min-height:2.6em;margin-bottom:8px">' + label + "</div>" +
          '<div style="' + MONO + ';font-size:17px;font-weight:800;color:' + nowColor + ';margin-bottom:3px">' + nowVal + "</div>" +
          '<div style="font-size:10.5px;color:var(--faint)">tenías pensado: <b style="color:var(--txt-2)">' + wasVal + "</b></div></div>";
      };

      var uncoveredColor = uncovered === 0 ? "var(--ok)" : uncovered <= baseUncovered ? "var(--warn)" : "var(--bad)";
      var scaleColor = scale >= 1 ? "var(--ok)" : scale >= 0.8 ? "var(--warn)" : "var(--bad)";
      var headColor = headroom >= 0 ? "var(--ok)" : "var(--bad)";

      return '<div style="border-top:1px solid var(--line);padding-top:16px;display:flex;flex-direction:column;gap:12px">' +
        '<div style="display:flex;align-items:center;justify-content:space-between">' +
        '<span style="font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--txt-2)">◆ Consecuencia de este ajuste</span>' +
        '<button data-action="reset-baseline" style="font-size:11px;color:var(--muted);background:transparent;border:1px solid var(--line-2);border-radius:999px;padding:5px 11px;cursor:pointer;font-family:var(--sans)">↺ Volver a lo que tenía pensado</button></div>' +
        '<div style="border-radius:12px;padding:12px 14px;display:flex;align-items:center;gap:11px;border:1px solid ' + moodBd + ";background:" + moodBg + '">' +
        '<span style="font-size:16px;font-weight:800;color:' + moodFg + '">' + moodIcon + "</span>" +
        '<div style="display:flex;flex-direction:column;gap:2px"><span style="font-size:12.5px;font-weight:700;color:' + moodFg + '">' + moodTitle + "</span>" +
        '<span style="font-size:11px;color:var(--muted)">' + moodSub + "</span></div></div>" +
        '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">' +
        card("Colchón sin cubrir (en plata)", money(uncovered), uncoveredColor, money(baseUncovered)) +
        card("Colchón para demoras o picos", pct(scale), scaleColor, pct(b.scale)) +
        card("Plata de sobra (o que falta)", moneySigned(headroom), headColor, moneySigned(baseHeadroom)) +
        "</div></div>";
    })();

    var aside = '<aside style="' + CARD + ';padding:20px;position:sticky;top:96px;display:flex;flex-direction:column;gap:20px">' +
      '<div><span style="font-size:13px;font-weight:600">Budget &amp; constraints</span><p style="margin:4px 0 0;font-size:11.5px;color:var(--muted);line-height:1.45">Lower the cap and watch each SKU\'s safety stock shrink toward its cycle-stock floor.</p></div>' +
      '<div style="display:flex;flex-direction:column;gap:9px"><div style="display:flex;justify-content:space-between;align-items:baseline">' +
      '<span style="font-size:12px;font-weight:500;color:var(--txt-2)">Investment cap</span>' +
      '<span data-live="budget" style="' + MONO + ';font-size:15px;font-weight:600;color:var(--accent)">' + money(state.budget) + "</span></div>" +
      '<input type="range" data-slider="budget" min="' + floor + '" max="' + ceil + '" step="250" value="' + state.budget + '" aria-label="Investment cap" style="width:100%">' +
      '<div style="display:flex;justify-content:space-between;' + MONO + ';font-size:10.5px;color:var(--muted)"><span>' + money(floor) + "</span><span>" + money(ceil) + "</span></div></div>" +
      '<div style="display:flex;flex-direction:column;gap:9px"><div style="display:flex;justify-content:space-between;align-items:baseline">' +
      '<span style="font-size:12px;font-weight:500;color:var(--txt-2)">Service level</span>' +
      '<span data-live="serviceLevel" style="' + MONO + ';font-size:13px;font-weight:600;color:var(--accent)">' + (state.serviceLevel * 100).toFixed(1) + "%</span></div>" +
      '<input type="range" data-slider="serviceLevel" min="0.80" max="0.999" step="0.005" value="' + state.serviceLevel + '" aria-label="Service level" style="width:100%"></div>' +
      consequence +
      '<div style="border-top:1px solid var(--line);padding-top:16px;display:flex;flex-direction:column;gap:11px">' +
      stat("Requested", money(req)) + stat("Cycle-stock floor", money(cycleFloor), "var(--bad)") +
      stat("Safety-stock scale", (scale * 100).toFixed(0) + "%", "var(--accent-bright)") + stat("Final investment", money(tot.final), "var(--ok)") + "</div>" +
      '<button data-action="export" class="btn-warm-action" style="margin-top:2px">' +
      (state.exported ? "✓ plan exported (CSV)" : "Commit &amp; export plan (CSV)") + "</button></aside>";

    var right = '<div style="display:flex;flex-direction:column;gap:16px;min-width:0">' + banner +
      '<div style="' + CARD + ';padding:18px 20px;display:flex;flex-direction:column;gap:14px">' +
      '<span style="font-size:12px;font-weight:600;color:var(--txt-2)">Allocation by SKU — <span style="color:var(--accent)">cycle</span> + <span style="color:var(--accent-warm)">safety</span></span>' +
      bars + "</div></div>";

    return '<div class="fade-up" style="display:grid;grid-template-columns:340px minmax(0,1fr);gap:22px;align-items:start">' + aside + right + "</div>";
  }

  function forecast() {
    var d = state.data;
    var cols = ["SKU", "Method", "Bias", "Bias spread", "MAE", "σₑ", "Flag"];
    var ths = cols.map(function (c, i) {
      var align = i === 2 || i === 4 || i === 5 ? "right" : "left";
      var pad = i === 0 || i === 6 ? "11px 18px" : "11px 12px";
      return '<th style="text-align:' + align + ";padding:" + pad + ";" + TH + '">' + c + "</th>";
    }).join("");
    var rows = d.skus.map(function (s) {
      var biasColor = Math.abs(s.bias) >= 2 ? "var(--bad)" : (Math.abs(s.bias) >= 1 ? "var(--warn)" : "var(--ok)");
      var markerLeft = (50 + Math.max(-48, Math.min(48, s.bias / 4 * 48))).toFixed(1);
      var flag = s.intermittent ? "Croston · intermittent" : (Math.abs(s.bias) >= 2 ? "high bias" : "healthy");
      return '<tr data-action="open-sku" data-sku="' + s.id + '" data-row tabindex="0" aria-label="Open ' + s.id + '" style="border-bottom:1px solid var(--line);cursor:pointer;transition:background .12s">' +
        '<td style="padding:12px 18px;font-weight:600;color:var(--txt)">' + s.id + "</td>" +
        '<td style="padding:12px 12px;color:var(--muted);font-size:12px">' + s.method + "</td>" +
        '<td style="padding:12px 12px;text-align:right;color:' + biasColor + ';font-weight:500">' + (s.bias >= 0 ? "+" : "") + fmt1(s.bias) + "</td>" +
        '<td style="padding:12px 12px"><div style="position:relative;height:8px;width:120px;border-radius:4px;background:var(--track)">' +
        '<div style="position:absolute;top:0;bottom:0;left:50%;width:1px;background:var(--line-2)"></div>' +
        '<div style="position:absolute;top:-1px;width:10px;height:10px;border-radius:50%;left:' + markerLeft + '%;background:' + biasColor + ';transform:translateX(-50%)"></div></div></td>' +
        '<td style="padding:12px 12px;text-align:right;color:var(--txt-2)">' + fmt1(s.mae) + "</td>" +
        '<td style="padding:12px 12px;text-align:right;color:var(--txt-2)">' + fmt1(s.error_std) + "</td>" +
        '<td style="padding:12px 18px">' + chip({ key: s.status.key, label: flag }) + "</td></tr>";
    }).join("");
    return '<div class="fade-up" style="' + CARD + ';overflow:hidden"><table style="width:100%;border-collapse:collapse;' + MONO + '">' +
      '<thead><tr style="border-bottom:1px solid var(--line)">' + ths + "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  }

  // ---- render ---------------------------------------------------------------
  function shell(inner) {
    return '<div style="min-height:100vh;color:var(--txt)">' + inner + "</div>";
  }

  var TAB_LABELS = { overview: "Portfolio", detail: "SKU Detail", budget: "Budget Planner", forecast: "Forecast Quality" };

  function focusKey(el) {
    if (!el || el === document.body) return null;
    var d = el.dataset || {};
    if (d.slider != null) return '[data-slider="' + d.slider + '"]' + (d.sku ? '[data-sku="' + d.sku + '"]' : "");
    if (d.action) return '[data-action="' + d.action + '"]' + (d.tab ? '[data-tab="' + d.tab + '"]' : "") + (d.sku ? '[data-sku="' + d.sku + '"]' : "");
    return null;
  }

  function announce(msg) {
    var n = document.getElementById("sr-status");
    if (n && n.textContent !== msg) n.textContent = msg;
  }

  function render() {
    var fk = focusKey(document.activeElement); // preserve focus across the re-render (WCAG 2.4.3)

    if (state.error) {
      root.innerHTML = shell('<div role="alert" style="padding:80px 28px;text-align:center"><div style="font-size:15px;font-weight:600;color:var(--bad)">Could not reach the engine</div>' +
        '<div style="font-size:12px;color:var(--muted);' + MONO + ';margin-top:8px">' + esc(state.error) + "</div>" +
        '<button data-action="retry" class="btn-warm-action" style="margin-top:18px">Retry</button></div>');
      announce("Error reaching the engine: " + state.error);
    } else if (!state.data) {
      root.innerHTML = shell('<div role="status" style="padding:120px 28px;text-align:center;color:var(--muted);font-size:13px;' + MONO + '">Loading portfolio from the engine…</div>');
    } else if (!state.data.skus.length) {
      root.innerHTML = shell(header() + nav() + '<main id="main-panel" role="tabpanel" style="padding:24px 28px 64px"><div role="status" style="padding:80px 28px;text-align:center;color:var(--muted);font-size:13px">No SKUs in this portfolio.</div></main>');
      announce("No SKUs in this portfolio");
    } else {
      var main = state.tab === "overview" ? overview() : state.tab === "detail" ? detail() : state.tab === "budget" ? budget() : forecast();
      root.innerHTML = shell(header() + nav() + '<main id="main-panel" role="tabpanel" aria-label="' + TAB_LABELS[state.tab] + '" style="padding:24px 28px 64px">' + main + "</main>");
      announce(TAB_LABELS[state.tab] + (state.tab === "detail" ? " — " + state.activeSkuId : "") + (state.exported ? " · plan exported" : ""));
    }

    if (fk) { var t = root.querySelector(fk); if (t && t.focus) t.focus({ preventScroll: true }); }
  }

  // ---- events ---------------------------------------------------------------
  function liveFormat(key, value) {
    if (key === "serviceLevel") return (value * 100).toFixed(1) + "%";
    if (key === "orderCost") return "$" + fmt0(value);
    if (key === "budget") return money(value);
    return String(value);
  }

  root.addEventListener("click", function (e) {
    var t = e.target.closest("[data-action]");
    if (!t) return;
    var a = t.dataset.action;
    if (a === "tab") { state.tab = t.dataset.tab; render(); }
    else if (a === "open-sku") { state.tab = "detail"; state.activeSkuId = t.dataset.sku; render(); }
    else if (a === "pill") { state.activeSkuId = t.dataset.sku; render(); }
    else if (a === "export") { state.exported = true; render(); }
    else if (a === "retry") { state.error = null; fetchData(); }
    else if (a === "reset-baseline") {
      if (state.baseline) { state.budget = state.baseline.budget; state.serviceLevel = state.baseline.serviceLevel; }
      state.exported = false; fetchData();
    }
  });

  root.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var t = e.target.closest('[data-row],[data-action="tab"]');
    if (t) { e.preventDefault(); t.click(); }
  });

  // live label update while dragging (no fetch)
  root.addEventListener("input", function (e) {
    var el = e.target;
    if (!el.dataset || el.dataset.slider == null) return;
    var key = el.dataset.slider, v = parseFloat(el.value);
    if (key === "serviceLevel") state.serviceLevel = v;
    else if (key === "orderCost") state.orderCost = v;
    else if (key === "budget") state.budget = v;
    else if (key === "lead") { state.leadOverride[el.dataset.sku] = parseInt(el.value, 10); }
    var live = key === "lead" ? "lead-" + el.dataset.sku : key;
    var labelText = key === "lead" ? el.value : liveFormat(key, v);
    Array.prototype.forEach.call(document.querySelectorAll('[data-live="' + live + '"]'), function (n) { n.textContent = labelText; });
  });

  // commit + recompute from the engine on release
  root.addEventListener("change", function (e) {
    if (e.target.dataset && e.target.dataset.slider != null) { state.exported = false; fetchData(); }
  });

  fetchData();
})();
