import { fmt, fmtD, cls, esc, fetchJson } from './utils.js';
import { store } from './state.js';

/* ── Chart instances (destroyed on re-render) ─────────────────────────── */
let _equityChart = null;
let _pnlChart = null;
let _drawdownChart = null;
let _sectorChart = null;
let _concentrationChart = null;
let _monthlyPnlChart = null;

/* ── Helpers ──────────────────────────────────────────────────────────── */

function _css(varName) {
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

function _chartColors() {
  return {
    accent: _css('--color-accent') || '#6366f1',
    pos: _css('--color-pos') || '#34d399',
    neg: _css('--color-neg') || '#f87171',
    text: _css('--color-text-secondary') || '#94a3b8',
    grid: _css('--color-border-subtle') || '#1e2235',
    surface: _css('--color-surface') || '#1a1d2e',
  };
}

const SECTOR_PALETTE = [
  '#6366f1', '#06b6d4', '#f59e0b', '#ef4444', '#8b5cf6',
  '#14b8a6', '#f97316', '#ec4899', '#22c55e', '#3b82f6',
  '#a855f7', '#eab308',
];

function _destroyChart(ref) {
  if (ref) ref.destroy();
  return null;
}

function _fmtCurrency(v) {
  if (v == null) return '—';
  return '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function _fmtPct(v) {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + Number(v).toFixed(1) + '%';
}

function _fmtMktCap(v) {
  if (v == null) return '—';
  if (v >= 1e12) return '$' + (v / 1e12).toFixed(1) + 'T';
  if (v >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B';
  if (v >= 1e6) return '$' + (v / 1e6).toFixed(0) + 'M';
  return '$' + Number(v).toLocaleString();
}

/* ── Main load functions (called by tab switch) ───────────────────────── */

export async function loadAnalytics() {
  const wrap = document.getElementById('analytics-content');
  if (!wrap) return;

  if (!store.analyticsState.loaded) {
    wrap.innerHTML = '<div class="loading">Loading analytics…</div>';
  }

  try {
    const [perf, exposure, concentration, income] = await Promise.all([
      fetchJson('/api/analytics/performance'),
      fetchJson('/api/analytics/exposure').catch(() => ({ sectors: [], total_value: 0 })),
      fetchJson('/api/analytics/concentration').catch(() => ({ holdings: [], hhi: 0, hhi_label: 'Unknown' })),
      fetchJson('/api/analytics/income-summary').catch(() => ({ stats: {}, monthly_pnl: { months: [], values: [] }, strategy_breakdown: {} })),
    ]);

    store.analyticsState.loaded = true;
    _renderAnalytics(wrap, perf, exposure, concentration, income);
  } catch (e) {
    wrap.innerHTML = '<div class="error">Error loading analytics: ' + esc(e.message) + '</div>';
  }
}

function _renderAnalytics(wrap, perf, exposure, concentration, income) {
  const hasPerf = perf.dates && perf.dates.length > 1;
  const hasExposure = exposure.sectors && exposure.sectors.length > 0;
  const hasConcen = concentration.holdings && concentration.holdings.length > 0;
  const hasIncome = income.monthly_pnl && income.monthly_pnl.months.length > 0;

  let html = '';

  // Performance section
  html += '<div class="an-section">';
  html += '<h2 class="an-section-title">Portfolio Performance</h2>';
  if (hasPerf) {
    const last = perf.equity[perf.equity.length - 1];
    const first = perf.equity[0];
    const totalReturn = perf.cumulative_return_pct[perf.cumulative_return_pct.length - 1];
    const todayPnl = perf.daily_pnl[perf.daily_pnl.length - 1];
    const maxDd = Math.min(...perf.drawdown_pct);

    html += '<div class="an-kpi-row">';
    html += `<div class="an-kpi"><div class="an-kpi-label">Current Value</div><div class="an-kpi-value">${_fmtCurrency(last)}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Total Return</div><div class="an-kpi-value ${cls(totalReturn)}">${_fmtPct(totalReturn)}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Last Day P&L</div><div class="an-kpi-value ${cls(todayPnl)}">${_fmtCurrency(todayPnl)}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Max Drawdown</div><div class="an-kpi-value neg">${_fmtPct(maxDd)}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Data Points</div><div class="an-kpi-value">${perf.dates.length} days</div></div>`;
    html += '</div>';
    html += '<div class="an-chart-grid">';
    html += '<div class="an-chart-panel"><h3 class="an-chart-title">Equity Curve</h3><canvas id="an-equity-chart"></canvas></div>';
    html += '<div class="an-chart-panel"><h3 class="an-chart-title">Daily P&L</h3><canvas id="an-pnl-chart"></canvas></div>';
    html += '</div>';
    html += '<div class="an-chart-grid">';
    html += '<div class="an-chart-panel"><h3 class="an-chart-title">Drawdown</h3><canvas id="an-drawdown-chart"></canvas></div>';
    html += '</div>';
  } else {
    html += '<p class="an-empty">Not enough balance snapshots yet. Visit the Balance tab daily to accumulate history.</p>';
  }
  html += '</div>';

  // Exposure section
  html += '<div class="an-section">';
  html += '<h2 class="an-section-title">Sector Exposure</h2>';
  if (hasExposure) {
    html += '<div class="an-exposure-layout">';
    html += '<div class="an-chart-panel an-chart-panel--pie"><canvas id="an-sector-chart"></canvas></div>';
    html += '<div class="an-exposure-table"><table><thead><tr><th>Sector</th><th>Value</th><th>Weight</th><th>Tickers</th></tr></thead><tbody>';
    for (const s of exposure.sectors) {
      html += `<tr><td>${esc(s.name)}</td><td>${_fmtCurrency(s.market_value)}</td><td>${s.pct.toFixed(1)}%</td><td class="an-ticker-list">${s.tickers.map(t => esc(t)).join(', ')}</td></tr>`;
    }
    html += '</tbody></table></div></div>';
  } else {
    html += '<p class="an-empty">Sector data loading… This uses Yahoo Finance for classification and may take a moment on first load.</p>';
  }
  html += '</div>';

  // Concentration section
  html += '<div class="an-section">';
  html += '<h2 class="an-section-title">Concentration</h2>';
  if (hasConcen) {
    html += '<div class="an-kpi-row">';
    html += `<div class="an-kpi"><div class="an-kpi-label">HHI Score</div><div class="an-kpi-value">${concentration.hhi}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Classification</div><div class="an-kpi-value">${esc(concentration.hhi_label)}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Total Holdings</div><div class="an-kpi-value">${concentration.total_positions}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Top 5 Weight</div><div class="an-kpi-value">${(concentration.holdings.slice(0, 5).reduce((s, h) => s + h.pct, 0)).toFixed(1)}%</div></div>`;
    html += '</div>';
    html += '<div class="an-chart-panel"><h3 class="an-chart-title">Top Holdings by Portfolio Weight</h3><canvas id="an-concentration-chart"></canvas></div>';
  } else {
    html += '<p class="an-empty">No equity positions to analyze.</p>';
  }
  html += '</div>';

  // Income summary section
  html += '<div class="an-section">';
  html += '<h2 class="an-section-title">Income Performance</h2>';
  if (hasIncome) {
    const st = income.stats || {};
    html += '<div class="an-kpi-row">';
    html += `<div class="an-kpi"><div class="an-kpi-label">Total Net P&L</div><div class="an-kpi-value ${cls(st.total_pnl)}">${_fmtCurrency(st.total_pnl)}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Win Rate</div><div class="an-kpi-value">${st.win_rate || 0}%</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Avg P&L / Trade</div><div class="an-kpi-value ${cls(st.avg_pnl_per_trade)}">${_fmtCurrency(st.avg_pnl_per_trade)}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Total Premium</div><div class="an-kpi-value">${_fmtCurrency(st.total_premium_collected)}</div></div>`;
    html += '</div>';
    html += '<div class="an-chart-panel"><h3 class="an-chart-title">Monthly P&L</h3><canvas id="an-monthly-pnl-chart"></canvas></div>';

    const strats = income.strategy_breakdown || {};
    if (Object.keys(strats).length) {
      html += '<div class="an-strat-table"><table><thead><tr><th>Strategy</th><th>Trades</th><th>P&L</th><th>Win Rate</th></tr></thead><tbody>';
      for (const [name, d] of Object.entries(strats).sort((a, b) => b[1].pnl - a[1].pnl)) {
        html += `<tr><td>${esc(name)}</td><td>${d.count}</td><td class="${cls(d.pnl)}">${_fmtCurrency(d.pnl)}</td><td>${d.win_rate}%</td></tr>`;
      }
      html += '</tbody></table></div>';
    }
  } else {
    html += '<p class="an-empty">No income trade data yet. Sync income trades from the Income P&L tab.</p>';
  }
  html += '</div>';

  wrap.innerHTML = html;

  // Render charts after DOM is in place
  if (hasPerf) _renderPerformanceCharts(perf);
  if (hasExposure) _renderSectorChart(exposure);
  if (hasConcen) _renderConcentrationChart(concentration);
  if (hasIncome) _renderMonthlyPnlChart(income.monthly_pnl);
}

/* ── Chart renderers ──────────────────────────────────────────────────── */

function _chartDefaults() {
  const c = _chartColors();
  return {
    color: c.text,
    borderColor: c.grid,
    backgroundColor: 'transparent',
    plugins: {
      legend: { display: false },
    },
    scales: {
      x: { ticks: { color: c.text, maxRotation: 45, font: { size: 10 } }, grid: { color: c.grid } },
      y: { ticks: { color: c.text, font: { size: 10 } }, grid: { color: c.grid } },
    },
  };
}

function _renderPerformanceCharts(perf) {
  const c = _chartColors();

  _equityChart = _destroyChart(_equityChart);
  const eqCtx = document.getElementById('an-equity-chart');
  if (eqCtx) {
    _equityChart = new Chart(eqCtx, {
      type: 'line',
      data: {
        labels: perf.dates,
        datasets: [{
          data: perf.equity,
          borderColor: c.accent,
          backgroundColor: c.accent + '20',
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 2,
        }],
      },
      options: {
        ..._chartDefaults(),
        interaction: { intersect: false, mode: 'index' },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => _fmtCurrency(ctx.raw),
            },
          },
        },
        scales: {
          ..._chartDefaults().scales,
          y: { ..._chartDefaults().scales.y, ticks: { ...(_chartDefaults().scales.y.ticks), callback: v => _fmtCurrency(v) } },
        },
      },
    });
  }

  _pnlChart = _destroyChart(_pnlChart);
  const pnlCtx = document.getElementById('an-pnl-chart');
  if (pnlCtx) {
    _pnlChart = new Chart(pnlCtx, {
      type: 'bar',
      data: {
        labels: perf.dates,
        datasets: [{
          data: perf.daily_pnl,
          backgroundColor: perf.daily_pnl.map(v => v >= 0 ? c.pos + 'cc' : c.neg + 'cc'),
          borderRadius: 2,
        }],
      },
      options: {
        ..._chartDefaults(),
        interaction: { intersect: false, mode: 'index' },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: { label: (ctx) => _fmtCurrency(ctx.raw) },
          },
        },
        scales: {
          ..._chartDefaults().scales,
          y: { ..._chartDefaults().scales.y, ticks: { ...(_chartDefaults().scales.y.ticks), callback: v => _fmtCurrency(v) } },
        },
      },
    });
  }

  _drawdownChart = _destroyChart(_drawdownChart);
  const ddCtx = document.getElementById('an-drawdown-chart');
  if (ddCtx) {
    _drawdownChart = new Chart(ddCtx, {
      type: 'line',
      data: {
        labels: perf.dates,
        datasets: [{
          data: perf.drawdown_pct,
          borderColor: c.neg,
          backgroundColor: c.neg + '20',
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 2,
        }],
      },
      options: {
        ..._chartDefaults(),
        interaction: { intersect: false, mode: 'index' },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: { label: (ctx) => ctx.raw.toFixed(2) + '%' },
          },
        },
        scales: {
          ..._chartDefaults().scales,
          y: { ..._chartDefaults().scales.y, ticks: { ...(_chartDefaults().scales.y.ticks), callback: v => v + '%' } },
        },
      },
    });
  }
}

