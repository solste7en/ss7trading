import { fmt, fmtD, cls, esc, fetchJson } from './utils.js';
import { store as S } from './state.js';

export function _ipFormatStrike(s) {
  if (s == null || s === '') return '0';
  const n = Number(s);
  if (Number.isNaN(n)) return String(s);
  return n.toFixed(2).replace(/\.?0+$/, '');
}

/** Human-readable leg close state (API uses title case / long labels). */
export function _ipLegCloseLabel(closeAction) {
  if (closeAction == null || closeAction === '') return 'open';
  if (closeAction === 'Expired') return 'expired';
  if (closeAction === 'Exchange or Exercise') return 'exercised';
  return closeAction;
}

/** Short (sold) leg strike for score denominator; else first leg. */
export function _incomeScoreStrike(legs) {
  if (!legs || !legs.length) return null;
  const shortLeg = legs.find(l => l.direction === 'short');
  const pick = shortLeg || legs[0];
  const k = pick != null && pick.strike != null ? Number(pick.strike) : NaN;
  return Number.isFinite(k) && k > 0 ? k : null;
}

/**
 * Closed-trade efficiency: net_pnl / sqrt(days_held + 1) * 100 / strike.
 * sqrt normalizes time sublinearly (Sharpe-like) so short holds aren't
 * disproportionately inflated vs longer trades.  Open trades: null (show —).
 */
export function _incomeEfficiencyScore(t) {
  if (!t || t.status === 'open') return null;
  if (t.net_pnl == null) return null;
  const strike = _incomeScoreStrike(t.legs || []);
  if (strike == null) return null;
  const d = t.days_held != null && t.days_held >= 0 ? t.days_held : 0;
  return (t.net_pnl / Math.sqrt(d + 1)) * (100 / strike);
}

export function setIncomePnlSort(key) {
  if (S.incomePnlSort.key === key) {
    S.incomePnlSort.dir = S.incomePnlSort.dir === 'desc' ? 'asc' : 'desc';
  } else {
    S.incomePnlSort.key = key;
    S.incomePnlSort.dir = 'desc';
  }
  _updateIpSortArrows();
  loadIncomeTrades(true);
}

export function _updateIpSortArrows() {
  const keys = ['open_date', 'close_date', 'recovery', 'recovery_pnl', 'net_pnl', 'days_held', 'net_premium', 'score'];
  for (const k of keys) {
    const el = document.getElementById('ip-sa-' + k);
    if (!el) continue;
    el.textContent = S.incomePnlSort.key === k ? (S.incomePnlSort.dir === 'desc' ? '▼' : '▲') : '';
  }
}

export async function _fetchRecovery(ticker) {
  if (S._ipRecoveryCache[ticker]) return S._ipRecoveryCache[ticker];
  try {
    const response = await fetch('/api/income/recovery?ticker=' + encodeURIComponent(ticker));
    if (!response.ok) {
      console.error(`Recovery fetch for ${ticker} failed: HTTP ${response.status}`);
      return null;
    }
    const res = await response.json();
    if (res.error) {
      console.error(`Recovery fetch for ${ticker} returned error:`, res.error);
      return null;
    }
    S._ipRecoveryCache[ticker] = res;
    return res;
  } catch (e) { console.error('Recovery fetch error:', e); return null; }
}

export function _getRecoveryForTrade(ticker, tradeId) {
  const cached = S._ipRecoveryCache[ticker];
  if (!cached) return null;
  return (cached.assignments || []).find(a => a.trade_id === tradeId) || null;
}

function _ipAppendDateRangeParams(params) {
  const rangeEl = document.getElementById('ip-date-range');
  const range = (rangeEl && rangeEl.value) || 'all';
  params.set('range', range);
  if (range === 'custom') {
    const df = document.getElementById('ip-custom-from')?.value;
    const dte = document.getElementById('ip-custom-to')?.value;
    if (df) params.set('date_from', df);
    if (dte) params.set('date_to', dte);
  }
}

export function onIpDateRangeChange() {
  const range = document.getElementById('ip-date-range')?.value || 'all';
  const wrap = document.getElementById('ip-custom-date-wrap');
  if (wrap) wrap.style.display = range === 'custom' ? 'inline-flex' : 'none';
  loadIncomeStats();
  loadIncomeTrades();
}

