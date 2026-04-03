// ── STATE ──────────────────────────────────────────────────────────────────
let DATA = null;
let activeTab = 'overview';
const charts = {};   // canvas-id → Chart instance

// ── FILE LOAD ─────────────────────────────────────────────────────────────
document.getElementById('fileInput').addEventListener('change', e => {
  const file = e.target.files[0]; if (!file) return;
  const r = new FileReader();
  r.onload = ev => {
    try {
      DATA = JSON.parse(ev.target.result);
      init();
    } catch(err) { alert('JSON parse error: ' + err.message); }
  };
  r.readAsText(file);
});

// ── EXAMPLE DATA LOAD ─────────────────────────────────────────────────────────────
document.getElementById('loadExampleData').addEventListener('click', e => {
  fetch('./signal_output.json')
    .then(response => response.json())
    .then(data => {
      DATA = data;
      init();
    }).catch(error => console.error('Error:', error));
});

// ── BACKTESTING LINK ─────────────────────────────────────────────────────────────
const backtestingLink = document.getElementById('backtestingLink');
backtestingLink.addEventListener('click', e => {
  window.location.href = './backtest_report.html';
});

// ── PYTHON SCRIPT LINK ─────────────────────────────────────────────────────────────
const pythonLink = document.getElementById('pythonScriptLink');
const pythonScriptUrls = ['../strategy.py', '../dashboard.py']
pythonLink.addEventListener('click', e => {
  pythonScriptUrls.forEach((url, index) => {
    setTimeout(() => {
      const link = document.createElement('a');
      link.href = url;
      link.download = '';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }, index * 500);
  });
});