function _renderSectorChart(exposure) {
  _sectorChart = _destroyChart(_sectorChart);
  const ctx = document.getElementById('an-sector-chart');
  if (!ctx) return;
  const c = _chartColors();

  _sectorChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: exposure.sectors.map(s => s.name),
      datasets: [{
        data: exposure.sectors.map(s => s.market_value),
        backgroundColor: exposure.sectors.map((_, i) => SECTOR_PALETTE[i % SECTOR_PALETTE.length]),
        borderColor: c.surface,
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          display: true,
          position: 'right',
          labels: { color: c.text, font: { size: 11 }, boxWidth: 12, padding: 8 },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.raw;
              const pct = exposure.sectors[ctx.dataIndex]?.pct || 0;
              return `${ctx.label}: ${_fmtCurrency(v)} (${pct.toFixed(1)}%)`;
            },
          },
        },
      },
    },
  });
}

function _renderConcentrationChart(concentration) {
  _concentrationChart = _destroyChart(_concentrationChart);
  const ctx = document.getElementById('an-concentration-chart');
  if (!ctx) return;
  const c = _chartColors();
  const top = concentration.holdings.slice(0, 15);

  _concentrationChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: top.map(h => h.symbol),
      datasets: [{
        data: top.map(h => h.pct),
        backgroundColor: c.accent + 'cc',
        borderRadius: 3,
      }],
    },
    options: {
      ..._chartDefaults(),
      indexAxis: 'y',
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const h = top[ctx.dataIndex];
              return `${h.pct.toFixed(1)}% · ${_fmtCurrency(h.market_value)}`;
            },
          },
        },
      },
      scales: {
        x: { ..._chartDefaults().scales.x, ticks: { ...(_chartDefaults().scales.x.ticks), callback: v => v + '%' } },
        y: { ..._chartDefaults().scales.y, ticks: { ...(_chartDefaults().scales.y.ticks), font: { size: 11 } } },
      },
    },
  });
}

