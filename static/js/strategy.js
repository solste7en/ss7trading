import { fmt, fmtD, cls, esc, fetchJson, ladderResultTableHtml, round2 } from './utils.js';
import { normalizeQty } from './utils.js';
import { store as S } from './state.js';
import { findChainContract, makeStrikeSelectOptions } from './optionChainHelpers.js';

export function _stratLadderEnabled() {
  const el = document.getElementById('strat-ladder-en');
  return el && el.checked;
}

export function _stratLadderStepsCount() {
  const sel = document.getElementById('strat-ladder-steps');
  const n = sel ? parseInt(sel.value, 10) : 3;
  return n >= 2 && n <= 7 ? n : 3;
}

export function _stratLadderPriceLabel() {
  if (S.stratMode === 'naked') return 'Limit (per share)';
  return 'Net credit/debit';
}

export function _updateStratSingleVsLadderVisibility() {
  const en = _stratLadderEnabled();
  ['sn-single-qp', 'sv-single-qp', 'sc-single-qp'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = en ? 'none' : '';
  });
}

export function _onStratLadderToggle() {
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

export function _seedStratLadderRung1FromSingle() {
  const r1 = document.querySelector('#strat-ladder-rungs .strat-rung-row[data-rung="1"]');
  if (!r1) return;
  const qIn = r1.querySelector('.strat-rung-qty');
  const pIn = r1.querySelector('.strat-rung-price');
  let q = 1, p = '';
  if (S.stratMode === 'naked') {
    q = parseInt(document.getElementById('sn-qty').value, 10) || 1;
    p = document.getElementById('sn-price').value;
  } else if (S.stratMode === 'vertical') {
    q = parseInt(document.getElementById('sv-qty').value, 10) || 1;
    p = document.getElementById('sv-price').value;
  } else if (S.stratMode === 'collar') {
    q = parseInt(document.getElementById('sc-qty').value, 10) || 1;
    p = document.getElementById('sc-price').value;
  }
  if (qIn && !qIn.dataset.touched) qIn.value = q;
  if (pIn && p && !pIn.dataset.touched) pIn.value = p;
}

export function _renderStratLadderRungs() {
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

export function _collectStratLadderRungs() {
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

export function _syncAutoPriceToStratLadderRung1() {
  if (!_stratLadderEnabled()) return;
  const r1 = document.querySelector('#strat-ladder-rungs .strat-rung-row[data-rung="1"]');
  if (!r1) return;
  const priceIn = r1.querySelector('.strat-rung-price');
  if (!priceIn || priceIn.dataset.touched) return;
  let src = null;
  if (S.stratMode === 'naked') src = document.getElementById('sn-price');
  else if (S.stratMode === 'vertical') src = document.getElementById('sv-price');
  else if (S.stratMode === 'collar') src = document.getElementById('sc-price');
  if (src && src.value) priceIn.value = src.value;
}

export function setStrategyMode(mode) {
  S.stratMode = mode;
  S.STRAT_MODES.forEach(m => {
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

export function onStrategyTickerChange() {
  const ticker = document.getElementById('strat-ticker').value.trim().toUpperCase();
  if (!ticker) {
    S._stratTicker = '';
    window.clearPositionPanel('strat-position-panel');
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
  const changed = ticker !== S._stratTicker;
  S._stratTicker = ticker;
  if (changed) {
    loadStrategyExpirations(ticker);
    loadStrategySuggestions(ticker);
    window.loadPositionSummaryForTicker(ticker, 'strat-position-panel');
    loadStrategyRecent();
    loadStrategyOrders();
  }
}

export async function loadStrategyExpirations(ticker) {
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

export async function loadStrategyChain() {
  const expiry  = document.getElementById('strat-expiry').value;
  const content = document.getElementById('strat-chain-content');
  const status  = document.getElementById('strat-chain-status');
  if (!expiry || !S._stratTicker) {
    content.innerHTML = '<div style="color:#475569;font-size:12px;padding:4px 0">Select an expiration to browse the chain</div>';
    document.getElementById('strat-chain-pager').style.display = 'none';
    return;
  }
  status.textContent = 'Loading…';
  content.innerHTML = '<div class="loading" style="padding:12px">Loading option chain…</div>';
  try {
    const data = await fetch('/api/option-chain?symbol=' + encodeURIComponent(S._stratTicker) +
      '&from_date=' + expiry + '&to_date=' + expiry + '&strike_count=60').then(r => r.json());
    if (data.error) throw new Error(data.error);
    S._stratChainData = data;

    const calls = data.calls[expiry] || [];
    const puts  = data.puts[expiry]  || [];
    S._chainAllStrikes = [...new Set([...calls.map(c => c.strike), ...puts.map(p => p.strike)])].sort((a,b) => a - b);
    S._chainCallMap = {}; calls.forEach(c => S._chainCallMap[c.strike] = c);
    S._chainPutMap  = {}; puts.forEach(p => S._chainPutMap[p.strike]  = p);

    if (!S._chainAllStrikes.length) {
      content.innerHTML = '<div style="color:#475569;font-size:12px;padding:4px 0">No contracts found</div>';
      status.textContent = '';
      document.getElementById('strat-chain-pager').style.display = 'none';
      return;
    }

    // Default to the middle page so ATM strikes are centered
    const totalPages = Math.ceil(S._chainAllStrikes.length / S.CHAIN_PAGE_SIZE);
    S._chainPageIdx = Math.floor(totalPages / 2);

    _renderChainPage();
    _populateStrikeDropdowns();
  } catch(e) {
    content.innerHTML = '<div style="color:#f87171;font-size:12px;padding:4px 0">Error: ' + esc(e.message) + '</div>';
    status.textContent = '';
    document.getElementById('strat-chain-pager').style.display = 'none';
  }
}

export function shiftChainPage(delta) {
  const totalPages = Math.ceil(S._chainAllStrikes.length / S.CHAIN_PAGE_SIZE);
  S._chainPageIdx = Math.max(0, Math.min(totalPages - 1, S._chainPageIdx + delta));
  _renderChainPage();
  _populateStrikeDropdowns();
}

export function _renderChainPage() {
  const content    = document.getElementById('strat-chain-content');
  const status     = document.getElementById('strat-chain-status');
  const pager      = document.getElementById('strat-chain-pager');
  const prevBtn    = document.getElementById('chain-prev-btn');
  const nextBtn    = document.getElementById('chain-next-btn');
  const pageLabel  = document.getElementById('chain-page-label');

  const totalPages = Math.ceil(S._chainAllStrikes.length / S.CHAIN_PAGE_SIZE);
  const start      = S._chainPageIdx * S.CHAIN_PAGE_SIZE;
  const pageStrikes = S._chainAllStrikes.slice(start, start + S.CHAIN_PAGE_SIZE);
  S._chainVisibleStrikes = pageStrikes;

  const fo = v => v == null ? '—' : Number(v).toFixed(2);
  const fv = v => v == null ? '—' : Number(v).toLocaleString();

  const rows = pageStrikes.map(strike => {
    const c  = S._chainCallMap[strike] || {};
    const p  = S._chainPutMap[strike]  || {};
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
    prevBtn.disabled  = (S._chainPageIdx === 0);
    nextBtn.disabled  = (S._chainPageIdx >= totalPages - 1);
    pageLabel.textContent = 'Page ' + (S._chainPageIdx + 1) + ' / ' + totalPages;
  } else {
    pager.style.display = 'none';
  }
}

export function onStratChainClick(e) {
  const td = e.target.closest('td[data-side]');
  if (!td) return;
  const side   = td.getAttribute('data-side');
  const strike = parseFloat(td.getAttribute('data-strike'));
  const expiry = document.getElementById('strat-expiry').value;
  if (!side || !strike || !expiry || !S._stratChainData) return;

  if (S.stratMode === 'naked') {
    document.getElementById('sn-type').value = side;
    _populateStrikeDropdowns();
    document.getElementById('sn-strike').value = strike;
  } else if (S.stratMode === 'vertical') {
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
  } else if (S.stratMode === 'collar') {
    if (side === 'CALL') {
      document.getElementById('sc-sell-type').value = 'CALL';
      _populateStrikeDropdowns();
      document.getElementById('sc-sell-strike').value = strike;
    } else {
      document.getElementById('sc-buy-type').value = 'PUT';
      _populateStrikeDropdowns();
      document.getElementById('sc-buy-strike').value = strike;
    }
  } else if (S.stratMode === 'bundle') {
    document.getElementById('sb-opt-type').value = side;
    _populateStrikeDropdowns();
    document.getElementById('sb-strike').value = strike;
  }
  _autoCalcStratPrice();
}

export async function loadStrategySuggestions(ticker) {
  const container = document.getElementById('strat-suggest-cards');
  container.innerHTML = '<div class="loading" style="padding:8px 0;font-size:12px">Analysing positions…</div>';
  try {
    const data = await fetch('/api/strategy-suggest?ticker=' + encodeURIComponent(ticker)).then(r => r.json());
    if (data.error) throw new Error(data.error);
    S._stratSuggestions = data.suggestions || [];

    if (!S._stratSuggestions.length) {
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
      S._stratSuggestions.map((s, i) => {
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

export async function applyStrategySuggestion(index) {
  const s = S._stratSuggestions[index];
  if (!s) return;

  setStrategyMode(s.strategy || 'naked');

  // If legs specify an expiry, set it and await chain load so dropdowns can be populated
  const legExpiry = s.legs && s.legs.length > 0 ? s.legs[0].expiry : null;
  if (legExpiry) {
    const sel = document.getElementById('strat-expiry');
    if (sel) sel.value = legExpiry;
  }
  await loadStrategyChain();  // ensures S._stratChainData is fresh before populating

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

export async function loadStrategyRecent(page) {
  const ticker = S._stratTicker;
  const container = document.getElementById('strat-recent');
  const countDiv  = document.getElementById('strat-recent-count');

  if (!ticker) {
    container.innerHTML = '<div style="color:#475569;font-size:12px">Enter a ticker to see recent trades</div>';
    countDiv.textContent = '';
    return;
  }

  if (page !== undefined) S.stratRecentState.page = page;
  else S.stratRecentState.page = 1;

  container.innerHTML = '<div class="loading" style="padding:8px">Loading…</div>';

  try {
    const params = new URLSearchParams({ ticker, category: 'option', limit: 15, page: S.stratRecentState.page });
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
        '<td>' + (r.quantity != null ? fmt(normalizeQty(r.action, r.quantity), 0) : '—') + '</td>' +
        '<td>' + (r.price != null ? '$' + fmt(r.price, 4) : '—') + '</td>' +
        '<td>' + optBadge + '</td>' +
        '<td>' + (r.option_strike != null ? '$' + fmt(r.option_strike) : '') + '</td>' +
        '<td style="color:#64748b">' + (r.option_expiry || '') + '</td>' +
      '</tr>';
    }).join('');

    const cur = S.stratRecentState.page, tp = res.pages;
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

export async function loadStrategyOrders() {
  const ticker = S._stratTicker;
  const container   = document.getElementById('strat-orders');
  const countDiv    = document.getElementById('strat-orders-count');
  const cancelBtn   = document.getElementById('strat-cancel-all-btn');

  if (!ticker) {
    container.innerHTML = '<div style="color:#475569;font-size:12px">Enter a ticker to see open orders</div>';
    countDiv.textContent = '';
    cancelBtn.style.display = 'none';
    S._stratOrders = [];
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
    S._stratOrders = orders;

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

export async function cancelStratOrder(orderId) {
  if (!confirm('Cancel order ' + orderId + '?')) return;
  try {
    const res = await fetch('/api/order/' + orderId, { method: 'DELETE' }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    S.ordersState.loaded = false;
    await loadStrategyOrders();
  } catch(e) { alert('Failed: ' + e.message); }
}

export async function cancelAllStratOrders() {
  const cancelable = S._stratOrders.filter(o => o.cancelable);
  if (!cancelable.length) { alert('No cancelable orders.'); return; }
  if (!confirm('Cancel ALL ' + cancelable.length + ' open orders for ' + S._stratTicker + '?')) return;

  const container = document.getElementById('strat-orders');
  container.innerHTML = '<div class="loading" style="padding:6px">Cancelling…</div>';

  let ok = 0, fail = 0;
  for (const o of cancelable) {
    try {
      await fetch('/api/order/' + o.order_id, { method: 'DELETE' }).then(r => r.json());
      ok++;
    } catch { fail++; }
  }
  S.ordersState.loaded = false;
  container.innerHTML = fail === 0
    ? '<div style="color:#86efac;font-size:12px;padding:4px 0">All ' + ok + ' cancelled.</div>'
    : '<div class="error" style="font-size:12px">' + ok + ' cancelled, ' + fail + ' failed.</div>';
  setTimeout(() => loadStrategyOrders(), 1500);
}

export function _stratExpiryDteDays() {
  const v = document.getElementById('strat-expiry').value;
  if (!v) return null;
  const end = new Date(v + 'T12:00:00');
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  end.setHours(0, 0, 0, 0);
  const diff = Math.round((end - start) / 86400000);
  return Math.max(1, diff);
}

export function _stratShortStrikeForScore() {
  if (S.stratMode === 'naked') {
    const x = parseFloat(document.getElementById('sn-strike').value);
    return Number.isFinite(x) && x > 0 ? x : null;
  }
  if (S.stratMode === 'vertical') {
    const x = parseFloat(document.getElementById('sv-sell-strike').value);
    return Number.isFinite(x) && x > 0 ? x : null;
  }
  if (S.stratMode === 'collar') {
    const x = parseFloat(document.getElementById('sc-sell-strike').value);
    return Number.isFinite(x) && x > 0 ? x : null;
  }
  return null;
}

/** Max dollars kept in a “perfect” short-premium outcome (credit kept, no close cost). */
export function _stratPerfectProfitDollars(info) {
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
export function _stratPreviewMaxEfficiencyScore() {
  if (S.stratMode === 'bundle') return null;
  const dte = _stratExpiryDteDays();
  if (dte == null) return null;
  const strike = _stratShortStrikeForScore();
  if (strike == null) return null;
  let info = null;
  try {
    if (S.stratMode === 'naked') info = _calcNakedPnl();
    else if (S.stratMode === 'vertical') info = _calcVerticalPnl();
    else if (S.stratMode === 'collar') info = _calcCollarPnl();
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

export function updateStratPnl() {
  const box = document.getElementById('strat-pnl');
  if (_stratLadderEnabled() && S.stratMode !== 'bundle') {
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
    if (S.stratMode === 'naked') {
      info = _calcNakedPnl();
    } else if (S.stratMode === 'vertical') {
      info = _calcVerticalPnl();
    } else if (S.stratMode === 'collar') {
      info = _calcCollarPnl();
    } else if (S.stratMode === 'bundle') {
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

export function _stratLadderPnlHtml() {
  const rungs = _collectStratLadderRungs();
  if (!rungs.length) return null;

  let rows = '';
  let sumCredit = 0, sumDebit = 0, nCredit = 0, nDebit = 0;
  let sumMaxProfitNum = 0, sumMaxLossNum = 0, nMaxProfit = 0, nMaxLoss = 0;
  let anyUnlimitedLoss = false, anyUnlimitedProfit = false;

  rungs.forEach((r, idx) => {
    let m = null;
    try {
      if (S.stratMode === 'naked') m = _calcNakedPnlFor(r.qty, r.price);
      else if (S.stratMode === 'vertical') m = _calcVerticalPnlFor(r.qty, r.price);
      else if (S.stratMode === 'collar') m = _calcCollarPnlFor(r.qty, r.price);
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
      if (S.stratMode === 'naked') m0 = _calcNakedPnlFor(r0.qty, r0.price);
      else if (S.stratMode === 'vertical') m0 = _calcVerticalPnlFor(r0.qty, r0.price);
      else if (S.stratMode === 'collar') m0 = _calcCollarPnlFor(r0.qty, r0.price);
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

export function _calcNakedPnl() {
  return _calcNakedPnlFor(
    parseInt(document.getElementById('sn-qty').value, 10) || 1,
    parseFloat(document.getElementById('sn-price').value)
  );
}

export function _calcNakedPnlFor(qty, price) {
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

export function _calcVerticalPnl() {
  return _calcVerticalPnlFor(
    parseInt(document.getElementById('sv-qty').value, 10) || 1,
    parseFloat(document.getElementById('sv-price').value)
  );
}

export function _calcVerticalPnlFor(qty, netPrice) {
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

export function _calcCollarPnl() {
  return _calcCollarPnlFor(
    parseInt(document.getElementById('sc-qty').value, 10) || 1,
    parseFloat(document.getElementById('sc-price').value) || 0
  );
}

export function _calcCollarPnlFor(qty, netPrice) {
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

/** Build one strategy order from per-rung qty/price (naked / vertical / collar only). */
export function _buildStrategyOrderCoreForRung(qty, price) {
  const ticker = S._stratTicker;
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

  if (S.stratMode === 'naked') {
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

  } else if (S.stratMode === 'vertical') {
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

  } else if (S.stratMode === 'collar') {
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

export function _buildStrategyPayload() {
  const duration = document.getElementById('strat-duration').value;
  const session  = document.getElementById('strat-session').value;

  if (S.stratMode === 'bundle') {
    const ticker   = S._stratTicker;
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
      if (S.stratMode === 'naked') {
        if (!r.price || r.price <= 0) throw new Error('Step ' + (i + 1) + ': limit price required');
      } else {
        const ot = S.stratMode === 'vertical'
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
  if (S.stratMode === 'naked') {
    qty = parseInt(document.getElementById('sn-qty').value, 10) || 1;
    price = parseFloat(document.getElementById('sn-price').value);
  } else if (S.stratMode === 'vertical') {
    qty = parseInt(document.getElementById('sv-qty').value, 10) || 1;
    price = parseFloat(document.getElementById('sv-price').value);
  } else if (S.stratMode === 'collar') {
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

export function _findChainContract(type, strike, expiry) {
  return findChainContract(S._stratChainData, type, strike, expiry);
}

// Populate all strike <select> elements from the currently loaded chain data.
// Each dropdown type-pair (CALL or PUT based on context) gets the right strikes.
// Build <option> HTML for a strike dropdown, using only the currently visible
// page strikes but keeping the currently selected value even if it's off-page
// (shown as an out-of-range entry so the form doesn't silently clear it).
export function _makeStrikeOpts(type, currentValue) {
  return makeStrikeSelectOptions(
    type,
    S._chainCallMap,
    S._chainPutMap,
    S._chainVisibleStrikes,
    currentValue,
    S._chainVisibleStrikes,
  );
}

export function _populateStrikeDropdowns() {
  if (!S._chainVisibleStrikes.length) return;

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
export function _autoCalcStratPrice() {
  if (!S._stratChainData) { updateStratPnl(); return; }
  const expiry = document.getElementById('strat-expiry').value;
  if (!expiry) { updateStratPnl(); return; }

  const getContract = (type, strikeVal) => {
    const strike = parseFloat(strikeVal);
    if (!strike) return null;
    return _findChainContract(type, strike, expiry);
  };

  if (S.stratMode === 'naked') {
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

  } else if (S.stratMode === 'vertical') {
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

  } else if (S.stratMode === 'collar') {
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

  } else if (S.stratMode === 'bundle') {
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

export function previewStrategy() {
  const resultDiv = document.getElementById('strat-result');
  try {
    const payload = _buildStrategyPayload();
    S._stratPendingOrder = payload;

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

export async function submitStrategy() {
  if (!S._stratPendingOrder) return;
  const payload = S._stratPendingOrder;
  S._stratPendingOrder = null;
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
      S.ordersState.loaded = false;
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
    S.ordersState.loaded = false;
    setTimeout(() => loadStrategyOrders(), 1000);
  } catch(e) {
    div.innerHTML = '<div class="error" style="margin-top:0">Order failed: ' + esc(e.message) + '</div>';
  }
}
