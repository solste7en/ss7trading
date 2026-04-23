import { fmt, fmtD, cls, esc } from './utils.js';
import { store as S } from './state.js';
import {
  earningsTagHtml,
  earningsTradingDaysSortKey,
  fetchEarningsMap,
  refreshEarningsForSymbol,
} from './earningsUi.js';

export async function initWatchlists() {
  if (S.wlState.initialized) { loadQuotes(); return; }
  S.wlState.initialized = true;
  const qTable = document.getElementById('q-table');
  if (qTable) {
    qTable.addEventListener('click', async e => {
      const tag = e.target.closest('[data-earn-refresh]');
      if (!tag) return;
      const sym = tag.dataset.symbol;
      if (!sym) return;
      const td = tag.closest('td');
      if (td) td.innerHTML = '<span class="earn-tag earn-tag-na">…</span>';
      const result = await refreshEarningsForSymbol(sym);
      if (result) S._qEarnMap[sym.toUpperCase()] = result[sym.toUpperCase()] ?? null;
      if (td) td.innerHTML = earningsTagHtml(S._qEarnMap[sym.toUpperCase()], { symbol: sym });
    });
  }
  try {
    const lists = await fetch('/api/watchlists').then(r => r.json());
    S.wlState.lists = lists;
    _renderWlTabs();
    loadQuotes();
  } catch (e) {
    loadQuotes();
  }
}

export function _renderWlTabs() {
  const container = document.getElementById('wl-tabs');
  const fixed = `<button class="wl-tab${S.wlState.currentId === 'positions' ? ' active' : ''}" data-list="positions" onclick="selectWatchlist('positions')">All Positions</button>`;
  const dynamic = S.wlState.lists.map(l =>
    `<button class="wl-tab${S.wlState.currentId === l.id ? ' active' : ''}" data-list="${l.id}" onclick="selectWatchlist(${l.id})">${esc(l.name)}</button>`
  ).join('');
  container.innerHTML = fixed + dynamic;
}

export function selectWatchlist(id) {
  S.wlState.currentId = id;
  _renderWlTabs();
  const isCustom = id !== 'positions';
  const editBar = document.getElementById('wl-edit-bar');
  const removeCol = document.getElementById('q-remove-col');
  editBar.style.display = isCustom ? 'flex' : 'none';
  if (removeCol) removeCol.style.display = isCustom ? '' : 'none';
  const list = S.wlState.lists.find(l => l.id === id);
  document.getElementById('q-list-label').textContent =
    isCustom && list ? `Live quotes — ${list.name}` : 'Live quotes — All Positions';
  loadQuotes();
}

export function showNewListForm() {
  document.getElementById('wl-new-form').style.display = 'flex';
  document.getElementById('wl-new-btn').style.display = 'none';
  document.getElementById('wl-name-input').focus();
}

export function hideNewListForm() {
  document.getElementById('wl-new-form').style.display = 'none';
  document.getElementById('wl-new-btn').style.display = '';
  document.getElementById('wl-name-input').value = '';
}

export async function createWatchlist() {
  const name = document.getElementById('wl-name-input').value.trim();
  if (!name) return;
  try {
    const res = await fetch('/api/watchlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }).then(r => r.json());
    if (res.error) { alert('Error: ' + res.error); return; }
    S.wlState.lists.push(res);
    hideNewListForm();
    selectWatchlist(res.id);
  } catch (e) { alert('Error: ' + e.message); }
}

export async function deleteCurrentList() {
  const id = S.wlState.currentId;
  if (id === 'positions') return;
  const list = S.wlState.lists.find(l => l.id === id);
  if (!confirm(`Delete list "${list?.name}"? This cannot be undone.`)) return;
  try {
    await fetch('/api/watchlists/' + id, { method: 'DELETE' });
    S.wlState.lists = S.wlState.lists.filter(l => l.id !== id);
    selectWatchlist('positions');
  } catch (e) { alert('Error: ' + e.message); }
}

export async function addWatchlistSymbol() {
  const id = S.wlState.currentId;
  if (id === 'positions') return;
  const sym = document.getElementById('wl-sym-input').value.trim().toUpperCase();
  if (!sym) return;
  try {
    await fetch(`/api/watchlists/${id}/symbols`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: sym }),
    });
    document.getElementById('wl-sym-input').value = '';
    const list = S.wlState.lists.find(l => l.id === id);
    if (list) list.symbol_count = (list.symbol_count || 0) + 1;
    _renderWlTabs();
    loadQuotes();
  } catch (e) { alert('Error: ' + e.message); }
}

