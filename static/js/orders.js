import { fmt, esc, fetchJson } from './utils.js';
import { store as S } from './state.js';

// ── Helpers: parsing + math ────────────────────────────────────────────────

function _isOptionOrder(o) {
  if (!o) return false;
  if (o.asset_type === 'OPTION') return true;
  return Array.isArray(o.legs_detail) && o.legs_detail.some(l => l && l.asset_type === 'OPTION');
}

function _orderUnderlyings(o) {
  if (Array.isArray(o.underlyings) && o.underlyings.length) return o.underlyings;
  return o.underlying ? [o.underlying] : [];
}

function _isSellOrder(o) {
  const top = (o.instruction || '').toUpperCase();
  if (top.includes('SELL')) return true;
  if (top.includes('BUY')) return false;
  // Fall back to legs_detail majority for spreads/multi-leg.
  const legs = Array.isArray(o.legs_detail) ? o.legs_detail : [];
  if (!legs.length) return false;
  const sells = legs.filter(l => (l.instruction || '').toUpperCase().includes('SELL')).length;
  return sells > legs.length / 2;
}

function _earliestExpiryISO(o) {
  if (!Array.isArray(o.legs_detail)) return null;
  let best = null;
  for (const l of o.legs_detail) {
    if (l && l.expiration_date) {
      if (!best || l.expiration_date < best) best = l.expiration_date;
    }
  }
  return best;
}

function _daysBetween(toISO) {
  if (!toISO) return null;
  // Treat both sides as UTC midnight for stable day math regardless of TZ.
  const ms = Date.parse(toISO.length <= 10 ? toISO + 'T00:00:00Z' : toISO.replace(' ', 'T') + 'Z');
  if (!Number.isFinite(ms)) return null;
  const today = new Date();
  const todayUTC = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  return Math.round((ms - todayUTC) / 86400000);
}

function _markForOrder(o, quotes) {
  if (!quotes) return null;
  // Single-leg option: prefer the OSI option mark if available.
  if (Array.isArray(o.legs_detail) && o.legs_detail.length === 1
      && o.legs_detail[0].asset_type === 'OPTION') {
    const q = quotes[o.legs_detail[0].symbol];
    if (q && q.last != null) return { mark: q.last, source: 'option' };
  }
  // Otherwise use the (first) underlying.
  const unders = _orderUnderlyings(o);
  for (const u of unders) {
    const q = quotes[u];
    if (q && q.last != null) return { mark: q.last, source: 'underlying' };
  }
  return null;
}

function _distancePct(o, mark) {
  const limit = o.price != null ? o.price : (o.stop_price != null ? o.stop_price : null);
  if (limit == null || mark == null || limit === 0) return null;
  // Signed: (mark - limit) / limit; positive means market above limit.
  return ((mark - limit) / limit) * 100;
}

// ── Warnings ───────────────────────────────────────────────────────────────

const WARN_LEVEL_HI = 'hi';
const WARN_LEVEL_MID = 'mid';
const WARN_LEVEL_LO = 'lo';