// ── HELPERS ───────────────────────────────────────────────────────────────
const pct  = (v, d=1) => (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%';
const usd  = v => '$' + Number(v).toLocaleString('en-US', {maximumFractionDigits: 0});
const sgn  = v => v >= 0 ? 'pos' : 'neg';
const fmtD = iso => iso.slice(5); // MM-DD

function signalClass(sig) {
  if (!sig) return '';
  if (sig.includes('CASH'))     return 'cash';
  if (sig.includes('SMALL'))    return 'small';
  if (sig.includes('MODERATE')) return 'moderate';
  if (sig.includes('LARGE'))    return 'large';
  return 'full';
}
function signalColor(alloc) {
  if (alloc === 0)    return '#3d4a5c';
  if (alloc <= 0.25)  return '#e8a020';
  if (alloc <= 0.50)  return '#4fa3f7';
  if (alloc <= 0.75)  return '#36d672';
  return '#36d672';
}

// ── CHART DEFAULTS ────────────────────────────────────────────────────────
function equityOpts(color, todayStr) {
  return {
    responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
    plugins: {
      legend: { labels: { color: '#888f9a', font: { family: "'DM Mono'" }, boxWidth: 12 } },
      tooltip: {
        backgroundColor: '#141820', borderColor: '#1e242f', borderWidth: 1,
        titleColor: '#e2eaf6', bodyColor: '#b8c4d4',
        callbacks: { label: ctx => ' ' + usd(ctx.parsed.y) }
      }
    },
    scales: {
      x: { type: 'time', time: { unit: 'week' },
           grid: { color: '#0e1115' },
           ticks: { color: '#888f9a', font: { family: "'DM Mono'", size: 9 } } },
      y: { grid: { color: '#141820' },
           ticks: { color: '#888f9a', font: { family: "'DM Mono'", size: 9 },
                    callback: v => '$' + (v / 1000).toFixed(0) + 'k' } }
    }
  };
}
function allocOpts() {
  return {
    responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
    plugins: { legend: { display: false },
      tooltip: { backgroundColor: '#141820', borderColor: '#1e242f', borderWidth: 1,
                 titleColor: '#e2eaf6', bodyColor: '#b8c4d4',
                 callbacks: { label: ctx => ' ' + (ctx.parsed.y * 100).toFixed(0) + '%' } } },
    scales: {
      x: { type: 'time', time: { unit: 'week' },
           grid: { color: '#0e1115' },
           ticks: { color: '#888f9a', font: { family: "'DM Mono'", size: 9 } } },
      y: { min: 0, max: 1, grid: { color: '#141820' },
           ticks: { color: '#888f9a', font: { family: "'DM Mono'", size: 9 },
                    callback: v => (v * 100).toFixed(0) + '%' } }
    }
  };
}

function todayAnnotationPlugin(todayStr) {
  return {
    id: 'todayLine',
    afterDraw(chart) {
      const xScale = chart.scales.x;
      if (!xScale) return;
      const x = xScale.getPixelForValue(new Date(todayStr).getTime());
      if (x < xScale.left || x > xScale.right) return;
      const ctx = chart.ctx;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(x, chart.chartArea.top);
      ctx.lineTo(x, chart.chartArea.bottom);
      ctx.strokeStyle = 'rgba(232,160,32,0.5)';
      ctx.lineWidth   = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.fillStyle = 'rgba(232,160,32,0.8)';
      ctx.font = "9px 'DM Mono'";
      ctx.fillText('TODAY', x + 3, chart.chartArea.top + 10);
      ctx.restore();
    }
  };
}

// ── UTILITY ──────────────────────────────────────────────────────────────
function round2(v) { return Math.round(v * 100) / 100; }

// ── BUILD CHART: PRE-CONTEXT + STRATEGY + FORECAST ───────────────────────
// Three visual zones on one continuous timeline:
//   ① Context (dim)   — raw equity price, rescaled to initial_capital, before strategy start
//   ② Portfolio       — actual portfolio value from strategy start → today
//   ③ Forecast fan    — Monte Carlo bands from today forward
// Two vertical marker lines: STRATEGY START and TODAY
function buildEquityForecastChart(canvasId, strat, stratStartStr, todayStr) {
  const el = document.getElementById(canvasId);
  if (!el) return;
  if (charts[canvasId]) { charts[canvasId].destroy(); }

  const color   = strat.color;
  const context = DATA.context || [];
  const hist    = strat.history;
  const fc      = strat.forecast;
  const initCap = DATA.initial_capital;

  // ── Zone ①: context — price rescaled so it ends at initial_capital
  // This lets the context line visually connect to the strategy line at start
  let contextData = [];
  if (context.length > 0) {
    const lastCtxPrice = context[context.length - 1].price;
    const scale = lastCtxPrice > 0 ? initCap / lastCtxPrice : 1;
    contextData = context.map(b => ({
      x:     b.date,
      y:     round2(b.price * scale),
      price: b.price,
      dd:    b.drawdown,
      spk:   b.oil_spike,
    }));
  }

  // ── Zone ②: strategy portfolio value
  const histData = hist.map(b => ({
    x:      b.date,
    y:      b.portfolio_value,
    shares: b.shares_value,
    cash:   b.cash_remaining,
    alloc:  b.allocation,
    sig:    b.signal,
  }));

  // ── Zone ③: forecast fan — bridge from last history bar
  const lastHist = hist[hist.length - 1];
  const bridge   = { x: lastHist.date, y: lastHist.portfolio_value };
  const fcP50 = [bridge, ...fc.dates.map((d, i) => ({ x: d, y: fc.p50[i] }))];
  const fcP25 = [bridge, ...fc.dates.map((d, i) => ({ x: d, y: fc.p25[i] }))];
  const fcP75 = [bridge, ...fc.dates.map((d, i) => ({ x: d, y: fc.p75[i] }))];
  const fcP10 = [bridge, ...fc.dates.map((d, i) => ({ x: d, y: fc.p10[i] }))];
  const fcP90 = [bridge, ...fc.dates.map((d, i) => ({ x: d, y: fc.p90[i] }))];

  const opts = equityOpts(color, todayStr);
  opts.plugins.legend.display = true;
  opts.plugins.legend.labels.filter = item => !item.text.startsWith('_');

  // Rich tooltip: different detail per zone
  opts.plugins.tooltip.callbacks = {
    title: items => items[0]?.label || '',
    label: ctx => {
      const v   = ctx.parsed.y;
      const raw = ctx.raw;
      if (raw?.shares !== undefined) {
        return [
          ` Portfolio: ${usd(v)}`,
          ` ├ Shares:  ${usd(raw.shares)}  (${(raw.alloc*100).toFixed(0)}%)`,
          ` └ Cash:    ${usd(raw.cash)}  (${((1-raw.alloc)*100).toFixed(0)}%)`,
          ` Signal:   ${raw.sig}`,
        ];
      }
      if (raw?.price !== undefined) {
        return [
          ` Mkt price: $${raw.price}  (rescaled: ${usd(v)})`,
          ` Drawdown:  ${(raw.dd*100).toFixed(1)}%`,
          ` Oil spike: ${(raw.spk*100).toFixed(1)}%`,
          ` (Pre-strategy context)`,
        ];
      }
      return ` ${ctx.dataset.label}: ${usd(v)}`;
    },
  };

  const dualAnnotationPlugin = makeMarkerPlugin('portMarkers_'+canvasId, stratStartStr, todayStr);

  charts[canvasId] = new Chart(el, {
    type: 'line',
    data: {
      datasets: [
        // outer forecast band P10–P90
        // fill: N (plain integer) = fill to dataset at absolute index N (Chart.js v4)
        { label: 'P10–P90', data: fcP90,
          borderWidth: 0, pointRadius: 0,
          backgroundColor: color + '18', fill: 1, tension: 0.4,
          borderColor: 'transparent' },
        { label: '_p10', data: fcP10,
          borderWidth: 0, pointRadius: 0,
          backgroundColor: color + '18', fill: false, tension: 0.4,
          borderColor: 'transparent' },
        // inner forecast band P25–P75
        { label: 'P25–P75', data: fcP75,
          borderWidth: 0, pointRadius: 0,
          backgroundColor: color + '30', fill: 3, tension: 0.4,
          borderColor: 'transparent' },
        { label: '_p25', data: fcP25,
          borderWidth: 0, pointRadius: 0,
          backgroundColor: color + '30', fill: false, tension: 0.4,
          borderColor: 'transparent' },
        // forecast median dashed
        { label: 'Forecast (median)',
          data: fcP50,
          borderColor: color + '88', borderWidth: 1.5,
          borderDash: [5, 3], pointRadius: 0, tension: 0.4, fill: false },
        // active strategy portfolio — solid
        { label: 'Portfolio Value',
          data: histData,
          borderColor: color, borderWidth: 2,
          backgroundColor: color + '12', fill: true,
          pointRadius: 0, tension: 0.3 },
        // pre-strategy context — dim, dashed, no fill
        { label: 'Market (pre-strategy)',
          data: contextData,
          borderColor: 'rgba(180,200,220,0.35)',
          borderWidth: 1.5,
          borderDash: [3, 3],
          backgroundColor: 'transparent', fill: false,
          pointRadius: 0, tension: 0.3 },
      ]
    },
    options: opts,
    plugins: [dualAnnotationPlugin],
  });
}

// ── BUILD CHART: EQUITY PRICE + OIL PRICE (dual Y-axis, all 3 phases) ────
function buildPriceChart(canvasId, strat, stratStartStr, todayStr) {
  const el = document.getElementById(canvasId);
  if (!el) return;
  if (charts[canvasId]) { charts[canvasId].destroy(); }

  const color   = strat.color;
  const context = DATA.context || [];
  const hist    = strat.history;
  const fc      = strat.forecast;

  // ── Equity price across all 3 phases ─────────────────────────────────
  // Context and history use real prices. Forecast uses eq_median.
  // We normalise equity to an index (first context bar = 100) for clarity.
  const firstEqPrice = context.length > 0 ? context[0].price
                      : hist.length > 0  ? hist[0].price
                      : 1;

  const norm = v => round2(v / firstEqPrice * 100);

  const ctxEq   = context.map(b => ({ x: b.date,      y: norm(b.price) }));
  const histEq  = hist.map(b    => ({ x: b.date,      y: norm(b.price) }));
  const fcEq    = fc.eq_median
    ? [{ x: hist[hist.length-1].date, y: norm(hist[hist.length-1].price) },
       ...fc.dates.map((d,i) => ({ x: d, y: norm(fc.eq_median[i]) }))]
    : [];

  // ── Oil price across all 3 phases (right axis, raw $) ────────────────
  const ctxOil  = context.map(b => ({ x: b.date,      y: b.oil_price }));
  const histOil = hist.map(b    => ({ x: b.date,      y: b.oil_price }));
  const fcOil   = fc.oil_median
    ? [{ x: hist[hist.length-1].date, y: hist[hist.length-1].oil_price },
       ...fc.dates.map((d,i) => ({ x: d, y: fc.oil_median[i] }))]
    : [];

  // Baseline reference line for oil
  const oilBase = DATA.oil_baseline;
  const allDates = [
    ...(context.length ? [context[0].date] : []),
    ...(fc.dates.length ? [fc.dates[fc.dates.length-1]] : [])
  ];
  const baselineData = allDates.length === 2
    ? [{ x: allDates[0], y: oilBase }, { x: allDates[1], y: oilBase }]
    : [];

  const markerPlugin = makeMarkerPlugin('priceMarkers_'+canvasId, stratStartStr, todayStr);

  const opts = {
    responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        display: true,
        labels: { color: '#888f9a', font: { family: "'DM Mono'" }, boxWidth: 10,
                  filter: item => !item.text.startsWith('_') }
      },
      tooltip: {
        backgroundColor: '#141820', borderColor: '#1e242f', borderWidth: 1,
        titleColor: '#e2eaf6', bodyColor: '#b8c4d4',
        callbacks: {
          label: ctx => {
            if (ctx.dataset.yAxisID === 'y2')
              return ` Oil: $${ctx.parsed.y.toFixed(2)}`;
            return ` Equity idx: ${ctx.parsed.y.toFixed(1)}`;
          }
        }
      }
    },
    scales: {
      x: { type: 'time', time: { unit: 'week' },
           grid: { color: '#0e1115' },
           ticks: { color: '#888f9a', font: { family: "'DM Mono'", size: 9 } } },
      y: { position: 'left', grid: { color: '#141820' },
           title: { display: true, text: 'Equity (indexed, start=100)',
                    color: '#3d4a5c', font: { family: "'DM Mono'", size: 8 } },
           ticks: { color: '#888f9a', font: { family: "'DM Mono'", size: 9 } } },
      y2: { position: 'right', grid: { drawOnChartArea: false },
            title: { display: true, text: 'Oil price ($)',
                     color: '#3d4a5c', font: { family: "'DM Mono'", size: 8 } },
            ticks: { color: '#888f9a', font: { family: "'DM Mono'", size: 9 },
                     callback: v => '$' + v.toFixed(0) } }
    }
  };

  charts[canvasId] = new Chart(el, {
    type: 'line',
    data: {
      datasets: [
        // Oil baseline reference
        ...(oilBase && baselineData.length ? [{
          label: 'Oil baseline',
          data: baselineData,
          yAxisID: 'y2',
          borderColor: 'rgba(54,214,114,0.35)', borderWidth: 1,
          borderDash: [4, 4], pointRadius: 0, fill: false, tension: 0,
        }] : []),
        // Context equity (dim)
        { label: '_ctx_eq', data: ctxEq, yAxisID: 'y',
          borderColor: 'rgba(180,200,220,0.28)', borderWidth: 1,
          borderDash: [3,3], pointRadius: 0, fill: false, tension: 0.3 },
        // History equity (solid)
        { label: 'Equity (indexed)', data: histEq, yAxisID: 'y',
          borderColor: color, borderWidth: 2,
          pointRadius: 0, fill: false, tension: 0.3 },
        // Forecast equity (dashed)
        { label: '_fc_eq', data: fcEq, yAxisID: 'y',
          borderColor: color + '66', borderWidth: 1.5,
          borderDash: [5,3], pointRadius: 0, fill: false, tension: 0.4 },
        // Context oil (dim)
        { label: '_ctx_oil', data: ctxOil, yAxisID: 'y2',
          borderColor: 'rgba(248,196,32,0.25)', borderWidth: 1,
          borderDash: [3,3], pointRadius: 0, fill: false, tension: 0.3 },
        // History oil (solid amber)
        { label: 'Oil price ($)', data: histOil, yAxisID: 'y2',
          borderColor: 'rgba(248,196,32,0.80)', borderWidth: 2,
          pointRadius: 0, fill: false, tension: 0.3 },
        // Forecast oil (dashed amber)
        { label: '_fc_oil', data: fcOil, yAxisID: 'y2',
          borderColor: 'rgba(248,196,32,0.40)', borderWidth: 1.5,
          borderDash: [5,3], pointRadius: 0, fill: false, tension: 0.4 },
      ]
    },
    options: opts,
    plugins: [markerPlugin],
  });
}

