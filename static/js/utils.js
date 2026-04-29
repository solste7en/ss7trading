export const fmt   = (v,d=2) => v==null ? '—' : Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
export const fmtD  = (v,d=2) => v==null ? '—' : (v>=0?'+':'') + fmt(v,d);
export const cls   = (v)     => v==null ? '' : (v>=0?'pos':'neg');
export const esc   = (s)     => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
export const _SELL_ACTIONS = new Set(['Sell', 'Sell Short', 'Sell to Open', 'Sell to Close']);
export const _BUY_ACTIONS  = new Set(['Buy', 'Buy to Cover', 'Buy to Open', 'Buy to Close']);
export function normalizeQty(action, qty) {
  if (qty == null) return null;
  const q = Number(qty);
  if (_SELL_ACTIONS.has(action) && q > 0) return -q;
  if (_BUY_ACTIONS.has(action) && q < 0) return Math.abs(q);
  return q;
}

/** SQLite / ISO-ish datetime for Realized G/L banner */
export function formatRgLastImport(raw) {
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
export function debounce(fn) { clearTimeout(_debTimer); _debTimer = setTimeout(fn, 400); }

/**
 * GET/POST JSON: parse body, throw on `error` field or non-OK status.
 */
export async function fetchJson(url, init) {
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
export function ladderResultTableHtml(results, options) {
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

export function round2(v) { return Math.round(v * 100) / 100; }

let _toastTimer = null;
function _showToast(msg, kind, ms) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.className = 'toast toast-' + kind;
  t.style.display = '';
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { t.style.display = 'none'; _toastTimer = null; }, ms);
}
export function showError(msg)   { _showToast(msg, 'error', 5000); }
export function showSuccess(msg) { _showToast(msg, 'success', 2500); }
