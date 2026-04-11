import { fmt, fmtD, cls, esc, normalizeQty, debounce, fetchJson, formatRgLastImport } from './utils.js';
import { store as S } from './state.js';

window._paginationRegistry = S._paginationRegistry;

export async function _loadHistoryLastSync() {
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

export async function loadHistory(resetPage=true) {
  if (resetPage) S.historyState.page = 1;
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
      page: S.historyState.page, limit, ticker, search, category
    });
    const res  = await fetchJson('/api/transactions?' + params);
    S.historyState.loaded = true;

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
        <td>${r.quantity!=null?fmt(normalizeQty(r.action, r.quantity),0):'—'}</td>
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

export function closeSyncModal() {
  document.getElementById('h-sync-modal').style.display = 'none';
}

export async function syncTrades() {
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

      window.loadHistory();
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

export async function loadGains(resetPage=true) {
  if (resetPage) S.gainsState.page = 1;
  const ticker = document.getElementById('g-ticker').value.trim();
  const term   = document.getElementById('g-term').value;
  const limit  = document.getElementById('g-limit').value;

  document.getElementById('g-loading').style.display='block';
  document.getElementById('g-table').style.display='none';
  document.getElementById('g-error').style.display='none';

  try {
    const params = new URLSearchParams({
      page: S.gainsState.page, limit, ticker, term
    });
    const res = await fetch('/api/realized_gains?'+params).then(r=>r.json());
    if (res.error) throw new Error(res.error);
    S.gainsState.loaded = true;

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

export function renderPagination(containerId, res, loadFn, stateObj, fnName) {
  if (!stateObj) {
    stateObj = loadFn === loadHistory ? S.historyState : S.gainsState;
    fnName   = loadFn === loadHistory ? 'loadHistory' : 'loadGains';
  }
  const { page, pages, total, limit } = res;
  const start = (page-1)*limit+1, end = Math.min(page*limit, total);
  const el = document.getElementById(containerId);
  if (pages <= 1) { el.innerHTML=''; return; }

  const uid = containerId;
  S._paginationRegistry[uid] = { state: stateObj, fn: loadFn };

  let btns = '';
  const addBtn = (p, label, active, disabled) =>
    `<button class="pg-btn${active?' active':''}" ${disabled?'disabled':''} onclick="
      window._paginationRegistry['${uid}'].state.page=${p};
      window._paginationRegistry['${uid}'].fn(false)">${label}</button>`;

  btns += addBtn(page-1,'‹ Prev', false, page===1);
  const lo=Math.max(1,page-3), hi=Math.min(pages,page+3);
  if (lo>1) btns += addBtn(1,'1',false,false) + (lo>2?'<span class="pg-info">…</span>':'');
  for (let p=lo;p<=hi;p++) btns += addBtn(p,p,p===page,false);
  if (hi<pages) btns += (hi<pages-1?'<span class="pg-info">…</span>':'') + addBtn(pages,pages,false,false);
  btns += addBtn(page+1,'Next ›',false,page===pages);

  el.innerHTML = `<span class="pg-info">${start}–${end} of ${total.toLocaleString()}</span>` + btns;
}