// ── Shared marker plugin factory ──────────────────────────────────────────
function makeMarkerPlugin(id, stratStartStr, todayStr) {
  return {
    id,
    afterDraw(chart) {
      const xScale = chart.scales.x;
      if (!xScale) return;
      const ctx2 = chart.ctx;
      const drawLine = (dateStr, clr, label, dash) => {
        const x = xScale.getPixelForValue(new Date(dateStr).getTime());
        if (x < xScale.left || x > xScale.right) return;
        ctx2.save();
        ctx2.beginPath();
        ctx2.moveTo(x, chart.chartArea.top);
        ctx2.lineTo(x, chart.chartArea.bottom);
        ctx2.strokeStyle = clr;
        ctx2.lineWidth   = 1.5;
        ctx2.setLineDash(dash);
        ctx2.stroke();
        ctx2.fillStyle = clr;
        ctx2.font = "9px 'DM Mono'";
        ctx2.fillText(label, x + 3, chart.chartArea.top + 10);
        ctx2.restore();
      };
      if (stratStartStr && stratStartStr !== todayStr)
        drawLine(stratStartStr,    'rgba(79,163,247,0.7)',  'START',       [3,3]);
      drawLine(todayStr,           'rgba(232,160,32,0.7)',  'TODAY',       [4,3]);
      if (DATA.war_end_date) {
        drawLine(DATA.war_end_date,'rgba(54,214,114,0.8)',  'WAR END',     [2,2]);
        // Also mark the end of the oil reversion window if we know it
        if (DATA.oil_revert_end_date)
          drawLine(DATA.oil_revert_end_date,'rgba(54,214,114,0.45)','OIL NORM',[2,4]);
      }
    }
  };
}

// ── BUILD CHART: ALLOCATION / DEPLOYMENT BARS ────────────────────────────
function buildAllocChart(canvasId, strat, stratStartStr, todayStr) {
  const el = document.getElementById(canvasId);
  if (!el) return;
  if (charts[canvasId]) { charts[canvasId].destroy(); }

  const hist = strat.history;
  const fc   = strat.forecast;

  const histBars = hist.map(b => ({
    x: b.date,
    y: b.allocation,
    color: signalColor(b.allocation) + 'cc',
  }));
  const fcBars = fc.dates.map((d, i) => ({
    x: d,
    y: fc.alloc_median[i],
    color: signalColor(fc.alloc_median[i]) + '55',
  }));
  const allBars = [...histBars, ...fcBars];

  const dualPlugin = makeMarkerPlugin('allocMarkers_'+canvasId, stratStartStr, todayStr);

  const opts = allocOpts();
  charts[canvasId] = new Chart(el, {
    type: 'bar',
    data: {
      datasets: [{
        label: 'Allocation',
        data: allBars.map(b => ({ x: b.x, y: b.y })),
        backgroundColor: allBars.map(b => b.color),
        borderWidth: 0,
        barPercentage: 1.0, categoryPercentage: 1.0,
      }]
    },
    options: opts,
    plugins: [dualPlugin],
  });
}

// ── FLOATING TOOLTIP (body-level — escapes overflow:auto clipping) ────────
let _tlTip = null;
function _getTip() {
  if (!_tlTip) {
    _tlTip = document.createElement('div');
    _tlTip.id = 'tl-float-tip';
    Object.assign(_tlTip.style, {
      position:      'fixed',
      display:       'none',
      pointerEvents: 'none',
      zIndex:        '99999',
      background:    'var(--bg4)',
      border:        '1px solid var(--border)',
      padding:       '5px 9px',
      whiteSpace:    'nowrap',
      fontFamily:    "'DM Mono', monospace",
      fontSize:      '.58rem',
      color:         'var(--hi)',
      lineHeight:    '1.6',
    });
    document.body.appendChild(_tlTip);
  }
  return _tlTip;
}

