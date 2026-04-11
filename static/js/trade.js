import { fmt, fmtD, cls, esc, fetchJson } from './utils.js';
import * as S from './state.js';

export function setTradeMode(mode) {
  S.tradeMode = mode;
  document.getElementById('btn-equity').classList.toggle('active', mode === 'equity');
  document.getElementById('btn-option').classList.toggle('active', mode === 'option');
  document.getElementById('form-equity').style.display = mode === 'equity' ? '' : 'none';
  document.getElementById('form-option').style.display = mode === 'option' ? '' : 'none';
  document.getElementById('trade-result').innerHTML = '';
  document.getElementById('trade-chain-wrap').style.display = mode === 'option' ? '' : 'none';
  syncTradeTicker();
  if (mode === 'option' && S._tradeTicker) loadOptionExpirations(S._tradeTicker);
}

export function syncTradeTicker() {
  const eqEl  = document.getElementById('eq-ticker');
  const optEl = document.getElementById('opt-underlying');
  if (S.tradeMode === 'equity') {
    const v = eqEl.value.trim().toUpperCase();
    if (v) optEl.value = v;
  } else {
    const v = optEl.value.trim().toUpperCase();
    if (v) eqEl.value = v;
  }
}

export function resetTradeQuotePlaceholder() {
  document.getElementById('trade-quote-card').innerHTML =
    '<div style="color:#475569;font-size:12px;padding:12px 0">Enter a ticker to see live quote</div>';
}

export function resetTradeChartPlaceholder() {
  document.getElementById('trade-chart-wrap').innerHTML =
    '<div style="color:#475569;font-size:12px;padding:40px 0;text-align:center">Chart loads when a ticker is entered</div>';
}

export function clearPositionPanel(panelId) {
  const el = document.getElementById(panelId);
  if (!el) return;
  el.style.display = 'none';
  el.innerHTML = '';
}

export function _parsePosOptDesc(desc) {
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

export async function loadPositionSummaryForTicker(ticker, panelId) {
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

export function onTradeTickerChange() {
  syncTradeTicker();
  const ticker = (S.tradeMode === 'equity'
    ? document.getElementById('eq-ticker').value
    : document.getElementById('opt-underlying').value).trim().toUpperCase();
  if (!ticker) {
    S._tradeTicker = '';
    clearPositionPanel('trade-position-panel');
    resetTradeQuotePlaceholder();
    resetTradeChartPlaceholder();
    return;
  }
  const changed = ticker !== S._tradeTicker;
  S._tradeTicker = ticker;
  loadTradeQuote(ticker);
  loadPositionSummaryForTicker(ticker, 'trade-position-panel');
  if (changed) loadTradingViewChart(ticker);
  if (S.tradeMode === 'option' && changed) loadOptionExpirations(ticker);
}

export function updateEqFields() {
  const ot = document.getElementById('eq-order-type').value;
  document.getElementById('eq-price-group').style.display = ['limit','stop_limit'].includes(ot) ? '' : 'none';
  document.getElementById('eq-stop-group').style.display  = ['stop','stop_limit'].includes(ot)  ? '' : 'none';
}

export function updateOptFields() {
  const ot = document.getElementById('opt-order-type').value;
  document.getElementById('opt-price-group').style.display = ot === 'limit' ? '' : 'none';
}

export async function loadTradeQuote(ticker) {
  const card = document.getElementById('trade-quote-card');
  card.innerHTML = '<div style="color:#64748b;font-size:12px;padding:8px 0">Loading quote for ' + esc(ticker) + '…</div>';
  try {
    const data = await fetch('/api/quote/' + encodeURIComponent(ticker)).then(r => r.json());
    if (data.error) throw new Error(data.error);
    S._tradeQuoteData = data;
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

export function tvExchangeForTicker(ticker) {
  const u = ticker.toUpperCase();
  if (/^(SPY|QQQ|DIA|IWM|TQQQ|SQQQ|GLD|SLV|TLT|VXX|UVXY|IEFA|EFA|EEM|HYG|LQD)$/i.test(ticker)) return 'AMEX';
  const nyse = /^(JPM|BAC|WFC|C|GS|MS|XOM|CVX|COP|DIS|NKE|KO|MCD|WMT|T|VZ|PFE|JNJ|UNH|HD|LOW|CAT|DE|BA|LMT|GE|MMM|IBM|ORCL|CSCO|INTC|AMD|XLE|XLF|XLV|XLI|XLP|XLU|XLK|XLB|XLRE|XLC)$/i;
  if (nyse.test(ticker)) return 'NYSE';
  return 'NASDAQ';
}

export function loadTradingViewChart(ticker) {
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

export async function loadOptionExpirations(ticker) {
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

export async function loadOptionChain() {
  const expiry = document.getElementById('chain-expiry').value;
  const content = document.getElementById('chain-content');
  const status  = document.getElementById('chain-status');
  if (!expiry || !S._tradeTicker) {
    content.innerHTML = '<div style="color:#475569;font-size:12px;padding:8px 0">Select an expiration to view the option chain</div>';
    return;
  }
  status.textContent = 'Loading…';
  content.innerHTML = '<div class="loading" style="padding:16px">Loading option chain…</div>';
  try {
    const data = await fetch('/api/option-chain?symbol=' + encodeURIComponent(S._tradeTicker) +
      '&from_date=' + expiry + '&to_date=' + expiry + '&strike_count=20').then(r => r.json());
    if (data.error) throw new Error(data.error);
    S._chainData = data;

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
      '<table class="chain-table" onclick="window.onChainClick(event)">' +
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

export function onChainClick(e) {
  const td = e.target.closest('td[data-side]');
  if (!td) return;
  const side   = td.getAttribute('data-side');
  const strike = parseFloat(td.getAttribute('data-strike'));
  const expiry = document.getElementById('chain-expiry').value;
  if (!side || !strike || !expiry || !S._chainData) return;

  const contracts = side === 'CALL' ? (S._chainData.calls[expiry] || []) : (S._chainData.puts[expiry] || []);
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

export function previewOrder(type) {
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

  S._pendingOrder = payload;
  const div = document.getElementById('trade-result');
  div.innerHTML =
    '<div class="preview-box">' +
      '<div class="preview-title">Review Order Before Submitting</div>' +
      '<div class="preview-summary">' + summary + '</div>' +
      '<div style="font-size:11px;color:#64748b;margin-bottom:14px">' +
        'Please verify all details before confirming.' +
      '</div>' +
      '<div class="preview-actions">' +
        '<button class="cancel-btn" onclick="window.clearTradeResult()">Cancel</button>' +
        '<button class="confirm-btn" onclick="window.submitPendingOrder()">Confirm &amp; Submit</button>' +
      '</div>' +
    '</div>';
}

export async function submitPendingOrder() {
  if (!S._pendingOrder) return;
  const payload = S._pendingOrder;
  S._pendingOrder = null;
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
    S.ordersState.loaded = false;
  } catch(e) {
    div.innerHTML = '<div class="error" style="margin-top:0">Order failed: ' + esc(e.message) + '</div>';
  }
}

export function clearTradeResult() {
  document.getElementById('trade-result').innerHTML = '';
  S._pendingOrder = null;
}

export function showTradeError(msg) {
  document.getElementById('trade-result').innerHTML =
    '<div class="error" style="margin-top:12px;padding:10px 14px;border-radius:6px">' + esc(msg) + '</div>';
}