function _renderMonthlyPnlChart(monthly) {
  _monthlyPnlChart = _destroyChart(_monthlyPnlChart);
  const ctx = document.getElementById('an-monthly-pnl-chart');
  if (!ctx) return;
  const c = _chartColors();

  _monthlyPnlChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: monthly.months,
      datasets: [{
        data: monthly.values,
        backgroundColor: monthly.values.map(v => v >= 0 ? c.pos + 'cc' : c.neg + 'cc'),
        borderRadius: 3,
      }],
    },
    options: {
      ..._chartDefaults(),
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: (ctx) => _fmtCurrency(ctx.raw) },
        },
      },
      scales: {
        ..._chartDefaults().scales,
        y: { ..._chartDefaults().scales.y, ticks: { ...(_chartDefaults().scales.y.ticks), callback: v => _fmtCurrency(v) } },
      },
    },
  });
}

/* ── Consolidation panel ──────────────────────────────────────────────── */

let _consolLists = [];
let _consolSelectedId = null;

export async function loadConsolidation() {
  const wrap = document.getElementById('consolidation-content');
  if (!wrap) return;
  wrap.innerHTML = '<div class="loading">Loading lists…</div>';

  try {
    const [listsData, consolData] = await Promise.all([
      fetchJson('/api/analytics/consolidation-lists'),
      fetchJson('/api/analytics/consolidation').catch(() => ({ groups: [], underwater: [], total_positions: 0 })),
    ]);
    _consolLists = listsData.lists || [];
    store.consolidationState.loaded = true;
    store.consolidationState.overlapData = consolData;
    if (!_consolSelectedId && _consolLists.length) _consolSelectedId = _consolLists[0].id;
    _renderConsolidation(wrap);
  } catch (e) {
    wrap.innerHTML = '<div class="error">Error: ' + esc(e.message) + '</div>';
  }
}