function _attachTipEvents(bar, html) {
  bar.addEventListener('mouseenter', e => {
    const tip = _getTip();
    tip.innerHTML = html;
    tip.style.display = 'block';
    _positionTip(e);
  });
  bar.addEventListener('mousemove', _positionTip);
  bar.addEventListener('mouseleave', () => {
    _getTip().style.display = 'none';
  });
}

function _positionTip(e) {
  const tip = _getTip();
  const pad = 10;
  const tw  = tip.offsetWidth;
  const th  = tip.offsetHeight;
  let   x   = e.clientX + pad;
  let   y   = e.clientY - th - pad;
  if (x + tw > window.innerWidth  - pad) x = e.clientX - tw - pad;
  if (y < pad)                           y = e.clientY + pad;
  tip.style.left = x + 'px';
  tip.style.top  = y + 'px';
}

// ── BUILD TIMELINE ────────────────────────────────────────────────────────
function buildTimeline(containerId, strat) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = '';

  strat.history.forEach(b => {
    const bar = document.createElement('div');
    bar.className = 'tbar';
    bar.style.background = signalColor(b.allocation);
    _attachTipEvents(bar,
      `${b.date}<br>${b.signal}<br>${(b.allocation*100).toFixed(0)}% allocated`
    );
    el.appendChild(bar);
  });

  const todayLine = document.createElement('div');
  todayLine.className = 'today-line';
  el.appendChild(todayLine);

  strat.forecast.dates.forEach((d, i) => {
    const bar = document.createElement('div');
    bar.className = 'tbar forecast';
    bar.style.background = signalColor(strat.forecast.alloc_median[i]);
    _attachTipEvents(bar,
      `${d}<br>Forecast median<br>${(strat.forecast.alloc_median[i]*100).toFixed(0)}% allocated`
    );
    el.appendChild(bar);
  });
}

// ── OVERVIEW PANEL ────────────────────────────────────────────────────────
function buildOverviewPanel(container) {
  const live = DATA.live;
  const sigCls = signalClass(live.signal);

  const warnings = (DATA.live.warnings || []).map(w =>
    `<div class="warn-band"><span class="w-icon">${w.slice(0,2)}</span><span class="w-msg">${w.slice(2).trim()}</span></div>`
  ).join('');

  const tiles = [
    { label: 'Price',        val: '$' + live.price.toFixed(2),       sub: DATA.ticker,       cls: 'accent' },
    { label: 'Drawdown',     val: (live.drawdown*100).toFixed(2)+'%', sub: 'from 20d high',  cls: live.drawdown > 0.07 ? 'green' : '' },
    { label: 'Oil Spike',    val: (live.oil_spike*100).toFixed(2)+'%', sub: 'vs 5d avg',     cls: Math.abs(live.oil_spike) > 0.1 ? 'blue' : '' },
    { label: '3-day Return', val: pct(live.return_3d),                sub: DATA.ticker,       cls: live.return_3d > 0 ? 'green' : 'red' },
    { label: 'Signal',       val: live.signal,                         sub: live.strategy,    cls: 'accent' },
    { label: 'Allocation',   val: (live.allocation*100).toFixed(0)+'%', sub: 'today',         cls: live.allocation > 0 ? 'green' : '' },
    { label: 'Strategy Start', val: DATA.strategy_start || '—',       sub: 'activated date', cls: 'blue' },
    { label: 'Oil Baseline', val: DATA.oil_baseline ? '$'+DATA.oil_baseline.toFixed(2) : '—',
                                                                        sub: 'pre-war oil price', cls: '' },
    { label: 'War End Date', val: DATA.war_end_date || 'Not set',      sub: 'projected reversion', cls: DATA.war_end_date ? 'green' : '' },
    { label: 'Pre-context',  val: (DATA.context?.length || 0) + 'd',   sub: 'market context before start', cls: '' },
    { label: 'Active Period',val: (DATA.strategies[0]?.history?.length || 0) + 'd', sub: 'strategy bars', cls: '' },
    { label: 'Forecast',     val: DATA.forecast_days + 'd',            sub: 'MC projection',  cls: 'blue' },
  ];

  const tileHtml = tiles.map(t => `
    <div class="sig-tile ${t.cls}">
      <div class="st-label">${t.label}</div>
      <div class="st-val ${t.cls==='red'?'neg':t.cls==='green'?'pos':''}">${t.val}</div>
      <div class="st-sub">${t.sub}</div>
    </div>`).join('');

  // Per-strategy portfolio value rows
  const stratRows = DATA.strategies.map(s => {
    const last  = s.history[s.history.length - 1];
    const fcEnd = s.forecast.p50[s.forecast.p50.length - 1];
    const pv    = last  ? last.portfolio_value  : 0;
    const ret   = DATA.initial_capital > 0 ? (pv / DATA.initial_capital) - 1 : 0;
    const lsig  = s.live_signal || (last ? last.signal : '—');
    const lalloc = s.live_allocation != null
                   ? (s.live_allocation * 100).toFixed(0) + '%'
                   : (last ? (last.allocation * 100).toFixed(0) + '%' : '—');
    return `<tr style="border-bottom:1px solid var(--border)">
      <td style="padding:8px 12px"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${s.color};margin-right:8px;vertical-align:middle"></span>${s.name}</td>
      <td style="padding:8px 12px;color:${ret>=0?'var(--green)':'var(--red)'};font-family:var(--mono)">${pct(ret)}</td>
      <td style="padding:8px 12px;font-family:var(--mono)">${usd(pv)}</td>
      <td style="padding:8px 12px;color:var(--muted);font-size:.75rem">${usd(last ? last.shares_value : 0)} shares + ${usd(last ? last.cash_remaining : 0)} cash</td>
      <td style="padding:8px 12px"><span class="sp-val ${signalClass(lsig)}" style="font-size:.78rem">${lsig}</span> <span style="color:var(--muted);font-size:.7rem">${lalloc}</span></td>
      <td style="padding:8px 12px;font-family:var(--mono);color:${s.color}88">${usd(fcEnd)}</td>
    </tr>`;
  }).join('');

  container.innerHTML = `
    ${warnings}
    <div class="signal-band">${tileHtml}</div>
    <table style="width:100%;border-collapse:collapse;font-family:var(--mono);font-size:.8rem;background:var(--bg2);border:1px solid var(--border)">
      <thead><tr style="border-bottom:1px solid var(--border)">
        <th style="text-align:left;padding:8px 12px;font-size:.58rem;letter-spacing:.14em;color:var(--muted)">STRATEGY</th>
        <th style="text-align:left;padding:8px 12px;font-size:.58rem;letter-spacing:.14em;color:var(--muted)">RETURN</th>
        <th style="text-align:left;padding:8px 12px;font-size:.58rem;letter-spacing:.14em;color:var(--muted)">PORTFOLIO VALUE</th>
        <th style="text-align:left;padding:8px 12px;font-size:.58rem;letter-spacing:.14em;color:var(--muted)">BREAKDOWN</th>
        <th style="text-align:left;padding:8px 12px;font-size:.58rem;letter-spacing:.14em;color:var(--muted)">LIVE SIGNAL</th>
        <th style="text-align:left;padding:8px 12px;font-size:.58rem;letter-spacing:.14em;color:var(--muted)">FORECAST END (p50)</th>
      </tr></thead>
      <tbody style="font-size:.82rem">
        ${stratRows}
      </tbody>
    </table>
    <div style="font-family:var(--mono);font-size:.6rem;color:var(--muted);line-height:1.8;background:var(--bg2);border:1px solid var(--border);padding:12px 16px">
      All values rebased to $${(DATA.initial_capital/1000).toFixed(0)}k at start of history window.
      Buy-Only equity = unrealized mark-to-market value. Forecast uses Monte Carlo (${DATA.forecast_days} trading days, 500 paths).
      Past performance does not guarantee future results. Not financial advice.
    </div>`;
}

