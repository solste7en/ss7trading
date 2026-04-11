import { fmt, fmtD, cls, esc, normalizeQty, fetchJson } from './utils.js';
import { store } from './state.js';

export function _overviewTradeRows(trades) {
  if (!trades.length) return '<tr><td colspan="5" style="color:#64748b">No trades found</td></tr>';
  return trades.map(r => {
    const isBuy = (r.action||'').toLowerCase().includes('buy');
    return `<tr>
      <td style="color:#64748b;font-size:12px">${r.trade_date}</td>
      <td class="${isBuy ? 'pos' : 'neg'}" style="font-size:12px">${esc(r.action)}</td>
      <td style="font-size:12px">${r.quantity != null ? fmt(normalizeQty(r.action, r.quantity), 0) : '—'}</td>
      <td style="font-size:12px">${r.price != null ? '$' + fmt(r.price, 4) : '—'}</td>
      <td class="${cls(r.amount)}" style="font-size:12px">${r.amount != null ? '$' + fmt(r.amount) : '—'}</td>
    </tr>`;
  }).join('');
}

export function _overviewPag(cur, total_pages, total, onclick) {
  if (total_pages <= 1) return `<div class="pg-info" style="margin-top:6px">${total.toLocaleString()} trades</div>`;
  const prev = cur <= 1 ? 'disabled' : '';
  const next = cur >= total_pages ? 'disabled' : '';
  return `<div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;gap:4px">
    <button class="pg-btn" ${prev} onclick="${onclick(cur-1)}">‹ Prev</button>
    <span class="pg-info">Page ${cur} / ${total_pages} · ${total.toLocaleString()} trades</span>
    <button class="pg-btn" ${next} onclick="${onclick(cur+1)}">Next ›</button>
  </div>`;
}

export async function loadOverview() {
  const container = document.getElementById('overview-content');
  container.innerHTML = '<div class="loading">Loading top tickers…</div>';
  try {
    const data = await fetchJson('/api/top-tickers');
    store.overviewState.loaded = true;

    const cards = data.tickers.map(t => {
      const countLabel = `${t.trade_count.toLocaleString()} trades `
        + `<span style="color:#475569">(${(t.equity_count||0).toLocaleString()} equity`
        + ` / ${(t.option_count||0).toLocaleString()} option)</span>`;

      const rows = _overviewTradeRows(t.recent_trades);
      const totalPages = Math.ceil((t.equity_count||0) / store.OVERVIEW_LIMIT);
      const pag = `<div id="tpag-${t.symbol}">${_overviewPag(1, totalPages, t.equity_count||0, p => `loadTickerPage('${t.symbol}',${p})`)}</div>`;

      return `<div class="ticker-card" id="tcard-${t.symbol}">
        <div class="ticker-card-header">
          <h3>${esc(t.symbol)}</h3>
          <span class="trade-count">${countLabel}</span>
          <button class="btn-ladder" onclick="openLadder('${t.symbol}')">Ladder</button>
        </div>
        <div id="ttable-${t.symbol}">
          <table><thead><tr><th>Date</th><th>Action</th><th>Qty</th><th>Price</th><th>Amount</th></tr></thead>
          <tbody>${rows}</tbody></table>
        </div>
        ${(t.equity_count||0) > 0 ? pag : ''}
      </div>`;
    }).join('');

    const customCard = `<div class="ticker-card" id="custom-ticker-card">
      <div class="ticker-card-header">
        <h3 style="white-space:nowrap">Lookup ticker</h3>
        <input type="text" id="custom-ticker-input" placeholder="e.g. AAPL" maxlength="10"
          style="width:90px;background:#1a1d2e;border:1px solid #2d3148;border-radius:5px;
                 color:#e2e8f0;padding:5px 8px;font-size:12px;outline:none;text-transform:uppercase"
          onkeydown="if(event.key==='Enter') searchCustomTicker()">
        <button class="btn-sm" onclick="searchCustomTicker()" style="padding:5px 10px">Search</button>
      </div>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <label style="display:flex;align-items:center;gap:6px;font-size:11px;color:#94a3b8;cursor:pointer">
          <input type="checkbox" id="custom-include-options" checked
                 onchange="if(window.customTickerState.symbol) loadCustomTickerPage(1)"
                 style="accent-color:#6366f1">
          Include options
        </label>
      </div>
      <div id="custom-ticker-result" style="color:#475569;font-size:12px">
        Enter a ticker above to view its trade history.
      </div>
    </div>`;

    container.innerHTML = '<div class="overview-grid">' + cards + customCard + '</div>';
  } catch(e) {
    container.innerHTML = '<div class="error">Error: ' + esc(e.message) + '</div>';
  }
}

