import { fmt, esc, fetchJson } from './utils.js';
import * as S from './state.js';

export async function loadOrders() {
  document.getElementById('ord-loading').style.display='block';
  document.getElementById('ord-table').style.display='none';
  document.getElementById('ord-empty').style.display='none';
  document.getElementById('ord-error').style.display='none';
  try {
    const raw = await fetchJson('/api/orders');
    S._allOrders = raw;
    S.ordersState.loaded = true;
    document.getElementById('ord-loading').style.display='none';
    filterOrders();
  } catch(e) {
    document.getElementById('ord-loading').style.display='none';
    document.getElementById('ord-error').style.display='block';
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
  const type   = document.getElementById('ord-type').value;
  const status = document.getElementById('ord-status').value;

  let rows = S._allOrders.filter(o => {
    if (ticker && !o.underlying.includes(ticker) && !o.symbol.includes(ticker)) return false;
    if (type   && o.order_type !== type)   return false;
    if (status && o.status !== status)     return false;
    return true;
  });

  rows.sort((a, b) => {
    let av = a[S._ordSortCol], bv = b[S._ordSortCol];
    if (av === null || av === undefined) av = '';
    if (bv === null || bv === undefined) bv = '';
    if (av < bv) return  S._ordSortDir;
    if (av > bv) return -S._ordSortDir;
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

export async function cancelOrder(orderId) {
  if (!confirm('Cancel order ' + orderId + '?')) return;
  try {
    const res = await fetch('/api/order/' + orderId, { method: 'DELETE' }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    await loadOrders();
  } catch(e) {
    alert('Failed to cancel order: ' + e.message);
  }
}