// ── STRATEGY PANEL ────────────────────────────────────────────────────────
function buildStrategyPanel(container, strat, stratStartStr, todayStr) {
  const last  = strat.history[strat.history.length - 1];
  const first = strat.history[0];
  const fcEnd = strat.forecast.p50[strat.forecast.p50.length - 1];
  const pv    = last ? last.portfolio_value : 0;
  // Use initial_capital as the return baseline — consistent across all strategies
  const ret   = DATA.initial_capital > 0 ? (pv / DATA.initial_capital) - 1 : 0;

  const isBuyOnly = strat.name === 'OilWar Buy-Only';

  const descMap = {
    'OilWar Active':   'Rebalances daily to the drawdown × oil signal target. Both buys and sells.',
    'OilWar Buy-Only': 'Accumulates shares on every buy signal. Never sells. Shows total portfolio value: shares + remaining cash.',
    'Buy & Hold':      'Passive benchmark. 100% invested from day one with no signal-based changes.',
  };

  // Use per-strategy live signal if available, fall back to last history bar
  const todaySignal = strat.live_signal  || (last ? last.signal    : '—');
  const todayAlloc  = strat.live_allocation != null
                      ? (strat.live_allocation * 100).toFixed(0) + '%'
                      : (last ? (last.allocation * 100).toFixed(0) + '%' : '—');

  // Per-strategy metrics (pre-computed in dashboard.py if available)
  const metrics    = strat.metrics || {};
  const metricHtml = metrics.sharpe != null ? `
    <div class="sig-tile">
      <div class="st-label">Sharpe Ratio</div>
      <div class="st-val ${metrics.sharpe >= 0 ? 'pos' : 'neg'}">${metrics.sharpe.toFixed(2)}</div>
      <div class="st-sub">annualised, history window</div>
    </div>
    <div class="sig-tile red">
      <div class="st-label">Max Drawdown</div>
      <div class="st-val neg">${pct(metrics.max_dd)}</div>
      <div class="st-sub">from peak, history window</div>
    </div>
    <div class="sig-tile">
      <div class="st-label">Annualised Vol</div>
      <div class="st-val">${(metrics.vol * 100).toFixed(1)}%</div>
      <div class="st-sub">${metrics.days}d window</div>
    </div>` : '';

  // KPI tiles — same layout for all strategies, showing portfolio breakdown
  const kpiHtml = `
    <div class="signal-band">
      <div class="sig-tile accent">
        <div class="st-label">Total Portfolio Value</div>
        <div class="st-val">${usd(pv)}</div>
        <div class="st-sub">shares + cash</div>
      </div>
      <div class="sig-tile">
        <div class="st-label">Shares (Invested)</div>
        <div class="st-val" style="color:${strat.color}">${usd(last ? last.shares_value : 0)}</div>
        <div class="st-sub">${last ? (last.allocation*100).toFixed(0) : 0}% of portfolio</div>
      </div>
      <div class="sig-tile">
        <div class="st-label">Cash (Uninvested)</div>
        <div class="st-val">${usd(last ? last.cash_remaining : 0)}</div>
        <div class="st-sub">${last ? ((1-last.allocation)*100).toFixed(0) : 100}% of portfolio</div>
      </div>
      <div class="sig-tile ${ret >= 0 ? 'green' : 'red'}">
        <div class="st-label">Return (vs Capital)</div>
        <div class="st-val ${sgn(ret)}">${pct(ret)}</div>
        <div class="st-sub">vs $${(DATA.initial_capital/1000).toFixed(0)}k initial</div>
      </div>
      <div class="sig-tile">
        <div class="st-label">Today's Signal</div>
        <div class="st-val ${signalClass(todaySignal)}">${todaySignal}</div>
        <div class="st-sub">${todayAlloc} allocation · ${DATA.live.date}</div>
      </div>
      <div class="sig-tile blue">
        <div class="st-label">Forecast End (p50)</div>
        <div class="st-val" style="color:${strat.color}aa">${usd(fcEnd)}</div>
        <div class="st-sub">median of ${DATA.forecast_days}d MC</div>
      </div>
      <div class="sig-tile">
        <div class="st-label">Forecast End (p90)</div>
        <div class="st-val pos">${usd(strat.forecast.p90[strat.forecast.p90.length-1])}</div>
        <div class="st-sub">bull scenario</div>
      </div>
      <div class="sig-tile">
        <div class="st-label">Forecast End (p10)</div>
        <div class="st-val neg">${usd(strat.forecast.p10[strat.forecast.p10.length-1])}</div>
        <div class="st-sub">bear scenario</div>
      </div>
      ${metricHtml}
    </div>`;

  // Extra note for Buy-Only explaining unrealized vs realised
  const buyOnlyNote = isBuyOnly ? `
    <div class="unreal-box">
      <div class="ub-title">🔒 Buy-Only — Unrealized Position</div>
      <div class="ub-row">
        <div class="ub-cell">
          <div class="ub-label">SHARES VALUE (UNREALIZED)</div>
          <div class="ub-val">${usd(last ? last.shares_value : 0)}</div>
        </div>
        <div class="ub-cell">
          <div class="ub-label">CASH NOT YET DEPLOYED</div>
          <div class="ub-val">${usd(last ? last.cash_remaining : 0)}</div>
        </div>
        <div class="ub-cell">
          <div class="ub-label">TOTAL IF SOLD TODAY</div>
          <div class="ub-val ${sgn(ret)}">${usd(pv)}</div>
        </div>
        <div class="ub-cell">
          <div class="ub-label">NOTE</div>
          <div class="ub-note">
            Shares accumulate at each buy signal and are never sold.
            Portfolio value = shares × today's price + undeployed cash.
            The forecast shows this total continuing to evolve as new buys may occur.
          </div>
        </div>
      </div>
    </div>` : '';

  // War-end callout — shown when war_end_date is set
  const warEndNote = DATA.war_end_date ? `
    <div style="background:rgba(54,214,114,.06);border:1px solid rgba(54,214,114,.22);padding:12px 18px;display:flex;gap:20px;flex-wrap:wrap;align-items:center">
      <div>
        <div style="font-family:var(--mono);font-size:.58rem;letter-spacing:.16em;color:var(--green);text-transform:uppercase;margin-bottom:4px">🕊 Projected War End</div>
        <div style="font-family:var(--mono);font-size:1.1rem;color:#fff">${DATA.war_end_date}</div>
      </div>
      <div>
        <div style="font-family:var(--mono);font-size:.58rem;letter-spacing:.14em;color:var(--muted);margin-bottom:4px">OIL PEAK AT WAR END</div>
        <div style="font-family:var(--mono);font-size:1.1rem;color:#f8c420">${DATA.oil_war_peak ? '$'+DATA.oil_war_peak.toFixed(2) : '—'}</div>
      </div>
      <div>
        <div style="font-family:var(--mono);font-size:.58rem;letter-spacing:.14em;color:var(--muted);margin-bottom:4px">OIL REVERSION TARGET</div>
        <div style="font-family:var(--mono);font-size:1.1rem;color:var(--text)">$${DATA.oil_baseline?.toFixed(2) || '—'} <span style="font-size:.7rem;color:var(--muted)">over ${DATA.oil_revert_days || '?'}d</span></div>
      </div>
      <div style="font-family:var(--mono);font-size:.65rem;color:var(--muted);max-width:360px;line-height:1.6">
        <b style="color:var(--text)">Phase 1</b> (now→war end): oil rises linearly to peak, driving buy signals.<br>
        <b style="color:var(--text)">Phase 2</b> (war end→+${DATA.oil_revert_days || '?'}d): oil falls back to pre-war baseline.<br>
        <b style="color:var(--text)">Phase 3</b>: oil anchors near baseline with reduced volatility.
      </div>
    </div>` : '';

  const key = strat.name.replace(/\W/g,'_');

  container.innerHTML = `
    <div class="strat-header">
      <div class="strat-dot" style="background:${strat.color}"></div>
      <div class="strat-name">${strat.name}</div>
      <div class="strat-desc">${descMap[strat.name] || ''}</div>
    </div>

    ${kpiHtml}
    ${buyOnlyNote}
    ${warEndNote}

    <div class="chart-card">
      <div class="cc-title">
        Portfolio Value — History &amp; Forecast
        <span class="cc-tag forecast">HOVER FOR SHARES + CASH BREAKDOWN</span>
      </div>
      <canvas id="ceq_${key}" style="height:220px"></canvas>
    </div>

    <div class="chart-card">
      <div class="cc-title">
        Equity &amp; Oil Prices — All Phases
        <span class="cc-tag forecast">EQUITY INDEXED (LEFT) · OIL $ (RIGHT)</span>
      </div>
      <canvas id="cpx_${key}" style="height:220px"></canvas>
    </div>

    <div class="chart-card">
      <div class="cc-title">
        ${isBuyOnly ? 'Cumulative Cash Deployed %' : 'Daily Allocation %'}
        <span class="cc-tag forecast">INCLUDES FORECAST MEDIAN</span>
      </div>
      <canvas id="cal_${key}" style="height:180px"></canvas>
    </div>

    <div class="timeline-wrap">
      <div class="timeline-title">Signal Timeline — History (solid) &nbsp;|&nbsp; Forecast Median (faded) &nbsp;|&nbsp; Hover for detail</div>
      <div class="timeline" id="tl_${key}"></div>
    </div>`;

  requestAnimationFrame(() => {
    buildEquityForecastChart(`ceq_${key}`, strat, stratStartStr, todayStr);
    buildPriceChart(`cpx_${key}`, strat, stratStartStr, todayStr);
    buildAllocChart(`cal_${key}`, strat, stratStartStr, todayStr);
    buildTimeline(`tl_${key}`, strat);
  });
}

