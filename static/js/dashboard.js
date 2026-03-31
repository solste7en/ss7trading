const fmt   = (v,d=2) => v==null ? '—' : Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const fmtD  = (v,d=2) => v==null ? '—' : (v>=0?'+':'') + fmt(v,d);
const cls   = (v)     => v==null ? '' : (v>=0?'pos':'neg');
const esc   = (s)     => String(s||'').replace(/</g,'&lt;');
let _debTimer = null;
function debounce(fn) { clearTimeout(_debTimer); _debTimer = setTimeout(fn, 400); }

// ── tab state ──────────────────────────────────────────────────────
const TAB_NAMES = ['overview','positions','quotes','history','gains','trade','ladder','orders'];
let currentTab = 'overview';

function switchTab(name) {
  currentTab = name;
  document.querySelectorAll('.tab').forEach((t,i) =>
    t.classList.toggle('active', TAB_NAMES[i] === name));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'overview' && !overviewState.loaded)  loadOverview();
  if (name === 'history'  && !historyState.loaded)   loadHistory();
  if (name === 'gains'    && !gainsState.loaded)     loadGains();
  if (name === 'orders'   && !ordersState.loaded)    loadOrders();
}

function refreshCurrent() {
  document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  if (currentTab === 'overview')  loadOverview();
  if (currentTab === 'positions') loadPositions();
  if (currentTab === 'quotes')    loadQuotes();
  if (currentTab === 'history')   loadHistory();
  if (currentTab === 'gains')     loadGains();
  if (currentTab === 'orders')    loadOrders();
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
    const data = await fetch('/api/top-tickers').then(r => r.json());
    if (data.error) throw new Error(data.error);
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
  ladderRecentState.page = 1;
  loadLadderRecent();
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

// ── Positions ─────────────────────────────────────────────────────
async function loadPositions() {
  try {
    const data = await fetch('/api/positions').then(r=>r.json());
    document.getElementById('pos-tbody').innerHTML = data.map(p => `<tr>
      <td><b>${esc(p.symbol)}</b></td>
      <td><span class="badge badge-${p.asset_type}">${p.asset_type}</span></td>
      <td>${fmt(p.quantity,0)}</td>
      <td>${p.avg_price!=null?'$'+fmt(p.avg_price,4):'—'}</td>
      <td>${p.market_value!=null?'$'+fmt(p.market_value):'—'}</td>
      <td class="${cls(p.unrealized_pl)}">${p.unrealized_pl!=null?'$'+fmtD(p.unrealized_pl):'—'}</td>
      <td class="${cls(p.day_pl)}">${p.day_pl!=null?'$'+fmtD(p.day_pl):'—'}</td>
      <td class="${cls(p.day_pl_pct)}">${p.day_pl_pct!=null?fmtD(p.day_pl_pct)+'%':'—'}</td>
    </tr>`).join('');
    document.getElementById('pos-loading').style.display='none';
    document.getElementById('pos-table').style.display='block';
  } catch(e) {
    document.getElementById('pos-loading').style.display='none';
    document.getElementById('pos-error').style.display='block';
    document.getElementById('pos-error').textContent='Error: '+e.message;
  }
}

// ── Quotes ────────────────────────────────────────────────────────
async function loadQuotes() {
  try {
    const data = await fetch('/api/quotes').then(r=>r.json());
    document.getElementById('q-tbody').innerHTML = data.map(q=>`<tr>
      <td><b>${esc(q.symbol)}</b></td><td>$${fmt(q.last)}</td>
      <td>${q.bid!=null?'$'+fmt(q.bid):'—'}</td>
      <td>${q.ask!=null?'$'+fmt(q.ask):'—'}</td>
      <td class="${cls(q.change)}">${q.change!=null?'$'+fmtD(q.change):'—'}</td>
      <td class="${cls(q.change_pct)}">${q.change_pct!=null?fmtD(q.change_pct)+'%':'—'}</td>
      <td>${q.volume!=null?Number(q.volume).toLocaleString():'—'}</td>
      <td>${q['52w_high']!=null?'$'+fmt(q['52w_high']):'—'}</td>
      <td>${q['52w_low']!=null?'$'+fmt(q['52w_low']):'—'}</td>
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

async function loadHistory(resetPage=true) {
  if (resetPage) historyState.page = 1;
  const ticker   = document.getElementById('h-ticker').value.trim();
  const search   = document.getElementById('h-search').value.trim();
  const category = document.getElementById('h-category').value;
  const limit    = document.getElementById('h-limit').value;

  document.getElementById('h-loading').style.display='block';
  document.getElementById('h-table').style.display='none';
  document.getElementById('h-error').style.display='none';

  try {
    const params = new URLSearchParams({
      page: historyState.page, limit, ticker, search, category
    });
    const res  = await fetch('/api/transactions?'+params).then(r=>r.json());
    if (res.error) throw new Error(res.error);
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
function renderPagination(containerId, res, loadFn) {
  const state = loadFn === loadHistory ? historyState : gainsState;
  const { page, pages, total, limit } = res;
  const start = (page-1)*limit+1, end = Math.min(page*limit, total);
  const el = document.getElementById(containerId);
  if (pages <= 1) { el.innerHTML=''; return; }

  let btns = '';
  const addBtn = (p, label, active, disabled) =>
    `<button class="pg-btn${active?' active':''}" ${disabled?'disabled':''} onclick="
      ${loadFn===loadHistory?'historyState':'gainsState'}.page=${p};
      ${loadFn===loadHistory?'loadHistory':'loadGains'}(false)">${label}</button>`;

  btns += addBtn(page-1,'‹ Prev', false, page===1);
  const lo=Math.max(1,page-3), hi=Math.min(pages,page+3);
  if (lo>1) btns += addBtn(1,'1',false,false) + (lo>2?'<span class="pg-info">…</span>':'');
  for (let p=lo;p<=hi;p++) btns += addBtn(p,p,p===page,false);
  if (hi<pages) btns += (hi<pages-1?'<span class="pg-info">…</span>':'') + addBtn(pages,pages,false,false);
  btns += addBtn(page+1,'Next ›',false,page===pages);

  el.innerHTML = `<span class="pg-info">${start}–${end} of ${total.toLocaleString()}</span>` + btns;
}

// ── Trade form ─────────────────────────────────────────────────────
let tradeMode = 'equity';
let _pendingOrder = null;

function setTradeMode(mode) {
  tradeMode = mode;
  document.getElementById('btn-equity').classList.toggle('active', mode === 'equity');
  document.getElementById('btn-option').classList.toggle('active', mode === 'option');
  document.getElementById('form-equity').style.display = mode === 'equity' ? '' : 'none';
  document.getElementById('form-option').style.display = mode === 'option' ? '' : 'none';
  document.getElementById('trade-result').innerHTML = '';
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
      ? '<span style="background:#4c1d1d;color:#fca5a5;padding:1px 6px;border-radius:3px;font-size:11px">PUT</span>'
      : '<span style="background:#14532d;color:#86efac;padding:1px 6px;border-radius:3px;font-size:11px">CALL</span>';
    const durLabel = dur === 'gtc' ? 'GTC' : 'Day';
    const sesLabel = {normal:'Normal', seamless:'Extended Hrs', am:'Pre-Market', pm:'Post-Market'}[ses];
    summary = `<b>${aLabel}</b> ${contracts} contract(s) — <b>${underlying}</b> ${expiry} $${strike} ${badge}<br>
               Order: ${ot.toUpperCase()} ${pLabel} · ${durLabel} · ${sesLabel}`;
  }

  _pendingOrder = payload;
  const div = document.getElementById('trade-result');
  div.innerHTML =
    '<div class="preview-box">' +
      '<div class="preview-title">⚠️ Review Order Before Submitting</div>' +
      '<div class="preview-summary">' + summary + '</div>' +
      '<div style="font-size:11px;color:#64748b;margin-bottom:14px">' +
        'Please verify all details before confirming.' +
      '</div>' +
      '<div class="preview-actions">' +
        '<button class="cancel-btn" onclick="clearTradeResult()">✕ Cancel</button>' +
        '<button class="confirm-btn" onclick="submitPendingOrder()">✓ Confirm &amp; Submit</button>' +
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
      '<div class="success-box">✅ Order submitted successfully!<br>' +
      '<b>Order ID: ' + esc(res.order_id) + '</b><br>' +
      '<small style="color:#6ee7b7;opacity:0.7">Switch to the "Open Orders" tab to track status.</small>' +
      '</div>';
    ordersState.loaded = false;
  } catch(e) {
    div.innerHTML = '<div class="error" style="margin-top:0">❌ Order failed: ' + esc(e.message) + '</div>';
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

const ladderRecentState = { page: 1, pages: 1, total: 0 };
const LADDER_RECENT_LIMIT = 20;
const LADDER_RECENT_ACTIONS = new Set([
  'Buy','Sell','Sell Short','Buy to Cover',
  'Buy to Open','Sell to Open','Buy to Close','Sell to Close'
]);

async function loadLadderRecent(page) {
  const ticker = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const sidebar = document.getElementById('lad-recent');
  if (!ticker) {
    sidebar.innerHTML = '<div style="color:#475569">Enter a ticker to see recent trades</div>';
    return;
  }
  if (page !== undefined) ladderRecentState.page = page;
  sidebar.innerHTML = '<div class="loading" style="padding:10px">Loading…</div>';
  try {
    const params = new URLSearchParams({
      ticker,
      limit: LADDER_RECENT_LIMIT,
      page: ladderRecentState.page,
    });
    const res = await fetch('/api/transactions?' + params).then(r => r.json());
    if (res.error) throw new Error(res.error);

    ladderRecentState.pages = res.pages;
    ladderRecentState.total = res.total;

    const trades = res.data.filter(r => LADDER_RECENT_ACTIONS.has(r.action));

    if (!trades.length && ladderRecentState.page === 1) {
      sidebar.innerHTML = '<div style="color:#475569">No recent trades for ' + esc(ticker) + '</div>';
      return;
    }

    const rows = trades.map(r => {
      const isBuy = (r.action||'').toLowerCase().includes('buy');
      const optBadge = r.option_type
        ? `<span class="badge badge-${r.option_type}" style="font-size:10px">${r.option_type}</span>`
        : '';
      return `<tr>
        <td style="color:#64748b;font-size:11px">${r.trade_date}</td>
        <td class="${isBuy ? 'pos' : 'neg'}" style="font-size:11px">${esc(r.action)}</td>
        <td style="font-size:11px">${r.quantity != null ? fmt(r.quantity, 0) : '—'}</td>
        <td style="font-size:11px">${r.price != null ? '$' + fmt(r.price, 4) : '—'}</td>
        <td style="font-size:11px">${optBadge}</td>
        <td style="font-size:11px">${r.option_strike != null ? '$' + fmt(r.option_strike) : ''}</td>
        <td style="font-size:11px;color:#64748b">${r.option_expiry || ''}</td>
      </tr>`;
    }).join('');

    const cur = ladderRecentState.page, total_pages = ladderRecentState.pages;
    const prevDisabled = cur <= 1 ? 'disabled' : '';
    const nextDisabled = cur >= total_pages ? 'disabled' : '';
    const pagination = total_pages > 1 ? `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;gap:6px">
        <button class="pg-btn" ${prevDisabled} onclick="loadLadderRecent(${cur - 1})">‹ Prev</button>
        <span class="pg-info">Page ${cur} of ${total_pages} · ${res.total.toLocaleString()} trades</span>
        <button class="pg-btn" ${nextDisabled} onclick="loadLadderRecent(${cur + 1})">Next ›</button>
      </div>` : `<div class="pg-info" style="margin-top:6px">${res.total.toLocaleString()} trades</div>`;

    sidebar.innerHTML =
      '<table style="width:100%"><thead><tr>' +
      '<th>Date</th><th>Action</th><th>Qty</th><th>Price</th><th>Type</th><th>Strike</th><th>Expiry</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>' + pagination;
  } catch(e) {
    sidebar.innerHTML = '<div class="error" style="font-size:12px">Error: ' + esc(e.message) + '</div>';
  }
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
    const res = await fetch('/api/order/ladder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        trade_type: 'equity',
        action, symbol: ticker,
        duration: dur, session: ses,
        rungs: validRungs
      })
    }).then(r => r.json());

    if (res.error) throw new Error(res.error);

    const rows = res.results.map(r => {
      const icon = r.status === 'ok'
        ? '<span class="rung-status rung-ok"></span>'
        : '<span class="rung-status rung-fail"></span>';
      return `<tr>
        <td>${r.rung}</td>
        <td>${fmt(r.qty, 0)}</td>
        <td>$${fmt(r.price)}</td>
        <td>${icon}${r.status === 'ok' ? 'Order #' + esc(r.order_id) : '<span class="neg">' + esc(r.error) + '</span>'}</td>
      </tr>`;
    }).join('');

    const ok = res.results.filter(r => r.status === 'ok').length;
    const fail = res.results.length - ok;
    const statusMsg = fail === 0
      ? `<div class="success-box">✅ All ${ok} orders submitted successfully!</div>`
      : `<div class="error">⚠️ ${ok} succeeded, ${fail} failed</div>`;

    resultDiv.innerHTML = statusMsg +
      '<table style="margin-top:10px"><thead><tr><th>#</th><th>Qty</th><th>Price</th><th>Result</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table>' +
      '<div style="margin-top:12px;color:#64748b;font-size:12px">Switch to "Open Orders" to track status.</div>';
    ordersState.loaded = false;
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
    const raw = await fetch('/api/orders').then(r => r.json());
    if (raw.error) throw new Error(raw.error);
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

// ── Init ──────────────────────────────────────────────────────────
document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
loadOverview();
loadPositions();
loadQuotes();
renderRungs();