export function selectConsolidationList(listId) {
  _consolSelectedId = listId;
  const detail = document.getElementById('consolidation-detail');
  if (detail) detail.innerHTML = '';
  _renderConsolidationListTabs();
  _renderConsolidationTickers();
}

function _renderConsolidation(wrap) {
  let html = '';

  // List selector tabs
  html += '<div class="an-consol-tabs" id="consol-list-tabs"></div>';

  // Ticker table for selected list
  html += '<div id="consol-ticker-table"></div>';

  // Detail panel (rendered when user clicks Analyze)
  html += '<div id="consolidation-detail"></div>';

  // Overlap groups (collapsed at bottom)
  html += '<div id="consol-overlap-section"></div>';

  wrap.innerHTML = html;
  _renderConsolidationListTabs();
  _renderConsolidationTickers();
  _renderOverlapGroups();
}

function _renderConsolidationListTabs() {
  const tabWrap = document.getElementById('consol-list-tabs');
  if (!tabWrap) return;

  const posLists = _consolLists.filter(l => l.type === 'position');
  const wlLists = _consolLists.filter(l => l.type === 'watchlist');

  let html = '<div class="an-consol-tab-row">';
  html += '<span class="an-consol-tab-label">Positions</span>';
  for (const l of posLists) {
    const active = l.id === _consolSelectedId ? ' an-consol-tab--active' : '';
    const count = l.tickers.length;
    html += `<button class="an-consol-tab${active}" onclick="selectConsolidationList('${esc(l.id)}')">${esc(l.name)} <span class="an-consol-tab-count">${count}</span></button>`;
  }
  if (wlLists.length) {
    html += '<span class="an-consol-tab-sep"></span>';
    html += '<span class="an-consol-tab-label">Watchlists</span>';
    for (const l of wlLists) {
      const active = l.id === _consolSelectedId ? ' an-consol-tab--active' : '';
      const count = l.tickers.length;
      html += `<button class="an-consol-tab${active}" onclick="selectConsolidationList('${esc(l.id)}')">${esc(l.name)} <span class="an-consol-tab-count">${count}</span></button>`;
    }
  }
  html += '</div>';
  tabWrap.innerHTML = html;
}