// ── INIT / NAV ────────────────────────────────────────────────────────────
function init() {
  if (!DATA) return;

  // Topbar
  document.getElementById('sfGen').textContent    = DATA.generated || '—';
  document.getElementById('sfTicker').textContent = DATA.ticker + ' / ' + DATA.oil_ticker;
  document.getElementById('signalPill').style.display = '';
  document.getElementById('spVal').textContent   = DATA.live.signal;
  document.getElementById('spVal').className     = 'sp-val ' + signalClass(DATA.live.signal);
  document.getElementById('spAlloc').textContent = (DATA.live.allocation * 100).toFixed(0) + '%';

  const todayStr      = DATA.live.date;
  const stratStartStr = DATA.strategy_start || todayStr;

  // Build nav
  const navEl = document.getElementById('navItems');
  navEl.innerHTML = '';

  const navDefs = [
    { id: 'overview', label: 'Overview', color: '#e8a020' },
    ...DATA.strategies.map(s => ({ id: s.name, label: s.name, color: s.color })),
  ];

  navDefs.forEach(nav => {
    const el = document.createElement('div');
    el.className = 'nav-item' + (nav.id === 'overview' ? ' active' : '');
    el.dataset.tab = nav.id;
    el.innerHTML = `<span class="nav-dot" style="background:${nav.color}"></span>${nav.label}`;
    el.addEventListener('click', () => switchTab(nav.id, stratStartStr, todayStr));
    navEl.appendChild(el);
  });

  // Build panels
  const area = document.getElementById('panelArea');
  area.innerHTML = '';

  const ovPanel = document.createElement('div');
  ovPanel.className = 'panel active';
  ovPanel.id = 'panel_overview';
  area.appendChild(ovPanel);
  buildOverviewPanel(ovPanel);

  DATA.strategies.forEach(s => {
    const p = document.createElement('div');
    p.className = 'panel';
    p.id = 'panel_' + s.name.replace(/\W/g,'_');
    area.appendChild(p);
  });

  switchTab('overview', stratStartStr, todayStr);
}

// ── HELP DRAWER ───────────────────────────────────────────────────────────