export function onIpCustomDateChange() {
  if (document.getElementById('ip-date-range')?.value !== 'custom') return;
  loadIncomeStats();
  loadIncomeTrades();
}

export function _ipOnStatusChange() {
  S._ipCardFilter = null;
  document.querySelectorAll('.ip-kpi-clickable').forEach(e => e.classList.remove('active'));
  const st = document.getElementById('ip-status').value;
  if (st === 'assigned') {
    S.incomePnlSort = { key: 'close_date', dir: 'desc' };
  }
  loadIncomeStats();
  loadIncomeTrades();
}

export function setIpCardFilter(filter) {
  if (S._ipCardFilter === filter) {
    S._ipCardFilter = null;   // toggle off
  } else {
    S._ipCardFilter = filter;
    if (filter === 'assigned') {
      S.incomePnlSort = { key: 'close_date', dir: 'desc' };
    }
  }
  // Update card active states
  ['win', 'perfect', 'closed', 'open', 'assigned'].forEach(f => {
    const el = document.getElementById('ip-kpi-card-' + f);
    if (el) el.classList.toggle('active', S._ipCardFilter === f);
  });
  loadIncomeStats();
  loadIncomeTrades(true);
}

export async function loadIncomeStats() {
  const ticker = document.getElementById('ip-ticker').value.trim().toUpperCase();
  const status = document.getElementById('ip-status').value;
  const strategy = document.getElementById('ip-strategy').value;
  try {
    const params = new URLSearchParams();
    if (ticker) params.set('ticker', ticker);
    if (status) params.set('status', status);
    if (strategy) params.set('strategy', strategy);
    if (S._ipCardFilter) params.set('outcome', S._ipCardFilter);
    _ipAppendDateRangeParams(params);
    const data = await fetch('/api/income/stats?' + params).then(r => r.json());
    if (data.error) throw new Error(data.error);

    const pnl = data.total_pnl || 0;
    const pnlEl = document.getElementById('ip-kpi-pnl');
    pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
    pnlEl.className = 'ip-kpi-value ' + (pnl >= 0 ? 'pos' : 'neg');

    const rpnl = data.total_true_recovery_pnl != null ? data.total_true_recovery_pnl : 0;
    const rEl = document.getElementById('ip-kpi-recovery-pnl');
    rEl.textContent = (rpnl >= 0 ? '+' : '') + '$' + rpnl.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
    rEl.className = 'ip-kpi-value ' + (rpnl >= 0 ? 'pos' : 'neg');

    const sumPnl = pnl + rpnl;
    const sumEl = document.getElementById('ip-kpi-sum-pnl');
    sumEl.textContent = (sumPnl >= 0 ? '+' : '') + '$' + sumPnl.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
    sumEl.className = 'ip-kpi-value ' + (sumPnl >= 0 ? 'pos' : 'neg');

    document.getElementById('ip-kpi-winrate').textContent = (data.win_rate || 0) + '%';
    document.getElementById('ip-kpi-perfect').textContent = (data.perfect_win_rate || 0) + '%';
    document.getElementById('ip-kpi-closed').textContent  = data.closed_trades || 0;
    document.getElementById('ip-kpi-open').textContent    = data.open_trades || 0;
    document.getElementById('ip-kpi-assigned').textContent = data.assigned_count || 0;

    if (data.last_synced) {
      const dt = new Date(data.last_synced + 'Z');
      document.getElementById('ip-last-sync').textContent = 'Last sync: ' + dt.toLocaleString();
    } else {
      document.getElementById('ip-last-sync').textContent = 'Not synced yet';
    }

    await loadIncomeTimeseries();
  } catch (e) {
    console.error('Income stats error:', e);
  }
}

