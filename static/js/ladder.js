import { fmt, fmtD, cls, esc, normalizeQty, fetchJson, ladderResultTableHtml } from './utils.js';
import { store as S } from './state.js';
import { renderOrders, enrichOrders } from './orders.js';

export function renderRungs() {
  if (typeof window !== 'undefined') window.ladderRungs = S.ladderRungs;
  const tbody = document.getElementById('lad-rungs-tbody');
  tbody.innerHTML = S.ladderRungs.map((r, i) =>
    `<tr>
      <td style="color:#475569;width:30px;text-align:center">${i+1}</td>
      <td><input type="number" min="1" step="1" value="${r.qty}" placeholder="100"
           oninput="window.ladderRungs[${i}].qty=this.value; window.updateLadderSummary()"></td>
      <td><input type="number" min="0.01" step="0.01" value="${r.price}" placeholder="0.00"
           oninput="window.ladderRungs[${i}].price=this.value; window.updateLadderSummary()"></td>
      <td><button class="rung-del" onclick="window.removeRung(${i})" title="Remove">&times;</button></td>
    </tr>`
  ).join('');
  updateLadderSummary();
}

export function addRung() {
  S.ladderRungs.push({qty:'', price:''});
  renderRungs();
}

export function removeRung(i) {
  if (S.ladderRungs.length <= 1) return;
  S.ladderRungs.splice(i, 1);
  renderRungs();
}

export function updateLadderSummary() {
  let totalQty = 0, totalValue = 0, valid = 0;
  S.ladderRungs.forEach(r => {
    const q = parseFloat(r.qty) || 0;
    const p = parseFloat(r.price) || 0;
    if (q > 0 && p > 0) { totalQty += q; totalValue += q * p; valid++; }
  });
  document.getElementById('lad-total-qty').textContent = fmt(totalQty, 0);
  document.getElementById('lad-total-value').textContent = '$' + fmt(totalValue);
  document.getElementById('lad-rung-count').textContent = valid;
}

export function ladderEvenSplit() {
  const totalQty  = parseFloat(document.getElementById('lad-split-qty').value) || 0;
  const startPrice = parseFloat(document.getElementById('lad-split-start').value) || 0;
  const endPrice   = parseFloat(document.getElementById('lad-split-end').value) || 0;
  const numRungs   = parseInt(document.getElementById('lad-split-rungs').value) || 2;

  if (totalQty <= 0 || startPrice <= 0 || endPrice <= 0 || numRungs < 1) return;

  const perRung = Math.floor(totalQty / numRungs);
  const remainder = totalQty - perRung * numRungs;
  const step = numRungs > 1 ? (endPrice - startPrice) / (numRungs - 1) : 0;

  S.ladderRungs = [];
  for (let i = 0; i < numRungs; i++) {
    S.ladderRungs.push({
      qty: String(perRung + (i < remainder ? 1 : 0)),
      price: (startPrice + step * i).toFixed(2)
    });
  }
  renderRungs();
}

export function ladderScaleUp() {
  const totalQty   = parseFloat(document.getElementById('lad-split-qty').value) || 0;
  const startPrice = parseFloat(document.getElementById('lad-split-start').value) || 0;
  const endPrice   = parseFloat(document.getElementById('lad-split-end').value) || 0;
  const numRungs   = parseInt(document.getElementById('lad-split-rungs').value) || 2;

  if (totalQty <= 0 || startPrice <= 0 || endPrice <= 0 || numRungs < 1) return;

  const weights = [];
  for (let i = 0; i < numRungs; i++) weights.push(i + 1);
  const wSum = weights.reduce((a, b) => a + b, 0);
  const step = numRungs > 1 ? (endPrice - startPrice) / (numRungs - 1) : 0;

  S.ladderRungs = [];
  let assigned = 0;
  for (let i = 0; i < numRungs; i++) {
    const q = i === numRungs - 1
      ? totalQty - assigned
      : Math.round(totalQty * weights[i] / wSum);
    assigned += q;
    S.ladderRungs.push({ qty: String(Math.max(1, q)), price: (startPrice + step * i).toFixed(2) });
  }
  renderRungs();
}

export function ladderScaleDown() {
  const totalQty   = parseFloat(document.getElementById('lad-split-qty').value) || 0;
  const startPrice = parseFloat(document.getElementById('lad-split-start').value) || 0;
  const endPrice   = parseFloat(document.getElementById('lad-split-end').value) || 0;
  const numRungs   = parseInt(document.getElementById('lad-split-rungs').value) || 2;

  if (totalQty <= 0 || startPrice <= 0 || endPrice <= 0 || numRungs < 1) return;

  const weights = [];
  for (let i = 0; i < numRungs; i++) weights.push(numRungs - i);
  const wSum = weights.reduce((a, b) => a + b, 0);
  const step = numRungs > 1 ? (endPrice - startPrice) / (numRungs - 1) : 0;

  S.ladderRungs = [];
  let assigned = 0;
  for (let i = 0; i < numRungs; i++) {
    const q = i === numRungs - 1
      ? totalQty - assigned
      : Math.round(totalQty * weights[i] / wSum);
    assigned += q;
    S.ladderRungs.push({ qty: String(Math.max(1, q)), price: (startPrice + step * i).toFixed(2) });
  }
  renderRungs();
}