function _renderConsolidationTickers() {
  const tableWrap = document.getElementById('consol-ticker-table');
  if (!tableWrap) return;

  const selected = _consolLists.find(l => l.id === _consolSelectedId);
  if (!selected || !selected.tickers.length) {
    tableWrap.innerHTML = '<div class="an-section"><p class="an-empty">No tickers in this list.</p></div>';
    return;
  }

  const tickers = selected.tickers;

  let html = '<div class="an-section">';
  html += `<h2 class="an-section-title">${esc(selected.name)}</h2>`;
  html += '<p class="an-section-desc">Click Analyze on any ticker for detailed consolidation analysis including fundamentals, peer comparison, ETF alternatives, and options strategies.</p>';
  html += '<div class="an-underwater-table"><table><thead><tr>';
  html += '<th>Symbol</th><th>Qty</th><th>Avg Cost</th><th>Current</th><th>Mkt Value</th><th>Unrealized P&L</th><th></th>';
  html += '</tr></thead><tbody>';
  for (const t of tickers) {
    const hasPos = t.avg_price != null && t.current_price != null;
    const plPct = hasPos ? ((t.current_price - Math.abs(t.avg_price)) / Math.abs(t.avg_price) * 100) : null;
    const plClass = t.unrealized_pl != null ? cls(t.unrealized_pl) : '';
    html += '<tr>';
    html += `<td><strong>${esc(t.symbol)}</strong></td>`;
    html += `<td>${t.quantity != null ? fmt(t.quantity, 0) : ''}</td>`;
    html += `<td>${t.avg_price != null ? '$' + fmt(Math.abs(t.avg_price)) : ''}</td>`;
    html += `<td>${t.current_price != null ? '$' + fmt(t.current_price) : ''}</td>`;
    html += `<td>${t.market_value ? _fmtCurrency(t.market_value) : ''}</td>`;
    html += `<td class="${plClass}">${t.unrealized_pl != null ? '$' + fmt(t.unrealized_pl) + (plPct != null ? ` (${plPct.toFixed(1)}%)` : '') : ''}</td>`;
    html += `<td><button class="an-detail-btn" onclick="loadConsolidationDetail('${esc(t.symbol)}')">Analyze</button></td>`;
    html += '</tr>';
  }
  html += '</tbody></table></div></div>';
  tableWrap.innerHTML = html;
}