export async function loadIncomeTrades(resetPage = true) {
  if (resetPage) S.incomePnlState.page = 1;
  const ticker   = document.getElementById('ip-ticker').value.trim().toUpperCase();
  const status   = document.getElementById('ip-status').value;
  const strategy = document.getElementById('ip-strategy').value;
  const limit    = parseInt(document.getElementById('ip-limit').value) || 25;

  document.getElementById('ip-loading').style.display = '';
  document.getElementById('ip-table').style.display = 'none';
  document.getElementById('ip-error').style.display = 'none';

  try {
    const params = new URLSearchParams({
      page: S.incomePnlState.page, limit, ticker, status, strategy,
      sort_by: S.incomePnlSort.key, sort_dir: S.incomePnlSort.dir
    });
    if (S._ipCardFilter) params.set('outcome', S._ipCardFilter);
    _ipAppendDateRangeParams(params);
    const res = await fetch('/api/income/trades?' + params).then(r => r.json());
    if (res.error) throw new Error(res.error);

    S.incomePnlState.loaded = true;
    document.getElementById('ip-loading').style.display = 'none';
    document.getElementById('ip-table').style.display = '';
    document.getElementById('ip-count').textContent = res.total + ' trades';

    const tickersNeedRec = [...new Set((res.data || []).filter(t => t.status === 'assigned').map(t => t.underlying))];
    // Invalidate cached recovery data for these tickers so each reload picks up
    // fresh backend state (important after CLI/background syncs that the UI
    // doesn't know about).
    tickersNeedRec.forEach(tk => { delete S._ipRecoveryCache[tk]; });
    await Promise.all(tickersNeedRec.map(u => _fetchRecovery(u)));

    S._ipLastTrades = res.data || [];
    _renderIncomeTrades(S._ipLastTrades);
    _updateIpSortArrows();
    window.renderPagination('ip-pagination', res, loadIncomeTrades, S.incomePnlState);
    if (_recDashOpen) loadRecoverySummary();
  } catch (e) {
    document.getElementById('ip-loading').style.display = 'none';
    document.getElementById('ip-error').textContent = 'Error: ' + e.message;
    document.getElementById('ip-error').style.display = '';
  }
}