const HELP_CONTENT = {

  overview: `
    <div class="hd-section">
      <div class="hd-section-title">Overview Panel</div>

      <div class="hd-item">
        <div class="hd-item-label">Today's Signal <span class="hd-badge accent">LIVE</span></div>
        <div class="hd-item-desc">The actionable signal computed right now by the OilWar Active strategy using live market data. This is what you act on today.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Allocation %</div>
        <div class="hd-item-desc">The fraction of your portfolio the signal recommends putting into equity today. 0% = stay in cash. 100% = fully invested.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Price &amp; 20-day High</div>
        <div class="hd-item-desc">Current equity close price and the rolling 20-day peak. The gap between them drives the drawdown signal.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Drawdown</div>
        <div class="hd-item-desc">How far the equity has fallen from its 20-day high, expressed as a percentage. Higher drawdowns trigger larger buy allocations — the strategy interprets dips as entry opportunities during conflict periods.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Oil Spike</div>
        <div class="hd-item-desc">The percentage difference between today's oil price and its 5-day average. A moderate spike (+5–20%) amplifies the buy signal. An extreme spike (&gt;20%) reduces it as a tail-risk guard.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">3-day Return</div>
        <div class="hd-item-desc">Equity return over the last 3 trading days. Positive momentum on low oil spike adds a +25% allocation boost. Negative momentum on high oil spike is a deterioration warning.</div>
      </div>
    </div>

    <div class="hd-section">
      <div class="hd-section-title">Signal Levels</div>
      <div class="hd-signal-grid">
        <div class="hd-sig-chip"><div class="hd-sig-dot" style="background:#3d4a5c"></div><div class="hd-sig-label">Hold / Cash</div><div class="hd-sig-alloc">0%</div></div>
        <div class="hd-sig-chip"><div class="hd-sig-dot" style="background:#e8a020"></div><div class="hd-sig-label">Small Buy</div><div class="hd-sig-alloc">≤25%</div></div>
        <div class="hd-sig-chip"><div class="hd-sig-dot" style="background:#4fa3f7"></div><div class="hd-sig-label">Moderate Buy</div><div class="hd-sig-alloc">≤50%</div></div>
        <div class="hd-sig-chip"><div class="hd-sig-dot" style="background:#36d672"></div><div class="hd-sig-label">Large Buy</div><div class="hd-sig-alloc">≤75%</div></div>
        <div class="hd-sig-chip" style="grid-column:1/-1"><div class="hd-sig-dot" style="background:#36d672;box-shadow:0 0 6px #36d672"></div><div class="hd-sig-label">Full Position</div><div class="hd-sig-alloc">100%</div></div>
      </div>
    </div>

    <div class="hd-section">
      <div class="hd-section-title">Strategy Comparison Table</div>
      <div class="hd-item">
        <div class="hd-item-label">Return</div>
        <div class="hd-item-desc">Total portfolio gain or loss since the strategy start date, measured against the initial capital. Positive = green, negative = red.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Portfolio Value</div>
        <div class="hd-item-desc">Current mark-to-market value of the portfolio: shares held (at today's price) plus uninvested cash.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Live Signal</div>
        <div class="hd-item-desc">Each strategy's own signal computed against today's market — not historical. OilWar Active rebalances; Buy-Only only adds; Buy &amp; Hold is always fully invested.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Forecast End (p50)</div>
        <div class="hd-item-desc">The median portfolio value at the end of the Monte Carlo forecast window. Half of simulated paths end above this, half below.</div>
      </div>
    </div>

    <div class="hd-section">
      <div class="hd-section-title">Signal Logic</div>
      <div class="hd-formula">
        Base alloc  = f(drawdown)<br>
        &nbsp; &lt;3%  → 0%  (cash)<br>
        &nbsp; 3–5% → 25%<br>
        &nbsp; 5–7% → 50%<br>
        &nbsp; 7–10%→ 75%<br>
        &nbsp; &gt;10% → 100%<br><br>
        Oil modifier:<br>
        &nbsp; spike &lt; −5% → ×1.10 (de-escalation boost)<br>
        &nbsp; −5 to +5%  → unchanged<br>
        &nbsp; +5 to +20% → ×1.20 (war premium add)<br>
        &nbsp; &gt;+20%     → ×0.70 (tail-risk cut)<br><br>
        Momentum boost:<br>
        &nbsp; 3d return &gt;+1.5% &amp; spike &lt;10% → +25%
      </div>
    </div>`,

  strategy: `
    <div class="hd-section">
      <div class="hd-section-title">KPI Tiles</div>
      <div class="hd-item">
        <div class="hd-item-label">Total Portfolio Value <span class="hd-badge accent">TOP LINE</span></div>
        <div class="hd-item-desc">Shares held at today's price plus all uninvested cash. This is what you would walk away with if you sold everything right now.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Shares (Invested)</div>
        <div class="hd-item-desc">The mark-to-market value of your equity position. For OilWar Active this fluctuates daily with rebalancing. For Buy-Only it grows as new shares are purchased and never shrinks from selling.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Cash (Uninvested)</div>
        <div class="hd-item-desc">Capital not currently deployed into equity. For Buy-Only this is the portion of initial capital not yet spent — it does not earn returns until deployed.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Return (vs Capital) <span class="hd-badge green">PERF</span></div>
        <div class="hd-item-desc">Gain or loss expressed as a percentage of the original starting capital, not the first bar's value. Consistent across all strategies.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Today's Signal <span class="hd-badge accent">LIVE</span></div>
        <div class="hd-item-desc">This strategy's own live signal computed against today's market conditions — each strategy type may produce a different value from the same inputs.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Forecast End (p10 / p50 / p90) <span class="hd-badge blue">MC</span></div>
        <div class="hd-item-desc">Monte Carlo percentile outcomes at the end of the forecast window. p50 is the median — an equal number of paths end above and below it. p90 is the bull scenario (only 10% of paths exceed it). p10 is the bear scenario.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Sharpe Ratio <span class="hd-badge muted">RISK</span></div>
        <div class="hd-item-desc">Annualised return divided by annualised volatility over the history window. Higher is better. Above 1.0 is generally considered good. Below 0 means the strategy lost money relative to its own risk.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Max Drawdown <span class="hd-badge red">RISK</span></div>
        <div class="hd-item-desc">The largest peak-to-trough decline in portfolio value during the history window. A useful measure of downside risk — how bad did it get at the worst point.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Annualised Vol</div>
        <div class="hd-item-desc">Standard deviation of daily returns scaled to a full year (×√252). Reflects how much the portfolio swings day-to-day.</div>
      </div>
    </div>

    <div class="hd-section">
      <div class="hd-section-title">Portfolio Value — History &amp; Forecast</div>
      <div class="hd-item">
        <div class="hd-item-label">Market (pre-strategy) <span class="hd-badge muted">CONTEXT</span></div>
        <div class="hd-item-desc">The raw equity price before your strategy start date, rescaled so it connects seamlessly to the portfolio value line. Shows what the market was doing in the lead-up period.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Portfolio Value <span class="hd-badge accent">HISTORY</span></div>
        <div class="hd-item-desc">Actual simulated portfolio value from strategy start through today. Hover any point for a full breakdown: shares value, cash remaining, allocation %, and signal at that date.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Forecast (median) <span class="hd-badge blue">FORECAST</span></div>
        <div class="hd-item-desc">The p50 median path from Monte Carlo simulation. The dashed line extends the strategy forward using simulated equity and oil returns.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">P25–P75 band <span class="hd-badge blue">FORECAST</span></div>
        <div class="hd-item-desc">The middle 50% of simulated outcomes. Half of all simulation paths stay within this shaded band — a reasonable central case range.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">P10–P90 band <span class="hd-badge blue">FORECAST</span></div>
        <div class="hd-item-desc">The outer 80% of simulated outcomes. Only 10% of paths go above the top edge (bull case) or below the bottom edge (bear case). The wide spread reflects genuine long-term uncertainty.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">START / TODAY / WAR END markers</div>
        <div class="hd-item-desc">Vertical dashed lines: blue = strategy activation date, amber = today (the history/forecast boundary), green = projected war end date where the oil reversion scenario begins.</div>
      </div>
    </div>

    <div class="hd-section">
      <div class="hd-section-title">Equity &amp; Oil Prices — All Phases</div>
      <div class="hd-item">
        <div class="hd-item-label">Equity (indexed, left axis)</div>
        <div class="hd-item-desc">Equity price normalised so the first context bar = 100. Makes the trend visible regardless of absolute price level. Context, history, and forecast are shown as three connected segments.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Oil price ($, right axis)</div>
        <div class="hd-item-desc">Raw WTI oil price in dollars. The forecast oil path follows a three-phase model: rising toward the war-peak, reverting to baseline after war end, then low-volatility drift near baseline.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Oil baseline (green dashed)</div>
        <div class="hd-item-desc">The pre-war oil price at the strategy start date. This is the long-run reversion target after the conflict resolves.</div>
      </div>
    </div>

    <div class="hd-section">
      <div class="hd-section-title">Daily Allocation % / Cash Deployed %</div>
      <div class="hd-item">
        <div class="hd-item-label">OilWar Active — Daily Allocation</div>
        <div class="hd-item-desc">The fraction of the portfolio invested in equity on each bar, colour-coded by signal level. Bars to the right of TODAY are forecast median allocations — how the strategy is expected to behave going forward.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">OilWar Buy-Only — Cumulative Cash Deployed</div>
        <div class="hd-item-desc">The running total of capital spent purchasing shares, expressed as a fraction of initial capital. This only ever rises — once cash is deployed buying shares, it is not returned. The forecast shows expected further deployment.</div>
      </div>
    </div>

    <div class="hd-section">
      <div class="hd-section-title">Signal Timeline</div>
      <div class="hd-item">
        <div class="hd-item-label">Colour bars</div>
        <div class="hd-item-desc">Each bar is one trading day. Colour encodes the allocation signal: dark grey = cash, amber = small buy, blue = moderate buy, bright green = large/full. Hover any bar for the exact date, signal label, and allocation %.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">TODAY marker</div>
        <div class="hd-item-desc">The amber vertical line separates realised history (solid bars) from forecast (faded bars). Faded bars show the expected median signal for each future trading day.</div>
      </div>
    </div>`,

  forecast: `
    <div class="hd-section">
      <div class="hd-section-title">Monte Carlo Forecast Model</div>
      <div class="hd-item">
        <div class="hd-item-label">How it works</div>
        <div class="hd-item-desc">The model runs 500 independent simulated paths forward from today. Each path generates correlated daily equity and oil returns using the 20-day historical volatility and a calibrated equity-oil correlation. The strategy's signal logic runs on each path, producing a portfolio value per day. Percentile bands are then taken across all 500 paths.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Phase 1 — War period <span class="hd-badge red">NOW → WAR END</span></div>
        <div class="hd-item-desc">Oil rises linearly toward the war-peak price. Equity drifts with its recent war-period trend. The strategy responds to rising oil with larger buy signals where drawdown warrants.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Phase 2 — Reversion <span class="hd-badge accent">WAR END → +N DAYS</span></div>
        <div class="hd-item-desc">Oil falls linearly back to the pre-war baseline over the configured reversion window. Equity recovers at a symmetric rate to its war-period decline. The strategy typically reduces position size as oil normalises.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Phase 3 — Post-war <span class="hd-badge green">THEREAFTER</span></div>
        <div class="hd-item-desc">Oil drifts near the baseline with reduced volatility. Equity returns to normal-vol random walk. Signals become driven purely by equity drawdown without an oil premium.</div>
      </div>
      <div class="hd-item">
        <div class="hd-item-label">Correlation</div>
        <div class="hd-item-desc">Equity and oil returns are negatively correlated in the model (oil up → equity down) using a 60-bar rolling estimate clamped to a realistic range. During the reversion phase the correlation is tightened to make the equity recovery more visible.</div>
      </div>
    </div>`,
};