export async function removeWatchlistSymbol(listId, symbol) {
  try {
    await fetch(`/api/watchlists/${listId}/symbols/${symbol}`, { method: 'DELETE' });
    loadQuotes();
  } catch (e) { alert('Error: ' + e.message); }
}

function _quoteField(row, col) {
  if (col === 'hi52') return row['52w_high'];
  if (col === 'lo52') return row['52w_low'];
  return row[col];
}

function _compareQuoteRows(a, b, earnMap, col, dir) {
  if (col === 'symbol') {
    const c = String(a.symbol || '').localeCompare(String(b.symbol || ''), undefined, { sensitivity: 'base' });
    if (c !== 0) return c * dir;
    return 0;
  }

  let av;
  let bv;
  if (col === 'earnings_td') {
    const ua = String(a.symbol || '').toUpperCase();
    const ub = String(b.symbol || '').toUpperCase();
    av = earningsTradingDaysSortKey(earnMap[ua]);
    bv = earningsTradingDaysSortKey(earnMap[ub]);
  } else {
    av = _quoteField(a, col);
    bv = _quoteField(b, col);
    av = av != null && av !== '' ? Number(av) : null;
    bv = bv != null && bv !== '' ? Number(bv) : null;
    if (Number.isNaN(av)) av = null;
    if (Number.isNaN(bv)) bv = null;
  }

  const na = av == null || (typeof av === 'number' && Number.isNaN(av));
  const nb = bv == null || (typeof bv === 'number' && Number.isNaN(bv));
  if (na && nb) {
    return String(a.symbol || '').localeCompare(String(b.symbol || ''), undefined, { sensitivity: 'base' }) * dir;
  }
  if (na) return 1 * dir;
  if (nb) return -1 * dir;
  if (av !== bv) return av < bv ? -dir : dir;
  return String(a.symbol || '').localeCompare(String(b.symbol || ''), undefined, { sensitivity: 'base' }) * dir;
}

function _sortedQuoteRows() {
  const rows = (S._qRows || []).slice();
  const earn = S._qEarnMap || {};
  const col = S._qSortCol || 'symbol';
  const dir = S._qSortDir != null ? S._qSortDir : 1;
  rows.sort((a, b) => _compareQuoteRows(a, b, earn, col, dir));
  return rows;
}

function _quoteRowHtml(q, id, isCustom) {
  const ed = S._qEarnMap[String(q.symbol || '').toUpperCase()];
  const earnHtml = earningsTagHtml(ed, { symbol: q.symbol });
  return `<tr>
      <td><b>${esc(q.symbol)}</b></td>
      <td class="q-earnings-cell">${earnHtml}</td>
      <td>$${fmt(q.last)}</td>
      <td>${q.bid != null ? '$' + fmt(q.bid) : '—'}</td>
      <td>${q.ask != null ? '$' + fmt(q.ask) : '—'}</td>
      <td class="${cls(q.change)}">${q.change != null ? '$' + fmtD(q.change) : '—'}</td>
      <td class="${cls(q.change_pct)}">${q.change_pct != null ? fmtD(q.change_pct) + '%' : '—'}</td>
      <td>${q.volume != null ? Number(q.volume).toLocaleString() : '—'}</td>
      <td>${q['52w_high'] != null ? '$' + fmt(q['52w_high']) : '—'}</td>
      <td>${q['52w_low'] != null ? '$' + fmt(q['52w_low']) : '—'}</td>
      ${isCustom ? `<td><button class="wl-remove-sym" onclick="removeWatchlistSymbol(${id},'${esc(q.symbol)}')">✕</button></td>` : ''}
    </tr>`;
}