export function _renderIncomeTrades(trades) {
  const tbody = document.getElementById('ip-tbody');
  let html = '';

  for (const t of trades) {
    const legs = t.legs || [];
    const hasLegs = legs.length > 1 || legs.length === 1;
    const isExpanded = S._ipExpanded.has(t.id);

    const legsSummary = legs.map(l => {
      const dir = l.direction === 'short' ? 'STO' : 'BTO';
      return dir + ' $' + _ipFormatStrike(l.strike) + (l.leg_type === 'PUT' ? 'P' : 'C');
    }).join(' / ');

    const stratBadge = _ipStratBadge(t.strategy);
    const statusBadge = _ipStatusBadge(t);
    const outcomeBadge = _ipOutcomeBadge(t);

    const pnl = t.net_pnl != null ? t.net_pnl : null;
    const pnlClass = pnl != null ? (pnl >= 0 ? 'pos' : 'neg') : '';
    const pnlStr = pnl != null ? (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) : '—';
    const pnlPctStr = t.net_pnl_pct != null ? (t.net_pnl_pct >= 0 ? '+' : '') + t.net_pnl_pct.toFixed(1) + '%' : '—';
    const premStr = t.net_premium != null ? '$' + t.net_premium.toFixed(2) : '—';
    const closeStr = t.close_cost != null && t.status !== 'open' ? '$' + t.close_cost.toFixed(2) : '—';
    const arrow = hasLegs ? (isExpanded ? '▼' : '▶') : '';

    const effScore = _incomeEfficiencyScore(t);
    const scoreClass = effScore != null ? (effScore >= 0 ? 'pos' : 'neg') : '';
    const scoreStr = effScore != null ? effScore.toFixed(4) : '—';

    let recCell = '—';
    let recPnlCell = '—';
    if (t.status === 'assigned' && t.recovery_target != null && t.recovery_target > 0) {
      recCell = (t.recovery_recovered != null ? t.recovery_recovered : 0) + '/' + t.recovery_target;
      const trp = t.true_recovery_pnl;
      if (trp != null) {
        const rc = trp >= 0 ? 'pos' : 'neg';
        recPnlCell = `<span class="${rc}">` + (trp >= 0 ? '+' : '') + '$' + trp.toFixed(2) + '</span>';
      }
    }

    html += `<tr class="ip-trade-row" onclick="toggleIncomeTrade(${t.id},'${esc(t.underlying)}','${esc(t.status)}')" style="cursor:pointer">
      <td class="ip-toggle-arrow">${arrow}</td>
      <td><b class="sym-link" onclick="event.stopPropagation(); openTrade('${esc(t.underlying)}')">${esc(t.underlying)}</b></td>
      <td>${stratBadge}</td>
      <td class="ip-legs-cell">${esc(legsSummary)}</td>
      <td class="ip-recovery-cell">${t.status === 'assigned' ? recCell : '—'}</td>
      <td class="ip-recovery-pnl-cell">${t.status === 'assigned' ? recPnlCell : '—'}</td>
      <td>${t.open_date || '—'}</td>
      <td>${t.close_date || '—'}</td>
      <td>${t.days_held != null ? t.days_held : '—'}</td>
      <td>${premStr}</td>
      <td>${closeStr}</td>
      <td class="${pnlClass}">${pnlStr}</td>
      <td class="${pnlClass}">${pnlPctStr}</td>
      <td>${statusBadge}</td>
      <td class="${scoreClass}" title="net P&amp;L ÷ √(days+1) × 100 ÷ short strike">${scoreStr}</td>
      <td>${outcomeBadge}</td>
    </tr>`;

    if (isExpanded && legs.length) {
      for (const l of legs) {
        const lDir = l.direction === 'short' ? 'Short' : 'Long';
        const lPnl = l.leg_pnl != null ? (l.leg_pnl >= 0 ? '+' : '') + '$' + l.leg_pnl.toFixed(2) : '—';
        const lPnlClass = l.leg_pnl != null ? (l.leg_pnl >= 0 ? 'pos' : 'neg') : '';
        html += `<tr class="ip-leg-row">
          <td></td>
          <td colspan="2" style="padding-left:24px;color:#94a3b8;font-size:11px">
            ${lDir} ${l.leg_type} $${_ipFormatStrike(l.strike)} exp ${l.expiry || '?'}
          </td>
          <td style="font-size:11px;color:#94a3b8">${esc(l.open_action||'')} → ${esc(_ipLegCloseLabel(l.close_action))}</td>
          <td></td><td></td>
          <td style="font-size:11px">${l.open_date||'—'}</td>
          <td style="font-size:11px">${l.close_date||'—'}</td>
          <td></td>
          <td style="font-size:11px">$${(l.open_price||0).toFixed(2)} × ${l.open_qty||0}</td>
          <td style="font-size:11px">${l.close_price != null ? '$'+l.close_price.toFixed(2) : '—'}</td>
          <td class="${lPnlClass}" style="font-size:11px">${lPnl}</td>
          <td colspan="4"></td>
        </tr>`;
      }
      // Recovery section for assigned trades (skipped for fully-exercised
      // spreads — both legs exercised/assigned nets out on the stock side).
      if (t.status === 'assigned' && t.is_fully_exercised) {
        html += `<tr class="ip-recovery-header"><td></td>
          <td colspan="15" style="padding:8px 24px;color:#64748b;font-size:11px;font-style:italic">
            Both legs exercised at expiry — stock position netted out, no recovery needed.</td></tr>`;
      } else if (t.status === 'assigned') {
        const rec = _getRecoveryForTrade(t.underlying, t.id);
        if (rec) {
          html += _renderRecoverySection(rec, t.underlying);
        } else {
          html += `<tr class="ip-recovery-header"><td></td>
            <td colspan="15" style="padding:8px 24px;color:#64748b;font-size:11px;font-style:italic">
              Loading recovery data…</td></tr>`;
        }
      }
    }
  }
  tbody.innerHTML = html;
}

export async function toggleIncomeTrade(id, ticker, status) {
  if (S._ipExpanded.has(id)) {
    S._ipExpanded.delete(id);
  } else {
    S._ipExpanded.add(id);
  }
  // Re-render from the cached trades — no need to refetch /api/income/trades
  // (the data didn't change, only the expanded-row state did).
  _renderIncomeTrades(S._ipLastTrades);

  // For assigned trades being expanded for the first time, kick off the
  // recovery fetch in the background so the recovery section populates.
  if (S._ipExpanded.has(id) && status === 'assigned' && !S._ipRecoveryCache[ticker]) {
    _fetchRecovery(ticker).then(() => _renderIncomeTrades(S._ipLastTrades));
  }
}

