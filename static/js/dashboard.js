const fmt   = (v,d=2) => v==null ? '—' : Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const fmtD  = (v,d=2) => v==null ? '—' : (v>=0?'+':'') + fmt(v,d);
const cls   = (v)     => v==null ? '' : (v>=0?'pos':'neg');
const esc   = (s)     => String(s||'').replace(/</g,'&lt;');

/** SQLite / ISO-ish datetime for Realized G/L banner */
function formatRgLastImport(raw) {
  if (raw == null || raw === '') return 'Unknown (no imported_at in DB, or column missing)';
  const s = String(raw).trim();
  const normalized = s.includes('T') ? s : s.replace(' ', 'T');
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleString(undefined, {
    dateStyle: 'medium', timeStyle: 'short',
  });
}

let _debTimer = null;
function debounce(fn) { clearTimeout(_debTimer); _debTimer = setTimeout(fn, 400); }

/**
 * GET/POST JSON: parse body, throw on `error` field or non-OK status.
 */
async function fetchJson(url, init) {
  const res = await fetch(url, init);
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (data.error) throw new Error(data.error);
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return data;
}

/**
 * Per-rung result rows for equity ladder and Income strategy-ladder APIs.
 * @param {Array<{rung:number,qty:number,price:number,status:string,order_id?:string,error?:string}>} results
 * @param {{ qtyColumnLabel?: string, tableExtraClass?: string, footerHtml?: string }} options
 */
function ladderResultTableHtml(results, options) {
  const opts = options || {};
  const qtyLabel = opts.qtyColumnLabel || 'Qty';
  const extraClass = opts.tableExtraClass ? ' ' + opts.tableExtraClass : '';
  const footer = opts.footerHtml || '';

  const rows = results.map(r => {
    const icon = r.status === 'ok'
      ? '<span class="rung-status rung-ok"></span>'
      : '<span class="rung-status rung-fail"></span>';
    const resultCell = r.status === 'ok'
      ? `${icon}Order #${esc(r.order_id)}`
      : `<span style="display:inline-flex;align-items:flex-start;gap:6px">${icon}<span class="neg ladder-result-msg">${esc(r.error)}</span></span>`;
    return `<tr>
        <td>${r.rung}</td>
        <td>${fmt(r.qty, 0)}</td>
        <td>$${fmt(r.price)}</td>
        <td class="ladder-result-cell">${resultCell}</td>
      </tr>`;
  }).join('');

  return '<table class="ladder-result-table' + extraClass + '" style="margin-top:10px">' +
    '<thead><tr><th>#</th><th>' + esc(qtyLabel) + '</th><th>Price</th><th>Result</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>' + footer;
}

// ── tab state ──────────────────────────────────────────────────────
let currentTab = 'positions';

