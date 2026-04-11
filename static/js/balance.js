import { esc, fmt, cls, fetchJson } from './utils.js';
import { store } from './state.js';

/**
 * Priority balance keys for a short-premium / income strategy.
 * Order matters — rendered left-to-right, top-to-bottom.
 * sign: true → color-code positive/negative.
 * tip: hover tooltip description.
 */
const KEY_METRICS = [
  { key: 'liquidationValue',          label: 'Net Liq Value',     sign: false,
    tip: 'Total account value if all positions were closed at current market prices.' },
  { key: 'equity',                    label: 'Equity',            sign: false,
    tip: 'Net worth in the account: assets minus margin debt.' },
  { key: 'cashBalance',               label: 'Cash Balance',      sign: true,
    tip: 'Settled cash on hand. Negative means you owe margin interest on a debit balance.' },
  { key: 'buyingPower',               label: 'Buying Power',      sign: false,
    tip: 'Maximum dollar amount available to open new positions (Reg-T or portfolio margin).' },
  { key: 'availableFunds',            label: 'Available Funds',   sign: false,
    tip: 'Funds available to trade without triggering a margin call, after accounting for open-order reserves.' },
  { key: 'cashAvailableForTrading',   label: 'Cash for Trading',  sign: false,
    tip: 'Settled cash that can be used immediately for new trades.' },
  { key: 'optionBuyingPower',         label: 'Option BP',         sign: false,
    tip: 'Buying power reserved for option trades. Selling naked options consumes this.' },
  { key: 'dayTradingBuyingPower',     label: 'Day-Trade BP',      sign: false,
    tip: 'Intraday buying power (4× maintenance excess for margin accounts). Resets daily.' },
  { key: 'maintenanceRequirement',    label: 'Maint. Req.',       sign: false,
    tip: 'Minimum equity the broker requires to keep current positions open. Drop below this and you face a margin call.' },
  { key: 'longMarketValue',           label: 'Long Mkt Val',      sign: false,
    tip: 'Market value of all long stock and ETF positions — the collateral behind your covered calls.' },
  { key: 'shortMarketValue',          label: 'Short Mkt Val',     sign: false,
    tip: 'Market value of short stock positions (from assignments or sell-short). Shown as negative.' },
  { key: 'longOptionMarketValue',     label: 'Long Opt Val',      sign: false,
    tip: 'Value of long option legs — protective puts, long sides of spreads/collars.' },
  { key: 'shortOptionMarketValue',    label: 'Short Opt Val',     sign: false,
    tip: 'Value of short option legs — the premium you collected. Ideally decays toward zero.' },
  { key: 'unsettledCash',             label: 'Unsettled Cash',    sign: true,
    tip: 'Proceeds from recent sales awaiting T+1 settlement. Cannot be withdrawn yet.' },
  { key: 'marginBalance',             label: 'Margin Balance',    sign: true,
    tip: 'Outstanding margin loan balance. A negative value means you are borrowing from the broker.' },
];

function humanizeKey(k) {
  return String(k || '').replace(/([A-Z])/g, ' $1').replace(/^./, c => c.toUpperCase()).trim();
}

function fmtVal(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') return '$' + fmt(v);
  return esc(String(v));
}

function buildKeyMetrics(accounts, aggregated) {
  const merged = {};

  for (const a of accounts) {
    const cb = a.current_balances || {};
    for (const [k, v] of Object.entries(cb)) {
      if (typeof v !== 'number') continue;
      merged[k] = (merged[k] || 0) + v;
    }
  }

  if (aggregated && typeof aggregated === 'object') {
    for (const [k, v] of Object.entries(aggregated)) {
      if (typeof v !== 'number') continue;
      if (!(k in merged)) merged[k] = v;
    }
  }

  const cards = [];
  for (const m of KEY_METRICS) {
    if (!(m.key in merged)) continue;
    const v = merged[m.key];
    const valClass = m.sign ? cls(v) : '';
    const tipAttr = m.tip ? ` data-tip="${esc(m.tip)}"` : '';
    cards.push(`<div class="bal-kpi"${tipAttr}>
      <div class="bal-kpi-label">${esc(m.label)}<span class="bal-kpi-help" aria-label="Info">?</span></div>
      <div class="bal-kpi-value ${valClass}">$${fmt(v)}</div>
    </div>`);
  }
  if (!cards.length) return '';
  return `<div class="bal-kpi-strip">${cards.join('')}</div>`;
}