export async function dismissRecovery(tradeId, ticker, remainingQty) {
  if (!confirm('Write off the remaining ' + remainingQty + ' shares? This marks the recovery as complete.')) return;
  try {
    const res = await fetch('/api/income/recovery/' + tradeId + '/dismiss', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({qty: remainingQty})
    }).then(r => r.json());
    if (res.error) { alert('Error: ' + res.error); return; }
    delete S._ipRecoveryCache[ticker];
    await _fetchRecovery(ticker);
    loadIncomeStats();
    loadIncomeTrades(false);
  } catch (e) { alert('Error: ' + e.message); }
}

export function _ipStratBadge(strategy) {
  const map = {
    naked_put: ['Naked PUT', 'ip-badge-naked'],
    naked_call: ['Naked CALL', 'ip-badge-naked'],
    put_spread: ['Put Spread', 'ip-badge-spread'],
    call_spread: ['Call Spread', 'ip-badge-spread'],
    collar: ['Collar', 'ip-badge-collar'],
    other: ['Other', 'ip-badge-other'],
  };
  const [label, cls] = map[strategy] || [strategy, 'ip-badge-other'];
  return `<span class="ip-badge ${cls}">${label}</span>`;
}

export function _ipStatusBadge(trade) {
  const status = typeof trade === 'string' ? trade : (trade.status || '');
  const map = {
    open: 'ip-status-open',
    closed: 'ip-status-closed',
    expired: 'ip-status-expired',
    assigned: 'ip-status-assigned',
  };
  let html = `<span class="ip-badge ${map[status] || ''}">${status}</span>`;
  if (status === 'assigned' && trade && trade.is_early_assignment) {
    html += `<span class="ip-badge ip-status-early">early</span>`;
  }
  if (status === 'assigned' && trade && trade.is_fully_exercised) {
    html += `<span class="ip-badge ip-status-exercised" title="Both legs exercised at expiry — stock position netted out, no recovery needed">exercised</span>`;
  }
  return html;
}

export function _ipOutcomeBadge(t) {
  if (t.status === 'open') return '<span class="ip-badge ip-outcome-open">—</span>';
  if (t.is_perfect_win) return '<span class="ip-badge ip-outcome-perfect">Perfect</span>';
  if (t.is_win) return '<span class="ip-badge ip-outcome-win">Win</span>';
  return '<span class="ip-badge ip-outcome-loss">Loss</span>';
}