export function buildOrderWarnings(o, ctx) {
  const quotes = (ctx && ctx.quoteBySymbol) || {};
  const earnings = (ctx && ctx.earningsByUnderlying) || {};
  const out = [];

  // Earnings within 7 days for any underlying.
  for (const u of _orderUnderlyings(o)) {
    const d = earnings[u];
    if (!d) continue;
    const days = _daysBetween(d);
    if (days != null && days >= 0 && days <= 7) {
      out.push({
        level: days <= 2 ? WARN_LEVEL_HI : WARN_LEVEL_MID,
        text: `Earnings ${u} ${days}d`,
      });
    }
  }

  // Option expiration approaching.
  const expISO = _earliestExpiryISO(o);
  if (expISO) {
    const dte = _daysBetween(expISO);
    if (dte != null && dte <= 2) {
      out.push({ level: WARN_LEVEL_HI, text: `Exp ${dte}d` });
    }
  }

  // Price has crossed in the unfavorable direction.
  const m = _markForOrder(o, quotes);
  if (m && o.price != null) {
    const distRaw = _distancePct(o, m.mark);
    if (distRaw != null) {
      const isSell = _isSellOrder(o);
      const unfavorable = isSell ? distRaw < 0 : distRaw > 0;
      const mag = Math.abs(distRaw);
      if (unfavorable && mag >= 5) {
        out.push({
          level: mag >= 10 ? WARN_LEVEL_HI : WARN_LEVEL_MID,
          text: `Price ${mag.toFixed(1)}% off`,
        });
      }
    }
  }

  // Order itself ages out (cancel_time / release_time).
  const expiresISO = o.cancel_time || o.release_time;
  if (expiresISO) {
    const days = _daysBetween(expiresISO);
    if (days != null && days >= 0 && days <= 14) {
      out.push({
        level: days <= 3 ? WARN_LEVEL_HI : WARN_LEVEL_MID,
        text: `Order expires ${days}d`,
      });
    }
  }

  // Stale: still open after 30+ days.
  if (o.entered_time) {
    const enteredDays = _daysBetween(o.entered_time);
    if (enteredDays != null && enteredDays <= -30) {
      out.push({ level: WARN_LEVEL_LO, text: `Stale ${-enteredDays}d` });
    }
  }

  // Partial fill.
  const q = Number(o.quantity) || 0;
  const f = Number(o.filled_quantity) || 0;
  if (f > 0 && f < q) {
    out.push({ level: WARN_LEVEL_LO, text: `Partial ${f}/${q}` });
  }

  // Future ideas (not built):
  //  - Ex-div Xd for short calls near ex-dividend (needs dividend calendar).
  //  - Wide spread when bid/ask spread exceeds X% (needs option chain quote).

  return out;
}

function _warningsHtml(warnings) {
  if (!warnings.length) return '<span style="color:#475569">—</span>';
  return warnings.map(w =>
    `<span class="ord-warn ord-warn-${w.level}">${esc(w.text)}</span>`
  ).join('');
}

// ── Cell builders ──────────────────────────────────────────────────────────

function _markCellHtml(o, quotes) {
  const m = _markForOrder(o, quotes);
  if (!m) return '<span style="color:#475569">—</span>';
  const dist = _distancePct(o, m.mark);
  const markStr = '$' + fmt(m.mark, m.source === 'option' ? 4 : 2);
  if (dist == null) return `<span>${markStr}</span>`;
  const mag = Math.abs(dist);
  const isSell = _isSellOrder(o);
  const unfavorable = isSell ? dist < 0 : dist > 0;
  let cls = 'ord-dist-ok';
  if (mag >= 5 || unfavorable) cls = unfavorable && mag >= 5 ? 'ord-dist-bad' : 'ord-dist-mid';
  if (mag <= 1) cls = 'ord-dist-ok';
  const sign = dist > 0 ? '+' : (dist < 0 ? '' : '');
  return `<div style="line-height:1.2">
    <div>${markStr}</div>
    <div class="${cls}" style="font-size:11px">${sign}${dist.toFixed(2)}%</div>
  </div>`;
}

function _dteCellHtml(o) {
  if (!_isOptionOrder(o)) return '<span style="color:#475569">—</span>';
  const expISO = _earliestExpiryISO(o);
  if (!expISO) return '<span style="color:#475569">—</span>';
  const dte = _daysBetween(expISO);
  if (dte == null) return '<span style="color:#475569">—</span>';
  let cls = '';
  if (dte <= 2) cls = 'ord-dist-bad';
  else if (dte <= 7) cls = 'ord-dist-mid';
  else cls = 'ord-dist-ok';
  return `<span class="${cls}" title="${esc(expISO)}">${dte}d</span>`;
}