export function onLadderTickerChange() {
  const lt = document.getElementById('lad-ticker').value.trim().toUpperCase();
  if (!lt) window.clearPositionPanel('lad-position-panel');
  else window.loadPositionSummaryForTicker(lt, 'lad-position-panel');

  S.ladderRecentState.page = 1;
  loadLadderRecent();
  loadLadderSuggest();
  loadLadderOrders();
}

export async function loadLadderSuggest() {
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

  S._lastSuggestTicker = ticker;
  status.textContent = 'analysing…';
  banner.innerHTML = '';

  // Reset verify panel when ticker changes
  S._verifyOpen = false;
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

    if (document.getElementById('lad-ticker').value.trim().toUpperCase() !== S._lastSuggestTicker) return;

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
        `<button class="strat-apply-btn" onclick="window.applyLadderSuggest()">Apply to Rungs</button>` +
      `</div>`;

    S.ladderSuggestion = data;

    // Only auto-apply when no rungs would fill immediately
    if (shouldAutoApply) applyLadderSuggest();

  } catch (e) {
    status.textContent = '';
    banner.innerHTML = `<div class="ladder-strategy-banner strat-dim">Error: ${esc(e.message)}</div>`;
    params.style.display = 'none';
  }
}

export async function loadLiveVerify() {
  const ticker = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const verifyDiv = document.getElementById('lad-verify-result');
  if (!ticker) return;

  // Toggle off if already open
  if (S._verifyOpen) {
    verifyDiv.style.display = 'none';
    S._verifyOpen = false;
    document.getElementById('lad-verify-btn').textContent = 'Verify vs API';
    return;
  }

  S._verifyOpen = true;
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
        <td>${r.quantity != null ? fmt(normalizeQty(r.action, r.quantity), 0) : '—'}</td>
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

export function applyLadderSuggest() {
  const data = S.ladderSuggestion;
  if (!data || !data.rungs || !data.rungs.length) return;

  // Set action dropdown
  const actionSelect = document.getElementById('lad-action');
  if (data.unwind_action && actionSelect) {
    actionSelect.value = data.unwind_action;
  }

  // Fill rungs
  S.ladderRungs = data.rungs.map(r => ({
    qty: String(r.qty),
    price: String(r.price),
  }));
  renderRungs();
}

export async function loadLadderRecent(page) {
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
    S.ladderRecentState.page = 1;
    S.ladderRecentState.eqCount = null;
    S.ladderRecentState.optCount = null;
  } else {
    S.ladderRecentState.page = page;
  }

  sidebar.innerHTML = '<div class="loading" style="padding:10px">Loading…</div>';

  try {
    const mainParams = { ticker, limit: S.LADDER_RECENT_LIMIT, page: S.ladderRecentState.page };
    if (!includeOpts) mainParams.category = 'equity';

    // Fetch trades + counts in parallel (counts only on first load of a ticker/toggle)
    const fetchTrades = fetch('/api/transactions?' + new URLSearchParams(mainParams)).then(r => r.json());
    const fetchCounts = (S.ladderRecentState.eqCount === null)
      ? Promise.all([
          fetch('/api/transactions?' + new URLSearchParams({ ticker, category: 'equity', limit: 10, page: 1 })).then(r => r.json()),
          fetch('/api/transactions?' + new URLSearchParams({ ticker, category: 'option',  limit: 10, page: 1 })).then(r => r.json()),
        ])
      : Promise.resolve(null);

    const [res, counts] = await Promise.all([fetchTrades, fetchCounts]);
    if (res.error) throw new Error(res.error);

    if (counts) {
      S.ladderRecentState.eqCount  = counts[0].total || 0;
      S.ladderRecentState.optCount = counts[1].total || 0;
    }

    S.ladderRecentState.pages = res.pages;
    S.ladderRecentState.total = res.total;

    // Count header
    const totalAll = S.ladderRecentState.eqCount + S.ladderRecentState.optCount;
    countDiv.innerHTML = `${totalAll.toLocaleString()} trades `
      + `<span style="color:#475569">(${S.ladderRecentState.eqCount.toLocaleString()} equity`
      + ` / ${S.ladderRecentState.optCount.toLocaleString()} option)</span>`;

    const trades = res.data.filter(r => S.LADDER_RECENT_ACTIONS.has(r.action));

    if (!trades.length && S.ladderRecentState.page === 1) {
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
        <td>${r.quantity != null ? fmt(normalizeQty(r.action, r.quantity), 0) : '—'}</td>
        <td>${r.price != null ? '$' + fmt(r.price, 4) : '—'}</td>
        <td>${optBadge}</td>
        <td>${r.option_strike != null ? '$' + fmt(r.option_strike) : ''}</td>
        <td style="color:#64748b">${r.option_expiry || ''}</td>
      </tr>`;
    }).join('');

    const cur = S.ladderRecentState.page, total_pages = S.ladderRecentState.pages;
    const prevDisabled = cur <= 1 ? 'disabled' : '';
    const nextDisabled = cur >= total_pages ? 'disabled' : '';
    const pagination = total_pages > 1
      ? `<div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;gap:6px">
           <button class="pg-btn" ${prevDisabled} onclick="window.loadLadderRecent(${cur - 1})">‹ Prev</button>
           <span class="pg-info">Page ${cur} of ${total_pages} · ${res.total.toLocaleString()} trades</span>
           <button class="pg-btn" ${nextDisabled} onclick="window.loadLadderRecent(${cur + 1})">Next ›</button>
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

export async function loadLadderOrders() {
  const ticker = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const container = document.getElementById('lad-orders');
  const countDiv = document.getElementById('lad-orders-count');
  const cancelAllBtn = document.getElementById('lad-cancel-all-btn');
  const includeOpts = document.getElementById('lad-orders-include-options').checked;

  if (!ticker) {
    container.innerHTML = '<div style="color:#475569;font-size:12px">Enter a ticker to see open orders</div>';
    countDiv.textContent = '';
    cancelAllBtn.style.display = 'none';
    S._ladderOrders = [];
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

    S._ladderOrders = orders;

    countDiv.innerHTML = `${orders.length} open order${orders.length !== 1 ? 's' : ''} `
      + `<span style="color:#475569">(${eqOrders.length} equity / ${optOrders.length} option)</span>`;

    if (orders.length === 0) {
      container.innerHTML = '<div style="color:#475569;font-size:12px">No open orders for ' + esc(ticker) + '</div>';
      return;
    }

    cancelAllBtn.style.display = '';

    const ctx = await enrichOrders(orders);
    renderOrders('lad-orders', null, orders, {
      variant: 'ladder',
      ctx,
      cancelHandlerName: 'cancelLadderOrder',
    });

  } catch (e) {
    container.innerHTML = '<div class="error" style="font-size:12px">Error: ' + esc(e.message) + '</div>';
    countDiv.textContent = '';
  }
}

export async function cancelLadderOrder(orderId) {
  if (!confirm('Cancel order ' + orderId + '?')) return;
  try {
    const res = await fetch('/api/order/' + orderId, { method: 'DELETE' }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    S.ordersState.loaded = false;
    await loadLadderOrders();
  } catch (e) {
    alert('Failed to cancel: ' + e.message);
  }
}

export async function cancelAllLadderOrders() {
  const cancelable = S._ladderOrders.filter(o => o.cancelable);
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

  S.ordersState.loaded = false;

  if (fail === 0) {
    container.innerHTML = `<div style="color:#86efac;font-size:12px;padding:6px 0">All ${ok} orders cancelled.</div>`;
  } else {
    container.innerHTML = `<div class="error" style="font-size:12px">${ok} cancelled, ${fail} failed. Refreshing…</div>`;
  }

  setTimeout(() => loadLadderOrders(), 1500);
}

export function previewLadder() {
  const ticker  = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const action  = document.getElementById('lad-action').value;
  const dur     = document.getElementById('lad-duration').value;
  const ses     = document.getElementById('lad-session').value;
  const resultDiv = document.getElementById('lad-result');

  if (!ticker) { resultDiv.innerHTML = '<div class="error">Ticker is required.</div>'; return; }

  const validRungs = S.ladderRungs.filter(r => parseFloat(r.qty) > 0 && parseFloat(r.price) > 0);
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
        '<button class="confirm-btn" onclick="window.submitLadder()">✓ Submit All Orders</button>' +
      '</div>' +
    '</div>';
}

export async function submitLadder() {
  if (S._ladderSubmitting) return;
  S._ladderSubmitting = true;

  const ticker  = document.getElementById('lad-ticker').value.trim().toUpperCase();
  const action  = document.getElementById('lad-action').value;
  const dur     = document.getElementById('lad-duration').value;
  const ses     = document.getElementById('lad-session').value;
  const resultDiv = document.getElementById('lad-result');

  const validRungs = S.ladderRungs
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
    S.ordersState.loaded = false;
    setTimeout(() => loadLadderOrders(), 1000);
  } catch(e) {
    resultDiv.innerHTML = '<div class="error">❌ Ladder submission failed: ' + esc(e.message) + '</div>';
  } finally {
    S._ladderSubmitting = false;
  }
}