function switchTab(name) {
  currentTab = name;
  // Match each tab by extracting its name from the onclick attribute — avoids
  // any reliance on DOM order which would break after tab reordering.
  document.querySelectorAll('.tab').forEach(t => {
    const m = (t.getAttribute('onclick') || '').match(/switchTab\('(\w+)'\)/);
    t.classList.toggle('active', !!(m && m[1] === name));
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'overview'  && !overviewState.loaded)  loadOverview();
  if (name === 'history'   && !historyState.loaded)   loadHistory();
  if (name === 'gains'     && !gainsState.loaded)     loadGains();
  if (name === 'orders'    && !ordersState.loaded)    loadOrders();
  if (name === 'quotes')   initWatchlists();
  if (name === 'incomepnl' && !incomePnlState.loaded) { loadIncomeStats(); loadIncomeTrades(); }
  if (name === 'strategy' && _stratTicker) {
    loadStrategySuggestions(_stratTicker);
    loadStrategyOrders();
  }
}

function refreshCurrent() {
  document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  if (currentTab === 'overview')  loadOverview();
  if (currentTab === 'positions') loadPositions();
  if (currentTab === 'quotes')    loadQuotes();
  if (currentTab === 'history')   loadHistory();
  if (currentTab === 'gains')     loadGains();
  if (currentTab === 'orders')    loadOrders();
  if (currentTab === 'incomepnl') { loadIncomeStats(); loadIncomeTrades(); }
  if (currentTab === 'strategy' && _stratTicker) {
    loadStrategySuggestions(_stratTicker);
    loadStrategyOrders();
    loadStrategyRecent();
  }
}

// ── Overview (Top Tickers) ────────────────────────────────────────
const overviewState = { loaded: false };
const OVERVIEW_LIMIT = 10;

function _overviewTradeRows(trades) {
  if (!trades.length) return '<tr><td colspan="5" style="color:#64748b">No trades found</td></tr>';
  return trades.map(r => {
    const isBuy = (r.action||'').toLowerCase().includes('buy');
    return `<tr>
      <td style="color:#64748b;font-size:12px">${r.trade_date}</td>
      <td class="${isBuy ? 'pos' : 'neg'}" style="font-size:12px">${esc(r.action)}</td>
      <td style="font-size:12px">${r.quantity != null ? fmt(r.quantity, 0) : '—'}</td>
      <td style="font-size:12px">${r.price != null ? '$' + fmt(r.price, 4) : '—'}</td>
      <td class="${cls(r.amount)}" style="font-size:12px">${r.amount != null ? '$' + fmt(r.amount) : '—'}</td>
    </tr>`;
  }).join('');
}

function _overviewPag(cur, total_pages, total, onclick) {
  if (total_pages <= 1) return `<div class="pg-info" style="margin-top:6px">${total.toLocaleString()} trades</div>`;
  const prev = cur <= 1 ? 'disabled' : '';
  const next = cur >= total_pages ? 'disabled' : '';
  return `<div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;gap:4px">
    <button class="pg-btn" ${prev} onclick="${onclick(cur-1)}">‹ Prev</button>
    <span class="pg-info">Page ${cur} / ${total_pages} · ${total.toLocaleString()} trades</span>
    <button class="pg-btn" ${next} onclick="${onclick(cur+1)}">Next ›</button>
  </div>`;
}

async function loadOverview() {
  const container = document.getElementById('overview-content');
  container.innerHTML = '<div class="loading">Loading top tickers…</div>';
  try {
    const data = await fetchJson('/api/top-tickers');
    overviewState.loaded = true;

    const cards = data.tickers.map(t => {
      const countLabel = `${t.trade_count.toLocaleString()} trades `
        + `<span style="color:#475569">(${(t.equity_count||0).toLocaleString()} equity`
        + ` / ${(t.option_count||0).toLocaleString()} option)</span>`;

      const rows = _overviewTradeRows(t.recent_trades);
      const hasNext = (t.equity_count || 0) > OVERVIEW_LIMIT;
      const pag = `<div id="tpag-${t.symbol}" style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;gap:4px">
        <button class="pg-btn" disabled>‹ Prev</button>
        <span class="pg-info">Page 1 · ${(t.equity_count||0).toLocaleString()} trades</span>
        <button class="pg-btn" ${hasNext ? '' : 'disabled'} onclick="loadTickerPage('${t.symbol}',2)">Next ›</button>
      </div>`;

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
                 onchange="if(customTickerState.symbol) loadCustomTickerPage(1)"
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

async function loadTickerPage(symbol, page) {
  const tableDiv = document.getElementById('ttable-' + symbol);
  const pagDiv   = document.getElementById('tpag-'   + symbol);
  if (!tableDiv) return;
  tableDiv.innerHTML = '<div class="loading" style="padding:10px">Loading…</div>';
  try {
    const params = new URLSearchParams({ ticker: symbol, category: 'equity', limit: OVERVIEW_LIMIT, page });
    const res = await fetch('/api/transactions?' + params).then(r => r.json());
    if (res.error) throw new Error(res.error);
    tableDiv.innerHTML =
      '<table><thead><tr><th>Date</th><th>Action</th><th>Qty</th><th>Price</th><th>Amount</th></tr></thead>'
      + '<tbody>' + _overviewTradeRows(res.data) + '</tbody></table>';
    if (pagDiv) pagDiv.outerHTML = _overviewPag(res.page, res.pages, res.total, p => `loadTickerPage('${symbol}',${p})`);
  } catch(e) {
    tableDiv.innerHTML = '<div class="error" style="font-size:12px">Error: ' + esc(e.message) + '</div>';
  }
}

function openLadder(symbol) {
  switchTab('ladder');
  document.getElementById('lad-ticker').value = symbol;
  onLadderTickerChange();
}

// ── Overview custom ticker lookup ─────────────────────────────────
const customTickerState = { symbol: '', page: 1, pages: 1, total: 0 };

async function searchCustomTicker() {
  const sym = document.getElementById('custom-ticker-input').value.trim().toUpperCase();
  if (!sym) return;
  customTickerState.symbol = sym;
  await loadCustomTickerPage(1);
}

async function loadCustomTickerPage(page) {
  const sym = customTickerState.symbol;
  if (!sym) return;
  customTickerState.page = page;

  const includeOpts = document.getElementById('custom-include-options').checked;
  const resultDiv = document.getElementById('custom-ticker-result');
  resultDiv.innerHTML = '<div class="loading" style="padding:10px">Loading ' + esc(sym) + '…</div>';
  try {
    const mainParams = { ticker: sym, limit: OVERVIEW_LIMIT, page };
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

// ── Positions (grouped, sortable table) ──────────────────────────
let _posData       = [];
let _posSortCol    = null;
let _posSortDir    = 1;
let _posExpanded   = new Set(); // set of underlying symbols whose options are expanded

function togglePosGroup(underlying) {
  if (_posExpanded.has(underlying)) _posExpanded.delete(underlying);
  else _posExpanded.add(underlying);
  _renderPositions();
}

function _renderPositions() {
  // Separate equity/ETF/cash from options
  const nonOptions = _posData.filter(p => p.asset_type !== 'OPTION');
  const options    = _posData.filter(p => p.asset_type === 'OPTION');

  // Sort non-option rows
  if (_posSortCol) {
    nonOptions.sort((a, b) => {
      let av = a[_posSortCol], bv = b[_posSortCol];
      if (av == null) av = _posSortDir > 0 ? Infinity : -Infinity;
      if (bv == null) bv = _posSortDir > 0 ? Infinity : -Infinity;
      if (typeof av === 'string') av = av.toLowerCase();
      if (typeof bv === 'string') bv = bv.toLowerCase();
      return av < bv ? -_posSortDir : av > bv ? _posSortDir : 0;
    });
  }

  // Build a map: underlying → [option positions]
  const optMap = {};
  options.forEach(p => {
    const key = p.underlying_symbol || p.symbol.split(/\s+/)[0];
    (optMap[key] = optMap[key] || []).push(p);
  });

  // Sort each group's options: by expiry (ascending) then strike (ascending) then put/call
  Object.values(optMap).forEach(grp => grp.sort((a, b) => {
    const expA = a.option_expiry || '', expB = b.option_expiry || '';
    if (expA !== expB) return expA < expB ? -1 : 1;
    const stA = a.option_strike ?? 0, stB = b.option_strike ?? 0;
    if (stA !== stB) return stA - stB;
    const pcA = a.put_call || '', pcB = b.put_call || '';
    return pcA < pcB ? -1 : pcA > pcB ? 1 : 0;
  }));

  // Collect underlyings that have options but no matching equity row
  const equitySymbols = new Set(nonOptions.map(p => p.symbol));
  const orphanUnderlyings = [...new Set(
    Object.keys(optMap).filter(u => !equitySymbols.has(u))
  )].sort();

  const html = [];

  // Format ISO expiry "2026-04-10" → "Apr 10 '26"
  const fmtExpiry = iso => {
    if (!iso) return '—';
    const [y, m, d] = iso.split('-');
    const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][parseInt(m,10)-1];
    return `${mon} ${parseInt(d,10)} '${y.slice(2)}`;
  };

  // Helper: render one data row (equity/ETF/cash or option child)
  const dataRow = (p, isChild = false) => {
    const isOpt = p.asset_type === 'OPTION';
    const pd    = isOpt ? 4 : 2;
    const pcBadge = p.put_call
      ? `<span class="badge badge-${p.put_call}" style="font-size:10px;padding:1px 6px">${p.put_call}</span>`
      : '—';

    // For option child rows: show a clean "$STRIKE" label with full OCC in tooltip
    // For equity rows: show bold ticker; expiry cell is empty
    let symbolCell, expiryCell;
    if (isChild) {
      const strikeLabel = p.option_strike != null ? `$${Number(p.option_strike).toFixed(2)}` : esc(p.symbol);
      symbolCell = `<span class="pos-opt-symbol" title="${esc(p.symbol)}">${strikeLabel}</span>`;
      expiryCell = `<span class="pos-opt-expiry${_isExpiringSoon(p.option_expiry) ? ' pos-expiry-soon' : ''}">${fmtExpiry(p.option_expiry)}</span>`;
    } else {
      symbolCell = `<b>${esc(p.symbol)}</b>`;
      expiryCell = '';
    }

    return `<tr class="${isChild ? 'pos-opt-row' : ''}">
      <td></td>
      <td>${symbolCell}</td>
      <td><span class="badge badge-${p.asset_type}">${p.asset_type}</span></td>
      <td>${pcBadge}</td>
      <td>${expiryCell}</td>
      <td>${fmt(p.quantity,0)}</td>
      <td>${p.avg_price!=null?'$'+fmt(p.avg_price,pd):'—'}</td>
      <td>${p.current_price!=null?'$'+fmt(p.current_price,pd):'—'}</td>
      <td>${p.market_value!=null?'$'+fmt(p.market_value):'—'}</td>
      <td class="${cls(p.unrealized_pl)}">${p.unrealized_pl!=null?'$'+fmtD(p.unrealized_pl):'—'}</td>
      <td class="${cls(p.day_pl)}">${p.day_pl!=null?'$'+fmtD(p.day_pl):'—'}</td>
      <td class="${cls(p.day_pl_pct)}">${p.day_pl_pct!=null?fmtD(p.day_pl_pct)+'%':'—'}</td>
    </tr>`;
  };

  // Flag options expiring within 7 days
  const _isExpiringSoon = iso => {
    if (!iso) return false;
    const diff = (new Date(iso) - new Date()) / 86400000;
    return diff >= 0 && diff <= 7;
  };

  // Helper: render options toggle row for a given underlying key
  const toggleRow = (underlying, opts) => {
    const expanded  = _posExpanded.has(underlying);
    const arrow     = expanded ? '▼' : '▶';
    const optCount  = opts.length;
    const totalMv   = opts.reduce((s, o) => s + (o.market_value || 0), 0);
    const totalUpl  = opts.every(o => o.unrealized_pl != null)
                      ? opts.reduce((s, o) => s + (o.unrealized_pl || 0), 0) : null;
    const mvStr     = '$' + fmt(totalMv);
    const uplStr    = totalUpl != null
      ? `<span class="${cls(totalUpl)}">$${fmtD(totalUpl)}</span>` : '—';
    return `<tr class="pos-opt-toggle" onclick="togglePosGroup('${esc(underlying)}')">
      <td class="pos-toggle-arrow">${arrow}</td>
      <td colspan="4" class="pos-toggle-label">
        <span class="pos-toggle-ticker">${esc(underlying)}</span>
        <span class="pos-toggle-meta">${optCount} option position${optCount !== 1 ? 's' : ''}</span>
      </td>
      <td></td><td></td><td></td>
      <td>${mvStr}</td>
      <td>${uplStr}</td>
      <td></td><td></td>
    </tr>`;
  };

  // Render each equity/ETF/cash row + its options toggle below it
  for (const p of nonOptions) {
    html.push(dataRow(p, false));
    const opts = optMap[p.symbol];
    if (opts && opts.length) {
      html.push(toggleRow(p.symbol, opts));
      if (_posExpanded.has(p.symbol)) {
        opts.forEach(o => html.push(dataRow(o, true)));
      }
    }
  }

  // Orphan option groups (options with no equity parent in the account)
  for (const underlying of orphanUnderlyings) {
    const opts = optMap[underlying];
    html.push(toggleRow(underlying, opts));
    if (_posExpanded.has(underlying)) {
      opts.forEach(o => html.push(dataRow(o, true)));
    }
  }

  document.getElementById('pos-tbody').innerHTML = html.join('');

  // Update sort arrows
  ['symbol','quantity','avg_price','current_price','market_value','unrealized_pl','day_pl','day_pl_pct'].forEach(col => {
    const el = document.getElementById('pa-' + col);
    if (el) el.textContent = col === _posSortCol ? (_posSortDir > 0 ? ' ▲' : ' ▼') : '';
  });
}

function sortPositions(col) {
  if (_posSortCol === col) _posSortDir *= -1;
  else { _posSortCol = col; _posSortDir = 1; }
  _renderPositions();
}

async function loadPositions() {
  try {
    const data = await fetchJson('/api/positions');
    _posData = data;
    _renderPositions();
    document.getElementById('pos-loading').style.display='none';
    document.getElementById('pos-table').style.display='block';
  } catch(e) {
    document.getElementById('pos-loading').style.display='none';
    document.getElementById('pos-error').style.display='block';
    document.getElementById('pos-error').textContent='Error: '+e.message;
  }
}

// ── Quotes & Watchlists ───────────────────────────────────────────
const wlState = { lists: [], currentId: 'positions', initialized: false };

async function initWatchlists() {
  if (wlState.initialized) { loadQuotes(); return; }
  wlState.initialized = true;
  try {
    const lists = await fetch('/api/watchlists').then(r => r.json());
    wlState.lists = lists;
    _renderWlTabs();
    loadQuotes();
  } catch(e) {
    loadQuotes();
  }
}

function _renderWlTabs() {
  const container = document.getElementById('wl-tabs');
  const fixed = `<button class="wl-tab${wlState.currentId==='positions'?' active':''}" data-list="positions" onclick="selectWatchlist('positions')">All Positions</button>`;
  const dynamic = wlState.lists.map(l =>
    `<button class="wl-tab${wlState.currentId===l.id?' active':''}" data-list="${l.id}" onclick="selectWatchlist(${l.id})">${esc(l.name)}</button>`
  ).join('');
  container.innerHTML = fixed + dynamic;
}

function selectWatchlist(id) {
  wlState.currentId = id;
  _renderWlTabs();
  const isCustom = id !== 'positions';
  const editBar = document.getElementById('wl-edit-bar');
  const removeCol = document.getElementById('q-remove-col');
  editBar.style.display = isCustom ? 'flex' : 'none';
  if (removeCol) removeCol.style.display = isCustom ? '' : 'none';
  const list = wlState.lists.find(l => l.id === id);
  document.getElementById('q-list-label').textContent =
    isCustom && list ? `Live quotes — ${list.name}` : 'Live quotes — All Positions';
  loadQuotes();
}

function showNewListForm() {
  document.getElementById('wl-new-form').style.display = 'flex';
  document.getElementById('wl-new-btn').style.display = 'none';
  document.getElementById('wl-name-input').focus();
}

function hideNewListForm() {
  document.getElementById('wl-new-form').style.display = 'none';
  document.getElementById('wl-new-btn').style.display = '';
  document.getElementById('wl-name-input').value = '';
}

async function createWatchlist() {
  const name = document.getElementById('wl-name-input').value.trim();
  if (!name) return;
  try {
    const res = await fetch('/api/watchlists', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name})
    }).then(r => r.json());
    if (res.error) { alert('Error: ' + res.error); return; }
    wlState.lists.push(res);
    hideNewListForm();
    selectWatchlist(res.id);
  } catch(e) { alert('Error: ' + e.message); }
}

async function deleteCurrentList() {
  const id = wlState.currentId;
  if (id === 'positions') return;
  const list = wlState.lists.find(l => l.id === id);
  if (!confirm(`Delete list "${list?.name}"? This cannot be undone.`)) return;
  try {
    await fetch('/api/watchlists/' + id, {method: 'DELETE'});
    wlState.lists = wlState.lists.filter(l => l.id !== id);
    selectWatchlist('positions');
  } catch(e) { alert('Error: ' + e.message); }
}

async function addWatchlistSymbol() {
  const id = wlState.currentId;
  if (id === 'positions') return;
  const sym = document.getElementById('wl-sym-input').value.trim().toUpperCase();
  if (!sym) return;
  try {
    await fetch(`/api/watchlists/${id}/symbols`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({symbol: sym})
    });
    document.getElementById('wl-sym-input').value = '';
    const list = wlState.lists.find(l => l.id === id);
    if (list) list.symbol_count = (list.symbol_count || 0) + 1;
    _renderWlTabs();
    loadQuotes();
  } catch(e) { alert('Error: ' + e.message); }
}

async function removeWatchlistSymbol(listId, symbol) {
  try {
    await fetch(`/api/watchlists/${listId}/symbols/${symbol}`, {method: 'DELETE'});
    loadQuotes();
  } catch(e) { alert('Error: ' + e.message); }
}

async function loadQuotes() {
  const id = wlState.currentId;
  document.getElementById('q-loading').style.display = 'block';
  document.getElementById('q-table').style.display = 'none';
  document.getElementById('q-error').style.display = 'none';
  const isCustom = id !== 'positions';
  const removeCol = document.getElementById('q-remove-col');
  if (removeCol) removeCol.style.display = isCustom ? '' : 'none';
  try {
    const url = isCustom ? `/api/quotes/list/${id}` : '/api/quotes';
    const data = await fetch(url).then(r => r.json());
    if (data.error) throw new Error(data.error);
    document.getElementById('q-tbody').innerHTML = data.map(q => `<tr>
      <td><b>${esc(q.symbol)}</b></td>
      <td>$${fmt(q.last)}</td>
      <td>${q.bid!=null?'$'+fmt(q.bid):'—'}</td>
      <td>${q.ask!=null?'$'+fmt(q.ask):'—'}</td>
      <td class="${cls(q.change)}">${q.change!=null?'$'+fmtD(q.change):'—'}</td>
      <td class="${cls(q.change_pct)}">${q.change_pct!=null?fmtD(q.change_pct)+'%':'—'}</td>
      <td>${q.volume!=null?Number(q.volume).toLocaleString():'—'}</td>
      <td>${q['52w_high']!=null?'$'+fmt(q['52w_high']):'—'}</td>
      <td>${q['52w_low']!=null?'$'+fmt(q['52w_low']):'—'}</td>
      ${isCustom ? `<td><button class="wl-remove-sym" onclick="removeWatchlistSymbol(${id},'${esc(q.symbol)}')">✕</button></td>` : ''}
    </tr>`).join('');
    document.getElementById('q-loading').style.display='none';
    document.getElementById('q-table').style.display='table';
  } catch(e) {
    document.getElementById('q-loading').style.display='none';
    document.getElementById('q-error').style.display='block';
    document.getElementById('q-error').textContent='Error: '+e.message;
  }
}

async function fetchQuote() {
  const sym = document.getElementById('quoteInput').value.trim().toUpperCase();
  if (!sym) return;
  const div = document.getElementById('quote-result');
  div.innerHTML = '<div class="loading">Loading '+sym+'…</div>';
  try {
    const q = await fetch('/api/quote/'+sym).then(r=>r.json());
    if (!q.symbol) { div.innerHTML='<div class="error">No data for '+sym+'</div>'; return; }
    div.innerHTML = `<div class="quote-card">
      <div class="sym">${esc(q.symbol)}</div>
      <div class="last ${cls(q.change)}">$${fmt(q.last)}</div>
      <div><label>Bid</label><div class="val">$${fmt(q.bid)}</div></div>
      <div><label>Ask</label><div class="val">$${fmt(q.ask)}</div></div>
      <div><label>Change</label><div class="val ${cls(q.change)}">${q.change!=null?'$'+fmtD(q.change):'—'}</div></div>
      <div><label>Change %</label><div class="val ${cls(q.change_pct)}">${q.change_pct!=null?fmtD(q.change_pct)+'%':'—'}</div></div>
      <div><label>Volume</label><div class="val">${q.volume!=null?Number(q.volume).toLocaleString():'—'}</div></div>
      <div><label>52W High</label><div class="val">$${fmt(q['52w_high'])}</div></div>
      <div><label>52W Low</label><div class="val">$${fmt(q['52w_low'])}</div></div>
    </div>`;
  } catch(e) { div.innerHTML='<div class="error">Error: '+e.message+'</div>'; }
}

// ── Trade History (paginated) ─────────────────────────────────────
const historyState = { page:1, loaded:false };

async function _loadHistoryLastSync() {
  try {
    const meta = await fetch('/api/trades/last-sync').then(r => r.json());
    const el = document.getElementById('h-last-sync');
    if (!el) return;
    if (meta.last_synced) {
      const dt = new Date(meta.last_synced + 'Z');
      el.textContent = 'Last sync: ' + dt.toLocaleString();
    } else {
      el.textContent = 'Not synced yet';
    }
  } catch (_) { /* non-fatal */ }
}

async function loadHistory(resetPage=true) {
  if (resetPage) historyState.page = 1;
  const ticker   = document.getElementById('h-ticker').value.trim();
  const search   = document.getElementById('h-search').value.trim();
  const category = document.getElementById('h-category').value;
  const limit    = document.getElementById('h-limit').value;

  document.getElementById('h-loading').style.display='block';
  document.getElementById('h-table').style.display='none';
  document.getElementById('h-error').style.display='none';

  _loadHistoryLastSync();

  try {
    const params = new URLSearchParams({
      page: historyState.page, limit, ticker, search, category
    });
    const res  = await fetchJson('/api/transactions?' + params);
    historyState.loaded = true;

    document.getElementById('h-count').textContent =
      `${res.total.toLocaleString()} total transactions`;

    document.getElementById('h-tbody').innerHTML = res.data.map(r => {
      const asgn = r.is_from_option_event
        ? `<span class="badge badge-OPTION" title="${r.linked_option_action||''}">asgn</span>` : '';
      return `<tr>
        <td>${r.trade_date}</td>
        <td>${esc(r.action)}</td>
        <td><span class="badge badge-${r.category}">${r.category}</span></td>
        <td><b>${esc(r.underlying||'')}</b></td>
        <td style="color:#94a3b8;max-width:220px;overflow:hidden;text-overflow:ellipsis">${esc(r.symbol)}</td>
        <td>${r.option_type?`<span class="badge badge-${r.option_type}">${r.option_type}</span>`:'—'}</td>
        <td>${r.option_strike!=null?'$'+fmt(r.option_strike):'—'}</td>
        <td>${r.option_expiry||'—'}</td>
        <td>${r.quantity!=null?fmt(r.quantity,0):'—'}</td>
        <td>${r.price!=null?'$'+fmt(r.price,4):'—'}</td>
        <td>${r.fees!=null?'$'+fmt(r.fees):'—'}</td>
        <td class="${cls(r.amount)}">${r.amount!=null?(r.amount>=0?'+':'')+'$'+fmt(Math.abs(r.amount)):'—'}</td>
        <td>${asgn}</td>
      </tr>`;
    }).join('');

    document.getElementById('h-loading').style.display='none';
    document.getElementById('h-table').style.display='block';
    renderPagination('h-pagination', res, loadHistory);
  } catch(e) {
    document.getElementById('h-loading').style.display='none';
    document.getElementById('h-error').style.display='block';
    document.getElementById('h-error').textContent='Error: '+e.message;
  }
}

// ── Trade Sync ────────────────────────────────────────────────────
function closeSyncModal() {
  document.getElementById('h-sync-modal').style.display = 'none';
}

async function syncTrades() {
  const btn  = document.getElementById('h-sync-btn');
  const icon = document.getElementById('h-sync-icon');
  const syncEl = document.getElementById('h-last-sync');
  btn.disabled = true;
  icon.classList.add('ip-spin');
  if (syncEl) syncEl.textContent = 'Syncing…';

  try {
    const res = await fetch('/api/trades/sync', { method: 'POST' }).then(r => r.json());

    const modal     = document.getElementById('h-sync-modal');
    const titleEl   = document.getElementById('h-sync-modal-title');
    const bodyEl    = document.getElementById('h-sync-modal-body');

    if (res.error) {
      titleEl.textContent = 'Sync Failed';
      bodyEl.innerHTML = `<div class="sync-modal-row">
        <span class="sync-modal-label">Error</span>
        <span class="sync-modal-value err">${esc(res.error)}</span>
      </div>`;
    } else {
      titleEl.textContent = '✓ Sync Complete';
      const lastSyncStr = res.last_synced
        ? new Date(res.last_synced + 'Z').toLocaleString()
        : '—';
      bodyEl.innerHTML = `
        <div class="sync-modal-row">
          <span class="sync-modal-label">Fetched from Schwab API</span>
          <span class="sync-modal-value">${(res.fetched||0).toLocaleString()} raw transactions</span>
        </div>
        <div class="sync-modal-row">
          <span class="sync-modal-label">Inserted</span>
          <span class="sync-modal-value pos">${(res.inserted||0).toLocaleString()}</span>
        </div>
        <div class="sync-modal-row">
          <span class="sync-modal-label">Skipped (duplicates)</span>
          <span class="sync-modal-value">${(res.skipped||0).toLocaleString()}</span>
        </div>
        <div class="sync-modal-row">
          <span class="sync-modal-label">Errors</span>
          <span class="sync-modal-value${res.errors ? ' err' : ''}">${(res.errors||0).toLocaleString()}</span>
        </div>
        <div class="sync-modal-row">
          <span class="sync-modal-label">Lookback window</span>
          <span class="sync-modal-value">${res.lookback_days} days</span>
        </div>
        ${res.most_traded_ticker ? `
        <div class="sync-modal-row">
          <span class="sync-modal-label">Most traded ticker</span>
          <span class="sync-modal-value highlight">${esc(res.most_traded_ticker)}</span>
        </div>` : ''}
        <div class="sync-modal-row">
          <span class="sync-modal-label">Synced at</span>
          <span class="sync-modal-value">${lastSyncStr}</span>
        </div>`;

      loadHistory();
    }

    modal.style.display = 'flex';
  } catch (e) {
    alert('Sync error: ' + e.message);
  } finally {
    btn.disabled = false;
    icon.classList.remove('ip-spin');
    _loadHistoryLastSync();
  }
}

// ── Realized G/L (paginated) ──────────────────────────────────────
const gainsState = { page:1, loaded:false };

async function loadGains(resetPage=true) {
  if (resetPage) gainsState.page = 1;
  const ticker = document.getElementById('g-ticker').value.trim();
  const term   = document.getElementById('g-term').value;
  const limit  = document.getElementById('g-limit').value;

  document.getElementById('g-loading').style.display='block';
  document.getElementById('g-table').style.display='none';
  document.getElementById('g-error').style.display='none';

  try {
    const params = new URLSearchParams({
      page: gainsState.page, limit, ticker, term
    });
    const res = await fetch('/api/realized_gains?'+params).then(r=>r.json());
    if (res.error) throw new Error(res.error);
    gainsState.loaded = true;

    document.getElementById('g-count').textContent =
      `${res.total.toLocaleString()} total closed positions`;
    const gImp = document.getElementById('g-last-import');
    if (gImp) gImp.textContent = formatRgLastImport(res.last_imported_at);

    document.getElementById('g-tbody').innerHTML = res.data.map(r => {
      const wsTag = r.wash_sale ? '<span class="badge badge-ws">WS</span>' : '—';
      return `<tr>
        <td>${r.closed_date}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;color:#94a3b8"
            title="${esc(r.symbol)}">${esc(r.symbol)}</td>
        <td><b>${esc(r.underlying||'')}</b></td>
        <td>${r.is_option?`<span class="badge badge-${r.option_type||'option'}">${r.option_type||'OPT'}</span>`
                         :'<span class="badge badge-equity">equity</span>'}</td>
        <td>${r.quantity!=null?fmt(r.quantity,0):'—'}</td>
        <td>${r.proceeds!=null?'$'+fmt(r.proceeds):'—'}</td>
        <td>${r.cost_basis!=null?'$'+fmt(r.cost_basis):'—'}</td>
        <td class="${cls(r.total_gl_amt)}">${r.total_gl_amt!=null?'$'+fmtD(r.total_gl_amt):'—'}</td>
        <td class="${cls(r.total_gl_pct)}">${r.total_gl_pct!=null?fmtD(r.total_gl_pct,1)+'%':'—'}</td>
        <td class="${cls(r.lt_gl_amt)}">${r.lt_gl_amt!=null?'$'+fmtD(r.lt_gl_amt):'—'}</td>
        <td class="${cls(r.st_gl_amt)}">${r.st_gl_amt!=null?'$'+fmtD(r.st_gl_amt):'—'}</td>
        <td>${wsTag}</td>
        <td class="neg">${r.disallowed_loss!=null&&r.disallowed_loss!=0?'$'+fmt(Math.abs(r.disallowed_loss)):'—'}</td>
      </tr>`;
    }).join('');

    document.getElementById('g-loading').style.display='none';
    document.getElementById('g-table').style.display='block';
    renderPagination('g-pagination', res, loadGains);
  } catch(e) {
    document.getElementById('g-loading').style.display='none';
    document.getElementById('g-error').style.display='block';
    document.getElementById('g-error').textContent='Error: '+e.message;
  }
}

// ── Pagination helper ─────────────────────────────────────────────
const _paginationRegistry = {};
function renderPagination(containerId, res, loadFn, stateObj, fnName) {
  // Backwards-compat: old callers pass loadFn without stateObj/fnName
  if (!stateObj) {
    stateObj = loadFn === loadHistory ? historyState : gainsState;
    fnName   = loadFn === loadHistory ? 'loadHistory' : 'loadGains';
  }
  const { page, pages, total, limit } = res;
  const start = (page-1)*limit+1, end = Math.min(page*limit, total);
  const el = document.getElementById(containerId);
  if (pages <= 1) { el.innerHTML=''; return; }

  const uid = containerId;
  _paginationRegistry[uid] = { state: stateObj, fn: loadFn };

  let btns = '';
  const addBtn = (p, label, active, disabled) =>
    `<button class="pg-btn${active?' active':''}" ${disabled?'disabled':''} onclick="
      _paginationRegistry['${uid}'].state.page=${p};
      _paginationRegistry['${uid}'].fn(false)">${label}</button>`;

  btns += addBtn(page-1,'‹ Prev', false, page===1);
  const lo=Math.max(1,page-3), hi=Math.min(pages,page+3);
  if (lo>1) btns += addBtn(1,'1',false,false) + (lo>2?'<span class="pg-info">…</span>':'');
  for (let p=lo;p<=hi;p++) btns += addBtn(p,p,p===page,false);
  if (hi<pages) btns += (hi<pages-1?'<span class="pg-info">…</span>':'') + addBtn(pages,pages,false,false);
  btns += addBtn(page+1,'Next ›',false,page===pages);

  el.innerHTML = `<span class="pg-info">${start}–${end} of ${total.toLocaleString()}</span>` + btns;
}

// ── Income P&L ────────────────────────────────────────────────────
const incomePnlState = { page: 1, loaded: false };
const _ipExpanded = new Set();
let _ipCardFilter = null;   // 'win' | 'perfect' | 'closed' | 'open' | 'assigned' | null
const _ipRecoveryCache = {};  // ticker -> {assignments: [...]}
let incomePnlSort = { key: 'open_date', dir: 'desc' };

function _ipFormatStrike(s) {
  if (s == null || s === '') return '0';
  const n = Number(s);
  if (Number.isNaN(n)) return String(s);
  return n.toFixed(2).replace(/\.?0+$/, '');
}

/** Human-readable leg close state (API uses title case / long labels). */
function _ipLegCloseLabel(closeAction) {
  if (closeAction == null || closeAction === '') return 'open';
  if (closeAction === 'Expired') return 'expired';
  if (closeAction === 'Exchange or Exercise') return 'exercised';
  return closeAction;
}

/** Short (sold) leg strike for score denominator; else first leg. */
function _incomeScoreStrike(legs) {
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
function _incomeEfficiencyScore(t) {
  if (!t || t.status === 'open') return null;
  if (t.net_pnl == null) return null;
  const strike = _incomeScoreStrike(t.legs || []);
  if (strike == null) return null;
  const days = t.days_held != null && t.days_held > 0 ? t.days_held : 1;
  return (t.net_pnl / days) * (100 / strike);
}

function setIncomePnlSort(key) {
  if (incomePnlSort.key === key) {
    incomePnlSort.dir = incomePnlSort.dir === 'desc' ? 'asc' : 'desc';
  } else {
    incomePnlSort.key = key;
    incomePnlSort.dir = 'desc';
  }
  _updateIpSortArrows();
  loadIncomeTrades(true);
}

function _updateIpSortArrows() {
  const keys = ['open_date', 'close_date', 'recovery', 'recovery_pnl', 'net_pnl', 'days_held', 'net_premium'];
  for (const k of keys) {
    const el = document.getElementById('ip-sa-' + k);
    if (!el) continue;
    el.textContent = incomePnlSort.key === k ? (incomePnlSort.dir === 'desc' ? '▼' : '▲') : '';
  }
}

async function _fetchRecovery(ticker) {
  if (_ipRecoveryCache[ticker]) return _ipRecoveryCache[ticker];
  try {
    const res = await fetch('/api/income/recovery?ticker=' + encodeURIComponent(ticker)).then(r => r.json());
    if (!res.error) _ipRecoveryCache[ticker] = res;
    return res;
  } catch (e) { console.error('Recovery fetch error:', e); return null; }
}

function _getRecoveryForTrade(ticker, tradeId) {
  const cached = _ipRecoveryCache[ticker];
  if (!cached) return null;
  return (cached.assignments || []).find(a => a.trade_id === tradeId) || null;
}

function _ipOnStatusChange() {
  _ipCardFilter = null;
  document.querySelectorAll('.ip-kpi-clickable').forEach(e => e.classList.remove('active'));
  const st = document.getElementById('ip-status').value;
  if (st === 'assigned') {
    incomePnlSort = { key: 'close_date', dir: 'desc' };
  }
  loadIncomeStats();
  loadIncomeTrades();
}

function setIpCardFilter(filter) {
  if (_ipCardFilter === filter) {
    _ipCardFilter = null;   // toggle off
  } else {
    _ipCardFilter = filter;
    if (filter === 'assigned') {
      incomePnlSort = { key: 'close_date', dir: 'desc' };
    }
  }
  // Update card active states
  ['win', 'perfect', 'closed', 'open', 'assigned'].forEach(f => {
    const el = document.getElementById('ip-kpi-card-' + f);
    if (el) el.classList.toggle('active', _ipCardFilter === f);
  });
  loadIncomeStats();
  loadIncomeTrades(true);
}

async function loadIncomeStats() {
  const ticker = document.getElementById('ip-ticker').value.trim().toUpperCase();
  const status = document.getElementById('ip-status').value;
  const strategy = document.getElementById('ip-strategy').value;
  try {
    const params = new URLSearchParams();
    if (ticker) params.set('ticker', ticker);
    if (status) params.set('status', status);
    if (strategy) params.set('strategy', strategy);
    if (_ipCardFilter) params.set('outcome', _ipCardFilter);
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

async function loadIncomeTrades(resetPage = true) {
  if (resetPage) incomePnlState.page = 1;
  const ticker   = document.getElementById('ip-ticker').value.trim().toUpperCase();
  const status   = document.getElementById('ip-status').value;
  const strategy = document.getElementById('ip-strategy').value;
  const limit    = parseInt(document.getElementById('ip-limit').value) || 25;

  document.getElementById('ip-loading').style.display = '';
  document.getElementById('ip-table').style.display = 'none';
  document.getElementById('ip-error').style.display = 'none';

  try {
    const params = new URLSearchParams({
      page: incomePnlState.page, limit, ticker, status, strategy,
      sort_by: incomePnlSort.key, sort_dir: incomePnlSort.dir
    });
    if (_ipCardFilter) params.set('outcome', _ipCardFilter);
    const res = await fetch('/api/income/trades?' + params).then(r => r.json());
    if (res.error) throw new Error(res.error);

    incomePnlState.loaded = true;
    document.getElementById('ip-loading').style.display = 'none';
    document.getElementById('ip-table').style.display = '';
    document.getElementById('ip-count').textContent = res.total + ' trades';

    const tickersNeedRec = [...new Set((res.data || []).filter(t => t.status === 'assigned').map(t => t.underlying))];
    await Promise.all(tickersNeedRec.map(u => _fetchRecovery(u)));

    _renderIncomeTrades(res.data);
    _updateIpSortArrows();
    renderPagination('ip-pagination', res, loadIncomeTrades, incomePnlState);
  } catch (e) {
    document.getElementById('ip-loading').style.display = 'none';
    document.getElementById('ip-error').textContent = 'Error: ' + e.message;
    document.getElementById('ip-error').style.display = '';
  }
}

function _renderIncomeTrades(trades) {
  const tbody = document.getElementById('ip-tbody');
  let html = '';

  for (const t of trades) {
    const legs = t.legs || [];
    const hasLegs = legs.length > 1 || legs.length === 1;
    const isExpanded = _ipExpanded.has(t.id);

    const legsSummary = legs.map(l => {
      const dir = l.direction === 'short' ? 'STO' : 'BTO';
      return dir + ' $' + _ipFormatStrike(l.strike) + (l.leg_type === 'PUT' ? 'P' : 'C');
    }).join(' / ');

    const stratBadge = _ipStratBadge(t.strategy);
    const statusBadge = _ipStatusBadge(t.status);
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

async function toggleIncomeTrade(id, ticker, status) {
  if (_ipExpanded.has(id)) {
    _ipExpanded.delete(id);
  } else {
    _ipExpanded.add(id);
    if (status === 'assigned' && ticker && !_ipRecoveryCache[ticker]) {
      await _fetchRecovery(ticker);
    }
  }
  loadIncomeTrades(false);
}

async function dismissRecovery(tradeId, ticker, remainingQty) {
  if (!confirm('Write off the remaining ' + remainingQty + ' shares? This marks the recovery as complete.')) return;
  try {
    const res = await fetch('/api/income/recovery/' + tradeId + '/dismiss', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({qty: remainingQty})
    }).then(r => r.json());
    if (res.error) { alert('Error: ' + res.error); return; }
    delete _ipRecoveryCache[ticker];
    await _fetchRecovery(ticker);
    loadIncomeStats();
    loadIncomeTrades(false);
  } catch (e) { alert('Error: ' + e.message); }
}

function _ipStratBadge(strategy) {
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

function _ipStatusBadge(status) {
  const map = {
    open: 'ip-status-open',
    closed: 'ip-status-closed',
    expired: 'ip-status-expired',
    assigned: 'ip-status-assigned',
  };
  return `<span class="ip-badge ${map[status] || ''}">${status}</span>`;
}

function _ipOutcomeBadge(t) {
  if (t.status === 'open') return '<span class="ip-badge ip-outcome-open">—</span>';
  if (t.is_perfect_win) return '<span class="ip-badge ip-outcome-perfect">Perfect</span>';
  if (t.is_win) return '<span class="ip-badge ip-outcome-win">Win</span>';
  return '<span class="ip-badge ip-outcome-loss">Loss</span>';
}

function _renderRecoverySection(rec, ticker) {
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

async function syncIncome() {
  const btn = document.getElementById('ip-sync-btn');
  const icon = document.getElementById('ip-sync-icon');
  btn.disabled = true;
  icon.classList.add('ip-spin');
  document.getElementById('ip-last-sync').textContent = 'Syncing…';

  try {
    const res = await fetch('/api/income/sync', { method: 'POST' }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    Object.keys(_ipRecoveryCache).forEach(k => delete _ipRecoveryCache[k]);
    loadIncomeStats();
    loadIncomeTrades();
  } catch (e) {
    alert('Sync error: ' + e.message);
  } finally {
    btn.disabled = false;
    icon.classList.remove('ip-spin');
  }
}

// ── Trade form ─────────────────────────────────────────────────────
let tradeMode = 'equity';
let _pendingOrder = null;
let _tradeTicker = '';
let _tradeQuoteData = null;

function setTradeMode(mode) {
  tradeMode = mode;
  document.getElementById('btn-equity').classList.toggle('active', mode === 'equity');
  document.getElementById('btn-option').classList.toggle('active', mode === 'option');
  document.getElementById('form-equity').style.display = mode === 'equity' ? '' : 'none';
  document.getElementById('form-option').style.display = mode === 'option' ? '' : 'none';
  document.getElementById('trade-result').innerHTML = '';
  document.getElementById('trade-chain-wrap').style.display = mode === 'option' ? '' : 'none';
  syncTradeTicker();
  if (mode === 'option' && _tradeTicker) loadOptionExpirations(_tradeTicker);
}

function syncTradeTicker() {
  const eqEl  = document.getElementById('eq-ticker');
  const optEl = document.getElementById('opt-underlying');
  if (tradeMode === 'equity') {
    const v = eqEl.value.trim().toUpperCase();
    if (v) optEl.value = v;
  } else {
    const v = optEl.value.trim().toUpperCase();
    if (v) eqEl.value = v;
  }
}

function resetTradeQuotePlaceholder() {
  document.getElementById('trade-quote-card').innerHTML =
    '<div style="color:#475569;font-size:12px;padding:12px 0">Enter a ticker to see live quote</div>';
}

function resetTradeChartPlaceholder() {
  document.getElementById('trade-chart-wrap').innerHTML =
    '<div style="color:#475569;font-size:12px;padding:40px 0;text-align:center">Chart loads when a ticker is entered</div>';
}

function clearPositionPanel(panelId) {
  const el = document.getElementById(panelId);
  if (!el) return;
  el.style.display = 'none';
  el.innerHTML = '';
}

function _parsePosOptDesc(desc) {
  // Parse Schwab option description like "LYFT INC 04/02/2026 $13 Put"
  // Returns { type: 'PUT'|'CALL', expiry: 'Apr 02 \'26', strike: '13.00' } or null
  const m = desc && desc.match(/(\d{2})\/(\d{2})\/(\d{4})\s+\$([0-9.]+)\s+(Put|Call)/i);
  if (!m) return null;
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const mon = months[parseInt(m[1], 10) - 1] || m[1];
  const yy = m[3].slice(2);
  return {
    type: m[5].toUpperCase(),
    expiry: mon + ' ' + parseInt(m[2], 10) + " '" + yy,
    strike: Number(m[4]).toFixed(2),
  };
}

async function loadPositionSummaryForTicker(ticker, panelId) {
  const el = document.getElementById(panelId);
  if (!el) return;
  const t = ticker.trim().toUpperCase();
  el.style.display = '';
  el.innerHTML = '<div class="pos-sum-panel-loading">Loading holdings…</div>';
  try {
    const rows = await fetch('/api/positions').then(r => r.json());
    if (!Array.isArray(rows)) {
      if (rows && rows.error) throw new Error(rows.error);
      throw new Error('Unexpected positions response');
    }
    const reOpt = new RegExp('^' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s');

    const equity  = rows.filter(p =>
      ['EQUITY', 'ETF', 'COLLECTIVE_INVESTMENT'].includes(p.asset_type) && p.symbol === t);
    const options = rows.filter(p => p.asset_type === 'OPTION' && reOpt.test(p.symbol));

    const allPos = [...equity, ...options].filter(p => p.quantity);
    if (!allPos.length) {
      el.innerHTML =
        '<div class="pos-sum-panel-header">Holdings</div>' +
        '<div class="pos-sum-none">No open position for ' + esc(t) + '</div>';
      return;
    }

    let tableRows = '';
    for (const p of equity) {
      if (!p.quantity) continue;
      const isLong = p.quantity > 0;
      const q = Math.abs(p.quantity).toLocaleString();
      const avg = p.avg_price != null ? '$' + Number(p.avg_price).toFixed(2) : '—';
      const mv  = p.market_value != null
        ? '$' + Number(p.market_value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : '—';
      const sideCls = isLong ? 'pos-badge-long' : 'pos-badge-short';
      tableRows +=
        '<tr>' +
        '<td><span class="pos-badge pos-badge-stock">STOCK</span></td>' +
        '<td><span class="pos-badge ' + sideCls + '">' + (isLong ? 'Long' : 'Short') + '</span></td>' +
        '<td class="pos-num">' + q + '</td>' +
        '<td class="pos-detail">@ ' + avg + ' avg</td>' +
        '<td class="pos-mkt">' + mv + '</td>' +
        '</tr>';
    }
    for (const p of options) {
      if (!p.quantity) continue;
      const isLong = p.quantity > 0;
      const q = Math.abs(p.quantity);
      const mv = p.market_value != null
        ? '$' + Number(p.market_value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : '—';
      const parsed = _parsePosOptDesc(p.description || '');
      const typeCls = parsed
        ? (parsed.type === 'CALL' ? 'pos-badge-call' : 'pos-badge-put')
        : 'pos-badge-opt';
      const typeLabel = parsed ? parsed.type : 'OPT';
      const detail = parsed ? parsed.expiry + '  $' + parsed.strike : esc(p.description || p.symbol);
      const sideCls = isLong ? 'pos-badge-long' : 'pos-badge-short';
      tableRows +=
        '<tr>' +
        '<td><span class="pos-badge ' + typeCls + '">' + typeLabel + '</span></td>' +
        '<td><span class="pos-badge ' + sideCls + '">' + (isLong ? 'Long' : 'Short') + '</span></td>' +
        '<td class="pos-num">' + q + '</td>' +
        '<td class="pos-detail">' + detail + '</td>' +
        '<td class="pos-mkt">' + mv + '</td>' +
        '</tr>';
    }

    el.innerHTML =
      '<div class="pos-sum-panel-header">Holdings</div>' +
      '<div class="pos-sum-table-wrap">' +
        '<table class="pos-sum-table">' +
          '<thead><tr>' +
            '<th>Type</th><th>Side</th><th>Qty</th><th>Detail</th><th>Mkt Value</th>' +
          '</tr></thead>' +
          '<tbody>' + tableRows + '</tbody>' +
        '</table>' +
      '</div>';
  } catch (e) {
    el.innerHTML = '<div class="pos-sum-err">Position error: ' + esc(e.message) + '</div>';
  }
}

function onTradeTickerChange() {
  syncTradeTicker();
  const ticker = (tradeMode === 'equity'
    ? document.getElementById('eq-ticker').value
    : document.getElementById('opt-underlying').value).trim().toUpperCase();
  if (!ticker) {
    _tradeTicker = '';
    clearPositionPanel('trade-position-panel');
    resetTradeQuotePlaceholder();
    resetTradeChartPlaceholder();
    return;
  }
  const changed = ticker !== _tradeTicker;
  _tradeTicker = ticker;
  loadTradeQuote(ticker);
  loadPositionSummaryForTicker(ticker, 'trade-position-panel');
  if (changed) loadTradingViewChart(ticker);
  if (tradeMode === 'option' && changed) loadOptionExpirations(ticker);
}

function updateEqFields() {
  const ot = document.getElementById('eq-order-type').value;
  document.getElementById('eq-price-group').style.display = ['limit','stop_limit'].includes(ot) ? '' : 'none';
  document.getElementById('eq-stop-group').style.display  = ['stop','stop_limit'].includes(ot)  ? '' : 'none';
}

function updateOptFields() {
  const ot = document.getElementById('opt-order-type').value;
  document.getElementById('opt-price-group').style.display = ot === 'limit' ? '' : 'none';
}

// ── Live quote card ───────────────────────────────────────────────
async function loadTradeQuote(ticker) {
  const card = document.getElementById('trade-quote-card');
  card.innerHTML = '<div style="color:#64748b;font-size:12px;padding:8px 0">Loading quote for ' + esc(ticker) + '…</div>';
  try {
    const data = await fetch('/api/quote/' + encodeURIComponent(ticker)).then(r => r.json());
    if (data.error) throw new Error(data.error);
    _tradeQuoteData = data;
    const chgCls = (data.change || 0) >= 0 ? 'pos' : 'neg';
    const spread = (data.bid != null && data.ask != null) ? (data.ask - data.bid).toFixed(2) : '—';
    card.innerHTML =
      '<div class="tqc-header">' +
        '<span class="tqc-sym">' + esc(data.symbol) + '</span>' +
        '<span class="tqc-desc">' + esc(data.description) + '</span>' +
      '</div>' +
      '<div class="tqc-price"><span class="' + chgCls + '">' + fmt(data.last) + '</span> ' +
        '<span class="' + chgCls + '" style="font-size:14px">' + fmtD(data.change) + ' (' + fmtD(data.change_pct) + '%)</span>' +
      '</div>' +
      '<div class="tqc-grid">' +
        '<div><label>Bid</label><div class="val">' + fmt(data.bid) + '</div></div>' +
        '<div><label>Ask</label><div class="val">' + fmt(data.ask) + '</div></div>' +
        '<div><label>Spread</label><div class="val">' + spread + '</div></div>' +
        '<div><label>Volume</label><div class="val">' + (data.volume != null ? Number(data.volume).toLocaleString() : '—') + '</div></div>' +
        '<div><label>52W High</label><div class="val">' + fmt(data['52w_high']) + '</div></div>' +
        '<div><label>52W Low</label><div class="val">' + fmt(data['52w_low']) + '</div></div>' +
      '</div>';

    // Pre-fill equity limit price if empty
    const eqPrice = document.getElementById('eq-price');
    if (!eqPrice.value && data.last) eqPrice.value = Number(data.last).toFixed(2);
  } catch(e) {
    card.innerHTML = '<div style="color:#f87171;font-size:12px;padding:8px 0">Quote error: ' + esc(e.message) + '</div>';
  }
}

// ── TradingView chart ─────────────────────────────────────────────
function tvExchangeForTicker(ticker) {
  const u = ticker.toUpperCase();
  if (/^(SPY|QQQ|DIA|IWM|TQQQ|SQQQ|GLD|SLV|TLT|VXX|UVXY|IEFA|EFA|EEM|HYG|LQD)$/i.test(ticker)) return 'AMEX';
  const nyse = /^(JPM|BAC|WFC|C|GS|MS|XOM|CVX|COP|DIS|NKE|KO|MCD|WMT|T|VZ|PFE|JNJ|UNH|HD|LOW|CAT|DE|BA|LMT|GE|MMM|IBM|ORCL|CSCO|INTC|AMD|XLE|XLF|XLV|XLI|XLP|XLU|XLK|XLB|XLRE|XLC)$/i;
  if (nyse.test(ticker)) return 'NYSE';
  return 'NASDAQ';
}

function loadTradingViewChart(ticker) {
  const wrap = document.getElementById('trade-chart-wrap');
  wrap.innerHTML = '';
  const exchange = tvExchangeForTicker(ticker);
  const sym = exchange + ':' + ticker.toUpperCase();
  const symSlug = sym.replace(':', '-');

  const container = document.createElement('div');
  container.className = 'tradingview-widget-container';
  container.style.height = '100%';
  container.style.width = '100%';

  const widgetDiv = document.createElement('div');
  widgetDiv.className = 'tradingview-widget-container__widget';
  widgetDiv.style.height = 'calc(100% - 28px)';
  widgetDiv.style.width = '100%';
  container.appendChild(widgetDiv);

  const copyright = document.createElement('div');
  copyright.className = 'tradingview-widget-copyright';
  copyright.style.padding = '2px 8px';
  copyright.style.fontSize = '10px';
  const a = document.createElement('a');
  a.href = 'https://www.tradingview.com/symbols/' + symSlug + '/';
  a.rel = 'noopener nofollow';
  a.target = '_blank';
  a.style.color = '#64748b';
  a.style.textDecoration = 'none';
  a.textContent = ticker.toUpperCase() + ' chart by TradingView';
  copyright.appendChild(a);
  container.appendChild(copyright);

  const script = document.createElement('script');
  script.type = 'text/javascript';
  script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
  script.async = true;
  script.textContent = JSON.stringify({
    autosize: true,
    symbol: sym,
    interval: 'D',
    timezone: 'America/New_York',
    theme: 'dark',
    style: '1',
    locale: 'en',
    backgroundColor: '#131621',
    gridColor: '#1e2235',
    allow_symbol_change: true,
    calendar: false,
    hide_top_toolbar: false,
    hide_side_toolbar: true,
    studies: ['Volume@tv-basicstudies'],
    support_host: 'https://www.tradingview.com',
  });
  container.appendChild(script);
  wrap.appendChild(container);
}

// ── Option expirations ────────────────────────────────────────────
async function loadOptionExpirations(ticker) {
  const sel = document.getElementById('chain-expiry');
  sel.innerHTML = '<option value="">Loading expirations…</option>';
  try {
    const data = await fetch('/api/option-expirations/' + encodeURIComponent(ticker)).then(r => r.json());
    if (data.error) throw new Error(data.error);
    if (!data.expirations || !data.expirations.length) {
      sel.innerHTML = '<option value="">No expirations found</option>';
      return;
    }
    sel.innerHTML = '<option value="">Select expiration (' + data.expirations.length + ' available)</option>' +
      data.expirations.map(d => '<option value="' + d + '">' + d + '</option>').join('');
  } catch(e) {
    sel.innerHTML = '<option value="">Error loading expirations</option>';
  }
}

// ── Option chain ──────────────────────────────────────────────────
let _chainData = null;

async function loadOptionChain() {
  const expiry = document.getElementById('chain-expiry').value;
  const content = document.getElementById('chain-content');
  const status  = document.getElementById('chain-status');
  if (!expiry || !_tradeTicker) {
    content.innerHTML = '<div style="color:#475569;font-size:12px;padding:8px 0">Select an expiration to view the option chain</div>';
    return;
  }
  status.textContent = 'Loading…';
  content.innerHTML = '<div class="loading" style="padding:16px">Loading option chain…</div>';
  try {
    const data = await fetch('/api/option-chain?symbol=' + encodeURIComponent(_tradeTicker) +
      '&from_date=' + expiry + '&to_date=' + expiry + '&strike_count=20').then(r => r.json());
    if (data.error) throw new Error(data.error);
    _chainData = data;

    const calls = data.calls[expiry] || [];
    const puts  = data.puts[expiry]  || [];
    const strikes = [...new Set([...calls.map(c => c.strike), ...puts.map(p => p.strike)])].sort((a,b) => a - b);
    const callMap = {}; calls.forEach(c => callMap[c.strike] = c);
    const putMap  = {}; puts.forEach(p => putMap[p.strike] = p);

    if (!strikes.length) {
      content.innerHTML = '<div style="color:#475569;font-size:12px;padding:8px 0">No contracts found for this expiration</div>';
      status.textContent = '';
      return;
    }

    const fmtOpt = v => v == null ? '—' : Number(v).toFixed(2);
    const fmtVol = v => v == null ? '—' : Number(v).toLocaleString();

    let rows = strikes.map(strike => {
      const c = callMap[strike] || {};
      const p = putMap[strike]  || {};
      const cItm = c.itm ? ' chain-td-itm' : '';
      const pItm = p.itm ? ' chain-td-itm' : '';
      return '<tr class="chain-row">' +
        '<td class="chain-td-call' + cItm + '" data-side="CALL" data-strike="' + strike + '">' + fmtOpt(c.bid) + '</td>' +
        '<td class="chain-td-call' + cItm + '" data-side="CALL" data-strike="' + strike + '">' + fmtOpt(c.ask) + '</td>' +
        '<td class="chain-td-call' + cItm + '" data-side="CALL" data-strike="' + strike + '">' + fmtOpt(c.last) + '</td>' +
        '<td class="chain-td-call' + cItm + '" data-side="CALL" data-strike="' + strike + '">' + fmtVol(c.volume) + '</td>' +
        '<td class="chain-td-call' + cItm + '" data-side="CALL" data-strike="' + strike + '">' + fmtVol(c.oi) + '</td>' +
        '<td class="chain-td-strike">' + fmtOpt(strike) + '</td>' +
        '<td class="chain-td-put' + pItm + '" data-side="PUT" data-strike="' + strike + '">' + fmtOpt(p.bid) + '</td>' +
        '<td class="chain-td-put' + pItm + '" data-side="PUT" data-strike="' + strike + '">' + fmtOpt(p.ask) + '</td>' +
        '<td class="chain-td-put' + pItm + '" data-side="PUT" data-strike="' + strike + '">' + fmtOpt(p.last) + '</td>' +
        '<td class="chain-td-put' + pItm + '" data-side="PUT" data-strike="' + strike + '">' + fmtVol(p.volume) + '</td>' +
        '<td class="chain-td-put' + pItm + '" data-side="PUT" data-strike="' + strike + '">' + fmtVol(p.oi) + '</td>' +
      '</tr>';
    }).join('');

    content.innerHTML =
      '<table class="chain-table" onclick="onChainClick(event)">' +
      '<thead><tr>' +
        '<th class="chain-th-calls" colspan="5">CALLS</th>' +
        '<th class="chain-th-strike">STRIKE</th>' +
        '<th class="chain-th-puts" colspan="5">PUTS</th>' +
      '</tr><tr>' +
        '<th class="chain-th-calls">Bid</th><th class="chain-th-calls">Ask</th><th class="chain-th-calls">Last</th>' +
        '<th class="chain-th-calls">Vol</th><th class="chain-th-calls">OI</th>' +
        '<th class="chain-th-strike"></th>' +
        '<th class="chain-th-puts">Bid</th><th class="chain-th-puts">Ask</th><th class="chain-th-puts">Last</th>' +
        '<th class="chain-th-puts">Vol</th><th class="chain-th-puts">OI</th>' +
      '</tr></thead>' +
      '<tbody>' + rows + '</tbody></table>' +
      '<div class="chain-click-hint">Click any call or put row to fill the option order form</div>';
    status.textContent = strikes.length + ' strikes';
  } catch(e) {
    content.innerHTML = '<div style="color:#f87171;font-size:12px;padding:8px 0">Error: ' + esc(e.message) + '</div>';
    status.textContent = '';
  }
}

function onChainClick(e) {
  const td = e.target.closest('td[data-side]');
  if (!td) return;
  const side   = td.getAttribute('data-side');
  const strike = parseFloat(td.getAttribute('data-strike'));
  const expiry = document.getElementById('chain-expiry').value;
  if (!side || !strike || !expiry || !_chainData) return;

  const contracts = side === 'CALL' ? (_chainData.calls[expiry] || []) : (_chainData.puts[expiry] || []);
  const contract = contracts.find(c => c.strike === strike);
  const mid = contract && contract.bid != null && contract.ask != null
    ? ((contract.bid + contract.ask) / 2).toFixed(2) : '';

  document.getElementById('opt-type').value    = side;
  document.getElementById('opt-expiry').value   = expiry;
  document.getElementById('opt-strike').value   = strike;
  document.getElementById('opt-price').value    = mid;
  document.getElementById('opt-order-type').value = 'limit';
  updateOptFields();

  document.getElementById('form-option').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Order preview & submit (unchanged logic, cleaned up) ──────────
function previewOrder(type) {
  let payload, summary;

  if (type === 'equity') {
    const ticker = document.getElementById('eq-ticker').value.trim().toUpperCase();
    const action = document.getElementById('eq-action').value;
    const qty    = document.getElementById('eq-qty').value;
    const ot     = document.getElementById('eq-order-type').value;
    const price  = document.getElementById('eq-price').value;
    const stop   = document.getElementById('eq-stop').value;

    if (!ticker) { showTradeError('Ticker is required.'); return; }
    if (!qty || qty <= 0) { showTradeError('Quantity must be a positive number.'); return; }
    if (['limit','stop_limit'].includes(ot) && !price) { showTradeError('Limit price is required.'); return; }
    if (['stop','stop_limit'].includes(ot) && !stop)   { showTradeError('Stop price is required.'); return; }

    const dur = document.getElementById('eq-duration').value;
    const ses = document.getElementById('eq-session').value;

    payload = { trade_type:'equity', symbol:ticker, action, quantity:qty, order_type:ot, duration:dur, session:ses };
    if (price && ['limit','stop_limit'].includes(ot)) payload.price = price;
    if (stop  && ['stop','stop_limit'].includes(ot))  payload.stop_price = stop;

    const aLabel = { buy:'Buy', sell:'Sell', sell_short:'Sell Short', buy_to_cover:'Buy to Cover' }[action];
    const pLabel = ot === 'market' ? 'at Market' : ot === 'stop' ? `Stop @$${stop}` :
                   ot === 'stop_limit' ? `Stop $${stop} / Limit $${price}` : `@$${price}`;
    const durLabel = dur === 'gtc' ? 'GTC' : 'Day';
    const sesLabel = {normal:'Normal', seamless:'Extended Hrs', am:'Pre-Market', pm:'Post-Market'}[ses];
    summary = `<b>${aLabel}</b> ${qty} shares of <b>${ticker}</b> — ${ot.replace('_',' ').toUpperCase()} ${pLabel} · ${durLabel} · ${sesLabel}`;

  } else {
    const underlying = document.getElementById('opt-underlying').value.trim().toUpperCase();
    const optType    = document.getElementById('opt-type').value;
    const action     = document.getElementById('opt-action').value;
    const expiry     = document.getElementById('opt-expiry').value;
    const strike     = document.getElementById('opt-strike').value;
    const contracts  = document.getElementById('opt-contracts').value;
    const ot         = document.getElementById('opt-order-type').value;
    const price      = document.getElementById('opt-price').value;

    if (!underlying)      { showTradeError('Underlying ticker is required.'); return; }
    if (!expiry)          { showTradeError('Expiration date is required.'); return; }
    if (!strike || strike <= 0) { showTradeError('Strike price is required.'); return; }
    if (!contracts || contracts < 1) { showTradeError('Contract count must be ≥ 1.'); return; }
    if (ot === 'limit' && !price) { showTradeError('Limit price is required.'); return; }

    const dur = document.getElementById('opt-duration').value;
    const ses = document.getElementById('opt-session').value;

    payload = { trade_type:'option', underlying, option_type:optType, action,
                expiry, strike, contracts, order_type:ot, duration:dur, session:ses };
    if (price && ot === 'limit') payload.price = price;

    const aLabel = { buy_to_open:'Buy to Open', sell_to_open:'Sell to Open',
                     buy_to_close:'Buy to Close', sell_to_close:'Sell to Close' }[action];
    const pLabel = ot === 'market' ? 'at Market' : `@$${price}/share ($${(price*100).toFixed(0)}/contract)`;
    const badge  = optType === 'PUT'
      ? '<span class="badge badge-PUT">PUT</span>'
      : '<span class="badge badge-CALL">CALL</span>';
    const durLabel = dur === 'gtc' ? 'GTC' : 'Day';
    const sesLabel = {normal:'Normal', seamless:'Extended Hrs', am:'Pre-Market', pm:'Post-Market'}[ses];
    summary = `<b>${aLabel}</b> ${contracts} contract(s) — <b>${underlying}</b> ${expiry} $${strike} ${badge}<br>
               Order: ${ot.toUpperCase()} ${pLabel} · ${durLabel} · ${sesLabel}`;
  }

  _pendingOrder = payload;
  const div = document.getElementById('trade-result');
  div.innerHTML =
    '<div class="preview-box">' +
      '<div class="preview-title">Review Order Before Submitting</div>' +
      '<div class="preview-summary">' + summary + '</div>' +
      '<div style="font-size:11px;color:#64748b;margin-bottom:14px">' +
        'Please verify all details before confirming.' +
      '</div>' +
      '<div class="preview-actions">' +
        '<button class="cancel-btn" onclick="clearTradeResult()">Cancel</button>' +
        '<button class="confirm-btn" onclick="submitPendingOrder()">Confirm &amp; Submit</button>' +
      '</div>' +
    '</div>';
}

async function submitPendingOrder() {
  if (!_pendingOrder) return;
  const payload = _pendingOrder;
  _pendingOrder = null;
  const div = document.getElementById('trade-result');
  div.innerHTML = '<div class="loading">Submitting order to Schwab…</div>';
  try {
    const res = await fetch('/api/order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(r => r.json());

    if (res.error) throw new Error(res.error);
    div.innerHTML =
      '<div class="success-box">Order submitted successfully!<br>' +
      '<b>Order ID: ' + esc(res.order_id) + '</b><br>' +
      '<small style="color:#6ee7b7;opacity:0.7">Switch to the "Open Orders" tab to track status.</small>' +
      '</div>';
    ordersState.loaded = false;
  } catch(e) {
    div.innerHTML = '<div class="error" style="margin-top:0">Order failed: ' + esc(e.message) + '</div>';
  }
}

function clearTradeResult() {
  document.getElementById('trade-result').innerHTML = '';
  _pendingOrder = null;
}

function showTradeError(msg) {
  document.getElementById('trade-result').innerHTML =
    '<div class="error" style="margin-top:12px;padding:10px 14px;border-radius:6px">' + msg + '</div>';
}

// ── Ladder Trade ──────────────────────────────────────────────────
let ladderRungs = [{qty:'', price:''}, {qty:'', price:''}];
let _ladderSubmitting = false;

function renderRungs() {
  const tbody = document.getElementById('lad-rungs-tbody');
  tbody.innerHTML = ladderRungs.map((r, i) =>
    `<tr>
      <td style="color:#475569;width:30px;text-align:center">${i+1}</td>
      <td><input type="number" min="1" step="1" value="${r.qty}" placeholder="100"
           oninput="ladderRungs[${i}].qty=this.value; updateLadderSummary()"></td>
      <td><input type="number" min="0.01" step="0.01" value="${r.price}" placeholder="0.00"
           oninput="ladderRungs[${i}].price=this.value; updateLadderSummary()"></td>
      <td><button class="rung-del" onclick="removeRung(${i})" title="Remove">&times;</button></td>
    </tr>`
  ).join('');
  updateLadderSummary();
}

function addRung() {
  ladderRungs.push({qty:'', price:''});
  renderRungs();
}

function removeRung(i) {
  if (ladderRungs.length <= 1) return;
  ladderRungs.splice(i, 1);
  renderRungs();
}

function updateLadderSummary() {
  let totalQty = 0, totalValue = 0, valid = 0;
  ladderRungs.forEach(r => {
    const q = parseFloat(r.qty) || 0;
    const p = parseFloat(r.price) || 0;
    if (q > 0 && p > 0) { totalQty += q; totalValue += q * p; valid++; }
  });
  document.getElementById('lad-total-qty').textContent = fmt(totalQty, 0);
  document.getElementById('lad-total-value').textContent = '$' + fmt(totalValue);
  document.getElementById('lad-rung-count').textContent = valid;
}

function ladderEvenSplit() {
  const totalQty  = parseFloat(document.getElementById('lad-split-qty').value) || 0;
  const startPrice = parseFloat(document.getElementById('lad-split-start').value) || 0;
  const endPrice   = parseFloat(document.getElementById('lad-split-end').value) || 0;
  const numRungs   = parseInt(document.getElementById('lad-split-rungs').value) || 2;

  if (totalQty <= 0 || startPrice <= 0 || endPrice <= 0 || numRungs < 1) return;

  const perRung = Math.floor(totalQty / numRungs);
  const remainder = totalQty - perRung * numRungs;
  const step = numRungs > 1 ? (endPrice - startPrice) / (numRungs - 1) : 0;

  ladderRungs = [];
  for (let i = 0; i < numRungs; i++) {
    ladderRungs.push({
      qty: String(perRung + (i < remainder ? 1 : 0)),
      price: (startPrice + step * i).toFixed(2)
    });
  }
  renderRungs();
}

function ladderScaleUp() {
  const totalQty   = parseFloat(document.getElementById('lad-split-qty').value) || 0;
  const startPrice = parseFloat(document.getElementById('lad-split-start').value) || 0;
  const endPrice   = parseFloat(document.getElementById('lad-split-end').value) || 0;
  const numRungs   = parseInt(document.getElementById('lad-split-rungs').value) || 2;

  if (totalQty <= 0 || startPrice <= 0 || endPrice <= 0 || numRungs < 1) return;

  const weights = [];
  for (let i = 0; i < numRungs; i++) weights.push(i + 1);
  const wSum = weights.reduce((a, b) => a + b, 0);
  const step = numRungs > 1 ? (endPrice - startPrice) / (numRungs - 1) : 0;

  ladderRungs = [];
  let assigned = 0;
  for (let i = 0; i < numRungs; i++) {
    const q = i === numRungs - 1
      ? totalQty - assigned
      : Math.round(totalQty * weights[i] / wSum);
    assigned += q;
    ladderRungs.push({ qty: String(Math.max(1, q)), price: (startPrice + step * i).toFixed(2) });
  }
  renderRungs();
}

function ladderScaleDown() {
  const totalQty   = parseFloat(document.getElementById('lad-split-qty').value) || 0;
  const startPrice = parseFloat(document.getElementById('lad-split-start').value) || 0;
  const endPrice   = parseFloat(document.getElementById('lad-split-end').value) || 0;
  const numRungs   = parseInt(document.getElementById('lad-split-rungs').value) || 2;

  if (totalQty <= 0 || startPrice <= 0 || endPrice <= 0 || numRungs < 1) return;

  const weights = [];
  for (let i = 0; i < numRungs; i++) weights.push(numRungs - i);
  const wSum = weights.reduce((a, b) => a + b, 0);
  const step = numRungs > 1 ? (endPrice - startPrice) / (numRungs - 1) : 0;

  ladderRungs = [];
  let assigned = 0;
  for (let i = 0; i < numRungs; i++) {
    const q = i === numRungs - 1
      ? totalQty - assigned
      : Math.round(totalQty * weights[i] / wSum);
    assigned += q;
    ladderRungs.push({ qty: String(Math.max(1, q)), price: (startPrice + step * i).toFixed(2) });
  }
  renderRungs();
}

// ── Position Unwind Strategy ──────────────────────────────────────
let _lastSuggestTicker = '';

function onLadderTickerChange() {
  const lt = document.getElementById('lad-ticker').value.trim().toUpperCase();
  if (!lt) clearPositionPanel('lad-position-panel');
  else loadPositionSummaryForTicker(lt, 'lad-position-panel');

  ladderRecentState.page = 1;
  loadLadderRecent();
  loadLadderSuggest();
  loadLadderOrders();
}

async function loadLadderSuggest() {
  const ticker = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const banner = document.getElementById('lad-strat-banner');
  const status = document.getElementById('lad-strat-status');
  const params = document.getElementById('lad-strat-params');

  if (!ticker) {
    banner.innerHTML = '<div class="ladder-strategy-banner strat-dim">Enter a ticker to analyse trade history</div>';
    status.textContent = '';
    params.style.display = 'none';
    return;
  }

  _lastSuggestTicker = ticker;
  status.textContent = 'analysing…';
  banner.innerHTML = '';

  // Reset verify panel when ticker changes
  _verifyOpen = false;
  const verifyBtn = document.getElementById('lad-verify-btn');
  const verifyDiv = document.getElementById('lad-verify-result');
  if (verifyBtn) { verifyBtn.textContent = 'Verify vs API'; verifyBtn.style.display = 'none'; }
  if (verifyDiv) { verifyDiv.style.display = 'none'; verifyDiv.innerHTML = ''; }

  const ws   = document.getElementById('lad-p-window').value || 5;
  const sp   = document.getElementById('lad-p-sellpct').value || 25;
  const pc   = document.getElementById('lad-p-premium').value || 77;
  const ms   = document.getElementById('lad-p-minstreak').value || 10;
  const mr   = document.getElementById('lad-p-maxrungs').value || 5;

  try {
    const q = new URLSearchParams({
      ticker, window_size: ws, sell_pct: sp,
      premium_cents: pc, min_streak: ms, max_rungs: mr,
    });

    // Fetch suggestion + live quote in parallel
    const [data, quoteRes] = await Promise.all([
      fetch('/api/ladder-suggest?' + q).then(r => r.json()),
      fetch('/api/quote/' + ticker).then(r => r.json()).catch(() => null),
    ]);
    if (data.error) throw new Error(data.error);

    if (document.getElementById('lad-ticker').value.trim().toUpperCase() !== _lastSuggestTicker) return;

    // Resolve reference price: live quote > last DB trade
    const livePrice = quoteRes && quoteRes.last != null ? quoteRes.last : null;
    const refPrice = livePrice ?? data.last_trade_price ?? null;
    const priceSource = livePrice != null ? 'Market' : (refPrice != null ? 'Last trade' : null);

    if (verifyBtn) verifyBtn.style.display = '';

    if (data.note || !data.rungs || data.rungs.length === 0) {
      status.textContent = '';
      params.style.display = 'none';
      const priceLine = refPrice != null
        ? `<div style="margin-bottom:4px;font-size:12px">${priceSource} price: <span class="strat-highlight" style="font-size:14px;font-weight:700">$${fmt(refPrice)}</span></div>`
        : '';
      let streakLabel = data.streak_count
        ? `${data.streak_count} <span class="strat-highlight">${esc(data.direction || '?')}</span> trades`
        : 'No accumulation streak';
      if (data.exit_trades && data.exit_trades.length > 0) {
        const exitQty = data.exit_trades.reduce((s, e) => s + e.qty, 0);
        streakLabel += ` (${data.exit_trades.length} partial unwind, ${fmt(exitQty, 0)} shares)`;
      }
      banner.innerHTML = priceLine + `<div class="ladder-strategy-banner strat-dim">${streakLabel}`
        + (data.note ? ` — ${esc(data.note)}` : '') + '</div>';
      return;
    }

    // --- price check: how many rungs would fill immediately? ---------------
    const isLong = data.direction === 'Buy';
    let immediateCount = 0;
    if (refPrice != null) {
      for (const r of data.rungs) {
        // Sell limit below market → fills immediately; Buy limit above market → fills immediately
        if (isLong && refPrice >= r.price) immediateCount++;
        else if (!isLong && refPrice <= r.price) immediateCount++;
      }
    }

    const dir = isLong ? 'buys' : 'sells short';
    const actionLabel = data.unwind_action === 'sell' ? 'Sell' : 'Buy to Cover';
    status.textContent = '';
    params.style.display = 'flex';

    // --- build the price header line ---------------------------------------
    let priceHeader = '';
    if (refPrice != null) {
      priceHeader = `<div style="margin-bottom:6px;font-size:12px">` +
        `${priceSource} price: <span class="strat-highlight" style="font-size:14px;font-weight:700">$${fmt(refPrice)}</span>` +
        `</div>`;
    }

    // --- build warning if applicable --------------------------------------
    let warningHtml = '';
    if (immediateCount > 0 && immediateCount === data.rungs.length) {
      warningHtml =
        `<div class="strat-warning">` +
          `⚠️ ${priceSource} price ($${fmt(refPrice)}) is already more favorable than <b>all ${data.rungs.length}</b> suggested prices — ` +
          `every order would fill immediately at market. ` +
          `Use <b>Quick Fill</b> below to structure a ladder around current price levels instead.` +
        `</div>`;
    } else if (immediateCount > 0) {
      warningHtml =
        `<div class="strat-warning">` +
          `⚠️ ${priceSource} price ($${fmt(refPrice)}) is already more favorable than <b>${immediateCount} of ${data.rungs.length}</b> suggested prices — ` +
          `those orders would fill immediately. Consider adjusting or using Quick Fill for those shares.` +
        `</div>`;
    }

    // --- rung table (flag immediate rows) ---------------------------------
    const rungSummary = data.rungs.map((r, i) => {
      const isImmediate = refPrice != null && (
        (isLong && refPrice >= r.price) || (!isLong && refPrice <= r.price));
      const rowStyle = isImmediate ? ' style="opacity:0.45"' : '';
      const flag = isImmediate ? ' <span style="color:#fbbf24;font-size:9px">INSTANT</span>' : '';
      return `<tr${rowStyle}><td style="color:#475569">${i + 1}</td>` +
        `<td>${fmt(r.qty, 0)}</td>` +
        `<td>$${fmt(r.price)}${flag}</td>` +
        `<td style="color:#64748b">avg $${fmt(r.window_avg, 2)} · ${fmt(r.window_shares, 0)} shares</td></tr>`;
    }).join('');

    const totalRungQty = data.rungs.reduce((s, r) => s + r.qty, 0);
    const totalRungVal = data.rungs.reduce((s, r) => s + r.qty * r.price, 0);

    const shouldAutoApply = immediateCount === 0;

    // --- partial unwind note ------------------------------------------------
    let exitNote = '';
    if (data.exit_trades && data.exit_trades.length > 0) {
      const exitQty = data.exit_trades.reduce((s, e) => s + e.qty, 0);
      const exitCost = data.exit_trades.reduce((s, e) => s + e.qty * e.price, 0);
      const exitAvg = exitQty > 0 ? (exitCost / exitQty).toFixed(2) : '—';
      const exitAction = isLong ? 'Sell' : 'Buy';
      exitNote =
        `<div style="font-size:11px;color:#fbbf24;margin:4px 0;line-height:1.4">` +
          `${data.exit_trades.length} partial unwind already executed: ` +
          `${exitAction} ${fmt(exitQty, 0)} shares @ avg $${exitAvg}` +
          (data.effective_max_rungs != null && data.effective_max_rungs < (data.params?.max_rungs ?? max_rungs)
            ? ` — suggesting ${data.effective_max_rungs} rungs instead of ${data.params?.max_rungs ?? max_rungs}`
            : '') +
        `</div>`;
    }

    // --- streak description -----------------------------------------------
    const hasExits = data.exit_trades && data.exit_trades.length > 0;
    const streakDesc = hasExits
      ? `<span class="strat-highlight">${data.streak_count}</span> ${esc(dir)} detected`
      : `<span class="strat-highlight">${data.streak_count}</span> consecutive ${esc(dir)} detected`;

    banner.innerHTML =
      priceHeader +
      `<div class="ladder-strategy-banner">` +
        streakDesc + ` — ` +
        `<span class="strat-highlight">${fmt(data.total_shares, 0)}</span> shares, ` +
        `avg <span class="strat-highlight">$${fmt(data.overall_avg, 2)}</span>` +
      `</div>` +
      exitNote +
      warningHtml +
      `<table style="width:100%;margin:6px 0"><thead><tr>` +
        `<th style="font-size:10px;padding:3px 6px">#</th>` +
        `<th style="font-size:10px;padding:3px 6px">Qty</th>` +
        `<th style="font-size:10px;padding:3px 6px">Price</th>` +
        `<th style="font-size:10px;padding:3px 6px">Window</th>` +
      `</tr></thead><tbody>${rungSummary}</tbody></table>` +
      `<div style="display:flex;align-items:center;gap:10px;margin-top:6px">` +
        `<span style="font-size:11px;color:#64748b">` +
          `${actionLabel} ${fmt(totalRungQty, 0)} shares · $${fmt(totalRungVal)} est. value` +
        `</span>` +
        `<button class="strat-apply-btn" onclick="applyLadderSuggest()">Apply to Rungs</button>` +
      `</div>`;

    window._ladderSuggestion = data;

    // Only auto-apply when no rungs would fill immediately
    if (shouldAutoApply) applyLadderSuggest();

  } catch (e) {
    status.textContent = '';
    banner.innerHTML = `<div class="ladder-strategy-banner strat-dim">Error: ${esc(e.message)}</div>`;
    params.style.display = 'none';
  }
}

// ── Live DB vs API cross-reference ───────────────────────────────
let _verifyOpen = false;

async function loadLiveVerify() {
  const ticker = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const verifyDiv = document.getElementById('lad-verify-result');
  if (!ticker) return;

  // Toggle off if already open
  if (_verifyOpen) {
    verifyDiv.style.display = 'none';
    _verifyOpen = false;
    document.getElementById('lad-verify-btn').textContent = 'Verify vs API';
    return;
  }

  _verifyOpen = true;
  document.getElementById('lad-verify-btn').textContent = 'Hide verify';
  verifyDiv.style.display = 'block';
  verifyDiv.innerHTML = '<div class="loading" style="padding:8px 0;font-size:12px">Fetching live data from Schwab API…</div>';

  try {
    const res = await fetch('/api/transactions/live?ticker=' + encodeURIComponent(ticker) + '&days=180').then(r => r.json());
    if (res.error) throw new Error(res.error);

    const apiRows = res.api_rows || [];
    const dbRows  = res.db_rows  || [];

    // Build a lookup of DB rows by (date, action, qty, price) for matching
    const dbKeys = new Set(dbRows.map(r =>
      `${r.trade_date}|${r.action}|${r.quantity}|${r.price}`));
    const apiKeys = new Set(apiRows.map(r =>
      `${r.trade_date}|${r.action}|${r.quantity}|${r.price}`));

    // Flag rows
    const apiMissing = apiRows.filter(r =>
      !dbKeys.has(`${r.trade_date}|${r.action}|${r.quantity}|${r.price}`));
    const dbExtra = dbRows.filter(r =>
      !apiKeys.has(`${r.trade_date}|${r.action}|${r.quantity}|${r.price}`));

    const statusHtml = (apiMissing.length === 0 && dbExtra.length === 0)
      ? `<div style="color:#34d399;font-size:11px;margin-bottom:6px">DB matches API — no discrepancies in the last ${res.days} days</div>`
      : `<div style="color:#fbbf24;font-size:11px;margin-bottom:6px">`
        + (apiMissing.length ? `${apiMissing.length} API trade(s) not in DB  ` : '')
        + (dbExtra.length    ? `${dbExtra.length} DB trade(s) not in API`      : '')
        + `</div>`;

    const makeRow = (r, highlight) => {
      const isBuy = (r.action || '').toLowerCase().includes('buy');
      const bg = highlight ? 'background:#44270a' : '';
      const optInfo = r.option_type
        ? ` <span style="color:#94a3b8;font-size:10px">${r.option_type} $${r.option_strike} ${r.option_expiry || ''}</span>`
        : '';
      return `<tr style="${bg}">
        <td style="color:#64748b">${r.trade_date}</td>
        <td class="${isBuy ? 'pos' : 'neg'}">${esc(r.action)}</td>
        <td>${r.quantity != null ? fmt(r.quantity, 0) : '—'}</td>
        <td>${r.price != null ? '$' + fmt(r.price, 4) : '—'}${optInfo}</td>
      </tr>`;
    };

    const apiRowsHtml = apiRows.length
      ? apiRows.map(r => {
          const key = `${r.trade_date}|${r.action}|${r.quantity}|${r.price}`;
          return makeRow(r, !dbKeys.has(key));
        }).join('')
      : '<tr><td colspan="4" style="color:#64748b">No trades returned by API</td></tr>';

    verifyDiv.innerHTML =
      `<div style="border-top:1px solid #2d3148;margin-top:10px;padding-top:10px">` +
      `<div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">` +
        `Live API — ${esc(ticker)} · last ${res.days} days` +
        ` <span style="color:#475569">(API: ${res.api_count} · DB: ${res.db_count})</span>` +
      `</div>` +
      statusHtml +
      `<div style="overflow-x:auto">` +
      `<table style="min-width:340px"><thead><tr>` +
        `<th style="font-size:10px;padding:3px 6px">Date</th>` +
        `<th style="font-size:10px;padding:3px 6px">Action</th>` +
        `<th style="font-size:10px;padding:3px 6px">Qty</th>` +
        `<th style="font-size:10px;padding:3px 6px">Price</th>` +
      `</tr></thead><tbody>${apiRowsHtml}</tbody></table></div>` +
      (apiMissing.length
        ? `<div style="margin-top:6px;font-size:11px;color:#fbbf24">` +
          `Rows highlighted amber are in the API but not in the DB — run sync_trades.py to import them.</div>`
        : '') +
      `</div>`;

  } catch (e) {
    verifyDiv.innerHTML = `<div class="error" style="font-size:12px;margin-top:8px">API error: ${esc(e.message)}</div>`;
  }
}

function applyLadderSuggest() {
  const data = window._ladderSuggestion;
  if (!data || !data.rungs || !data.rungs.length) return;

  // Set action dropdown
  const actionSelect = document.getElementById('lad-action');
  if (data.unwind_action && actionSelect) {
    actionSelect.value = data.unwind_action;
  }

  // Fill rungs
  ladderRungs = data.rungs.map(r => ({
    qty: String(r.qty),
    price: String(r.price),
  }));
  renderRungs();
}

const ladderRecentState = { page: 1, pages: 1, total: 0, eqCount: null, optCount: null };
const LADDER_RECENT_LIMIT = 20;
const LADDER_RECENT_ACTIONS = new Set([
  'Buy','Sell','Sell Short','Buy to Cover',
  'Buy to Open','Sell to Open','Buy to Close','Sell to Close'
]);

async function loadLadderRecent(page) {
  const ticker = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const sidebar = document.getElementById('lad-recent');
  const countDiv = document.getElementById('lad-recent-count');
  const includeOpts = document.getElementById('lad-include-options').checked;

  if (!ticker) {
    sidebar.innerHTML = '<div style="color:#475569;font-size:12px">Enter a ticker to see recent trades</div>';
    countDiv.textContent = '';
    return;
  }

  // Reset counts when page is not specified (new ticker or toggle change)
  const isNewQuery = page === undefined;
  if (isNewQuery) {
    ladderRecentState.page = 1;
    ladderRecentState.eqCount = null;
    ladderRecentState.optCount = null;
  } else {
    ladderRecentState.page = page;
  }

  sidebar.innerHTML = '<div class="loading" style="padding:10px">Loading…</div>';

  try {
    const mainParams = { ticker, limit: LADDER_RECENT_LIMIT, page: ladderRecentState.page };
    if (!includeOpts) mainParams.category = 'equity';

    // Fetch trades + counts in parallel (counts only on first load of a ticker/toggle)
    const fetchTrades = fetch('/api/transactions?' + new URLSearchParams(mainParams)).then(r => r.json());
    const fetchCounts = (ladderRecentState.eqCount === null)
      ? Promise.all([
          fetch('/api/transactions?' + new URLSearchParams({ ticker, category: 'equity', limit: 10, page: 1 })).then(r => r.json()),
          fetch('/api/transactions?' + new URLSearchParams({ ticker, category: 'option',  limit: 10, page: 1 })).then(r => r.json()),
        ])
      : Promise.resolve(null);

    const [res, counts] = await Promise.all([fetchTrades, fetchCounts]);
    if (res.error) throw new Error(res.error);

    if (counts) {
      ladderRecentState.eqCount  = counts[0].total || 0;
      ladderRecentState.optCount = counts[1].total || 0;
    }

    ladderRecentState.pages = res.pages;
    ladderRecentState.total = res.total;

    // Count header
    const totalAll = ladderRecentState.eqCount + ladderRecentState.optCount;
    countDiv.innerHTML = `${totalAll.toLocaleString()} trades `
      + `<span style="color:#475569">(${ladderRecentState.eqCount.toLocaleString()} equity`
      + ` / ${ladderRecentState.optCount.toLocaleString()} option)</span>`;

    const trades = res.data.filter(r => LADDER_RECENT_ACTIONS.has(r.action));

    if (!trades.length && ladderRecentState.page === 1) {
      sidebar.innerHTML = '<div style="color:#475569;font-size:12px">No trades found for ' + esc(ticker) + '</div>';
      return;
    }

    const rows = trades.map(r => {
      const isBuy = (r.action||'').toLowerCase().includes('buy');
      const optBadge = r.option_type
        ? `<span class="badge badge-${r.option_type}" style="font-size:10px;padding:1px 5px">${r.option_type}</span>`
        : '';
      return `<tr>
        <td style="color:#64748b">${r.trade_date}</td>
        <td class="${isBuy ? 'pos' : 'neg'}">${esc(r.action)}</td>
        <td>${r.quantity != null ? fmt(r.quantity, 0) : '—'}</td>
        <td>${r.price != null ? '$' + fmt(r.price, 4) : '—'}</td>
        <td>${optBadge}</td>
        <td>${r.option_strike != null ? '$' + fmt(r.option_strike) : ''}</td>
        <td style="color:#64748b">${r.option_expiry || ''}</td>
      </tr>`;
    }).join('');

    const cur = ladderRecentState.page, total_pages = ladderRecentState.pages;
    const prevDisabled = cur <= 1 ? 'disabled' : '';
    const nextDisabled = cur >= total_pages ? 'disabled' : '';
    const pagination = total_pages > 1
      ? `<div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;gap:6px">
           <button class="pg-btn" ${prevDisabled} onclick="loadLadderRecent(${cur - 1})">‹ Prev</button>
           <span class="pg-info">Page ${cur} of ${total_pages} · ${res.total.toLocaleString()} trades</span>
           <button class="pg-btn" ${nextDisabled} onclick="loadLadderRecent(${cur + 1})">Next ›</button>
         </div>`
      : `<div class="pg-info" style="margin-top:6px">${res.total.toLocaleString()} trades</div>`;

    sidebar.innerHTML =
      '<table><thead><tr>' +
      '<th>Date</th><th>Action</th><th>Qty</th><th>Price</th><th>Type</th><th>Strike</th><th>Expiry</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>' + pagination;
  } catch(e) {
    sidebar.innerHTML = '<div class="error" style="font-size:12px">Error: ' + esc(e.message) + '</div>';
  }
}

// ── Ladder Open Orders ────────────────────────────────────────────
let _ladderOrders = [];

async function loadLadderOrders() {
  const ticker = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const container = document.getElementById('lad-orders');
  const countDiv = document.getElementById('lad-orders-count');
  const cancelAllBtn = document.getElementById('lad-cancel-all-btn');
  const includeOpts = document.getElementById('lad-orders-include-options').checked;

  if (!ticker) {
    container.innerHTML = '<div style="color:#475569;font-size:12px">Enter a ticker to see open orders</div>';
    countDiv.textContent = '';
    cancelAllBtn.style.display = 'none';
    _ladderOrders = [];
    return;
  }

  container.innerHTML = '<div class="loading" style="padding:8px">Loading orders…</div>';
  countDiv.textContent = '';
  cancelAllBtn.style.display = 'none';

  try {
    const raw = await fetch('/api/orders').then(r => r.json());
    if (raw.error) throw new Error(raw.error);

    let orders = raw.filter(o =>
      o.underlying === ticker || o.symbol === ticker || o.underlying.startsWith(ticker));

    const eqOrders = orders.filter(o => o.asset_type === 'EQUITY');
    const optOrders = orders.filter(o => o.asset_type !== 'EQUITY');

    if (!includeOpts) orders = eqOrders;

    _ladderOrders = orders;

    countDiv.innerHTML = `${orders.length} open order${orders.length !== 1 ? 's' : ''} `
      + `<span style="color:#475569">(${eqOrders.length} equity / ${optOrders.length} option)</span>`;

    if (orders.length === 0) {
      container.innerHTML = '<div style="color:#475569;font-size:12px">No open orders for ' + esc(ticker) + '</div>';
      return;
    }

    cancelAllBtn.style.display = '';

    const rows = orders.map(o => {
      const sideClass = (o.instruction || '').includes('SELL') ? 'neg' : 'pos';
      const priceStr = o.price != null ? '$' + fmt(o.price, 2)
                     : o.stop_price != null ? 'Stop $' + fmt(o.stop_price) : '—';
      const isOpt = o.asset_type !== 'EQUITY';
      const symbolLabel = isOpt
        ? `<span style="color:#94a3b8" title="${esc(o.symbol)}">${esc(o.symbol.substring(0, 20))}</span>`
        : '';
      const cancelBtn = o.cancelable
        ? `<button class="cancel-single-btn" onclick="cancelLadderOrder('${esc(o.order_id)}')">✕</button>`
        : '';
      return `<tr>
        <td><span class="${sideClass}">${esc(o.instruction)}</span></td>
        <td>${fmt(o.quantity, 0)}</td>
        <td>${priceStr}</td>
        <td><span class="badge badge-status-${o.status}" style="font-size:9px;padding:1px 5px">${o.status}</span></td>
        <td>${symbolLabel}</td>
        <td>${cancelBtn}</td>
      </tr>`;
    }).join('');

    container.innerHTML =
      '<table><thead><tr>' +
      '<th>Side</th><th>Qty</th><th>Price</th><th>Status</th><th>Symbol</th><th></th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';

  } catch (e) {
    container.innerHTML = '<div class="error" style="font-size:12px">Error: ' + esc(e.message) + '</div>';
    countDiv.textContent = '';
  }
}

async function cancelLadderOrder(orderId) {
  if (!confirm('Cancel order ' + orderId + '?')) return;
  try {
    const res = await fetch('/api/order/' + orderId, { method: 'DELETE' }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    ordersState.loaded = false;
    await loadLadderOrders();
  } catch (e) {
    alert('Failed to cancel: ' + e.message);
  }
}

async function cancelAllLadderOrders() {
  const cancelable = _ladderOrders.filter(o => o.cancelable);
  if (cancelable.length === 0) {
    alert('No cancelable orders.');
    return;
  }

  const ticker = document.getElementById('lad-ticker').value.trim().toUpperCase();
  if (!confirm(`Cancel ALL ${cancelable.length} open orders for ${ticker}?\n\nThis cannot be undone.`)) return;

  const container = document.getElementById('lad-orders');
  container.innerHTML = `<div class="loading" style="padding:8px">Cancelling ${cancelable.length} orders…</div>`;

  let ok = 0, fail = 0;
  for (const o of cancelable) {
    try {
      const res = await fetch('/api/order/' + o.order_id, { method: 'DELETE' }).then(r => r.json());
      if (res.error) throw new Error(res.error);
      ok++;
    } catch {
      fail++;
    }
  }

  ordersState.loaded = false;

  if (fail === 0) {
    container.innerHTML = `<div style="color:#86efac;font-size:12px;padding:6px 0">All ${ok} orders cancelled.</div>`;
  } else {
    container.innerHTML = `<div class="error" style="font-size:12px">${ok} cancelled, ${fail} failed. Refreshing…</div>`;
  }

  setTimeout(() => loadLadderOrders(), 1500);
}

function previewLadder() {
  const ticker  = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const action  = document.getElementById('lad-action').value;
  const dur     = document.getElementById('lad-duration').value;
  const ses     = document.getElementById('lad-session').value;
  const resultDiv = document.getElementById('lad-result');

  if (!ticker) { resultDiv.innerHTML = '<div class="error">Ticker is required.</div>'; return; }

  const validRungs = ladderRungs.filter(r => parseFloat(r.qty) > 0 && parseFloat(r.price) > 0);
  if (validRungs.length === 0) { resultDiv.innerHTML = '<div class="error">At least one rung with qty and price is required.</div>'; return; }

  const prices = validRungs.map(r => parseFloat(r.price));
  if (new Set(prices).size !== prices.length) { resultDiv.innerHTML = '<div class="error">Duplicate prices found — each rung must have a unique price.</div>'; return; }

  const aLabel = { buy:'Buy', sell:'Sell', sell_short:'Sell Short', buy_to_cover:'Buy to Cover' }[action];
  const durLabel = dur === 'gtc' ? 'GTC' : 'Day';
  const sesLabel = {normal:'Normal', seamless:'Extended Hrs', am:'Pre-Market', pm:'Post-Market'}[ses];

  let totalQty = 0, totalVal = 0;
  const rungRows = validRungs.map((r, i) => {
    const q = parseFloat(r.qty), p = parseFloat(r.price);
    totalQty += q; totalVal += q * p;
    return `<tr><td>${i+1}</td><td>${fmt(q,0)} shares</td><td>$${fmt(p)}</td><td>$${fmt(q*p)}</td></tr>`;
  }).join('');

  resultDiv.innerHTML =
    '<div class="preview-box" style="max-width:640px">' +
      '<div class="preview-title">⚠️ Review Ladder Order</div>' +
      '<div class="preview-summary">' +
        `<b>${aLabel}</b> <b>${ticker}</b> — ${validRungs.length} rungs · ${fmt(totalQty,0)} total shares · $${fmt(totalVal)} est. value<br>` +
        `${durLabel} · ${sesLabel}` +
      '</div>' +
      '<table style="margin:10px 0"><thead><tr><th>#</th><th>Qty</th><th>Price</th><th>Value</th></tr></thead>' +
      '<tbody>' + rungRows + '</tbody></table>' +
      '<div class="preview-actions">' +
        '<button class="cancel-btn" onclick="document.getElementById(\'lad-result\').innerHTML=\'\'">✕ Cancel</button>' +
        '<button class="confirm-btn" onclick="submitLadder()">✓ Submit All Orders</button>' +
      '</div>' +
    '</div>';
}

async function submitLadder() {
  if (_ladderSubmitting) return;
  _ladderSubmitting = true;

  const ticker  = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const action  = document.getElementById('lad-action').value;
  const dur     = document.getElementById('lad-duration').value;
  const ses     = document.getElementById('lad-session').value;
  const resultDiv = document.getElementById('lad-result');

  const validRungs = ladderRungs
    .filter(r => parseFloat(r.qty) > 0 && parseFloat(r.price) > 0)
    .map(r => ({ quantity: parseFloat(r.qty), price: parseFloat(r.price) }));

  resultDiv.innerHTML = '<div class="loading">Submitting ' + validRungs.length + ' orders…</div>';

  try {
    const res = await fetchJson('/api/order/ladder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        trade_type: 'equity',
        action, symbol: ticker,
        duration: dur, session: ses,
        rungs: validRungs
      })
    });

    const ok = res.results.filter(r => r.status === 'ok').length;
    const fail = res.results.length - ok;
    const statusMsg = fail === 0
      ? `<div class="success-box">✅ All ${ok} orders submitted successfully!</div>`
      : `<div class="error">⚠️ ${ok} succeeded, ${fail} failed</div>`;

    resultDiv.innerHTML = statusMsg + ladderResultTableHtml(res.results, {
      qtyColumnLabel: 'Qty',
      footerHtml: '<div class="ladder-result-hint">Switch to "Open Orders" to track status.</div>',
    });
    ordersState.loaded = false;
    setTimeout(() => loadLadderOrders(), 1000);
  } catch(e) {
    resultDiv.innerHTML = '<div class="error">❌ Ladder submission failed: ' + esc(e.message) + '</div>';
  } finally {
    _ladderSubmitting = false;
  }
}

// ── Open Orders ────────────────────────────────────────────────────
let _allOrders = [];
let _ordSortCol = 'entered_time', _ordSortDir = -1;
const ordersState = { loaded: false };

async function loadOrders() {
  document.getElementById('ord-loading').style.display='block';
  document.getElementById('ord-table').style.display='none';
  document.getElementById('ord-empty').style.display='none';
  document.getElementById('ord-error').style.display='none';
  try {
    const raw = await fetchJson('/api/orders');
    _allOrders = raw;
    ordersState.loaded = true;
    document.getElementById('ord-loading').style.display='none';
    filterOrders();
  } catch(e) {
    document.getElementById('ord-loading').style.display='none';
    document.getElementById('ord-error').style.display='block';
    document.getElementById('ord-error').textContent = 'Error: ' + e.message;
  }
}

function sortOrders(col) {
  if (_ordSortCol === col) _ordSortDir *= -1;
  else { _ordSortCol = col; _ordSortDir = -1; }
  filterOrders();
}

function filterOrders() {
  const ticker = document.getElementById('ord-ticker').value.trim().toUpperCase();
  const type   = document.getElementById('ord-type').value;
  const status = document.getElementById('ord-status').value;

  let rows = _allOrders.filter(o => {
    if (ticker && !o.underlying.includes(ticker) && !o.symbol.includes(ticker)) return false;
    if (type   && o.order_type !== type)   return false;
    if (status && o.status !== status)     return false;
    return true;
  });

  rows.sort((a, b) => {
    let av = a[_ordSortCol], bv = b[_ordSortCol];
    if (av === null || av === undefined) av = '';
    if (bv === null || bv === undefined) bv = '';
    if (av < bv) return  _ordSortDir;
    if (av > bv) return -_ordSortDir;
    return 0;
  });

  document.getElementById('ord-count').textContent = rows.length + ' open order' + (rows.length !== 1 ? 's' : '');

  if (rows.length === 0) {
    document.getElementById('ord-table').style.display='none';
    document.getElementById('ord-empty').style.display='block';
    return;
  }
  document.getElementById('ord-empty').style.display='none';
  document.getElementById('ord-table').style.display='block';

  document.getElementById('ord-tbody').innerHTML = rows.map(o => {
    const sideClass = (o.instruction||'').includes('SELL') ? 'badge-PUT' : 'badge-equity';
    const priceStr  = o.price != null ? '$' + fmt(o.price, 4)
                    : o.stop_price != null ? 'Stop $' + fmt(o.stop_price) : '—';
    const cancelBtn = o.cancelable
      ? '<button class="cancel-order-btn" onclick="cancelOrder(' + esc(o.order_id) + ')">Cancel</button>'
      : '—';
    return '<tr>' +
      '<td style="color:#475569;font-size:11px;font-family:monospace">' + esc(o.order_id) + '</td>' +
      '<td><span class="badge badge-status-' + o.status + '">' + o.status + '</span></td>' +
      '<td style="color:#94a3b8">' + o.order_type + '</td>' +
      '<td><b>' + esc(o.underlying) + '</b></td>' +
      '<td style="color:#64748b;max-width:200px;overflow:hidden;text-overflow:ellipsis" title="' + esc(o.symbol) + '">' + esc(o.symbol) + '</td>' +
      '<td><span class="badge ' + sideClass + '">' + esc(o.instruction) + '</span></td>' +
      '<td>' + fmt(o.quantity, 0) + '</td>' +
      '<td>' + fmt(o.filled_quantity, 0) + '</td>' +
      '<td>' + priceStr + '</td>' +
      '<td style="color:#64748b;font-size:12px">' + o.entered_time + '</td>' +
      '<td>' + cancelBtn + '</td>' +
    '</tr>';
  }).join('');
}

async function cancelOrder(orderId) {
  if (!confirm('Cancel order ' + orderId + '?')) return;
  try {
    const res = await fetch('/api/order/' + orderId, { method: 'DELETE' }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    await loadOrders();
  } catch(e) {
    alert('Failed to cancel order: ' + e.message);
  }
}

// ── Options Strategy Tab ──────────────────────────────────────────
let stratMode = 'naked';
let _stratTicker = '';
let _stratChainData = null;
let _stratPendingOrder = null;
let _stratSuggestions = [];
let _stratOrders = [];

const STRAT_MODES = ['naked', 'vertical', 'collar', 'bundle'];

function _stratLadderEnabled() {
  const el = document.getElementById('strat-ladder-en');
  return el && el.checked;
}

function _stratLadderStepsCount() {
  const sel = document.getElementById('strat-ladder-steps');
  const n = sel ? parseInt(sel.value, 10) : 3;
  return n >= 2 && n <= 7 ? n : 3;
}

function _stratLadderPriceLabel() {
  if (stratMode === 'naked') return 'Limit (per share)';
  return 'Net credit/debit';
}

function _updateStratSingleVsLadderVisibility() {
  const en = _stratLadderEnabled();
  ['sn-single-qp', 'sv-single-qp', 'sc-single-qp'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = en ? 'none' : '';
  });
}

function _onStratLadderToggle() {
  const en = _stratLadderEnabled();
  const stepsWrap = document.getElementById('strat-ladder-steps-wrap');
  if (stepsWrap) stepsWrap.style.display = en ? '' : 'none';
  const rungBox = document.getElementById('strat-ladder-rungs');
  if (en) {
    _renderStratLadderRungs();
    _seedStratLadderRung1FromSingle();
  } else if (rungBox) {
    rungBox.style.display = 'none';
    rungBox.innerHTML = '';
  }
  _updateStratSingleVsLadderVisibility();
  _syncAutoPriceToStratLadderRung1();
  updateStratPnl();
}

function _seedStratLadderRung1FromSingle() {
  const r1 = document.querySelector('#strat-ladder-rungs .strat-rung-row[data-rung="1"]');
  if (!r1) return;
  const qIn = r1.querySelector('.strat-rung-qty');
  const pIn = r1.querySelector('.strat-rung-price');
  let q = 1, p = '';
  if (stratMode === 'naked') {
    q = parseInt(document.getElementById('sn-qty').value, 10) || 1;
    p = document.getElementById('sn-price').value;
  } else if (stratMode === 'vertical') {
    q = parseInt(document.getElementById('sv-qty').value, 10) || 1;
    p = document.getElementById('sv-price').value;
  } else if (stratMode === 'collar') {
    q = parseInt(document.getElementById('sc-qty').value, 10) || 1;
    p = document.getElementById('sc-price').value;
  }
  if (qIn && !qIn.dataset.touched) qIn.value = q;
  if (pIn && p && !pIn.dataset.touched) pIn.value = p;
}

function _renderStratLadderRungs() {
  const container = document.getElementById('strat-ladder-rungs');
  if (!container) return;
  if (!_stratLadderEnabled()) {
    container.style.display = 'none';
    return;
  }
  const n = _stratLadderStepsCount();
  const priceLbl = _stratLadderPriceLabel();
  let html = '<div class="strat-ladder-rungs-head"><span>Step</span><span>Contracts</span><span>' + esc(priceLbl) + '</span></div>';
  for (let i = 1; i <= n; i++) {
    html +=
      '<div class="strat-rung-row form-grid" data-rung="' + i + '">' +
      '<div class="form-group strat-rung-step"><label>#' + i + '</label></div>' +
      '<div class="form-group">' +
        '<label class="sr-only">Contracts step ' + i + '</label>' +
        '<input type="number" class="strat-rung-qty" min="1" step="1" value="1" placeholder="1" ' +
        'data-rung="' + i + '" oninput="this.dataset.touched=1;updateStratPnl()">' +
      '</div>' +
      '<div class="form-group">' +
        '<label class="sr-only">Price step ' + i + '</label>' +
        '<input type="number" class="strat-rung-price" step="0.01" min="0" placeholder="0.00" ' +
        'data-rung="' + i + '" oninput="this.dataset.touched=1;updateStratPnl()">' +
      '</div>' +
      '</div>';
  }
  container.innerHTML = html;
  container.style.display = 'block';
  _syncAutoPriceToStratLadderRung1();
}

function _collectStratLadderRungs() {
  const rows = document.querySelectorAll('#strat-ladder-rungs .strat-rung-row');
  const out = [];
  rows.forEach(row => {
    const qEl = row.querySelector('.strat-rung-qty');
    const pEl = row.querySelector('.strat-rung-price');
    if (!qEl || !pEl) return;
    const q = parseInt(qEl.value, 10) || 0;
    const p = parseFloat(pEl.value);
    out.push({ qty: q, price: p });
  });
  return out;
}

function _syncAutoPriceToStratLadderRung1() {
  if (!_stratLadderEnabled()) return;
  const r1 = document.querySelector('#strat-ladder-rungs .strat-rung-row[data-rung="1"]');
  if (!r1) return;
  const priceIn = r1.querySelector('.strat-rung-price');
  if (!priceIn || priceIn.dataset.touched) return;
  let src = null;
  if (stratMode === 'naked') src = document.getElementById('sn-price');
  else if (stratMode === 'vertical') src = document.getElementById('sv-price');
  else if (stratMode === 'collar') src = document.getElementById('sc-price');
  if (src && src.value) priceIn.value = src.value;
}

function setStrategyMode(mode) {
  stratMode = mode;
  STRAT_MODES.forEach(m => {
    const btn = document.getElementById('strat-btn-' + m);
    if (btn) btn.classList.toggle('active', m === mode);
    const sec = document.getElementById('strat-form-' + m);
    if (sec) sec.style.display = m === mode ? '' : 'none';
  });
  document.getElementById('strat-result').innerHTML = '';
  document.getElementById('strat-pnl').style.display = 'none';

  const ladPanel = document.getElementById('strat-ladder-panel');
  if (ladPanel) ladPanel.style.display = mode === 'bundle' ? 'none' : '';

  if (mode === 'bundle') {
    const en = document.getElementById('strat-ladder-en');
    if (en) en.checked = false;
    const stepsWrap = document.getElementById('strat-ladder-steps-wrap');
    if (stepsWrap) stepsWrap.style.display = 'none';
    const rungBox = document.getElementById('strat-ladder-rungs');
    if (rungBox) { rungBox.innerHTML = ''; rungBox.style.display = 'none'; }
  } else if (_stratLadderEnabled()) {
    _renderStratLadderRungs();
    _seedStratLadderRung1FromSingle();
  }

  _updateStratSingleVsLadderVisibility();
  _populateStrikeDropdowns();
  updateStratPnl();
}

function onStrategyTickerChange() {
  const ticker = document.getElementById('strat-ticker').value.trim().toUpperCase();
  if (!ticker) {
    _stratTicker = '';
    clearPositionPanel('strat-position-panel');
    document.getElementById('strat-suggest-cards').innerHTML =
      '<div style="color:#475569;font-size:12px">Enter a ticker to get strategy suggestions</div>';
    document.getElementById('strat-recent').innerHTML =
      '<div style="color:#475569;font-size:12px">Enter a ticker to see recent trades</div>';
    document.getElementById('strat-orders').innerHTML =
      '<div style="color:#475569;font-size:12px">Enter a ticker to see open orders</div>';
    document.getElementById('strat-recent-count').textContent = '';
    document.getElementById('strat-orders-count').textContent = '';
    return;
  }
  const changed = ticker !== _stratTicker;
  _stratTicker = ticker;
  if (changed) {
    loadStrategyExpirations(ticker);
    loadStrategySuggestions(ticker);
    loadPositionSummaryForTicker(ticker, 'strat-position-panel');
    loadStrategyRecent();
    loadStrategyOrders();
  }
}

async function loadStrategyExpirations(ticker) {
  const sel = document.getElementById('strat-expiry');
  sel.innerHTML = '<option value="">Loading…</option>';
  try {
    const data = await fetch('/api/option-expirations/' + encodeURIComponent(ticker)).then(r => r.json());
    if (data.error) throw new Error(data.error);
    if (!data.expirations || !data.expirations.length) {
      sel.innerHTML = '<option value="">No expirations found</option>';
      return;
    }
    sel.innerHTML = '<option value="">Select expiration (' + data.expirations.length + ')</option>'
      + data.expirations.map(d => '<option value="' + d + '">' + d + '</option>').join('');
  } catch(e) {
    sel.innerHTML = '<option value="">Error loading expirations</option>';
  }
}

// Chain pagination state
const CHAIN_PAGE_SIZE = 20;
let _chainAllStrikes = [];
let _chainCallMap    = {};
let _chainPutMap     = {};
let _chainPageIdx    = 0;   // index into pages; set to middle page on load
let _chainVisibleStrikes = [];

async function loadStrategyChain() {
  const expiry  = document.getElementById('strat-expiry').value;
  const content = document.getElementById('strat-chain-content');
  const status  = document.getElementById('strat-chain-status');
  if (!expiry || !_stratTicker) {
    content.innerHTML = '<div style="color:#475569;font-size:12px;padding:4px 0">Select an expiration to browse the chain</div>';
    document.getElementById('strat-chain-pager').style.display = 'none';
    return;
  }
  status.textContent = 'Loading…';
  content.innerHTML = '<div class="loading" style="padding:12px">Loading option chain…</div>';
  try {
    const data = await fetch('/api/option-chain?symbol=' + encodeURIComponent(_stratTicker) +
      '&from_date=' + expiry + '&to_date=' + expiry + '&strike_count=60').then(r => r.json());
    if (data.error) throw new Error(data.error);
    _stratChainData = data;

    const calls = data.calls[expiry] || [];
    const puts  = data.puts[expiry]  || [];
    _chainAllStrikes = [...new Set([...calls.map(c => c.strike), ...puts.map(p => p.strike)])].sort((a,b) => a - b);
    _chainCallMap = {}; calls.forEach(c => _chainCallMap[c.strike] = c);
    _chainPutMap  = {}; puts.forEach(p => _chainPutMap[p.strike]  = p);

    if (!_chainAllStrikes.length) {
      content.innerHTML = '<div style="color:#475569;font-size:12px;padding:4px 0">No contracts found</div>';
      status.textContent = '';
      document.getElementById('strat-chain-pager').style.display = 'none';
      return;
    }

    // Default to the middle page so ATM strikes are centered
    const totalPages = Math.ceil(_chainAllStrikes.length / CHAIN_PAGE_SIZE);
    _chainPageIdx = Math.floor(totalPages / 2);

    _renderChainPage();
    _populateStrikeDropdowns();
  } catch(e) {
    content.innerHTML = '<div style="color:#f87171;font-size:12px;padding:4px 0">Error: ' + esc(e.message) + '</div>';
    status.textContent = '';
    document.getElementById('strat-chain-pager').style.display = 'none';
  }
}

function shiftChainPage(delta) {
  const totalPages = Math.ceil(_chainAllStrikes.length / CHAIN_PAGE_SIZE);
  _chainPageIdx = Math.max(0, Math.min(totalPages - 1, _chainPageIdx + delta));
  _renderChainPage();
  _populateStrikeDropdowns();
}

function _renderChainPage() {
  const content    = document.getElementById('strat-chain-content');
  const status     = document.getElementById('strat-chain-status');
  const pager      = document.getElementById('strat-chain-pager');
  const prevBtn    = document.getElementById('chain-prev-btn');
  const nextBtn    = document.getElementById('chain-next-btn');
  const pageLabel  = document.getElementById('chain-page-label');

  const totalPages = Math.ceil(_chainAllStrikes.length / CHAIN_PAGE_SIZE);
  const start      = _chainPageIdx * CHAIN_PAGE_SIZE;
  const pageStrikes = _chainAllStrikes.slice(start, start + CHAIN_PAGE_SIZE);
  _chainVisibleStrikes = pageStrikes;

  const fo = v => v == null ? '—' : Number(v).toFixed(2);
  const fv = v => v == null ? '—' : Number(v).toLocaleString();

  const rows = pageStrikes.map(strike => {
    const c  = _chainCallMap[strike] || {};
    const p  = _chainPutMap[strike]  || {};
    const cI = c.itm ? ' chain-td-itm' : '';
    const pI = p.itm ? ' chain-td-itm' : '';
    return '<tr class="chain-row">' +
      '<td class="chain-td-call' + cI + '" data-side="CALL" data-strike="' + strike + '">' + fo(c.bid) + '</td>' +
      '<td class="chain-td-call' + cI + '" data-side="CALL" data-strike="' + strike + '">' + fo(c.ask) + '</td>' +
      '<td class="chain-td-call' + cI + '" data-side="CALL" data-strike="' + strike + '">' + fo(c.last) + '</td>' +
      '<td class="chain-td-call' + cI + '" data-side="CALL" data-strike="' + strike + '">' + fv(c.volume) + '</td>' +
      '<td class="chain-td-call' + cI + '" data-side="CALL" data-strike="' + strike + '">' + fv(c.oi) + '</td>' +
      '<td class="chain-td-strike">' + fo(strike) + '</td>' +
      '<td class="chain-td-put' + pI + '" data-side="PUT" data-strike="' + strike + '">' + fo(p.bid) + '</td>' +
      '<td class="chain-td-put' + pI + '" data-side="PUT" data-strike="' + strike + '">' + fo(p.ask) + '</td>' +
      '<td class="chain-td-put' + pI + '" data-side="PUT" data-strike="' + strike + '">' + fo(p.last) + '</td>' +
      '<td class="chain-td-put' + pI + '" data-side="PUT" data-strike="' + strike + '">' + fv(p.volume) + '</td>' +
      '<td class="chain-td-put' + pI + '" data-side="PUT" data-strike="' + strike + '">' + fv(p.oi) + '</td>' +
    '</tr>';
  }).join('');

  content.innerHTML =
    '<table class="chain-table" onclick="onStratChainClick(event)">' +
    '<thead><tr>' +
      '<th class="chain-th-calls" colspan="5">CALLS</th>' +
      '<th class="chain-th-strike">STRIKE</th>' +
      '<th class="chain-th-puts" colspan="5">PUTS</th>' +
    '</tr><tr>' +
      '<th class="chain-th-calls">Bid</th><th class="chain-th-calls">Ask</th><th class="chain-th-calls">Last</th>' +
      '<th class="chain-th-calls">Vol</th><th class="chain-th-calls">OI</th>' +
      '<th class="chain-th-strike"></th>' +
      '<th class="chain-th-puts">Bid</th><th class="chain-th-puts">Ask</th><th class="chain-th-puts">Last</th>' +
      '<th class="chain-th-puts">Vol</th><th class="chain-th-puts">OI</th>' +
    '</tr></thead>' +
    '<tbody>' + rows + '</tbody></table>' +
    '<div class="chain-click-hint">Click any call or put row to fill the strategy form</div>';

  // Update status and pager
  const lo = pageStrikes.length ? pageStrikes[0].toFixed(2) : '—';
  const hi = pageStrikes.length ? pageStrikes[pageStrikes.length - 1].toFixed(2) : '—';
  status.textContent = pageStrikes.length + ' strikes ($' + lo + ' – $' + hi + ')';

  if (totalPages > 1) {
    pager.style.display = 'flex';
    prevBtn.disabled  = (_chainPageIdx === 0);
    nextBtn.disabled  = (_chainPageIdx >= totalPages - 1);
    pageLabel.textContent = 'Page ' + (_chainPageIdx + 1) + ' / ' + totalPages;
  } else {
    pager.style.display = 'none';
  }
}

function onStratChainClick(e) {
  const td = e.target.closest('td[data-side]');
  if (!td) return;
  const side   = td.getAttribute('data-side');
  const strike = parseFloat(td.getAttribute('data-strike'));
  const expiry = document.getElementById('strat-expiry').value;
  if (!side || !strike || !expiry || !_stratChainData) return;

  if (stratMode === 'naked') {
    document.getElementById('sn-type').value = side;
    _populateStrikeDropdowns();
    document.getElementById('sn-strike').value = strike;
  } else if (stratMode === 'vertical') {
    document.getElementById('sv-type').value = side;
    _populateStrikeDropdowns();
    if (!document.getElementById('sv-sell-strike').value) {
      document.getElementById('sv-sell-strike').value = strike;
    } else if (!document.getElementById('sv-buy-strike').value) {
      document.getElementById('sv-buy-strike').value = strike;
    } else {
      document.getElementById('sv-sell-strike').value = strike;
      document.getElementById('sv-buy-strike').value = '';
    }
  } else if (stratMode === 'collar') {
    if (side === 'CALL') {
      document.getElementById('sc-sell-type').value = 'CALL';
      _populateStrikeDropdowns();
      document.getElementById('sc-sell-strike').value = strike;
    } else {
      document.getElementById('sc-buy-type').value = 'PUT';
      _populateStrikeDropdowns();
      document.getElementById('sc-buy-strike').value = strike;
    }
  } else if (stratMode === 'bundle') {
    document.getElementById('sb-opt-type').value = side;
    _populateStrikeDropdowns();
    document.getElementById('sb-strike').value = strike;
  }
  _autoCalcStratPrice();
}

// ── Suggestions ──────────────────────────────────────────────────
async function loadStrategySuggestions(ticker) {
  const container = document.getElementById('strat-suggest-cards');
  container.innerHTML = '<div class="loading" style="padding:8px 0;font-size:12px">Analysing positions…</div>';
  try {
    const data = await fetch('/api/strategy-suggest?ticker=' + encodeURIComponent(ticker)).then(r => r.json());
    if (data.error) throw new Error(data.error);
    _stratSuggestions = data.suggestions || [];

    if (!_stratSuggestions.length) {
      const posLabel = data.equity_qty > 0 ? `Long ${data.equity_qty} shares`
                     : data.equity_qty < 0 ? `Short ${Math.abs(data.equity_qty)} shares`
                     : 'No equity position';
      container.innerHTML =
        '<div style="color:#64748b;font-size:12px">' + posLabel + ' — no suggestions available. ' +
        'The engine needs ≥100 shares for option strategies.</div>';
      return;
    }

    const eqLabel = data.equity_qty > 0 ? `<span class="pos">Long ${data.equity_qty}</span>`
                  : data.equity_qty < 0 ? `<span class="neg">Short ${Math.abs(data.equity_qty)}</span>`
                  : 'No position';
    const priceLabel = data.quote && data.quote.last != null ? ` · $${fmt(data.quote.last)}` : '';

    container.innerHTML =
      '<div style="font-size:12px;color:#94a3b8;margin-bottom:8px">' +
        esc(ticker) + ': ' + eqLabel + priceLabel +
      '</div>' +
      _stratSuggestions.map((s, i) => {
        const badgeCls = 'strat-badge-' + (s.strategy || 'naked');
        return '<div class="strat-suggest-card" onclick="applyStrategySuggestion(' + i + ')">' +
          '<div class="strat-suggest-card-title">' +
            '<span class="strat-suggest-badge ' + badgeCls + '">' + esc(s.strategy) + '</span> ' +
            esc(s.title) +
          '</div>' +
          '<div class="strat-suggest-card-desc">' + esc(s.description) + '</div>' +
          '<div class="strat-suggest-card-detail">' + esc(s.detail || '') + '</div>' +
        '</div>';
      }).join('');
  } catch(e) {
    container.innerHTML = '<div style="color:#f87171;font-size:12px">Error: ' + esc(e.message) + '</div>';
  }
}

async function applyStrategySuggestion(index) {
  const s = _stratSuggestions[index];
  if (!s) return;

  setStrategyMode(s.strategy || 'naked');

  // If legs specify an expiry, set it and await chain load so dropdowns can be populated
  const legExpiry = s.legs && s.legs.length > 0 ? s.legs[0].expiry : null;
  if (legExpiry) {
    const sel = document.getElementById('strat-expiry');
    if (sel) sel.value = legExpiry;
  }
  await loadStrategyChain();  // ensures _stratChainData is fresh before populating

  if (s.strategy === 'naked' && s.legs && s.legs.length === 1) {
    const leg = s.legs[0];
    document.getElementById('sn-type').value   = leg.option_type || 'CALL';
    _populateStrikeDropdowns();
    document.getElementById('sn-action').value = leg.instruction || 'SELL_TO_OPEN';
    document.getElementById('sn-strike').value = leg.strike || '';
    document.getElementById('sn-qty').value    = leg.quantity || 1;
  } else if (s.strategy === 'vertical' && s.legs && s.legs.length === 2) {
    const sellLeg = s.legs.find(l => l.instruction.includes('SELL'));
    const buyLeg  = s.legs.find(l => l.instruction.includes('BUY'));
    if (sellLeg && buyLeg) {
      document.getElementById('sv-type').value = sellLeg.option_type || 'CALL';
      _populateStrikeDropdowns();
      document.getElementById('sv-sell-strike').value = sellLeg.strike || '';
      document.getElementById('sv-buy-strike').value  = buyLeg.strike  || '';
      document.getElementById('sv-qty').value         = sellLeg.quantity || 1;
      document.getElementById('sv-order-type').value  = s.order_type || 'NET_CREDIT';
    }
  } else if (s.strategy === 'collar' && s.legs && s.legs.length === 2) {
    const sellLeg = s.legs.find(l => l.instruction.includes('SELL'));
    const buyLeg  = s.legs.find(l => l.instruction.includes('BUY'));
    if (sellLeg && buyLeg) {
      document.getElementById('sc-sell-type').value = sellLeg.option_type || 'CALL';
      document.getElementById('sc-buy-type').value  = buyLeg.option_type  || 'PUT';
      _populateStrikeDropdowns();
      document.getElementById('sc-sell-strike').value = sellLeg.strike || '';
      document.getElementById('sc-buy-strike').value  = buyLeg.strike  || '';
      document.getElementById('sc-qty').value         = sellLeg.quantity || 1;
      document.getElementById('sc-order-type').value  = s.order_type || 'NET_ZERO';
    }
  }
  _autoCalcStratPrice();
  document.querySelector('.strat-forms-col').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Strategy sidebar: recent trades ──────────────────────────────
const stratRecentState = { page: 1 };

async function loadStrategyRecent(page) {
  const ticker = _stratTicker;
  const container = document.getElementById('strat-recent');
  const countDiv  = document.getElementById('strat-recent-count');

  if (!ticker) {
    container.innerHTML = '<div style="color:#475569;font-size:12px">Enter a ticker to see recent trades</div>';
    countDiv.textContent = '';
    return;
  }

  if (page !== undefined) stratRecentState.page = page;
  else stratRecentState.page = 1;

  container.innerHTML = '<div class="loading" style="padding:8px">Loading…</div>';

  try {
    const params = new URLSearchParams({ ticker, category: 'option', limit: 15, page: stratRecentState.page });
    const res = await fetch('/api/transactions?' + params).then(r => r.json());
    if (res.error) throw new Error(res.error);

    countDiv.textContent = res.total.toLocaleString() + ' option trades';

    if (!res.data.length) {
      container.innerHTML = '<div style="color:#475569;font-size:12px">No option trades for ' + esc(ticker) + '</div>';
      return;
    }

    const rows = res.data.map(r => {
      const isBuy = (r.action||'').toLowerCase().includes('buy');
      const optBadge = r.option_type
        ? '<span class="badge badge-' + r.option_type + '" style="font-size:10px;padding:1px 5px">' + r.option_type + '</span>'
        : '';
      return '<tr>' +
        '<td style="color:#64748b">' + r.trade_date + '</td>' +
        '<td class="' + (isBuy ? 'pos' : 'neg') + '">' + esc(r.action) + '</td>' +
        '<td>' + (r.quantity != null ? fmt(r.quantity, 0) : '—') + '</td>' +
        '<td>' + (r.price != null ? '$' + fmt(r.price, 4) : '—') + '</td>' +
        '<td>' + optBadge + '</td>' +
        '<td>' + (r.option_strike != null ? '$' + fmt(r.option_strike) : '') + '</td>' +
        '<td style="color:#64748b">' + (r.option_expiry || '') + '</td>' +
      '</tr>';
    }).join('');

    const cur = stratRecentState.page, tp = res.pages;
    const pagination = tp > 1
      ? '<div style="display:flex;align-items:center;justify-content:space-between;margin-top:6px;gap:4px">' +
          '<button class="pg-btn" ' + (cur <= 1 ? 'disabled' : '') + ' onclick="loadStrategyRecent(' + (cur-1) + ')">‹</button>' +
          '<span class="pg-info">Page ' + cur + '/' + tp + '</span>' +
          '<button class="pg-btn" ' + (cur >= tp ? 'disabled' : '') + ' onclick="loadStrategyRecent(' + (cur+1) + ')">›</button>' +
        '</div>'
      : '';

    container.innerHTML =
      '<table><thead><tr>' +
      '<th>Date</th><th>Action</th><th>Qty</th><th>Price</th><th>Type</th><th>Strike</th><th>Expiry</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>' + pagination;
  } catch(e) {
    container.innerHTML = '<div class="error" style="font-size:12px">Error: ' + esc(e.message) + '</div>';
  }
}

// ── Strategy sidebar: open orders ────────────────────────────────
async function loadStrategyOrders() {
  const ticker = _stratTicker;
  const container   = document.getElementById('strat-orders');
  const countDiv    = document.getElementById('strat-orders-count');
  const cancelBtn   = document.getElementById('strat-cancel-all-btn');

  if (!ticker) {
    container.innerHTML = '<div style="color:#475569;font-size:12px">Enter a ticker to see open orders</div>';
    countDiv.textContent = '';
    cancelBtn.style.display = 'none';
    _stratOrders = [];
    return;
  }

  container.innerHTML = '<div class="loading" style="padding:6px">Loading…</div>';
  countDiv.textContent = '';
  cancelBtn.style.display = 'none';

  try {
    const raw = await fetch('/api/orders').then(r => r.json());
    if (raw.error) throw new Error(raw.error);

    const orders = raw.filter(o =>
      o.underlying === ticker || o.symbol === ticker || o.underlying.startsWith(ticker));
    _stratOrders = orders;

    countDiv.textContent = orders.length + ' open order' + (orders.length !== 1 ? 's' : '');

    if (!orders.length) {
      container.innerHTML = '<div style="color:#475569;font-size:12px">No open orders for ' + esc(ticker) + '</div>';
      return;
    }

    cancelBtn.style.display = '';

    const rows = orders.map(o => {
      const sc = (o.instruction || '').includes('SELL') ? 'neg' : 'pos';
      const p = o.price != null ? '$' + fmt(o.price, 2)
              : o.stop_price != null ? 'Stp $' + fmt(o.stop_price) : '—';
      const cancelHtml = o.cancelable
        ? '<button class="cancel-single-btn" onclick="cancelStratOrder(\'' + esc(o.order_id) + '\')">✕</button>'
        : '';
      return '<tr>' +
        '<td><span class="' + sc + '">' + esc(o.instruction) + '</span></td>' +
        '<td>' + fmt(o.quantity, 0) + '</td>' +
        '<td>' + p + '</td>' +
        '<td><span class="badge badge-status-' + o.status + '" style="font-size:9px;padding:1px 5px">' + o.status + '</span></td>' +
        '<td>' + cancelHtml + '</td>' +
      '</tr>';
    }).join('');

    container.innerHTML =
      '<table><thead><tr><th>Side</th><th>Qty</th><th>Price</th><th>Status</th><th></th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table>';
  } catch(e) {
    container.innerHTML = '<div class="error" style="font-size:12px">Error: ' + esc(e.message) + '</div>';
  }
}

async function cancelStratOrder(orderId) {
  if (!confirm('Cancel order ' + orderId + '?')) return;
  try {
    const res = await fetch('/api/order/' + orderId, { method: 'DELETE' }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    ordersState.loaded = false;
    await loadStrategyOrders();
  } catch(e) { alert('Failed: ' + e.message); }
}

async function cancelAllStratOrders() {
  const cancelable = _stratOrders.filter(o => o.cancelable);
  if (!cancelable.length) { alert('No cancelable orders.'); return; }
  if (!confirm('Cancel ALL ' + cancelable.length + ' open orders for ' + _stratTicker + '?')) return;

  const container = document.getElementById('strat-orders');
  container.innerHTML = '<div class="loading" style="padding:6px">Cancelling…</div>';

  let ok = 0, fail = 0;
  for (const o of cancelable) {
    try {
      await fetch('/api/order/' + o.order_id, { method: 'DELETE' }).then(r => r.json());
      ok++;
    } catch { fail++; }
  }
  ordersState.loaded = false;
  container.innerHTML = fail === 0
    ? '<div style="color:#86efac;font-size:12px;padding:4px 0">All ' + ok + ' cancelled.</div>'
    : '<div class="error" style="font-size:12px">' + ok + ' cancelled, ' + fail + ' failed.</div>';
  setTimeout(() => loadStrategyOrders(), 1500);
}

// ── P&L preview ──────────────────────────────────────────────────
function _stratExpiryDteDays() {
  const v = document.getElementById('strat-expiry').value;
  if (!v) return null;
  const end = new Date(v + 'T12:00:00');
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  end.setHours(0, 0, 0, 0);
  const diff = Math.round((end - start) / 86400000);
  return Math.max(1, diff);
}

function _stratShortStrikeForScore() {
  if (stratMode === 'naked') {
    const x = parseFloat(document.getElementById('sn-strike').value);
    return Number.isFinite(x) && x > 0 ? x : null;
  }
  if (stratMode === 'vertical') {
    const x = parseFloat(document.getElementById('sv-sell-strike').value);
    return Number.isFinite(x) && x > 0 ? x : null;
  }
  if (stratMode === 'collar') {
    const x = parseFloat(document.getElementById('sc-sell-strike').value);
    return Number.isFinite(x) && x > 0 ? x : null;
  }
  return null;
}

/** Max dollars kept in a “perfect” short-premium outcome (credit kept, no close cost). */
function _stratPerfectProfitDollars(info) {
  if (!info) return null;
  if (typeof info.max_profit === 'number') return info.max_profit;
  if (typeof info.net_credit === 'number') return info.net_credit;
  if (info.net_cost === 0) return 0;
  return null;
}

/**
 * Pre-trade hypothetical: max_profit (or net credit) ÷ DTE ÷ short strike × 100.
 * Uses calendar days to expiration; ignores fees and path risk.
 */
function _stratPreviewMaxEfficiencyScore() {
  if (stratMode === 'bundle') return null;
  const dte = _stratExpiryDteDays();
  if (dte == null) return null;
  const strike = _stratShortStrikeForScore();
  if (strike == null) return null;
  let info = null;
  try {
    if (stratMode === 'naked') info = _calcNakedPnl();
    else if (stratMode === 'vertical') info = _calcVerticalPnl();
    else if (stratMode === 'collar') info = _calcCollarPnl();
  } catch (e) { info = null; }
  if (!info) return null;
  const dollars = _stratPerfectProfitDollars(info);
  if (dollars == null || typeof dollars !== 'number') return null;
  const score = (dollars / dte) * (100 / strike);
  return {
    valueStr: score.toFixed(4),
    title: 'Hypothetical if max profit is realized by expiration: max profit (or net credit) ÷ days to expiry ÷ short strike × 100. Ignores fees, assignment, and path.',
  };
}

function updateStratPnl() {
  const box = document.getElementById('strat-pnl');
  if (_stratLadderEnabled() && stratMode !== 'bundle') {
    const ladderHtml = _stratLadderPnlHtml();
    if (!ladderHtml) {
      box.style.display = 'none';
      return;
    }
    box.style.display = '';
    box.innerHTML = ladderHtml;
    return;
  }

  let info = null;

  try {
    if (stratMode === 'naked') {
      info = _calcNakedPnl();
    } else if (stratMode === 'vertical') {
      info = _calcVerticalPnl();
    } else if (stratMode === 'collar') {
      info = _calcCollarPnl();
    } else if (stratMode === 'bundle') {
      info = null;
    }
  } catch(e) { info = null; }

  if (!info) {
    box.style.display = 'none';
    return;
  }

  box.style.display = '';
  const items = Object.entries(info).map(([k, v]) => {
    const label = k.replace(/_/g, ' ');
    let valCls = '';
    if (typeof v === 'number') {
      if (k.includes('profit') || k.includes('credit')) valCls = v >= 0 ? 'pos' : 'neg';
      if (k.includes('loss')) valCls = 'neg';
    }
    const display = typeof v === 'number' ? '$' + fmt(v) : (v || '—');
    return '<div class="strat-pnl-item"><label>' + esc(label) + '</label><div class="val ' + valCls + '">' + display + '</div></div>';
  }).join('');

  const scorePrev = _stratPreviewMaxEfficiencyScore();
  const scoreHtml = scorePrev
    ? '<div class="strat-pnl-item" title="' + esc(scorePrev.title) + '"><label>Max score (perfect, ÷DTE)</label><div class="val pos">' + esc(scorePrev.valueStr) + '</div></div>'
    : '';

  box.innerHTML = '<div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;font-weight:600">P&L Preview</div>' +
    '<div class="strat-pnl-grid">' + items + scoreHtml + '</div>';
}

function _stratLadderPnlHtml() {
  const rungs = _collectStratLadderRungs();
  if (!rungs.length) return null;

  let rows = '';
  let sumCredit = 0, sumDebit = 0, nCredit = 0, nDebit = 0;
  let sumMaxProfitNum = 0, sumMaxLossNum = 0, nMaxProfit = 0, nMaxLoss = 0;
  let anyUnlimitedLoss = false, anyUnlimitedProfit = false;

  rungs.forEach((r, idx) => {
    let m = null;
    try {
      if (stratMode === 'naked') m = _calcNakedPnlFor(r.qty, r.price);
      else if (stratMode === 'vertical') m = _calcVerticalPnlFor(r.qty, r.price);
      else if (stratMode === 'collar') m = _calcCollarPnlFor(r.qty, r.price);
    } catch (e) { m = null; }

    const step = idx + 1;
    if (!m) {
      rows += '<tr class="strat-ladder-pnl-row"><td>#' + step + '</td><td colspan="4" class="strat-ladder-muted">—</td></tr>';
      return;
    }

    const nc = m.net_credit;
    const nd = m.net_debit != null ? m.net_debit : (m.net_cost != null ? m.net_cost : null);
    if (typeof nc === 'number') { sumCredit += nc; nCredit++; }
    if (typeof nd === 'number') { sumDebit += nd; nDebit++; }

    const mp = m.max_profit;
    const ml = m.max_loss;
    if (typeof mp === 'number') { sumMaxProfitNum += mp; nMaxProfit++; }
    else if (mp === 'Unlimited') anyUnlimitedProfit = true;
    if (typeof ml === 'number') { sumMaxLossNum += ml; nMaxLoss++; }
    else if (ml === 'Unlimited') anyUnlimitedLoss = true;

    const cred = typeof nc === 'number' ? '$' + fmt(nc) : '—';
    const deb = typeof nd === 'number' ? '$' + fmt(nd) : '—';
    const mpDisp = typeof mp === 'number' ? '$' + fmt(mp) : esc(String(mp || '—'));
    const mlDisp = typeof ml === 'number' ? '$' + fmt(ml) : esc(String(ml || '—'));

    rows += '<tr class="strat-ladder-pnl-row"><td>#' + step + '</td><td>' + r.qty + ' @ $' + fmt(r.price) + '</td><td>' + cred + '</td><td>' + deb + '</td><td class="strat-ladder-pnl-mm">' + mpDisp + ' / ' + mlDisp + '</td></tr>';
  });

  const totCred = nCredit ? '$' + fmt(sumCredit) : '—';
  const totDeb = nDebit ? '$' + fmt(sumDebit) : '—';
  const totMp = anyUnlimitedProfit ? '∞' : (nMaxProfit ? '$' + fmt(sumMaxProfitNum) : '—');
  const totMl = anyUnlimitedLoss ? '∞' : (nMaxLoss ? '$' + fmt(sumMaxLossNum) : '—');

  const foot = '<tr class="strat-ladder-pnl-totals"><td><b>Total</b></td><td></td><td><b>' + totCred + '</b></td><td><b>' + totDeb + '</b></td><td><b>' + totMp + ' / ' + totMl + '</b></td></tr>';

  let scoreNote = '';
  const dteL = _stratExpiryDteDays();
  const strikeL = _stratShortStrikeForScore();
  const r0 = rungs[0];
  if (dteL != null && strikeL != null && r0) {
    let m0 = null;
    try {
      if (stratMode === 'naked') m0 = _calcNakedPnlFor(r0.qty, r0.price);
      else if (stratMode === 'vertical') m0 = _calcVerticalPnlFor(r0.qty, r0.price);
      else if (stratMode === 'collar') m0 = _calcCollarPnlFor(r0.qty, r0.price);
    } catch (e) { m0 = null; }
    const dollars0 = _stratPerfectProfitDollars(m0);
    if (dollars0 != null && typeof dollars0 === 'number') {
      const sc0 = (dollars0 / dteL) * (100 / strikeL);
      const stitle = 'Hypothetical for rung 1: max profit ÷ DTE ÷ short strike × 100; ignores fees and path.';
      scoreNote = '<p class="strat-ladder-muted" style="margin-top:8px;font-size:11px" title="' + esc(stitle) + '">Max score (perfect, ÷DTE), rung 1: <b class="pos">' + sc0.toFixed(4) + '</b></p>';
    }
  }

  return (
    '<div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;font-weight:600">P&amp;L Preview (ladder)</div>' +
    '<div class="strat-ladder-pnl-wrap"><table class="strat-ladder-pnl-table">' +
    '<thead><tr><th>Step</th><th>Qty @ price</th><th>Credit</th><th>Debit</th><th>Max P / Max L</th></tr></thead>' +
    '<tbody>' + rows + foot + '</tbody></table></div>' + scoreNote
  );
}

function _calcNakedPnl() {
  return _calcNakedPnlFor(
    parseInt(document.getElementById('sn-qty').value, 10) || 1,
    parseFloat(document.getElementById('sn-price').value)
  );
}

function _calcNakedPnlFor(qty, price) {
  const strike = parseFloat(document.getElementById('sn-strike').value);
  const type   = document.getElementById('sn-type').value;
  const action = document.getElementById('sn-action').value;
  if (!strike || !price || !qty) return null;

  const isSell = action.includes('SELL');
  const total  = price * qty * 100;

  if (isSell && type === 'CALL') {
    return {
      net_credit: total,
      max_profit: total,
      max_loss: 'Unlimited',
      breakeven: round2(strike + price),
    };
  } else if (isSell && type === 'PUT') {
    return {
      net_credit: total,
      max_profit: total,
      max_loss: round2((strike - price) * qty * 100),
      breakeven: round2(strike - price),
    };
  } else if (!isSell && type === 'CALL') {
    return {
      net_debit: total,
      max_profit: 'Unlimited',
      max_loss: total,
      breakeven: round2(strike + price),
    };
  } else {
    return {
      net_debit: total,
      max_profit: round2((strike - price) * qty * 100),
      max_loss: total,
      breakeven: round2(strike - price),
    };
  }
}

function _calcVerticalPnl() {
  return _calcVerticalPnlFor(
    parseInt(document.getElementById('sv-qty').value, 10) || 1,
    parseFloat(document.getElementById('sv-price').value)
  );
}

function _calcVerticalPnlFor(qty, netPrice) {
  const sellStrike = parseFloat(document.getElementById('sv-sell-strike').value);
  const buyStrike  = parseFloat(document.getElementById('sv-buy-strike').value);
  const orderType  = document.getElementById('sv-order-type').value;
  if (!sellStrike || !buyStrike) return null;
  if (orderType !== 'NET_ZERO' && (netPrice == null || isNaN(netPrice))) return null;

  const width   = Math.abs(buyStrike - sellStrike);
  const isCredit = orderType === 'NET_CREDIT';
  const total    = (netPrice || 0) * qty * 100;

  if (isCredit) {
    return {
      net_credit: total,
      max_profit: total,
      max_loss:   round2((width - netPrice) * qty * 100),
      breakeven:  round2(sellStrike + (sellStrike < buyStrike ? netPrice : -netPrice)),
    };
  } else {
    return {
      net_debit:  total,
      max_profit: round2((width - netPrice) * qty * 100),
      max_loss:   total,
      breakeven:  round2(buyStrike - (buyStrike < sellStrike ? netPrice : -netPrice)),
    };
  }
}

function _calcCollarPnl() {
  return _calcCollarPnlFor(
    parseInt(document.getElementById('sc-qty').value, 10) || 1,
    parseFloat(document.getElementById('sc-price').value) || 0
  );
}

function _calcCollarPnlFor(qty, netPrice) {
  const sellStrike = parseFloat(document.getElementById('sc-sell-strike').value);
  const buyStrike  = parseFloat(document.getElementById('sc-buy-strike').value);
  const orderType  = document.getElementById('sc-order-type').value;
  if (!sellStrike || !buyStrike) return null;

  const total = (netPrice || 0) * qty * 100;
  const isCredit = orderType === 'NET_CREDIT';

  return {
    [isCredit ? 'net_credit' : (orderType === 'NET_ZERO' ? 'net_cost' : 'net_debit')]:
      orderType === 'NET_ZERO' ? 0 : total,
    protection_floor: Math.min(sellStrike, buyStrike),
    cap_ceiling: Math.max(sellStrike, buyStrike),
  };
}

function round2(v) { return Math.round(v * 100) / 100; }

// ── Strategy preview & submit ────────────────────────────────────
/** Build one strategy order from per-rung qty/price (naked / vertical / collar only). */
function _buildStrategyOrderCoreForRung(qty, price) {
  const ticker = _stratTicker;
  const expiry = document.getElementById('strat-expiry').value;
  if (!ticker) throw new Error('Ticker is required');
  if (!expiry) throw new Error('Expiration is required');

  const OP = (type, strike) => {
    const m = expiry.split('-');
    return ticker.padEnd(6) +
      m[0].slice(2) + m[1] + m[2] +
      type[0] +
      String(Math.round(strike * 1000)).padStart(8, '0');
  };

  let legs, orderType, strategy, summary, priceOut;

  if (stratMode === 'naked') {
    const optType = document.getElementById('sn-type').value;
    const action  = document.getElementById('sn-action').value;
    const strike  = parseFloat(document.getElementById('sn-strike').value);
    if (!strike || strike <= 0) throw new Error('Strike is required');
    if (!price || price <= 0) throw new Error('Limit price is required');
    if (!qty || qty < 1) throw new Error('Contracts must be at least 1');

    const chain  = _findChainContract(optType, strike, expiry);
    const symbol = chain ? chain.symbol : OP(optType, strike);

    legs = [{ type: 'option', instruction: action, symbol, quantity: qty }];
    orderType = 'LIMIT';
    strategy  = 'naked';
    priceOut  = price;

    const aLabel = { SELL_TO_OPEN: 'Sell to Open', BUY_TO_OPEN: 'Buy to Open',
                     BUY_TO_CLOSE: 'Buy to Close', SELL_TO_CLOSE: 'Sell to Close' }[action];
    const badge  = optType === 'PUT' ? '<span class="badge badge-PUT">PUT</span>' : '<span class="badge badge-CALL">CALL</span>';
    summary = `<b>${aLabel}</b> ${qty} ${badge} @ $${strike} — ${expiry}<br>Limit: $${price}/sh ($${(price * 100).toFixed(0)}/contract)`;

  } else if (stratMode === 'vertical') {
    const optType    = document.getElementById('sv-type').value;
    const sellStrike = parseFloat(document.getElementById('sv-sell-strike').value);
    const buyStrike  = parseFloat(document.getElementById('sv-buy-strike').value);
    orderType        = document.getElementById('sv-order-type').value;
    if (!sellStrike || !buyStrike) throw new Error('Both strikes are required');
    if (!qty || qty < 1) throw new Error('Contracts must be at least 1');
    if (orderType !== 'NET_ZERO' && (price == null || isNaN(price) || price < 0)) throw new Error('Price is required');

    const sellSym = (_findChainContract(optType, sellStrike, expiry) || {}).symbol || OP(optType, sellStrike);
    const buySym  = (_findChainContract(optType, buyStrike, expiry) || {}).symbol || OP(optType, buyStrike);

    legs = [
      { type: 'option', instruction: 'SELL_TO_OPEN', symbol: sellSym, quantity: qty },
      { type: 'option', instruction: 'BUY_TO_OPEN',  symbol: buySym,  quantity: qty },
    ];
    strategy = 'vertical';
    priceOut = orderType === 'NET_ZERO' ? undefined : price;
    const badge = optType === 'PUT' ? '<span class="badge badge-PUT">PUT</span>' : '<span class="badge badge-CALL">CALL</span>';
    summary = `<b>Vertical ${badge} Spread</b> — Sell $${sellStrike} / Buy $${buyStrike} × ${qty}<br>${expiry} · ${orderType.replace(/_/g,' ')} $${price != null ? price : 0}`;

  } else if (stratMode === 'collar') {
    const sellType   = document.getElementById('sc-sell-type').value;
    const sellStrike = parseFloat(document.getElementById('sc-sell-strike').value);
    const buyType    = document.getElementById('sc-buy-type').value;
    const buyStrike  = parseFloat(document.getElementById('sc-buy-strike').value);
    orderType        = document.getElementById('sc-order-type').value;
    if (!sellStrike || !buyStrike) throw new Error('Both strikes are required');
    if (!qty || qty < 1) throw new Error('Contracts must be at least 1');
    if (orderType !== 'NET_ZERO' && (price == null || isNaN(price) || price < 0)) throw new Error('Price is required');

    const sellSym = (_findChainContract(sellType, sellStrike, expiry) || {}).symbol || OP(sellType, sellStrike);
    const buySym  = (_findChainContract(buyType, buyStrike, expiry) || {}).symbol || OP(buyType, buyStrike);

    legs = [
      { type: 'option', instruction: 'SELL_TO_OPEN', symbol: sellSym, quantity: qty },
      { type: 'option', instruction: 'BUY_TO_OPEN',  symbol: buySym,  quantity: qty },
    ];
    strategy = 'collar';
    priceOut = orderType === 'NET_ZERO' ? undefined : (price || 0);
    summary = `<b>Collar</b> — Sell ${sellType} $${sellStrike} + Buy ${buyType} $${buyStrike} × ${qty}<br>${expiry} · ${orderType.replace(/_/g,' ')} $${price != null ? price : 0}`;

  } else {
    throw new Error('Unknown strategy mode');
  }

  return { strategy, legs, order_type: orderType, price: priceOut, _summaryRung: summary };
}

function _buildStrategyPayload() {
  const duration = document.getElementById('strat-duration').value;
  const session  = document.getElementById('strat-session').value;

  if (stratMode === 'bundle') {
    const ticker   = _stratTicker;
    const expiry   = document.getElementById('strat-expiry').value;
    if (!ticker)  throw new Error('Ticker is required');
    if (!expiry)  throw new Error('Expiration is required');

    const OP = (type, strike) => {
      const m = expiry.split('-');
      return ticker.padEnd(6) +
        m[0].slice(2) + m[1] + m[2] +
        type[0] +
        String(Math.round(strike * 1000)).padStart(8, '0');
    };

    const eqAction = document.getElementById('sb-eq-action').value;
    const eqQty    = parseInt(document.getElementById('sb-eq-qty').value, 10);
    const optType  = document.getElementById('sb-opt-type').value;
    const optAction = document.getElementById('sb-opt-action').value;
    const strike    = parseFloat(document.getElementById('sb-strike').value);
    const optQty    = parseInt(document.getElementById('sb-opt-qty').value, 10) || 1;
    const price     = parseFloat(document.getElementById('sb-price').value) || 0;
    const orderType = document.getElementById('sb-order-type').value;

    if (!eqQty || eqQty <= 0)  throw new Error('Equity quantity is required');
    if (!strike || strike <= 0) throw new Error('Strike is required');

    const optSym = (_findChainContract(optType, strike, expiry) || {}).symbol || OP(optType, strike);

    const legs = [
      { type: 'equity', instruction: eqAction, symbol: ticker, quantity: eqQty },
      { type: 'option', instruction: optAction, symbol: optSym, quantity: optQty },
    ];
    const strategy = 'bundle';

    const eqLabel = { BUY:'Buy', SELL:'Sell', SELL_SHORT:'Sell Short', BUY_TO_COVER:'Buy to Cover' }[eqAction];
    const optLabel = { SELL_TO_OPEN:'STO', BUY_TO_OPEN:'BTO' }[optAction];
    const summary = `<b>Bundle</b> — ${eqLabel} ${eqQty} shares + ${optLabel} ${optQty} ${optType} $${strike}<br>${expiry} · ${orderType.replace(/_/g,' ')} $${price}`;

    return {
      strategy, legs, order_type: orderType,
      price: orderType === 'NET_ZERO' ? undefined : price,
      duration, session, _summary: summary,
    };
  }

  if (_stratLadderEnabled()) {
    const rungs = _collectStratLadderRungs();
    const expect = _stratLadderStepsCount();
    if (rungs.length !== expect) {
      throw new Error('Ladder has ' + rungs.length + ' steps but ' + expect + ' were selected — toggle ladder to refresh');
    }
    for (let i = 0; i < rungs.length; i++) {
      const r = rungs[i];
      if (!r.qty || r.qty < 1) throw new Error('Step ' + (i + 1) + ': enter contracts (min 1)');
      if (stratMode === 'naked') {
        if (!r.price || r.price <= 0) throw new Error('Step ' + (i + 1) + ': limit price required');
      } else {
        const ot = stratMode === 'vertical'
          ? document.getElementById('sv-order-type').value
          : document.getElementById('sc-order-type').value;
        if (ot !== 'NET_ZERO' && (r.price == null || isNaN(r.price) || r.price < 0)) {
          throw new Error('Step ' + (i + 1) + ': net price required');
        }
      }
    }

    const orders = rungs.map(r => {
      const core = _buildStrategyOrderCoreForRung(r.qty, r.price);
      return {
        strategy: core.strategy,
        legs: core.legs,
        order_type: core.order_type,
        price: core.price,
        duration,
        session,
        _rung_qty: r.qty,
        _rung_price: r.price,
        _summaryRung: core._summaryRung,
      };
    });

    const rows = orders.map((o, i) =>
      '<tr><td>' + (i + 1) + '</td><td>' + esc(String(o._rung_qty)) + '</td><td>$' + fmt(o._rung_price) + '</td><td class="strat-ladder-prev-detail">' + o._summaryRung + '</td></tr>'
    ).join('');

    const summary =
      '<div class="strat-ladder-preview-table-wrap">' +
      '<table class="strat-ladder-preview-table"><thead><tr><th>Step</th><th>Contracts</th><th>Price</th><th>Detail</th></tr></thead><tbody>' +
      rows + '</tbody></table></div>';

    return { _ladder: true, orders, _summary: summary };
  }

  let qty, price;
  if (stratMode === 'naked') {
    qty = parseInt(document.getElementById('sn-qty').value, 10) || 1;
    price = parseFloat(document.getElementById('sn-price').value);
  } else if (stratMode === 'vertical') {
    qty = parseInt(document.getElementById('sv-qty').value, 10) || 1;
    price = parseFloat(document.getElementById('sv-price').value);
  } else if (stratMode === 'collar') {
    qty = parseInt(document.getElementById('sc-qty').value, 10) || 1;
    price = parseFloat(document.getElementById('sc-price').value) || 0;
  } else {
    throw new Error('Unknown strategy mode');
  }

  const core = _buildStrategyOrderCoreForRung(qty, price);
  return {
    strategy: core.strategy,
    legs: core.legs,
    order_type: core.order_type,
    price: core.price,
    duration,
    session,
    _summary: core._summaryRung,
  };
}

function _findChainContract(type, strike, expiry) {
  if (!_stratChainData) return null;
  const map = type === 'CALL' ? _stratChainData.calls : _stratChainData.puts;
  const contracts = map ? (map[expiry] || []) : [];
  return contracts.find(c => c.strike === strike) || null;
}

// Populate all strike <select> elements from the currently loaded chain data.
// Each dropdown type-pair (CALL or PUT based on context) gets the right strikes.
// Build <option> HTML for a strike dropdown, using only the currently visible
// page strikes but keeping the currently selected value even if it's off-page
// (shown as an out-of-range entry so the form doesn't silently clear it).
function _makeStrikeOpts(type, currentValue) {
  const map = type === 'CALL' ? _chainCallMap : _chainPutMap;
  // Strikes to show = visible page; if selected value is outside page, append it
  let strikes = _chainVisibleStrikes.slice();
  const curFloat = parseFloat(currentValue);
  if (curFloat && !strikes.includes(curFloat)) strikes = [...strikes, curFloat].sort((a,b)=>a-b);

  let html = '<option value="">— select strike —</option>';
  strikes.forEach(k => {
    const c   = map[k] || {};
    const sel = (curFloat === k) ? ' selected' : '';
    const mid = (c.bid != null && c.ask != null) ? ' · mid ' + ((c.bid + c.ask) / 2).toFixed(2) : '';
    const outOfPage = !_chainVisibleStrikes.includes(k) ? ' ◀ off-page' : '';
    html += `<option value="${k}"${sel}>$${k.toFixed(2)} (bid ${(c.bid || 0).toFixed(2)} / ask ${(c.ask || 0).toFixed(2)}${mid}${outOfPage})</option>`;
  });
  return html;
}

function _populateStrikeDropdowns() {
  if (!_chainVisibleStrikes.length) return;

  const snSel = document.getElementById('sn-strike');
  snSel.innerHTML = _makeStrikeOpts(document.getElementById('sn-type').value, snSel.value);

  const svSell = document.getElementById('sv-sell-strike');
  const svBuy  = document.getElementById('sv-buy-strike');
  const svType = document.getElementById('sv-type').value;
  svSell.innerHTML = _makeStrikeOpts(svType, svSell.value);
  svBuy.innerHTML  = _makeStrikeOpts(svType, svBuy.value);

  const scSell = document.getElementById('sc-sell-strike');
  const scBuy  = document.getElementById('sc-buy-strike');
  scSell.innerHTML = _makeStrikeOpts(document.getElementById('sc-sell-type').value, scSell.value);
  scBuy.innerHTML  = _makeStrikeOpts(document.getElementById('sc-buy-type').value,  scBuy.value);

  const sbSel = document.getElementById('sb-strike');
  sbSel.innerHTML = _makeStrikeOpts(document.getElementById('sb-opt-type').value, sbSel.value);
}

// Auto-calculate and prefill the price field whenever a strike or type changes.
// For a sell leg: use bid (what you receive). For a buy leg: use ask (what you pay).
// Net = sell_bid - buy_ask.
function _autoCalcStratPrice() {
  if (!_stratChainData) { updateStratPnl(); return; }
  const expiry = document.getElementById('strat-expiry').value;
  if (!expiry) { updateStratPnl(); return; }

  const getContract = (type, strikeVal) => {
    const strike = parseFloat(strikeVal);
    if (!strike) return null;
    return _findChainContract(type, strike, expiry);
  };

  if (stratMode === 'naked') {
    const type   = document.getElementById('sn-type').value;
    const action = document.getElementById('sn-action').value;
    const c      = getContract(type, document.getElementById('sn-strike').value);
    if (c) {
      const isSell = action.includes('SELL');
      // sell: use bid (conservative); buy: use ask (conservative)
      const price = isSell
        ? (c.bid != null ? c.bid : null)
        : (c.ask != null ? c.ask : null);
      if (price != null) document.getElementById('sn-price').value = price.toFixed(2);
    }

  } else if (stratMode === 'vertical') {
    const type    = document.getElementById('sv-type').value;
    const sellC   = getContract(type, document.getElementById('sv-sell-strike').value);
    const buyC    = getContract(type, document.getElementById('sv-buy-strike').value);
    if (sellC && buyC && sellC.bid != null && buyC.ask != null) {
      const net = sellC.bid - buyC.ask;
      document.getElementById('sv-price').value = Math.abs(net).toFixed(2);
      document.getElementById('sv-order-type').value = net >= 0 ? 'NET_CREDIT' : 'NET_DEBIT';
    } else if (sellC && !buyC && sellC.bid != null) {
      document.getElementById('sv-price').value = sellC.bid.toFixed(2);
    }

  } else if (stratMode === 'collar') {
    const sellType = document.getElementById('sc-sell-type').value;
    const buyType  = document.getElementById('sc-buy-type').value;
    const sellC    = getContract(sellType, document.getElementById('sc-sell-strike').value);
    const buyC     = getContract(buyType,  document.getElementById('sc-buy-strike').value);
    if (sellC && buyC && sellC.bid != null && buyC.ask != null) {
      const net = sellC.bid - buyC.ask;
      document.getElementById('sc-price').value = Math.abs(net).toFixed(2);
      document.getElementById('sc-order-type').value = net >= 0 ? 'NET_CREDIT' : (net === 0 ? 'NET_ZERO' : 'NET_DEBIT');
    } else if (sellC && !buyC && sellC.bid != null) {
      document.getElementById('sc-price').value = sellC.bid.toFixed(2);
    }

  } else if (stratMode === 'bundle') {
    const type   = document.getElementById('sb-opt-type').value;
    const action = document.getElementById('sb-opt-action').value;
    const c      = getContract(type, document.getElementById('sb-strike').value);
    if (c) {
      const isSell = action.includes('SELL');
      const price  = isSell ? c.bid : c.ask;
      if (price != null) document.getElementById('sb-price').value = price.toFixed(2);
    }
  }

  _syncAutoPriceToStratLadderRung1();
  updateStratPnl();
}

function previewStrategy() {
  const resultDiv = document.getElementById('strat-result');
  try {
    const payload = _buildStrategyPayload();
    _stratPendingOrder = payload;

    const durLabel = document.getElementById('strat-duration').value === 'gtc' ? 'GTC' : 'Day';
    const sesLabel = {normal:'Normal', seamless:'Extended Hrs'}[document.getElementById('strat-session').value] || 'Normal';
    const isLad = !!payload._ladder;
    const title = isLad
      ? ('Review Strategy Ladder (' + payload.orders.length + ' rungs)')
      : 'Review Strategy Order';

    resultDiv.innerHTML =
      '<div class="preview-box">' +
        '<div class="preview-title">' + esc(title) + '</div>' +
        '<div class="preview-summary">' + payload._summary + '<br>' + durLabel + ' · ' + sesLabel + '</div>' +
        '<div style="font-size:11px;color:#64748b;margin-bottom:14px">Verify all details before confirming.</div>' +
        '<div class="preview-actions">' +
          '<button class="cancel-btn" onclick="document.getElementById(\'strat-result\').innerHTML=\'\'">Cancel</button>' +
          '<button class="confirm-btn" onclick="submitStrategy()">Confirm &amp; Submit</button>' +
        '</div>' +
      '</div>';
  } catch(e) {
    resultDiv.innerHTML = '<div class="error" style="margin-top:12px;padding:10px 14px;border-radius:6px">' + esc(e.message) + '</div>';
  }
}

async function submitStrategy() {
  if (!_stratPendingOrder) return;
  const payload = _stratPendingOrder;
  _stratPendingOrder = null;
  const div = document.getElementById('strat-result');

  if (payload._ladder) {
    const n = payload.orders.length;
    div.innerHTML = '<div class="loading">Submitting ' + n + ' strategy orders…</div>';
    try {
      const res = await fetchJson('/api/order/strategy-ladder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ orders: payload.orders }),
      });

      const ok = res.results.filter(r => r.status === 'ok').length;
      const fail = res.results.length - ok;
      const statusMsg = fail === 0
        ? `<div class="success-box">All ${ok} strategy orders submitted.</div>`
        : `<div class="error">⚠ ${ok} succeeded, ${fail} failed</div>`;

      div.innerHTML = statusMsg + ladderResultTableHtml(res.results, {
        qtyColumnLabel: 'Contracts',
        tableExtraClass: 'strat-ladder-result-table',
        footerHtml: '<div class="ladder-result-hint">Check Open Orders for partial fills.</div>',
      });
      ordersState.loaded = false;
      setTimeout(() => loadStrategyOrders(), 1000);
    } catch (e) {
      div.innerHTML = '<div class="error" style="margin-top:0">Ladder failed: ' + esc(e.message) + '</div>';
    }
    return;
  }

  div.innerHTML = '<div class="loading">Submitting strategy order…</div>';
  try {
    const body = { ...payload };
    delete body._summary;
    const res = await fetch('/api/order/strategy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(r => r.json());

    if (res.error) throw new Error(res.error);
    div.innerHTML =
      '<div class="success-box">Strategy order submitted!<br>' +
      '<b>Order ID: ' + esc(res.order_id) + '</b><br>' +
      '<small style="color:#6ee7b7;opacity:0.7">Check "Open Orders" to track.</small></div>';
    ordersState.loaded = false;
    setTimeout(() => loadStrategyOrders(), 1000);
  } catch(e) {
    div.innerHTML = '<div class="error" style="margin-top:0">Order failed: ' + esc(e.message) + '</div>';
  }
}

// ── Init ──────────────────────────────────────────────────────────
document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
loadPositions();
loadOverview();
initWatchlists();
renderRungs();