export function _renderRecoverySection(rec, ticker) {
  const total = rec.assigned_qty;
  const recovered = rec.recovered_qty || 0;
  const dismissed = rec.dismissed_qty || 0;
  const effectiveTarget = total - dismissed;
  const pct = effectiveTarget > 0 ? Math.min(100, Math.round(100 * recovered / effectiveTarget)) : 100;
  const remaining = rec.remaining_qty || 0;
  const pnl = rec.recovery_pnl || 0;
  const pnlClass = pnl >= 0 ? 'pos' : 'neg';
  const pnlStr = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
  const truePnl = rec.true_recovery_pnl || 0;
  const truePnlClass = truePnl >= 0 ? 'pos' : 'neg';
  const truePnlStr = (truePnl >= 0 ? '+' : '') + '$' + truePnl.toFixed(2);
  const isComplete = rec.is_complete;
  const trades = rec.recovery_trades || [];

  let html = '';

  // Header row with progress bar
  const statusLabel = isComplete
    ? '<span class="ip-badge ip-recovery-done">Recovered</span>'
    : `<span class="ip-recovery-remain">${remaining} remaining</span>`;
  const dismissedLabel = dismissed > 0 ? ` <span class="ip-recovery-dismissed">(${dismissed} written off)</span>` : '';

  html += `<tr class="ip-recovery-header"><td></td>
    <td colspan="15" style="padding:6px 24px">
      <div class="ip-recovery-summary">
        <span class="ip-recovery-title">Recovery</span>
        <span class="ip-recovery-progress-text">${recovered} / ${effectiveTarget} shares (${pct}%)</span>
        ${dismissedLabel}
        <span class="ip-recovery-pnl ${pnlClass}">${pnlStr}</span>
        <span style="color:#64748b;font-size:11px">/</span>
        <span class="ip-recovery-pnl ${truePnlClass}" title="True P&L vs assignment price">${truePnlStr}</span>
        ${statusLabel}
        ${!isComplete && remaining > 0 ? `<button class="ip-recovery-closeout" onclick="event.stopPropagation(); dismissRecovery(${rec.trade_id},'${esc(ticker)}',${remaining + dismissed})">Close Out</button>` : ''}
      </div>
      <div class="ip-recovery-bar-bg"><div class="ip-recovery-bar-fill" style="width:${pct}%"></div></div>
    </td></tr>`;

  // Individual recovery trades
  for (const rt of trades) {
    const pps = rt.pnl_per_share != null ? rt.pnl_per_share : 0;
    const perShClass = pps >= 0 ? 'pos' : 'neg';
    const perShStr = (pps >= 0 ? '+' : '') + '$' + pps.toFixed(2) + '/sh';
    const rp = rt.pnl != null ? rt.pnl : 0;
    const rtPnlClass = rp >= 0 ? 'pos' : 'neg';
    const rtPnlStr = (rp >= 0 ? '+' : '') + '$' + rp.toFixed(2);
    const tp = rt.true_pnl != null ? rt.true_pnl : 0;
    const rtTruePnlClass = tp >= 0 ? 'pos' : 'neg';
    const rtTruePnlStr = (tp >= 0 ? '+' : '') + '$' + tp.toFixed(2);
    html += `<tr class="ip-recovery-row"><td></td>
      <td colspan="2" style="padding-left:36px;font-size:11px;color:#94a3b8">
        ${esc(rt.action)} ${rt.qty} shares @ $${rt.price.toFixed(2)}
      </td>
      <td style="font-size:11px;color:#64748b">vs strike $${_ipFormatStrike(rec.strike)} <span class="${perShClass}" style="margin-left:6px;font-size:11px">${perShStr}</span></td>
      <td></td>
      <td style="font-size:11px"><span class="${rtPnlClass}">${rtPnlStr}</span><span style="color:#64748b"> / </span><span class="${rtTruePnlClass}">${rtTruePnlStr}</span></td>
      <td style="font-size:11px">${rt.date}</td>
      <td colspan="8"></td>
    </tr>`;
  }

  return html;
}

// ── Income weekly chart (premium bars + cumulative P&L line) ───────────

let _ipTsChart = null;

function _ipTsCssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function _destroyIpTsChart() {
  if (_ipTsChart) {
    _ipTsChart.destroy();
    _ipTsChart = null;
  }
}

function _ipTsFmt$(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  const s = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  return (n < 0 ? '-$' : '$') + s;
}