function _renderOverlapGroups() {
  const wrap = document.getElementById('consol-overlap-section');
  if (!wrap) return;

  const consolData = store.consolidationState.overlapData || {};
  const groups = consolData.groups || [];

  if (!groups.length) {
    wrap.innerHTML = '';
    return;
  }

  let html = '<details class="an-section an-overlap-collapsible">';
  html += '<summary class="an-section-title an-overlap-summary">Sector Overlap Groups <span class="an-overlap-summary-count">' + groups.length + ' groups</span></summary>';
  html += '<p class="an-section-desc">Holdings grouped by sector/industry. Groups with 2+ stocks represent potential consolidation opportunities.</p>';
  for (const g of groups) {
    const scored = g.scored || g.tickers;
    html += '<div class="an-overlap-card">';
    html += '<div class="an-overlap-header">';
    html += `<span class="an-overlap-group">${esc(g.group)}</span>`;
    html += `<span class="an-overlap-count">${g.count} holdings · ${_fmtCurrency(g.total_value)}</span>`;
    if (g.etf_alternatives && g.etf_alternatives.length) {
      html += `<span class="an-overlap-etfs">ETFs: ${g.etf_alternatives.map(e => esc(e)).join(', ')}</span>`;
    }
    html += '</div>';
    html += '<table class="an-overlap-table"><thead><tr>';
    html += '<th>Symbol</th><th>Score</th><th>Rec.</th><th>Mkt Value</th><th>P&L</th><th>Revenue Growth</th><th>Margin</th><th>From 52w High</th><th></th>';
    html += '</tr></thead><tbody>';
    for (const t of scored) {
      const rec = t.recommendation || '—';
      const recClass = rec === 'keep' ? 'an-rec-keep' : (rec === 'consolidate' ? 'an-rec-consolidate' : '');
      const f = t.fundamentals || {};
      html += `<tr class="${recClass}">`;
      html += `<td><strong>${esc(t.symbol)}</strong></td>`;
      html += `<td>${t.score != null ? t.score.toFixed(1) : '—'}</td>`;
      html += `<td class="${recClass}">${esc(rec)}</td>`;
      html += `<td>${_fmtCurrency(t.market_value)}</td>`;
      html += `<td class="${cls(t.unrealized_pl)}">${t.unrealized_pl != null ? '$' + fmt(t.unrealized_pl) : '—'}</td>`;
      html += `<td>${f.revenue_growth != null ? (f.revenue_growth * 100).toFixed(1) + '%' : '—'}</td>`;
      html += `<td>${f.profit_margin != null ? (f.profit_margin * 100).toFixed(1) + '%' : '—'}</td>`;
      html += `<td>${f.pct_from_52w_high != null ? f.pct_from_52w_high.toFixed(1) + '%' : '—'}</td>`;
      html += `<td><button class="an-detail-btn" onclick="loadConsolidationDetail('${esc(t.symbol)}')">Analyze</button></td>`;
      html += '</tr>';
    }
    html += '</tbody></table></div>';
  }
  html += '</details>';
  wrap.innerHTML = html;
}

/* ── Consolidation detail for a single ticker ─────────────────────────── */

export async function loadConsolidationDetail(symbol) {
  const wrap = document.getElementById('consolidation-detail');
  if (!wrap) return;
  wrap.innerHTML = '<div class="loading">Loading detailed analysis for ' + esc(symbol) + '…</div>';
  wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });

  try {
    const data = await fetchJson('/api/analytics/consolidation/' + encodeURIComponent(symbol));
    _renderConsolidationDetail(wrap, data);
  } catch (e) {
    wrap.innerHTML = '<div class="error">Error: ' + esc(e.message) + '</div>';
  }
}