export async function loadTickerPage(symbol, page) {
  const tableDiv = document.getElementById('ttable-' + symbol);
  const pagDiv   = document.getElementById('tpag-'   + symbol);
  if (!tableDiv) return;
  tableDiv.innerHTML = '<div class="loading" style="padding:10px">Loading…</div>';
  try {
    const params = new URLSearchParams({ ticker: symbol, category: 'equity', limit: store.OVERVIEW_LIMIT, page });
    const res = await fetch('/api/transactions?' + params).then(r => r.json());
    if (res.error) throw new Error(res.error);
    tableDiv.innerHTML =
      '<table><thead><tr><th>Date</th><th>Action</th><th>Qty</th><th>Price</th><th>Amount</th></tr></thead>'
      + '<tbody>' + _overviewTradeRows(res.data) + '</tbody></table>';
    if (pagDiv) pagDiv.innerHTML = _overviewPag(res.page, res.pages, res.total, p => `loadTickerPage('${symbol}',${p})`);
  } catch(e) {
    tableDiv.innerHTML = '<div class="error" style="font-size:12px">Error: ' + esc(e.message) + '</div>';
  }
}

export function openLadder(symbol) {
  window.switchTab('ladder');
  document.getElementById('lad-ticker').value = symbol;
  window.onLadderTickerChange();
}

export async function searchCustomTicker() {
  const sym = document.getElementById('custom-ticker-input').value.trim().toUpperCase();
  if (!sym) return;
  store.customTickerState.symbol = sym;
  await loadCustomTickerPage(1);
}

export async function loadCustomTickerPage(page) {
  const sym = store.customTickerState.symbol;
  if (!sym) return;
  store.customTickerState.page = page;

  const includeOpts = document.getElementById('custom-include-options').checked;
  const resultDiv = document.getElementById('custom-ticker-result');
  resultDiv.innerHTML = '<div class="loading" style="padding:10px">Loading ' + esc(sym) + '…</div>';
  try {
    const mainParams = { ticker: sym, limit: store.OVERVIEW_LIMIT, page };
    if (!includeOpts) mainParams.category = 'equity';

    const mainRes = await fetch('/api/transactions?' + new URLSearchParams(mainParams)).then(r => r.json());
    if (mainRes.error) throw new Error(mainRes.error);

    // Fetch both counts in parallel for the header
    const [eqCount, optCount] = await Promise.all([
      fetch('/api/transactions?' + new URLSearchParams({ ticker: sym, category: 'equity', limit: 10, page: 1 })).then(r => r.json()).then(r => r.total || 0),
      fetch('/api/transactions?' + new URLSearchParams({ ticker: sym, category: 'option', limit: 10, page: 1 })).then(r => r.json()).then(r => r.total || 0),
    ]);

    const total = eqCount + optCount;
    const countLabel = `${total.toLocaleString()} trades `
      + `<span style="color:#475569">(${eqCount.toLocaleString()} equity`
      + ` / ${optCount.toLocaleString()} option)</span>`;

    const pag = _overviewPag(mainRes.page, mainRes.pages, mainRes.total, p => `loadCustomTickerPage(${p})`);

    resultDiv.innerHTML =
      `<div class="ticker-card-header" style="margin-bottom:10px">
        <h3 style="font-size:18px;font-weight:700">${esc(sym)}</h3>
        <span class="trade-count">${countLabel}</span>
        <button class="btn-ladder" onclick="openLadder('${esc(sym)}')">Ladder</button>
      </div>` +
      '<table style="width:100%"><thead><tr><th>Date</th><th>Action</th><th>Qty</th><th>Price</th><th>Amount</th></tr></thead>'
      + '<tbody>' + _overviewTradeRows(mainRes.data) + '</tbody></table>'
      + pag;
  } catch(e) {
    resultDiv.innerHTML = '<div class="error" style="font-size:12px">Error: ' + esc(e.message) + '</div>';
  }
}