function _tifCellHtml(o) {
  const dur = (o.duration || '').replace(/_/g, ' ').toLowerCase();
  const expiresISO = o.cancel_time || o.release_time;
  let tail = '';
  if (expiresISO) {
    const days = _daysBetween(expiresISO);
    if (days != null) {
      const cls = days <= 3 ? 'ord-dist-bad' : (days <= 14 ? 'ord-dist-mid' : 'ord-dist-ok');
      tail = ` · <span class="${cls}">${days}d left</span>`;
    }
  }
  if (!dur && !tail) return '<span style="color:#475569">—</span>';
  return `<span style="color:#94a3b8;font-size:11px">${esc(dur || '')}${tail}</span>`;
}

// ── Numeric-aware sort ─────────────────────────────────────────────────────

const NUMERIC_SORT_COLS = new Set([
  'quantity', 'filled_quantity', 'price', 'distance_pct', 'dte',
]);

function _sortValue(o, col, ctx) {
  if (col === 'distance_pct') {
    const m = _markForOrder(o, (ctx && ctx.quoteBySymbol) || {});
    return m ? _distancePct(o, m.mark) : null;
  }
  if (col === 'dte') {
    const e = _earliestExpiryISO(o);
    return e ? _daysBetween(e) : null;
  }
  return o[col];
}

function _cmp(av, bv, dir, numeric) {
  const aNull = av === null || av === undefined || av === '';
  const bNull = bv === null || bv === undefined || bv === '';
  if (aNull && bNull) return 0;
  if (aNull) return 1;
  if (bNull) return -1;
  if (numeric) {
    const an = Number(av), bn = Number(bv);
    if (an < bn) return dir;
    if (an > bn) return -dir;
    return 0;
  }
  if (av < bv) return dir;
  if (av > bv) return -dir;
  return 0;
}

// ── Enrichment context ─────────────────────────────────────────────────────

export async function enrichOrders(orders) {
  const symbols = new Set();
  const underlyings = new Set();
  for (const o of (orders || [])) {
    for (const u of _orderUnderlyings(o)) {
      symbols.add(u);
      underlyings.add(u);
    }
    if (Array.isArray(o.legs_detail) && o.legs_detail.length === 1
        && o.legs_detail[0].asset_type === 'OPTION'
        && o.legs_detail[0].symbol) {
      symbols.add(o.legs_detail[0].symbol);
    }
  }

  const symList = Array.from(symbols);
  const undList = Array.from(underlyings);

  const ctx = { quoteBySymbol: {}, earningsByUnderlying: {} };

  const tasks = [];
  if (symList.length) {
    const qp = new URLSearchParams({ symbols: symList.join(',') });
    tasks.push(
      fetch('/api/quotes/symbols?' + qp).then(r => r.json()).then(rows => {
        if (Array.isArray(rows)) {
          for (const q of rows) if (q && q.symbol) ctx.quoteBySymbol[q.symbol] = q;
        }
      }).catch(() => {})
    );
  }
  if (undList.length) {
    const ep = new URLSearchParams({ symbols: undList.join(',') });
    tasks.push(
      fetch('/api/earnings?' + ep).then(r => r.json()).then(map => {
        if (map && typeof map === 'object' && !map.error) {
          ctx.earningsByUnderlying = map;
        }
      }).catch(() => {})
    );
  }

  await Promise.all(tasks);
  return ctx;
}

// ── Renderers ──────────────────────────────────────────────────────────────

function _renderFullRows(orders, ctx) {
  return orders.map(o => {
    const sideClass = _isSellOrder(o) ? 'badge-PUT' : 'badge-equity';
    const priceStr = o.price != null ? '$' + fmt(o.price, 4)
                   : o.stop_price != null ? 'Stop $' + fmt(o.stop_price) : '—';
    const cancelBtn = o.cancelable
      ? '<button class="cancel-order-btn" onclick="cancelOrder(' + esc(o.order_id) + ')">Cancel</button>'
      : '—';
    const warnings = buildOrderWarnings(o, ctx);
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
      '<td>' + _markCellHtml(o, ctx.quoteBySymbol) + '</td>' +
      '<td>' + _dteCellHtml(o) + '</td>' +
      '<td>' + _tifCellHtml(o) + '</td>' +
      '<td>' + _warningsHtml(warnings) + '</td>' +
      '<td style="color:#64748b;font-size:12px">' + esc(o.entered_time || '') + '</td>' +
      '<td>' + cancelBtn + '</td>' +
    '</tr>';
  }).join('');
}