function _renderConsolidationDetail(wrap, data) {
  const info = data.info || {};
  const pos = data.position;
  const peers = data.scored_peers || [];
  const etfs = data.etf_alternatives || [];
  const swaps = data.tax_loss_swaps || {};

  let html = '<div class="an-detail-panel">';
  html += `<h2 class="an-detail-title">${esc(data.symbol)} — Consolidation Analysis</h2>`;

  // Position summary
  if (pos) {
    const plPct = pos.avg_price ? ((pos.current_price - Math.abs(pos.avg_price)) / Math.abs(pos.avg_price) * 100) : null;
    html += '<div class="an-kpi-row">';
    html += `<div class="an-kpi"><div class="an-kpi-label">Quantity</div><div class="an-kpi-value">${fmt(pos.quantity, 0)}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Avg Cost</div><div class="an-kpi-value">$${fmt(Math.abs(pos.avg_price || 0))}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Current Price</div><div class="an-kpi-value">$${fmt(pos.current_price)}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Market Value</div><div class="an-kpi-value">${_fmtCurrency(pos.market_value)}</div></div>`;
    html += `<div class="an-kpi"><div class="an-kpi-label">Unrealized P&L</div><div class="an-kpi-value ${cls(pos.unrealized_pl)}">${_fmtCurrency(pos.unrealized_pl)}${plPct != null ? ` (${plPct.toFixed(1)}%)` : ''}</div></div>`;
    html += '</div>';
  }

  // Fundamentals
  html += '<div class="an-detail-section">';
  html += '<h3>Fundamentals</h3>';
  html += '<div class="an-fundamentals-grid">';
  html += `<div><span class="an-fun-label">Sector</span><span class="an-fun-value">${esc(info.sector || '—')}</span></div>`;
  html += `<div><span class="an-fun-label">Industry</span><span class="an-fun-value">${esc(info.industry || '—')}</span></div>`;
  html += `<div><span class="an-fun-label">Market Cap</span><span class="an-fun-value">${_fmtMktCap(info.marketCap)}</span></div>`;
  html += `<div><span class="an-fun-label">P/E (trailing)</span><span class="an-fun-value">${info.trailingPE != null ? info.trailingPE.toFixed(1) : '—'}</span></div>`;
  html += `<div><span class="an-fun-label">P/E (forward)</span><span class="an-fun-value">${info.forwardPE != null ? info.forwardPE.toFixed(1) : '—'}</span></div>`;
  html += `<div><span class="an-fun-label">Revenue Growth</span><span class="an-fun-value">${info.revenueGrowth != null ? (info.revenueGrowth * 100).toFixed(1) + '%' : '—'}</span></div>`;
  html += `<div><span class="an-fun-label">Profit Margin</span><span class="an-fun-value">${info.profitMargins != null ? (info.profitMargins * 100).toFixed(1) + '%' : '—'}</span></div>`;
  html += `<div><span class="an-fun-label">ROE</span><span class="an-fun-value">${info.returnOnEquity != null ? (info.returnOnEquity * 100).toFixed(1) + '%' : '—'}</span></div>`;
  html += `<div><span class="an-fun-label">Beta</span><span class="an-fun-value">${info.beta != null ? info.beta.toFixed(2) : '—'}</span></div>`;
  html += `<div><span class="an-fun-label">Div Yield</span><span class="an-fun-value">${info.dividendYield != null ? (info.dividendYield * 100).toFixed(2) + '%' : '—'}</span></div>`;
  html += `<div><span class="an-fun-label">From 52w High</span><span class="an-fun-value ${cls(info.pct_from_52w_high)}">${info.pct_from_52w_high != null ? info.pct_from_52w_high.toFixed(1) + '%' : '—'}</span></div>`;
  html += '</div></div>';

  // Peer comparison table
  if (peers.length > 1) {
    html += '<div class="an-detail-section">';
    html += '<h3>Peer Comparison</h3>';
    html += '<table class="an-peer-table"><thead><tr>';
    html += '<th>Symbol</th><th>Score</th><th>Rec.</th><th>Mkt Cap</th><th>P/E</th><th>Rev Growth</th><th>Margin</th><th>ROE</th><th>From 52w High</th><th>Position P&L</th>';
    html += '</tr></thead><tbody>';
    for (const p of peers) {
      const f = p.fundamentals || {};
      const recClass = p.recommendation === 'keep' ? 'an-rec-keep' : (p.recommendation === 'consolidate' ? 'an-rec-consolidate' : '');
      html += `<tr class="${recClass}">`;
      html += `<td><strong>${esc(p.symbol)}</strong>${p.symbol === data.symbol ? ' ★' : ''}</td>`;
      html += `<td>${p.score != null ? p.score.toFixed(1) : '—'}</td>`;
      html += `<td class="${recClass}">${esc(p.recommendation || '—')}</td>`;
      html += `<td>${_fmtMktCap(f.market_cap)}</td>`;
      html += `<td>${f.pe_ratio != null ? f.pe_ratio.toFixed(1) : '—'}</td>`;
      html += `<td>${f.revenue_growth != null ? (f.revenue_growth * 100).toFixed(1) + '%' : '—'}</td>`;
      html += `<td>${f.profit_margin != null ? (f.profit_margin * 100).toFixed(1) + '%' : '—'}</td>`;
      html += `<td>${f.roe != null ? (f.roe * 100).toFixed(1) + '%' : '—'}</td>`;
      html += `<td>${f.pct_from_52w_high != null ? f.pct_from_52w_high.toFixed(1) + '%' : '—'}</td>`;
      html += `<td class="${cls(p.unrealized_pl)}">${p.unrealized_pl != null ? '$' + fmt(p.unrealized_pl) : (p.in_portfolio === false ? 'not held' : '—')}</td>`;
      html += '</tr>';
    }
    html += '</tbody></table></div>';
  }

  // ETF alternatives
  if (etfs.length) {
    html += '<div class="an-detail-section">';
    html += '<h3>ETF Alternatives</h3>';
    html += '<p class="an-section-desc">Sell individual stock and buy a sector ETF to maintain exposure while consolidating.</p>';
    html += '<div class="an-etf-cards">';
    for (const e of etfs) {
      const ei = e.info || {};
      html += '<div class="an-etf-card">';
      html += `<div class="an-etf-symbol">${esc(e.symbol)}</div>`;
      html += `<div class="an-etf-name">${esc(ei.shortName || ei.longName || '')}</div>`;
      if (ei.currentPrice) html += `<div>Price: $${fmt(ei.currentPrice)}</div>`;
      if (ei.dividendYield != null) html += `<div>Yield: ${(ei.dividendYield * 100).toFixed(2)}%</div>`;
      html += '</div>';
    }
    html += '</div></div>';
  }

  // Tax loss swap suggestions
  if (swaps.swaps && swaps.swaps.length) {
    html += '<div class="an-detail-section">';
    html += '<h3>Tax-Loss Harvest Candidates</h3>';
    if (swaps.wash_sale_note) {
      html += `<div class="an-wash-sale-warning">${esc(swaps.wash_sale_note)}</div>`;
    }
    html += '<div class="an-swap-list">';
    for (const s of swaps.swaps) {
      html += `<div class="an-swap-item">`;
      html += `<strong>${esc(s.symbol)}</strong>`;
      html += `<span class="an-swap-match">${esc(s.match)}</span>`;
      html += `<span>${esc(s.name || '')}</span>`;
      if (s.market_cap) html += `<span>${_fmtMktCap(s.market_cap)}</span>`;
      html += '</div>';
    }
    html += '</div></div>';
  }

  // Underwater strategies section (loads async)
  html += '<div id="consolidation-uw-strategies"></div>';

  html += '</div>';
  wrap.innerHTML = html;

  // If position is underwater, auto-load strategies
  if (data.position && data.position.unrealized_pl < 0) {
    _loadUnderwaterStrategies(data.symbol);
  }
}

