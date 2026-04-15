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
 * Closed-trade efficiency: net_pnl / max(1,days_held) * 100 / strike.
 * Open trades: null (show —).
 */
export function _incomeEfficiencyScore(t) {
  if (!t || t.status === 'open') return null;
  if (t.net_pnl == null) return null;
  const strike = _incomeScoreStrike(t.legs || []);
  if (strike == null) return null;
  const days = t.days_held != null && t.days_held > 0 ? t.days_held : 1;
  return (t.net_pnl / days) * (100 / strike);
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
  const keys = ['open_date', 'close_date', 'recovery', 'recovery_pnl', 'net_pnl', 'days_held', 'net_premium'];
  for (const k of keys) {
    const el = document.getElementById('ip-sa-' + k);
    if (!el) continue;
    el.textContent = S.incomePnlSort.key === k ? (S.incomePnlSort.dir === 'desc' ? '▼' : '▲') : '';
  }
}

export async function _fetchRecovery(ticker) {
  if (S._ipRecoveryCache[ticker]) return S._ipRecoveryCache[ticker];
  try {
    const res = await fetch('/api/income/recovery?ticker=' + encodeURIComponent(ticker)).then(r => r.json());
    if (!res.error) S._ipRecoveryCache[ticker] = res;
    return res;
  } catch (e) { console.error('Recovery fetch error:', e); return null; }
}

export function _getRecoveryForTrade(ticker, tradeId) {
  const cached = S._ipRecoveryCache[ticker];
  if (!cached) return null;
  return (cached.assignments || []).find(a => a.trade_id === tradeId) || null;
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
    const data = await fetch('/api/income/stats?' + params).then(r => r.json());
    if (data.error) throw new Error(data.error);

    const pnl = data.total_pnl || 0;
    const pnlEl = document.getElementById('ip-kpi-pnl');
    pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
    pnlEl.className = 'ip-kpi-value ' + (pnl >= 0 ? 'pos' : 'neg');

    const rpnl = data.total_recovery_pnl != null ? data.total_recovery_pnl : 0;
    const rEl = document.getElementById('ip-kpi-recovery-pnl');
    rEl.textContent = (rpnl >= 0 ? '+' : '') + '$' + rpnl.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
    rEl.className = 'ip-kpi-value ' + (rpnl >= 0 ? 'pos' : 'neg');

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
    const res = await fetch('/api/income/trades?' + params).then(r => r.json());
    if (res.error) throw new Error(res.error);

    S.incomePnlState.loaded = true;
    document.getElementById('ip-loading').style.display = 'none';
    document.getElementById('ip-table').style.display = '';
    document.getElementById('ip-count').textContent = res.total + ' trades';

    const tickersNeedRec = [...new Set((res.data || []).filter(t => t.status === 'assigned').map(t => t.underlying))];
    await Promise.all(tickersNeedRec.map(u => _fetchRecovery(u)));

    _renderIncomeTrades(res.data);
    _updateIpSortArrows();
    window.renderPagination('ip-pagination', res, loadIncomeTrades, S.incomePnlState);
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
      if (t.recovery_pnl != null) {
        const rc = t.recovery_pnl >= 0 ? 'pos' : 'neg';
        recPnlCell = `<span class="${rc}">` + (t.recovery_pnl >= 0 ? '+' : '') + '$' + t.recovery_pnl.toFixed(2) + '</span>';
      }
    }

    html += `<tr class="ip-trade-row" onclick="toggleIncomeTrade(${t.id},'${esc(t.underlying)}','${esc(t.status)}')" style="cursor:pointer">
      <td class="ip-toggle-arrow">${arrow}</td>
      <td><b>${esc(t.underlying)}</b></td>
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
      <td class="${scoreClass}" title="net P&amp;L ÷ max(1,days) ÷ short strike × 100">${scoreStr}</td>
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
      // Recovery section for assigned trades
      if (t.status === 'assigned') {
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
    if (status === 'assigned' && ticker && !S._ipRecoveryCache[ticker]) {
      await _fetchRecovery(ticker);
    }
  }
  loadIncomeTrades(false);
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
        ${statusLabel}
        ${!isComplete && remaining > 0 ? `<button class="ip-recovery-closeout" onclick="event.stopPropagation(); dismissRecovery(${rec.trade_id},'${esc(ticker)}',${remaining + dismissed})">Close Out</button>` : ''}
      </div>
      <div class="ip-recovery-bar-bg"><div class="ip-recovery-bar-fill" style="width:${pct}%"></div></div>
    </td></tr>`;

  // Individual recovery trades
  for (const rt of trades) {
    const rtPnlClass = rt.pnl >= 0 ? 'pos' : 'neg';
    const rtPnlStr = (rt.pnl >= 0 ? '+' : '') + '$' + rt.pnl.toFixed(2);
    html += `<tr class="ip-recovery-row"><td></td>
      <td colspan="2" style="padding-left:36px;font-size:11px;color:#94a3b8">
        ${esc(rt.action)} ${rt.qty} shares @ $${rt.price.toFixed(2)}
      </td>
      <td style="font-size:11px;color:#64748b">vs strike $${_ipFormatStrike(rec.strike)}</td>
      <td></td><td></td>
      <td style="font-size:11px">${rt.date}</td>
      <td colspan="4"></td>
      <td class="${rtPnlClass}" style="font-size:11px">${rtPnlStr}</td>
      <td style="font-size:11px;color:#64748b">${rt.pnl_per_share >= 0 ? '+' : ''}$${rt.pnl_per_share.toFixed(2)}/sh</td>
      <td colspan="3"></td>
    </tr>`;
  }

  return html;
}

export async function syncIncome() {
  const btn = document.getElementById('ip-sync-btn');
  const icon = document.getElementById('ip-sync-icon');
  btn.disabled = true;
  icon.classList.add('ip-spin');
  document.getElementById('ip-last-sync').textContent = 'Syncing…';

  try {
    const res = await fetch('/api/income/sync', { method: 'POST' }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    Object.keys(S._ipRecoveryCache).forEach(k => delete S._ipRecoveryCache[k]);
    loadIncomeStats();
    loadIncomeTrades();
  } catch (e) {
    alert('Sync error: ' + e.message);
  } finally {
    btn.disabled = false;
    icon.classList.remove('ip-spin');
  }
}