function _renderLadderRows(orders, ctx, cancelHandlerName) {
  return orders.map(o => {
    const sideClass = _isSellOrder(o) ? 'neg' : 'pos';
    const priceStr = o.price != null ? '$' + fmt(o.price, 2)
                   : o.stop_price != null ? 'Stop $' + fmt(o.stop_price) : '—';
    const isOpt = o.asset_type !== 'EQUITY';
    const symbolLabel = isOpt
      ? `<span style="color:#94a3b8" title="${esc(o.symbol)}">${esc((o.symbol || '').substring(0, 20))}</span>`
      : '';
    const cancelBtn = o.cancelable
      ? `<button class="cancel-single-btn" onclick="window.${cancelHandlerName}('${esc(o.order_id)}')">✕</button>`
      : '';
    const warnings = buildOrderWarnings(o, ctx);
    const m = _markForOrder(o, ctx.quoteBySymbol);
    const dist = m ? _distancePct(o, m.mark) : null;
    const distStr = dist != null
      ? `<span class="${dist > 5 || dist < -5 ? 'ord-dist-mid' : 'ord-dist-ok'}" style="font-size:11px">${dist > 0 ? '+' : ''}${dist.toFixed(1)}%</span>`
      : '<span style="color:#475569">—</span>';
    return `<tr>
      <td><span class="${sideClass}">${esc(o.instruction)}</span></td>
      <td>${fmt(o.quantity, 0)}</td>
      <td>${priceStr}</td>
      <td>${distStr}</td>
      <td><span class="badge badge-status-${o.status}" style="font-size:9px;padding:1px 5px">${o.status}</span></td>
      <td>${symbolLabel}</td>
      <td>${_warningsHtml(warnings)}</td>
      <td>${cancelBtn}</td>
    </tr>`;
  }).join('');
}

function _renderStrategyRows(orders, ctx, cancelHandlerName) {
  return orders.map(o => {
    const sc = _isSellOrder(o) ? 'neg' : 'pos';
    const p = o.price != null ? '$' + fmt(o.price, 2)
            : o.stop_price != null ? 'Stp $' + fmt(o.stop_price) : '—';
    const cancelHtml = o.cancelable
      ? `<button class="cancel-single-btn" onclick="${cancelHandlerName}('${esc(o.order_id)}')">✕</button>`
      : '';
    const warnings = buildOrderWarnings(o, ctx);
    const m = _markForOrder(o, ctx.quoteBySymbol);
    const dist = m ? _distancePct(o, m.mark) : null;
    const distStr = dist != null
      ? `<span class="${dist > 5 || dist < -5 ? 'ord-dist-mid' : 'ord-dist-ok'}" style="font-size:11px">${dist > 0 ? '+' : ''}${dist.toFixed(1)}%</span>`
      : '<span style="color:#475569">—</span>';
    return `<tr>
      <td><span class="${sc}">${esc(o.instruction)}</span></td>
      <td>${fmt(o.quantity, 0)}</td>
      <td>${p}</td>
      <td>${distStr}</td>
      <td><span class="badge badge-status-${o.status}" style="font-size:9px;padding:1px 5px">${o.status}</span></td>
      <td>${_warningsHtml(warnings)}</td>
      <td>${cancelHtml}</td>
    </tr>`;
  }).join('');
}

/**
 * Shared order renderer.
 * - variant 'full'     → full Open Orders tab (table is set up in dashboard.html).
 * - variant 'ladder'   → ladder sidebar table; injects own table HTML.
 * - variant 'strategy' → strategy sidebar table; injects own table HTML.
 */