function buildHelpContent(tabId) {
  const sections = [];
  if (tabId === 'overview') {
    sections.push(HELP_CONTENT.overview);
  } else {
    sections.push(HELP_CONTENT.strategy);
    sections.push(HELP_CONTENT.forecast);
  }
  document.getElementById('helpBody').innerHTML = sections.join('<hr class="hd-divider">');
}

(function initHelp() {
  const btn      = document.getElementById('helpBtn');
  const drawer   = document.getElementById('helpDrawer');
  const backdrop = document.getElementById('helpBackdrop');
  const closeBtn = document.getElementById('helpClose');

  function openHelp()  { drawer.classList.add('open'); backdrop.classList.add('open'); }
  function closeHelp() { drawer.classList.remove('open'); backdrop.classList.remove('open'); }

  btn.addEventListener('click', openHelp);
  closeBtn.addEventListener('click', closeHelp);
  backdrop.addEventListener('click', closeHelp);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeHelp(); });

  // Expose so switchTab can update the content
  window._updateHelp = buildHelpContent;
  buildHelpContent('overview');
})();

function switchTab(tabId, stratStartStr, todayStr) {
  if (window._updateHelp) window._updateHelp(tabId);
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tabId);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));

  const panelId = 'panel_' + (tabId === 'overview' ? 'overview' : tabId.replace(/\W/g,'_'));
  const panel   = document.getElementById(panelId);
  if (!panel) return;
  panel.classList.add('active');

  const ctxDays  = DATA.context?.length || 0;
  const histDays = DATA.strategies[0]?.history?.length || 0;

  document.getElementById('pageTitle').textContent = tabId === 'overview' ? 'Overview' : tabId;
  document.getElementById('pageSubtitle').textContent = tabId === 'overview'
    ? `${ctxDays}d context · ${histDays}d strategy · ${DATA.forecast_days}d forecast · ${DATA.strategies.length} strategies`
    : `${ctxDays}d pre-strategy context + ${histDays}d active + ${DATA.forecast_days}d forecast`;



  if (tabId !== 'overview') {
    const strat = DATA.strategies.find(s => s.name === tabId);
    if (!strat) return;
    if(!panel.dataset.built) {
      panel.dataset.built = '1';
      buildStrategyPanel(panel, strat, stratStartStr, todayStr);
    }
    document.getElementById('signalPill').style.display = '';
    document.getElementById('spVal').textContent = strat.live_signal;
    document.getElementById('spVal').className     = 'sp-val ' + signalClass(strat.live_signal);
  }else {
    //Don't display the signal pill if the strategy is not active or we are on the overview page
    document.getElementById('signalPill').style.display = 'none';
  }
}