async function _loadUnderwaterStrategies(symbol) {
  const wrap = document.getElementById('consolidation-uw-strategies');
  if (!wrap) return;
  wrap.innerHTML = '<div class="loading">Loading options strategies for ' + esc(symbol) + '…</div>';

  try {
    const data = await fetchJson('/api/analytics/underwater-strategies/' + encodeURIComponent(symbol));
    const strats = data.strategies || [];
    if (!strats.length) {
      wrap.innerHTML = '<div class="an-detail-section"><h3>Options Strategies</h3><p class="an-empty">No options strategies available (position may have fewer than 100 shares, or no suitable option chain found).</p></div>';
      return;
    }

    let html = '<div class="an-detail-section"><h3>Options Strategies for Underwater Position</h3>';
    html += '<p class="an-section-desc">Strategies to generate income or manage risk while holding this underwater position.</p>';
    html += '<div class="an-strat-cards">';

    for (const s of strats) {
      const stratClass = s.strategy === 'sell_and_harvest' ? 'an-strat-card--harvest' :
                         s.strategy === 'etf_swap' ? 'an-strat-card--swap' : 'an-strat-card--cc';
      html += `<div class="an-strat-card ${stratClass}">`;
      html += `<div class="an-strat-card-title">${esc(s.title)}</div>`;
      html += `<div class="an-strat-card-desc">${esc(s.description)}</div>`;
      html += `<div class="an-strat-card-detail">${esc(s.detail)}</div>`;
      if (s.annualized_yield != null) {
        html += `<div class="an-strat-card-metric">Ann. yield: <strong>${(s.annualized_yield * 100).toFixed(0)}%</strong></div>`;
      }
      if (s.months_to_recover != null && s.months_to_recover < 100) {
        html += `<div class="an-strat-card-metric">Recovery cycles: <strong>~${s.months_to_recover.toFixed(0)}</strong></div>`;
      }
      if (s.tax_loss != null) {
        html += `<div class="an-strat-card-metric">Tax loss: <strong>${_fmtCurrency(s.tax_loss)}</strong></div>`;
      }
      html += '</div>';
    }

    html += '</div></div>';
    wrap.innerHTML = html;
  } catch (e) {
    wrap.innerHTML = '<div class="error">Error loading strategies: ' + esc(e.message) + '</div>';
  }
}