export function renderOrders(containerId, countId, orders, opts) {
  const variant = (opts && opts.variant) || 'full';
  const ctx = (opts && opts.ctx) || { quoteBySymbol: {}, earningsByUnderlying: {} };
  const cancelHandlerName = (opts && opts.cancelHandlerName) || 'cancelOrder';

  if (countId) {
    const countEl = document.getElementById(countId);
    if (countEl) {
      countEl.textContent = orders.length + ' open order' + (orders.length !== 1 ? 's' : '');
    }
  }

  if (variant === 'full') {
    const tbody = document.getElementById('ord-tbody');
    if (tbody) tbody.innerHTML = _renderFullRows(orders, ctx);
    return;
  }

  const container = document.getElementById(containerId);
  if (!container) return;

  if (variant === 'ladder') {
    container.innerHTML =
      '<table><thead><tr>' +
      '<th>Side</th><th>Qty</th><th>Price</th><th>Dist</th><th>Status</th><th>Symbol</th><th>Warn</th><th></th>' +
      '</tr></thead><tbody>' + _renderLadderRows(orders, ctx, cancelHandlerName) + '</tbody></table>';
    return;
  }

  if (variant === 'strategy') {
    container.innerHTML =
      '<table><thead><tr>' +
      '<th>Side</th><th>Qty</th><th>Price</th><th>Dist</th><th>Status</th><th>Warn</th><th></th>' +
      '</tr></thead><tbody>' + _renderStrategyRows(orders, ctx, cancelHandlerName) + '</tbody></table>';
  }
}

// ── Open Orders tab ────────────────────────────────────────────────────────

export async function loadOrders() {
  document.getElementById('ord-loading').style.display = 'block';
  document.getElementById('ord-table').style.display = 'none';
  document.getElementById('ord-empty').style.display = 'none';
  document.getElementById('ord-error').style.display = 'none';
  try {
    const raw = await fetchJson('/api/orders');
    S._allOrders = raw;
    S.ordersState.loaded = true;
    // Enrich once per load (cached map of quotes + earnings).
    S._ordersCtx = await enrichOrders(raw);
    document.getElementById('ord-loading').style.display = 'none';
    filterOrders();
  } catch (e) {
    document.getElementById('ord-loading').style.display = 'none';
    document.getElementById('ord-error').style.display = 'block';
    document.getElementById('ord-error').textContent = 'Error: ' + e.message;
  }
}

export function sortOrders(col) {
  if (S._ordSortCol === col) S._ordSortDir *= -1;
  else { S._ordSortCol = col; S._ordSortDir = -1; }
  filterOrders();
}

export function filterOrders() {
  const ticker = document.getElementById('ord-ticker').value.trim().toUpperCase();
  const type = document.getElementById('ord-type').value;
  const status = document.getElementById('ord-status').value;
  const ctx = S._ordersCtx || { quoteBySymbol: {}, earningsByUnderlying: {} };

  let rows = (S._allOrders || []).filter(o => {
    if (ticker && !o.underlying.includes(ticker) && !o.symbol.includes(ticker)) return false;
    if (type && o.order_type !== type) return false;
    if (status && o.status !== status) return false;
    return true;
  });

  const col = S._ordSortCol;
  const dir = S._ordSortDir;
  const numeric = NUMERIC_SORT_COLS.has(col);
  rows.sort((a, b) => _cmp(_sortValue(a, col, ctx), _sortValue(b, col, ctx), dir, numeric));

  if (rows.length === 0) {
    document.getElementById('ord-table').style.display = 'none';
    document.getElementById('ord-empty').style.display = 'block';
    const countEl = document.getElementById('ord-count');
    if (countEl) countEl.textContent = '0 open orders';
    return;
  }
  document.getElementById('ord-empty').style.display = 'none';
  document.getElementById('ord-table').style.display = 'block';

  renderOrders('ord-tbody', 'ord-count', rows, { variant: 'full', ctx });
}

export async function cancelOrder(orderId) {
  if (!confirm('Cancel order ' + orderId + '?')) return;
  try {
    const res = await fetch('/api/order/' + orderId, { method: 'DELETE' }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    await loadOrders();
  } catch (e) {
    alert('Failed to cancel order: ' + e.message);
  }
}