function renderBalanceGrid(obj, excludeKeys) {
  if (!obj || typeof obj !== 'object') return '';
  const skip = excludeKeys || new Set();
  const keys = Object.keys(obj).filter(k => !skip.has(k)).sort();
  if (!keys.length) return '<p class="bal-empty">No additional fields.</p>';
  return '<div class="bal-detail-grid">' + keys.map(k =>
    `<div class="bal-detail-row">
      <span class="bal-detail-label">${esc(humanizeKey(k))}</span>
      <span class="bal-detail-val">${fmtVal(obj[k])}</span>
    </div>`
  ).join('') + '</div>';
}

function renderCollapsible(label, obj) {
  if (!obj || typeof obj !== 'object' || !Object.keys(obj).length) return '';
  return `<details class="bal-collapsible">
    <summary>${esc(label)}</summary>
    ${renderBalanceGrid(obj)}
  </details>`;
}

function initTooltips(container) {
  const tip = document.createElement('div');
  tip.className = 'bal-tooltip';
  tip.style.display = 'none';
  document.body.appendChild(tip);

  container.addEventListener('mouseenter', (e) => {
    const kpi = e.target.closest('.bal-kpi[data-tip]');
    if (!kpi) return;
    tip.textContent = kpi.dataset.tip;
    tip.style.display = 'block';
    const rect = kpi.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8));
    tip.style.left = left + 'px';
    tip.style.top = (rect.bottom + 8) + 'px';
  }, true);

  container.addEventListener('mouseleave', (e) => {
    const kpi = e.target.closest('.bal-kpi[data-tip]');
    if (!kpi) return;
    tip.style.display = 'none';
  }, true);
}

async function triggerSnapshot(statusEl) {
  try {
    const res = await fetchJson('/api/account-balances/snapshot', { method: 'POST' });
    if (statusEl) {
      const label = res.already_saved
        ? `Snapshot already saved for ${esc(res.as_of_date)}`
        : `Snapshot saved · ${esc(res.as_of_date)}`;
      statusEl.textContent = label;
      statusEl.className = 'bal-snap-status bal-snap-status--' + (res.already_saved ? 'cached' : 'ok');
    }
  } catch (_) {
    if (statusEl) {
      statusEl.textContent = 'Snapshot unavailable';
      statusEl.className = 'bal-snap-status bal-snap-status--err';
    }
  }
}

export async function loadBalance() {
  const container = document.getElementById('balance-content');
  if (!container) return;
  container.innerHTML = '<div class="loading">Loading balances…</div>';
  try {
    const data = await fetchJson('/api/account-balances');
    store.balanceState.loaded = true;

    const accounts = data.accounts || [];
    const agg = data.aggregated_balance;

    if (!accounts.length && !agg) {
      container.innerHTML = '<p style="color:var(--color-text-muted)">No linked accounts returned.</p>';
      return;
    }

    const metricsHtml = buildKeyMetrics(accounts, agg);

    const usedKeys = new Set(KEY_METRICS.map(m => m.key));
    if (agg && typeof agg === 'object') {
      for (const k of Object.keys(agg)) usedKeys.add(k);
    }

    const acctCards = accounts.map(a => {
      const title = `${esc(a.type || 'Account')} · ${esc(a.account_display || '—')}`;
      const rt = a.round_trips != null ? a.round_trips : '—';
      const dt = a.is_day_trader === true ? 'Yes' : a.is_day_trader === false ? 'No' : '—';
      return `<div class="bal-card">
        <h3 class="bal-card-title">${title}</h3>
        <div class="bal-card-meta">Round trips: ${esc(String(rt))} · Day trader: ${esc(dt)}</div>
        <div class="bal-section-label">Current Balances</div>
        ${renderBalanceGrid(a.current_balances, usedKeys)}
        ${renderCollapsible('Projected Balances', a.projected_balances)}
        ${renderCollapsible('Initial Balances (day start)', a.initial_balances)}
      </div>`;
    }).join('');

    const snapBar = '<div class="bal-snap-bar"><span class="bal-snap-label">Daily snapshot:</span>'
      + '<span class="bal-snap-status bal-snap-status--pending" id="bal-snap-status">Saving…</span></div>';

    container.innerHTML = snapBar + metricsHtml + acctCards;
    initTooltips(container);

    // Fire snapshot in background — non-blocking
    triggerSnapshot(document.getElementById('bal-snap-status'));
  } catch (e) {
    container.innerHTML = '<div class="error">Error: ' + esc(e.message) + '</div>';
  }
}