export async function loadIncomeTimeseries() {
  const canvas = document.getElementById('ip-timeseries-chart');
  const empty = document.getElementById('ip-chart-empty');
  const wrap = document.querySelector('#ip-chart-panel .ip-chart-canvas-wrap');
  const sub = document.getElementById('ip-chart-sub');
  if (!canvas || typeof Chart === 'undefined') return;

  const ticker = document.getElementById('ip-ticker').value.trim().toUpperCase();
  const status = document.getElementById('ip-status').value;
  const strategy = document.getElementById('ip-strategy').value;
  const params = new URLSearchParams();
  if (ticker) params.set('ticker', ticker);
  if (status) params.set('status', status);
  if (strategy) params.set('strategy', strategy);
  if (S._ipCardFilter) params.set('outcome', S._ipCardFilter);
  _ipAppendDateRangeParams(params);

  try {
    const data = await fetch('/api/income/timeseries?' + params).then(r => r.json());
    if (data.error) throw new Error(data.error);

    const weeks = data.week_starts || [];
    if (!weeks.length) {
      _destroyIpTsChart();
      if (empty) {
        empty.textContent = 'No trades in this window.';
        empty.style.display = '';
      }
      if (wrap) wrap.style.display = 'none';
      if (sub) sub.textContent = '';
      return;
    }
    if (empty) {
      empty.textContent = 'No trades in this window.';
      empty.style.display = 'none';
    }
    if (wrap) wrap.style.display = '';

    if (sub) {
      if (data.date_from && data.date_to) {
        sub.textContent = data.date_from + ' → ' + data.date_to;
      } else {
        sub.textContent = 'All dates · weeks with activity';
      }
    }

    const labels = weeks.map(w => {
      const d = new Date(w + 'T12:00:00');
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    });

    const accent = _ipTsCssVar('--color-accent') || '#6366f1';
    const pos = _ipTsCssVar('--color-pos') || '#34d399';
    const text = _ipTsCssVar('--color-text-secondary') || '#94a3b8';
    const grid = _ipTsCssVar('--color-border-subtle') || '#1e2235';

    _destroyIpTsChart();
    const ctx = canvas.getContext('2d');
    _ipTsChart = new Chart(ctx, {
      data: {
        labels,
        datasets: [
          {
            type: 'bar',
            label: 'Weekly Sum Net P&L',
            data: data.weekly_sum_net || [],
            yAxisID: 'y',
            backgroundColor: accent + 'aa',
            borderRadius: 3,
            order: 1,
          },
          {
            type: 'line',
            label: 'Cumulative Sum Net P&L',
            data: data.cumulative_sum_net || [],
            yAxisID: 'y1',
            borderColor: pos,
            backgroundColor: pos + '18',
            borderWidth: 2,
            tension: 0.25,
            pointRadius: 0,
            pointHoverRadius: 4,
            fill: false,
            order: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: { color: text, font: { size: 11 }, boxWidth: 12, padding: 10 },
          },
          tooltip: {
            callbacks: {
              label: (c) => {
                const v = c.raw;
                return `${c.dataset.label}: ${_ipTsFmt$(v)}`;
              },
              afterBody: (items) => {
                if (!items.length) return [];
                const idx = items[0].dataIndex;
                const o = data.weekly_option_pnl?.[idx];
                const r = data.weekly_recovery_pnl?.[idx];
                if (o == null && r == null) return [];
                const lines = [];
                if (o != null) lines.push(`Option (week): ${_ipTsFmt$(o)}`);
                if (r != null) lines.push(`Recovery (week): ${_ipTsFmt$(r)}`);
                return lines;
              },
            },
          },
        },
        scales: {
          x: {
            ticks: { color: text, maxRotation: 45, font: { size: 10 }, maxTicksLimit: 16 },
            grid: { color: grid },
          },
          y: {
            position: 'left',
            title: { display: true, text: 'Weekly Sum Net P&L', color: text, font: { size: 10 } },
            ticks: { color: text, callback: v => _ipTsFmt$(v) },
            grid: { color: grid },
          },
          y1: {
            position: 'right',
            title: { display: true, text: 'Cumulative Sum Net P&L', color: text, font: { size: 10 } },
            ticks: { color: text, callback: v => _ipTsFmt$(v) },
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
  } catch (e) {
    console.error('Income timeseries error:', e);
    _destroyIpTsChart();
    if (empty) {
      empty.textContent = 'Could not load chart.';
      empty.style.display = '';
    }
    if (wrap) wrap.style.display = 'none';
  }
}

// ── Recovery Dashboard ──────────────────────────────────────────────────

let _recDashOpen = false;

export function toggleRecoverySummary() {
  _recDashOpen = !_recDashOpen;
  const panel = document.getElementById('ip-rec-dash');
  const arrow = document.getElementById('ip-rec-dash-arrow');
  if (!panel || !arrow) return;
  panel.style.display = _recDashOpen ? '' : 'none';
  arrow.textContent = _recDashOpen ? '▼' : '▶';
  if (_recDashOpen) loadRecoverySummary();
}

export async function loadRecoverySummary() {
  const body  = document.getElementById('ip-rec-dash-body');
  const loading = document.getElementById('ip-rec-dash-loading');
  const err   = document.getElementById('ip-rec-dash-error');
  const count = document.getElementById('ip-rec-dash-count');
  if (!body) return;

  loading.style.display = '';
  err.style.display = 'none';
  body.innerHTML = '';

  const params = new URLSearchParams();
  const tickerEl = document.getElementById('ip-ticker');
  const strategyEl = document.getElementById('ip-strategy');
  if (tickerEl && tickerEl.value) params.set('ticker', tickerEl.value.trim().toUpperCase());
  if (strategyEl && strategyEl.value) params.set('strategy', strategyEl.value);
  _ipAppendDateRangeParams(params);

  try {
    const res = await fetch('/api/income/recovery-summary?' + params).then(r => r.json());
    loading.style.display = 'none';
    if (res.error) throw new Error(res.error);

    const rows = res.summary || [];
    if (count) count.textContent = rows.length ? `${rows.length} ticker${rows.length > 1 ? 's' : ''}` : '';

    if (!rows.length) {
      body.innerHTML = '<div class="ip-rec-dash-empty">No assigned trades with recovery activity in this window.</div>';
      return;
    }

    body.innerHTML = `
      <table class="ip-rec-dash-tbl">
        <thead><tr>
          <th>Ticker</th>
          <th title="Number of assigned trades in the selected window">Assignments</th>
          <th>Assigned Shares</th>
          <th>Recovered</th>
          <th>Remaining</th>
          <th>Progress</th>
          <th title="Recovery P&L vs strike price">Rec. P&L</th>
          <th title="True P&L vs assignment stock price">True P&L</th>
          <th>Status</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => {
            const pct = r.pct_complete;
            const recPnl = r.recovery_pnl;
            const truPnl = r.true_recovery_pnl;
            const recCls  = recPnl  >= 0 ? 'pos' : 'neg';
            const truCls  = truPnl  >= 0 ? 'pos' : 'neg';
            const fmtPnl  = v => (v >= 0 ? '+' : '') + '$' + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
            const statusBadge = r.is_complete
              ? '<span class="ip-badge ip-rec-dash-done">Complete</span>'
              : '<span class="ip-badge ip-rec-dash-progress">In Progress</span>';
            return `<tr>
              <td><strong>${esc(r.ticker)}</strong></td>
              <td>${r.num_assignments}</td>
              <td>${r.assigned_qty.toLocaleString()}</td>
              <td>${r.recovered_qty.toLocaleString()}</td>
              <td class="${r.remaining_qty > 0 ? 'ip-rec-dash-remaining' : ''}">${r.remaining_qty > 0 ? r.remaining_qty.toLocaleString() : '—'}</td>
              <td class="ip-rec-dash-bar-cell">
                <div class="ip-rec-dash-bar-wrap">
                  <div class="ip-rec-dash-bar-fill" style="width:${Math.min(pct,100)}%"></div>
                </div>
                <span class="ip-rec-dash-pct">${pct}%</span>
              </td>
              <td class="${recCls}">${fmtPnl(recPnl)}</td>
              <td class="${truCls}">${fmtPnl(truPnl)}</td>
              <td>${statusBadge}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>`;
  } catch (e) {
    loading.style.display = 'none';
    err.textContent = 'Error: ' + e.message;
    err.style.display = '';
  }
}

export async function syncIncome(mode = 'incremental') {
  const dd = document.getElementById('ip-sync-dropdown');
  if (dd) dd.style.display = 'none';

  const btn = document.getElementById('ip-sync-btn');
  const caret = document.getElementById('ip-sync-caret');
  const icon = document.getElementById('ip-sync-icon');
  btn.disabled = true;
  if (caret) caret.disabled = true;
  icon.classList.add('ip-spin');
  document.getElementById('ip-last-sync').textContent = 'Syncing…';

  try {
    const res = await fetch('/api/income/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    Object.keys(S._ipRecoveryCache).forEach(k => delete S._ipRecoveryCache[k]);
    loadIncomeStats();
    loadIncomeTrades();
  } catch (e) {
    alert('Sync error: ' + e.message);
  } finally {
    btn.disabled = false;
    if (caret) caret.disabled = false;
    icon.classList.remove('ip-spin');
  }
}

export function toggleSyncDropdown(e) {
  e.stopPropagation();
  const dd = document.getElementById('ip-sync-dropdown');
  if (!dd) return;
  dd.style.display = dd.style.display === 'none' ? 'block' : 'none';
}

// Close the dropdown when clicking outside it.
document.addEventListener('click', e => {
  if (!e.target.closest('#ip-sync-split')) {
    const dd = document.getElementById('ip-sync-dropdown');
    if (dd) dd.style.display = 'none';
  }
});