function _renderQuotesTableBody() {
  const id = S._qListId;
  const isCustom = S._qIsCustom;
  const sorted = _sortedQuoteRows();
  document.getElementById('q-tbody').innerHTML = sorted.map(q => _quoteRowHtml(q, id, isCustom)).join('');
}

function _updateQuoteSortArrows() {
  const col = S._qSortCol || 'symbol';
  document.querySelectorAll('#q-table thead .sort-arrow[data-qa]').forEach(span => {
    const c = span.getAttribute('data-qa');
    span.textContent = c === col ? (S._qSortDir > 0 ? ' ▲' : ' ▼') : '';
  });
}

export function sortQuotes(col) {
  if (S._qSortCol === col) S._qSortDir *= -1;
  else {
    S._qSortCol = col;
    S._qSortDir = 1;
  }
  _renderQuotesTableBody();
  _updateQuoteSortArrows();
}

export async function loadQuotes() {
  const id = S.wlState.currentId;
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
    const syms = data.map(q => q.symbol).filter(Boolean);
    const earnMap = await fetchEarningsMap(syms);

    S._qRows = Array.isArray(data) ? data : [];
    S._qEarnMap = earnMap;
    S._qIsCustom = isCustom;
    S._qListId = id;
    S._qSortCol = 'symbol';
    S._qSortDir = 1;

    _renderQuotesTableBody();
    _updateQuoteSortArrows();

    document.getElementById('q-loading').style.display = 'none';
    document.getElementById('q-table').style.display = 'table';
  } catch (e) {
    document.getElementById('q-loading').style.display = 'none';
    document.getElementById('q-error').style.display = 'block';
    document.getElementById('q-error').textContent = 'Error: ' + e.message;
  }
}

export async function fetchQuote() {
  const sym = document.getElementById('quoteInput').value.trim().toUpperCase();
  if (!sym) return;
  const div = document.getElementById('quote-result');
  div.innerHTML = '<div class="loading">Loading ' + esc(sym) + '…</div>';
  try {
    const [q, earnMap] = await Promise.all([
      fetch('/api/quote/' + sym).then(r => r.json()),
      fetchEarningsMap([sym]),
    ]);
    if (!q.symbol) { div.innerHTML = '<div class="error">No data for ' + esc(sym) + '</div>'; return; }
    const earnLine = earningsTagHtml(earnMap[sym.toUpperCase()]);
    div.innerHTML = `<div class="quote-card">
      <div class="sym">${esc(q.symbol)}</div>
      <div class="quote-earn-line">${earnLine}</div>
      <div class="last ${cls(q.change)}">$${fmt(q.last)}</div>
      <div><label>Bid</label><div class="val">$${fmt(q.bid)}</div></div>
      <div><label>Ask</label><div class="val">$${fmt(q.ask)}</div></div>
      <div><label>Change</label><div class="val ${cls(q.change)}">${q.change != null ? '$' + fmtD(q.change) : '—'}</div></div>
      <div><label>Change %</label><div class="val ${cls(q.change_pct)}">${q.change_pct != null ? fmtD(q.change_pct) + '%' : '—'}</div></div>
      <div><label>Volume</label><div class="val">${q.volume != null ? Number(q.volume).toLocaleString() : '—'}</div></div>
      <div><label>52W High</label><div class="val">$${fmt(q['52w_high'])}</div></div>
      <div><label>52W Low</label><div class="val">$${fmt(q['52w_low'])}</div></div>
    </div>`;
  } catch (e) { div.innerHTML = '<div class="error">Error: ' + esc(e.message) + '</div>'; }
}
