import { fmt, fmtD, cls, esc, normalizeQty, fetchJson } from './utils.js';
import { store as S } from './state.js';
import { earningsTagHtml, earningsTradingDaysSortKey, fetchEarningsMap, refreshEarningsForSymbol } from './earningsUi.js';

export function _posUnderlyingFromOption(p) {
  return String(p.underlying_symbol || p.symbol.split(/\s+/)[0] || '').toUpperCase();
}

export function _collectPositionTabKeys(data) {
  const nonOpt = data.filter(p => p.asset_type !== 'OPTION');
  const opts = data.filter(p => p.asset_type === 'OPTION');
  const underlyings = new Set();
  const shortEq = [];
  nonOpt.forEach(p => {
    underlyings.add(String(p.symbol).toUpperCase());
    if (p.quantity < 0 && p.asset_type !== 'CASH_EQUIVALENT') {
      shortEq.push(String(p.symbol).toUpperCase());
    }
  });
  opts.forEach(p => underlyings.add(_posUnderlyingFromOption(p)));
  return { underlyings: [...underlyings], short_equity: shortEq };
}

export function bindPositionsDnD() {
  if (S._posDndBound) return;
  S._posDndBound = true;
  const root = document.getElementById('pos-sections');
  if (!root) return;

  root.addEventListener('click', e => {
    const tr = e.target.closest('tr.pos-opt-toggle');
    if (!tr || tr.dataset.expandUnderlying == null) return;
    if (e.target.closest('.pos-drag-handle')) return;
    if (e.target.closest('button')) return;
    togglePosGroup(tr.dataset.expandUnderlying);
  });

  root.addEventListener('click', async e => {
    const tag = e.target.closest('[data-earn-refresh]');
    if (!tag) return;
    const sym = tag.dataset.symbol;
    if (!sym) return;
    const td = tag.closest('td');
    if (td) td.innerHTML = '<span class="earn-tag earn-tag-na">…</span>';
    const result = await refreshEarningsForSymbol(sym);
    if (result) S._posEarnings[sym.toUpperCase()] = result[sym.toUpperCase()] ?? null;
    if (td) td.innerHTML = earningsTagHtml(S._posEarnings[sym.toUpperCase()], { symbol: sym });
  });

  root.addEventListener('dragstart', e => {
    S._posDndPayload = null;
    const h = e.target.closest('.pos-drag-handle');
    if (!h || h.dataset.underlying == null) return;
    const tr = h.closest('tr');
    const srcList = tr && tr.dataset.listId != null ? parseInt(tr.dataset.listId, 10) : NaN;
    S._posDndPayload = {
      type: 'symbol',
      underlying: String(h.dataset.underlying).toUpperCase(),
      sourceListId: srcList,
    };
    e.dataTransfer.setData('application/json', JSON.stringify(S._posDndPayload));
    e.dataTransfer.effectAllowed = 'move';
    if (tr) tr.classList.add('pos-dragging');
  });

  root.addEventListener('dragend', () => {
    document.querySelectorAll('.pos-dragging').forEach(el => el.classList.remove('pos-dragging'));
    document.querySelectorAll('.pos-list-dropzone.drag-over, tr.pos-symbol-reorder-target.drag-over').forEach(el => {
      el.classList.remove('drag-over');
    });
    S._posDndPayload = null;
  });

  root.addEventListener('dragover', e => {
    const p = S._posDndPayload;
    if (!p || p.type !== 'symbol') return;
    const row = e.target.closest('tr.pos-symbol-reorder-target');
    const zone = e.target.closest('.pos-list-dropzone');
    if (row || zone) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
    }
  });

  root.addEventListener('dragenter', e => {
    const p = S._posDndPayload;
    if (!p || p.type !== 'symbol') return;
    const row = e.target.closest('tr.pos-symbol-reorder-target');
    if (row) row.classList.add('drag-over');
    else {
      const zone = e.target.closest('.pos-list-dropzone');
      if (zone) zone.classList.add('drag-over');
    }
  });

  root.addEventListener('dragleave', e => {
    const row = e.target.closest('tr.pos-symbol-reorder-target');
    if (row && !row.contains(e.relatedTarget)) row.classList.remove('drag-over');
    const sum = e.target.closest('.pos-list-summary');
    if (sum && !sum.contains(e.relatedTarget)) sum.classList.remove('drag-over');
    const zone = e.target.closest('.pos-list-table-wrap');
    if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove('drag-over');
  });

  root.addEventListener('drop', async e => {
    const raw = e.dataTransfer.getData('application/json');
    let payload = S._posDndPayload;
    try {
      if (raw) payload = JSON.parse(raw);
    } catch (_) {}
    if (!payload || payload.type !== 'symbol') return;

    const u = String(payload.underlying || '').toUpperCase();
    const srcList = parseInt(payload.sourceListId, 10);
    if (!u || !Number.isFinite(srcList)) return;

    const row = e.target.closest('tr.pos-symbol-reorder-target');
    if (row) {
      const targetList = parseInt(row.dataset.listId, 10);
      const targetBlock = row.dataset.posBlock;
      if (Number.isFinite(targetList) && targetBlock && targetList !== srcList) {
        e.preventDefault();
        row.classList.remove('drag-over');
        try {
          const res = await fetchJson('/api/position-assignments', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [u]: targetList }),
          });
          S._posAssignments[u] = targetList;
          _renderPositions();
        } catch (err) {
          alert(err.message || String(err));
        }
        return;
      }
    }

    const z = e.target.closest('.pos-list-dropzone');
    if (!z) return;
    e.preventDefault();
    z.classList.remove('drag-over');
    const lid = parseInt(z.dataset.listId, 10);
    if (!Number.isFinite(lid)) return;
    if (lid === srcList) return;
    try {
      const res = await fetchJson('/api/position-assignments', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [u]: lid }),
      });
      S._posAssignments[u] = lid;
      _renderPositions();
    } catch (err) {
      alert(err.message || String(err));
    }
  });
}

export function togglePosGroup(underlying) {
  if (S._posExpanded.has(underlying)) S._posExpanded.delete(underlying);
  else S._posExpanded.add(underlying);
  _renderPositions();
}

export async function movePositionList(listId, delta) {
  const listsSorted = (S._posLists || []).slice().sort((a, b) => {
    if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
    return a.id - b.id;
  });
  const idx = listsSorted.findIndex(l => l.id === listId);
  if (idx < 0) return;
  const nidx = idx + delta;
  if (nidx < 0 || nidx >= listsSorted.length) return;
  const order = listsSorted.map(l => l.id);
  const t = order[idx];
  order[idx] = order[nidx];
  order[nidx] = t;
  try {
    const res = await fetchJson('/api/position-lists/reorder', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order }),
    });
    S._posLists = res.lists || S._posLists;
    _renderPositions();
  } catch (e) {
    alert(e.message || String(e));
  }
}

export function _posVsRecentSortValue(underlying, equityRow) {
  const met = S._posRecentMetrics[underlying];
  if (!met) return null;
  const cur = equityRow && equityRow.asset_type !== 'OPTION' ? equityRow.current_price : null;
  if (cur == null) return null;
  const f = met.avg_price;
  if (f == null || Number.isNaN(f)) return null;
  const m = Math.abs(cur);
  const diff = m - f;
  const isShort = equityRow.quantity < 0;
  return isShort ? f - m : diff;
}

export function _posSortCompare(a, b) {
  if (!S._posSortCol) return 0;
  if (S._posSortCol === 'recent_avg') {
    const ma = S._posRecentMetrics[a.symbol];
    const mb = S._posRecentMetrics[b.symbol];
    let av = ma ? ma.avg_price : null;
    let bv = mb ? mb.avg_price : null;
    if (av == null) av = S._posSortDir > 0 ? Infinity : -Infinity;
    if (bv == null) bv = S._posSortDir > 0 ? Infinity : -Infinity;
    return av < bv ? -S._posSortDir : av > bv ? S._posSortDir : 0;
  }
  if (S._posSortCol === 'recent_net') {
    const ma = S._posRecentMetrics[a.symbol];
    const mb = S._posRecentMetrics[b.symbol];
    let av = ma ? ma.net_shares : null;
    let bv = mb ? mb.net_shares : null;
    if (av == null) av = S._posSortDir > 0 ? Infinity : -Infinity;
    if (bv == null) bv = S._posSortDir > 0 ? Infinity : -Infinity;
    return av < bv ? -S._posSortDir : av > bv ? S._posSortDir : 0;
  }
  if (S._posSortCol === 'vs_recent_pct') {
    let av = _posVsRecentSortValue(a.symbol, a);
    let bv = _posVsRecentSortValue(b.symbol, b);
    if (av == null) av = S._posSortDir > 0 ? Infinity : -Infinity;
    if (bv == null) bv = S._posSortDir > 0 ? Infinity : -Infinity;
    return av < bv ? -S._posSortDir : av > bv ? S._posSortDir : 0;
  }
  if (S._posSortCol === 'earnings_td') {
    const ua = String(a.symbol || '').toUpperCase();
    const ub = String(b.symbol || '').toUpperCase();
    const av = earningsTradingDaysSortKey(S._posEarnings[ua]);
    const bv = earningsTradingDaysSortKey(S._posEarnings[ub]);
    if (av !== bv) return av < bv ? -S._posSortDir : S._posSortDir;
    return String(a.symbol || '').localeCompare(String(b.symbol || ''), undefined, { sensitivity: 'base' }) * S._posSortDir;
  }
  let av = a[S._posSortCol];
  let bv = b[S._posSortCol];
  if (av == null) av = S._posSortDir > 0 ? Infinity : -Infinity;
  if (bv == null) bv = S._posSortDir > 0 ? Infinity : -Infinity;
  if (typeof av === 'string') av = av.toLowerCase();
  if (typeof bv === 'string') bv = bv.toLowerCase();
  return av < bv ? -S._posSortDir : av > bv ? S._posSortDir : 0;
}

export function _posBlocksForList(listId) {
  const nonOptions = S._posData.filter(p => p.asset_type !== 'OPTION');
  const options = S._posData.filter(p => p.asset_type === 'OPTION');
  const optMap = {};
  options.forEach(p => {
    const key = _posUnderlyingFromOption(p);
    (optMap[key] = optMap[key] || []).push(p);
  });
  Object.values(optMap).forEach(grp => grp.sort((a, b) => {
    const expA = a.option_expiry || '';
    const expB = b.option_expiry || '';
    if (expA !== expB) return expA < expB ? -1 : 1;
    const stA = a.option_strike ?? 0;
    const stB = b.option_strike ?? 0;
    if (stA !== stB) return stA - stB;
    const pcA = a.put_call || '';
    const pcB = b.put_call || '';
    return pcA < pcB ? -1 : pcA > pcB ? 1 : 0;
  }));
  const equitySymbols = new Set(nonOptions.map(p => p.symbol));
  const orphanUnderlyings = [...new Set(Object.keys(optMap).filter(u => !equitySymbols.has(u)))];

  let bucket = nonOptions.filter(p => (S._posAssignments[p.symbol] || S.POSITION_LIST_OTHER_ID) === listId);
  if (S._posSortCol) bucket = bucket.slice().sort(_posSortCompare);

  let orph = orphanUnderlyings.filter(u => (S._posAssignments[u] || S.POSITION_LIST_OTHER_ID) === listId);
  if (S._posSortCol) {
    orph = orph.slice().sort((a, b) => _posSortCompare({ symbol: a }, { symbol: b }));
  }

  const equityBlocks = bucket.map(p => ({ kind: 'equity', p, opts: optMap[p.symbol] || [] }));
  const orphanBlocks = orph.map(u => ({ kind: 'orphan', underlying: u, opts: optMap[u] || [] }));

  if (S._posSortCol) {
    return [...equityBlocks, ...orphanBlocks];
  }

  const symKey = b => (b.kind === 'equity' ? b.p.symbol : b.underlying);
  return [...equityBlocks, ...orphanBlocks].sort((a, b) => {
    const va = S._posVolume365[symKey(a)] ?? 0;
    const vb = S._posVolume365[symKey(b)] ?? 0;
    if (vb !== va) return vb - va;
    return symKey(a).localeCompare(symKey(b));
  });
}

export function _posRecentMetricCells(underlying, equityRow) {
  const met = S._posRecentMetrics[underlying];
  const na = '<span class="pos-metric-na" title="Need 10+ equity fills in last 365d (equity only)">N/A</span>';
  if (!met) {
    return { recent: na, net: '—', vs: '—' };
  }
  const fillAvg = met.avg_price;
  const recentCell =
    fillAvg != null && !Number.isNaN(fillAvg) ? `$${fmt(fillAvg, 2)}` : '—';

  const netShares = met.net_shares;
  const netCell = netShares != null
    ? `<span class="${netShares >= 0 ? 'pos' : 'neg'}">${netShares >= 0 ? '+' : ''}${netShares}</span>`
    : '—';

  const cur = equityRow && equityRow.asset_type !== 'OPTION' ? equityRow.current_price : null;
  if (cur == null || fillAvg == null || Number.isNaN(fillAvg)) {
    return { recent: recentCell, net: netCell, vs: na };
  }
  const isShort = equityRow.quantity < 0;
  const m = Math.abs(cur);
  const diff = m - fillAvg;
  const pct = fillAvg ? (diff / fillAvg) * 100 : 0;
  const favorable = isShort ? diff < 0 : diff > 0;
  const uClass = favorable ? 'pos' : 'neg';
  const vsStr = `<span class="${uClass}">${fmtD(diff, 2)} (${fmtD(pct, 2)}%)</span>`;
  return { recent: recentCell, net: netCell, vs: vsStr };
}

export function _renderPositions() {
  bindPositionsDnD();

  const fmtExpiry = iso => {
    if (!iso) return '—';
    const [y, m, d] = iso.split('-');
    const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][parseInt(m, 10) - 1];
    return `${mon} ${parseInt(d, 10)} '${y.slice(2)}`;
  };

  const _isExpiringSoon = iso => {
    if (!iso) return false;
    const diff = (new Date(iso) - new Date()) / 86400000;
    return diff >= 0 && diff <= 7;
  };

  const _earnForSym = sym => {
    const d = S._posEarnings[String(sym || '').toUpperCase()];
    return earningsTagHtml(d, { symbol: sym });
  };

  const dataRow = (p, isChild, underlyingKey, listId) => {
    const isOpt = p.asset_type === 'OPTION';
    const pd = isOpt ? 4 : 2;
    const pcBadge = p.put_call
      ? `<span class="badge badge-${p.put_call}" style="font-size:10px;padding:1px 6px">${p.put_call}</span>`
      : '—';
    let symbolCell;
    let expiryCell;
    if (isChild) {
      const strikeLabel = p.option_strike != null ? `$${Number(p.option_strike).toFixed(2)}` : esc(p.symbol);
      symbolCell = `<span class="pos-opt-symbol" title="${esc(p.symbol)}">${strikeLabel}</span>`;
      expiryCell = `<span class="pos-opt-expiry${_isExpiringSoon(p.option_expiry) ? ' pos-expiry-soon' : ''}">${fmtExpiry(p.option_expiry)}</span>`;
    } else {
      symbolCell = `<b class="sym-link" onclick="openTrade('${esc(p.symbol)}')">${esc(p.symbol)}</b>`;
      expiryCell = '';
    }
    const earnSym = isChild ? underlyingKey : p.symbol;
    const earnCell = _earnForSym(earnSym);
    const uK = underlyingKey || p.symbol;
    let recentC, netC, vsC;
    if (isChild) {
      recentC = '—';
      netC = '—';
      vsC = '—';
    } else {
      const cells = _posRecentMetricCells(uK, p);
      recentC = cells.recent;
      netC = cells.net;
      vsC = cells.vs;
    }
    const firstTd = isChild
      ? '<td></td>'
      : `<td><span class="pos-drag-handle" draggable="true" data-underlying="${esc(uK)}" title="Drag to move to another list">☰</span></td>`;
    const trOpen = isChild
      ? '<tr class="pos-opt-row">'
      : `<tr class="pos-equity-row pos-symbol-reorder-target" data-list-id="${listId}" data-pos-block="${esc(p.symbol)}">`;
    return `${trOpen}
      ${firstTd}
      <td>${symbolCell}</td>
      <td><span class="badge badge-${p.asset_type}">${p.asset_type}</span></td>
      <td>${pcBadge}</td>
      <td>${expiryCell}</td>
      <td class="pos-earnings-cell">${earnCell}</td>
      <td>${fmt(p.quantity, 0)}</td>
      <td>${p.avg_price != null ? '$' + fmt(p.avg_price, pd) : '—'}</td>
      <td>${p.current_price != null ? '$' + fmt(p.current_price, pd) : '—'}</td>
      <td class="pos-num">${recentC}</td>
      <td class="pos-num">${netC}</td>
      <td class="pos-num">${vsC}</td>
      <td>${p.market_value != null ? '$' + fmt(p.market_value) : '—'}</td>
      <td class="${cls(p.unrealized_pl)}">${p.unrealized_pl != null ? '$' + fmtD(p.unrealized_pl) : '—'}</td>
      <td class="${cls(p.day_pl)}">${p.day_pl != null ? '$' + fmtD(p.day_pl) : '—'}</td>
      <td class="${cls(p.day_pl_pct)}">${p.day_pl_pct != null ? fmtD(p.day_pl_pct) + '%' : '—'}</td>
    </tr>`;
  };

  const toggleRow = (underlying, opts, equityParent, isOrphan, listId) => {
    const expanded = S._posExpanded.has(underlying);
    const arrow = expanded ? '▼' : '▶';
    const optCount = opts.length;
    const totalMv = opts.reduce((s, o) => s + (o.market_value || 0), 0);
    const totalUpl = opts.every(o => o.unrealized_pl != null)
      ? opts.reduce((s, o) => s + (o.unrealized_pl || 0), 0) : null;
    const mvStr = '$' + fmt(totalMv);
    const uplStr = totalUpl != null
      ? `<span class="${cls(totalUpl)}">$${fmtD(totalUpl)}</span>` : '—';
    const eqForMet = equityParent || null;
    const { recent: rc, net: nc, vs: vc } = _posRecentMetricCells(underlying, eqForMet);
    const trClass = 'pos-opt-toggle' + (isOrphan ? ' pos-symbol-reorder-target' : '');
    const firstToggleTd = isOrphan
      ? `<td class="pos-toggle-arrow"><span class="pos-drag-handle" draggable="true" data-underlying="${esc(underlying)}" title="Drag to move to another list">☰</span><span class="pos-opt-expand-btn">${arrow}</span></td>`
      : `<td class="pos-toggle-arrow"><span class="pos-opt-expand-btn">${arrow}</span></td>`;
    return `<tr class="${trClass}" data-expand-underlying="${esc(underlying)}" data-list-id="${listId}" data-pos-block="${esc(underlying)}">
      ${firstToggleTd}
      <td colspan="4" class="pos-toggle-label">
        <span class="pos-toggle-ticker sym-link" onclick="event.stopPropagation(); openTrade('${esc(underlying)}')">${esc(underlying)}</span>
        <span class="pos-toggle-meta">${optCount} option position${optCount !== 1 ? 's' : ''}</span>
      </td>
      <td class="pos-earnings-cell">${_earnForSym(underlying)}</td>
      <td></td><td></td><td></td>
      <td class="pos-num">${rc}</td>
      <td class="pos-num">${nc}</td>
      <td class="pos-num">${vc}</td>
      <td>${mvStr}</td>
      <td>${uplStr}</td>
      <td></td><td></td>
    </tr>`;
  };

  const thead = `<thead><tr>
    <th style="width:28px"></th>
    <th class="sortable" onclick="window.sortPositions('symbol')">Symbol / Strike <span class="sort-arrow" data-pa="symbol"></span></th>
    <th>Type</th>
    <th>P/C</th>
    <th>Expiry</th>
    <th class="sortable" title="Sort by trading days until earnings" onclick="window.sortPositions('earnings_td')">Earnings <span class="sort-arrow" data-pa="earnings_td"></span></th>
    <th class="sortable" onclick="window.sortPositions('quantity')">Qty <span class="sort-arrow" data-pa="quantity"></span></th>
    <th class="sortable" onclick="window.sortPositions('avg_price')">Avg Price <span class="sort-arrow" data-pa="avg_price"></span></th>
    <th class="sortable" onclick="window.sortPositions('current_price')">Mkt Price <span class="sort-arrow" data-pa="current_price"></span></th>
    <th class="sortable" onclick="window.sortPositions('recent_avg')">10-fill avg <span class="sort-arrow" data-pa="recent_avg"></span></th>
    <th class="sortable" onclick="window.sortPositions('recent_net')">10F Net <span class="sort-arrow" data-pa="recent_net"></span></th>
    <th class="sortable" onclick="window.sortPositions('vs_recent_pct')">vs recent <span class="sort-arrow" data-pa="vs_recent_pct"></span></th>
    <th class="sortable" onclick="window.sortPositions('market_value')">Mkt Value <span class="sort-arrow" data-pa="market_value"></span></th>
    <th class="sortable" onclick="window.sortPositions('unrealized_pl')">Unrealized P&amp;L <span class="sort-arrow" data-pa="unrealized_pl"></span></th>
    <th class="sortable" onclick="window.sortPositions('day_pl')">Day P&amp;L <span class="sort-arrow" data-pa="day_pl"></span></th>
    <th class="sortable" onclick="window.sortPositions('day_pl_pct')">Day % <span class="sort-arrow" data-pa="day_pl_pct"></span></th>
  </tr></thead>`;

  const listsSorted = (S._posLists || []).slice().sort((a, b) => {
    if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
    return a.id - b.id;
  });

  const nLists = listsSorted.length;
  const sections = listsSorted.map((list, idx) => {
    const blocks = _posBlocksForList(list.id);
    const inner = [];
    blocks.forEach(block => {
      if (block.kind === 'equity') {
        inner.push(dataRow(block.p, false, block.p.symbol, list.id));
        const opts = block.opts;
        if (opts.length) {
          inner.push(toggleRow(block.p.symbol, opts, block.p, false, list.id));
          if (S._posExpanded.has(block.p.symbol)) {
            opts.forEach(o => inner.push(dataRow(o, true, block.p.symbol, list.id)));
          }
        }
      } else {
        inner.push(toggleRow(block.underlying, block.opts, null, true, list.id));
        if (S._posExpanded.has(block.underlying)) {
          block.opts.forEach(o => inner.push(dataRow(o, true, block.underlying, list.id)));
        }
      }
    });
    const count = blocks.reduce((n, b) => n + 1 + (b.opts && b.opts.length ? 1 : 0), 0);
    const deleteBtn = list.is_system ? '' :
      `<button type="button" class="pos-list-delete-btn" title="Delete list"
        onclick="event.preventDefault();event.stopPropagation();window.deletePositionList(${list.id})">✕</button>`;
    const renameBtn = `<button type="button" class="pos-list-rename-btn" title="Rename"
      onclick="event.preventDefault();event.stopPropagation();window.beginPositionListRename(${list.id})">✎</button>`;
    const moveBtns = `<span class="pos-list-move-btns">
      <button type="button" class="pos-list-move-btn" title="Move list up" ${idx === 0 ? 'disabled' : ''}
        onclick="event.preventDefault();event.stopPropagation();window.movePositionList(${list.id},-1)">↑</button>
      <button type="button" class="pos-list-move-btn" title="Move list down" ${idx >= nLists - 1 ? 'disabled' : ''}
        onclick="event.preventDefault();event.stopPropagation();window.movePositionList(${list.id},1)">↓</button>
    </span>`;
    return `<details class="pos-list-section" open data-list-id="${list.id}">
      <summary class="pos-list-summary pos-list-dropzone" data-list-id="${list.id}">
        <span class="pos-list-summary-title" id="pos-list-title-${list.id}">${esc(list.name)}</span>
        ${renameBtn}
        ${moveBtns}
        ${deleteBtn}
        <span class="pos-list-count">${blocks.length} underlying</span>
      </summary>
      <div class="table-wrap pos-list-table-wrap pos-list-dropzone" data-list-id="${list.id}">
        <table class="pos-tbl">${thead}<tbody>${inner.join('') || '<tr><td colspan="16" class="pos-list-empty">Drop tickers here</td></tr>'}</tbody></table>
      </div>
    </details>`;
  });

  const el = document.getElementById('pos-sections');
  if (el) el.innerHTML = sections.join('');

  const cols = ['symbol', 'quantity', 'avg_price', 'current_price', 'recent_avg', 'recent_net',
    'vs_recent_pct', 'market_value', 'unrealized_pl', 'day_pl', 'day_pl_pct', 'earnings_td'];
  document.querySelectorAll('#pos-sections .sort-arrow[data-pa]').forEach(span => {
    const col = span.getAttribute('data-pa');
    span.textContent = col === S._posSortCol ? (S._posSortDir > 0 ? ' ▲' : ' ▼') : '';
  });
}

export function sortPositions(col) {
  if (S._posSortCol === col) S._posSortDir *= -1;
  else {
    S._posSortCol = col;
    S._posSortDir = 1;
  }
  _renderPositions();
}

/** Read a CSS custom property from :root at call time (theme-aware). */
function _cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

const _PIE_TOP_N_LONG = 8;
const _PIE_TOP_N_SHORT = 5;
const _PIE_OTHERS_MINI_LEGEND_TOP = 4;
const _PIE_MIN_SHORT_ABS = 500;
const _PIE_MIN_SHORT_PCT = 0.015;
const _PIE_COLORS = [
  '#6366f1','#22d3ee','#f59e0b','#10b981','#ef4444',
  '#a78bfa','#f472b6','#06b6d4','#84cc16','#fb923c',
  '#e879f9','#2dd4bf','#fbbf24','#f87171','#34d399',
];

export function _destroyPosOthersMiniChart() {
  if (S._posOthersMiniChart) {
    S._posOthersMiniChart.destroy();
    S._posOthersMiniChart = null;
  }
}

export function _clearPosOthersHideTimer() {
  if (S._posOthersHideTimer) {
    clearTimeout(S._posOthersHideTimer);
    S._posOthersHideTimer = null;
  }
}

export function _initPosOthersPopoverHandlers() {
  if (S._posOthersPopoverListeners) return;
  S._posOthersPopoverListeners = true;
  const host = document.getElementById('pos-pie-external-tooltip');
  if (!host) return;
  host.addEventListener('mouseenter', () => {
    host.dataset.popHover = '1';
    _clearPosOthersHideTimer();
  });
  host.addEventListener('mouseleave', () => {
    host.dataset.popHover = '0';
    host.style.opacity = '0';
    host.classList.remove('pos-pie-external-tooltip--interactive', 'pos-pie-external-tooltip--wide');
    host.setAttribute('aria-hidden', 'true');
    _destroyPosOthersMiniChart();
    const mw = document.getElementById('pos-pie-mini-wrap');
    if (mw) mw.style.display = 'none';
  });
}

export function _fmtPieUsd(v) {
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

export function _bucketPieLong(items, topN) {
  if (items.length <= topN) return { rows: items.slice(), othersDetail: [] };
  const top = items.slice(0, topN);
  const rest = items.slice(topN);
  const restSum = rest.reduce((s, i) => s + i.val, 0);
  if (restSum > 0) top.push({ sym: 'Others', val: restSum });
  return { rows: top, othersDetail: rest };
}

export function _bucketPieShort(items, topN, shortBookTotal) {
  if (!items.length) return { rows: [], othersDetail: [] };
  const thresh = Math.max(shortBookTotal * _PIE_MIN_SHORT_PCT, _PIE_MIN_SHORT_ABS);
  const big = [];
  const small = [];
  items.forEach(i => (i.val >= thresh ? big : small).push(i));
  big.sort((a, b) => b.val - a.val);
  const cap = Math.min(topN, Math.max(1, big.length));
  const topBig = big.slice(0, cap);
  const tailBig = big.slice(cap);
  const mergedTail = [...tailBig, ...small].sort((a, b) => b.val - a.val);
  const tailSum = mergedTail.reduce((s, i) => s + i.val, 0);
  const rows = topBig.slice();
  if (tailSum > 0) rows.push({ sym: 'Others', val: tailSum });
  return { rows, othersDetail: mergedTail };
}

export function _makePieConfig(rows, meta) {
  const { bookTotal, othersDetail, sideLabel } = meta;
  const labels = rows.map(d => d.sym);
  const values = rows.map(d => d.val);
  const chartTotal = values.reduce((s, x) => s + Number(x), 0);
  const legendColor = _cssVar('--color-text', '#e2e8f0');
  const border = _cssVar('--color-surface', '#1a1d2e');
  const book = bookTotal || chartTotal;
  const sideWord = sideLabel === 'short' ? 'short' : 'long';

  const externalTooltip = context => {
    const host = document.getElementById('pos-pie-external-tooltip');
    const inner = document.getElementById('pos-pie-external-tooltip-inner');
    const miniWrap = document.getElementById('pos-pie-mini-wrap');
    if (!host || !inner || !miniWrap) return;

    const { chart, tooltip } = context;
    if (tooltip.opacity === 0) {
      _clearPosOthersHideTimer();
      S._posOthersHideTimer = setTimeout(() => {
        S._posOthersHideTimer = null;
        if (host.dataset.popHover === '1') return;
        host.style.opacity = '0';
        host.classList.remove('pos-pie-external-tooltip--interactive', 'pos-pie-external-tooltip--wide');
        host.setAttribute('aria-hidden', 'true');
        _destroyPosOthersMiniChart();
        miniWrap.style.display = 'none';
      }, 120);
      return;
    }

    _clearPosOthersHideTimer();

    const tps = tooltip.dataPoints;
    if (!tps || !tps.length) return;
    const idx = tps[0].dataIndex;
    const lab = chart.data.labels[idx];
    const v = Number(chart.data.datasets[0].data[idx]);
    const tot = chart.data.datasets[0].data.reduce((s, x) => s + Number(x), 0);
    const pctChart = tot ? ((v / tot) * 100).toFixed(1) : '0.0';

    const rect = chart.canvas.getBoundingClientRect();
    let left = rect.left + tooltip.x;
    let top = rect.top + tooltip.y;
    const pad = 12;
    const w = host.offsetWidth || 260;
    const h = host.offsetHeight || 120;
    left = Math.max(pad, Math.min(left, window.innerWidth - w - pad));
    top = Math.max(pad, Math.min(top, window.innerHeight - h - pad));
    host.style.left = `${left}px`;
    host.style.top = `${top}px`;
    host.style.opacity = '1';
    host.setAttribute('aria-hidden', 'false');

    const isOthers = lab === 'Others' && othersDetail.length > 0;
    if (isOthers) {
      host.classList.add('pos-pie-external-tooltip--wide', 'pos-pie-external-tooltip--interactive');
      const n = othersDetail.length;
      const sumOthers = othersDetail.reduce((s, d) => s + d.val, 0);
      const pctBookAll = book ? ((sumOthers / book) * 100).toFixed(1) : '0.0';
      inner.innerHTML =
        `<div class="pie-ext-title">${esc(lab)}</div>` +
        `<div class="pie-ext-sub">${n} names · $${_fmtPieUsd(sumOthers)} total · ${pctChart}% of this chart · ${pctBookAll}% of ${sideWord} book</div>`;
      miniWrap.style.display = 'block';
      _destroyPosOthersMiniChart();
      const miniCanvas = document.getElementById('pos-pie-mini-canvas');
      if (miniCanvas && typeof Chart !== 'undefined') {
        const totO = othersDetail.reduce((s, d) => s + d.val, 0);
        S._posOthersMiniChart = new Chart(miniCanvas.getContext('2d'), {
          type: 'doughnut',
          data: {
            labels: othersDetail.map(d => d.sym),
            datasets: [{
              data: othersDetail.map(d => d.val),
              backgroundColor: othersDetail.map((_, i) => _PIE_COLORS[i % _PIE_COLORS.length]),
              borderColor: border,
              borderWidth: 1,
              hoverOffset: 8,
            }],
          },
          options: {
            animation: false,
            responsive: true,
            maintainAspectRatio: true,
            cutout: '40%',
            interaction: { mode: 'nearest', intersect: true },
            plugins: {
              legend: {
                display: true,
                position: 'bottom',
                labels: {
                  color: legendColor,
                  font: { size: 9, weight: '500' },
                  padding: 5,
                  boxWidth: 10,
                  usePointStyle: true,
                  pointStyleWidth: 6,
                  generateLabels: c => {
                    const ds = c.data.datasets[0];
                    const t = ds.data.reduce((s, x) => s + Number(x), 0);
                    const m0 = c.getDatasetMeta(0);
                    const nShow = Math.min(_PIE_OTHERS_MINI_LEGEND_TOP, c.data.labels.length);
                    const out = [];
                    for (let i = 0; i < nShow; i++) {
                      const lb = c.data.labels[i];
                      const val = Number(ds.data[i]);
                      const pct = t ? ((val / t) * 100).toFixed(1) : '0.0';
                      const hidden = m0.data[i] ? m0.data[i].hidden === true : false;
                      out.push({
                        text: `${lb}  $${_fmtPieUsd(val)} (${pct}%)`,
                        fillStyle: ds.backgroundColor[i],
                        strokeStyle: border,
                        lineWidth: 1,
                        hidden,
                        index: i,
                        fontColor: legendColor,
                        color: legendColor,
                      });
                    }
                    return out;
                  },
                },
              },
              tooltip: {
                enabled: true,
                callbacks: {
                  title: items => (items.length ? items[0].label : ''),
                  label: c => {
                    const val = Number(c.dataset.data[c.dataIndex]);
                    const pO = totO ? ((val / totO) * 100).toFixed(1) : '0.0';
                    const pB = book ? ((val / book) * 100).toFixed(1) : '0.0';
                    return ` $${_fmtPieUsd(val)} · ${pO}% of Others · ${pB}% of ${sideWord}`;
                  },
                },
              },
            },
          },
        });
      }
    } else {
      host.classList.remove('pos-pie-external-tooltip--wide', 'pos-pie-external-tooltip--interactive');
      _destroyPosOthersMiniChart();
      miniWrap.style.display = 'none';
      inner.innerHTML =
        `<div class="pie-ext-title">${esc(lab)}</div>` +
        `<div>$${_fmtPieUsd(v)} (${pctChart}% of this chart)</div>`;
    }
  };

  return {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: labels.map((_, i) => _PIE_COLORS[i % _PIE_COLORS.length]),
        borderColor: border,
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      cutout: '45%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: legendColor,
            font: { size: 11, weight: '500' },
            padding: 10,
            usePointStyle: true,
            pointStyleWidth: 8,
            generateLabels: chart => {
              const ds = chart.data.datasets[0];
              const tot = ds.data.reduce((s, x) => s + Number(x), 0);
              const meta0 = chart.getDatasetMeta(0);
              return chart.data.labels.map((lab, i) => {
                const val = Number(ds.data[i]);
                const pct = tot ? ((val / tot) * 100).toFixed(1) : '0.0';
                const hidden = meta0.data[i] ? meta0.data[i].hidden === true : false;
                return {
                  text: `${lab}  $${_fmtPieUsd(val)} (${pct}%)`,
                  fillStyle: Array.isArray(ds.backgroundColor) ? ds.backgroundColor[i] : ds.backgroundColor,
                  strokeStyle: border,
                  lineWidth: 2,
                  hidden,
                  index: i,
                  fontColor: legendColor,
                  color: legendColor,
                };
              });
            },
          },
        },
        tooltip: {
          enabled: true,
          external: externalTooltip,
        },
      },
    },
  };
}

export function _renderPositionCharts() {
  _initPosOthersPopoverHandlers();
  const wrap = document.getElementById('pos-chart-wrap');
  if (!wrap || typeof Chart === 'undefined') return;
  if (!S._posData || !S._posData.length) { wrap.style.display = 'none'; return; }

  const grouped = {};
  S._posData.forEach(p => {
    const key = p.asset_type === 'OPTION'
      ? _posUnderlyingFromOption(p)
      : p.symbol;
    if (!grouped[key]) grouped[key] = 0;
    grouped[key] += (p.market_value || 0);
  });

  const longItems = [], shortItems = [];
  for (const [sym, mv] of Object.entries(grouped)) {
    if (mv > 0) longItems.push({ sym, val: mv });
    else if (mv < 0) shortItems.push({ sym, val: Math.abs(mv) });
  }
  longItems.sort((a, b) => b.val - a.val);
  shortItems.sort((a, b) => b.val - a.val);

  const totalLong = longItems.reduce((s, i) => s + i.val, 0);
  const totalShort = shortItems.reduce((s, i) => s + i.val, 0);

  const longB = _bucketPieLong(longItems, _PIE_TOP_N_LONG);
  const shortB = _bucketPieShort(shortItems, _PIE_TOP_N_SHORT, totalShort);

  const sumText = document.getElementById('pos-chart-summary-text');
  const barLong = document.getElementById('pos-chart-bar-long');
  const barShort = document.getElementById('pos-chart-bar-short');
  const sumBar = document.getElementById('pos-chart-summary-bar');
  if (sumText && barLong && barShort && sumBar) {
    const ratio = totalLong > 0 ? ((totalShort / totalLong) * 100).toFixed(1) : (totalShort > 0 ? '—' : '0.0');
    sumText.textContent =
      `Long $${_fmtPieUsd(totalLong)} · Short $${_fmtPieUsd(totalShort)} · Short / Long ${ratio}%`;
    const comb = totalLong + totalShort;
    if (comb > 0) {
      const lw = (totalLong / comb) * 100;
      barLong.style.width = `${lw}%`;
      barShort.style.width = `${100 - lw}%`;
      sumBar.style.display = 'flex';
    } else {
      barLong.style.width = '50%';
      barShort.style.width = '50%';
      sumBar.style.display = 'none';
    }
  }

  _clearPosOthersHideTimer();
  _destroyPosOthersMiniChart();
  const extTip = document.getElementById('pos-pie-external-tooltip');
  if (extTip) {
    extTip.style.opacity = '0';
    extTip.classList.remove('pos-pie-external-tooltip--interactive', 'pos-pie-external-tooltip--wide');
    extTip.setAttribute('aria-hidden', 'true');
  }
  const extMini = document.getElementById('pos-pie-mini-wrap');
  if (extMini) extMini.style.display = 'none';
  if (S._posChartLong) S._posChartLong.destroy();
  if (S._posChartShort) S._posChartShort.destroy();

  const longCanvas = document.getElementById('pos-chart-long');
  const shortCanvas = document.getElementById('pos-chart-short');

  if (longB.rows.length) {
    S._posChartLong = new Chart(longCanvas.getContext('2d'), _makePieConfig(longB.rows, {
      bookTotal: totalLong,
      othersDetail: longB.othersDetail,
      sideLabel: 'long',
    }));
    longCanvas.closest('.pos-chart-panel').style.display = '';
  } else {
    longCanvas.closest('.pos-chart-panel').style.display = 'none';
  }

  if (shortB.rows.length) {
    S._posChartShort = new Chart(shortCanvas.getContext('2d'), _makePieConfig(shortB.rows, {
      bookTotal: totalShort,
      othersDetail: shortB.othersDetail,
      sideLabel: 'short',
    }));
    shortCanvas.closest('.pos-chart-panel').style.display = '';
  } else {
    shortCanvas.closest('.pos-chart-panel').style.display = 'none';
  }

  wrap.style.display = (longB.rows.length || shortB.rows.length) ? 'block' : 'none';
}

export async function loadPositions() {
  try {
    const [data, listsRes] = await Promise.all([
      fetchJson('/api/positions'),
      fetchJson('/api/position-lists'),
    ]);
    S._posData = data;
    S._posLists = listsRes.lists || [];
    const { underlyings, short_equity } = _collectPositionTabKeys(data);
    const sync = await fetchJson('/api/position-assignments/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ underlyings, short_equity }),
    });
    S._posAssignments = sync.assignments || {};
    S._posVolume365 = sync.volume_365d || {};
    const metrics = await fetchJson('/api/position-recent-metrics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols: underlyings }),
    });
    S._posRecentMetrics = metrics.metrics || {};
    S._posEarnings = await fetchEarningsMap(underlyings);
    document.getElementById('pos-loading').style.display = 'none';
    document.getElementById('pos-error').style.display = 'none';
    const wrap = document.getElementById('pos-table-wrap');
    const toolbar = document.getElementById('pos-toolbar');
    if (wrap) wrap.style.display = 'block';
    if (toolbar) toolbar.style.display = 'flex';
    _renderPositions();
    _renderPositionCharts();
  } catch (e) {
    document.getElementById('pos-loading').style.display = 'none';
    document.getElementById('pos-error').style.display = 'block';
    document.getElementById('pos-error').textContent = 'Error: ' + e.message;
  }
}

export function showNewPositionListForm() {
  document.getElementById('pos-new-form').style.display = 'flex';
  document.getElementById('pos-new-list-btn').style.display = 'none';
  document.getElementById('pos-new-name-input').focus();
}

export function hideNewPositionListForm() {
  document.getElementById('pos-new-form').style.display = 'none';
  document.getElementById('pos-new-list-btn').style.display = '';
  document.getElementById('pos-new-name-input').value = '';
}

export async function createPositionList() {
  const name = document.getElementById('pos-new-name-input').value.trim();
  if (!name) return;
  try {
    await fetchJson('/api/position-lists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const listsRes = await fetchJson('/api/position-lists');
    S._posLists = listsRes.lists || [];
    hideNewPositionListForm();
    _renderPositions();
  } catch (e) {
    alert(e.message || String(e));
  }
}

export async function beginPositionListRename(listId) {
  const span = document.getElementById('pos-list-title-' + listId);
  if (!span) return;
  const cur = span.textContent;
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'pos-list-rename-input';
  input.value = cur;
  span.replaceWith(input);
  input.focus();
  input.select();

  let finished = false;
  const finish = async commit => {
    if (finished) return;
    finished = true;
    if (!input.parentNode) return;
    const newSpan = document.createElement('span');
    newSpan.className = 'pos-list-summary-title';
    newSpan.id = 'pos-list-title-' + listId;
    if (commit) {
      const v = input.value.trim();
      if (v && v !== cur) {
        try {
          await fetchJson('/api/position-lists/' + listId, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: v }),
          });
          const listsRes = await fetchJson('/api/position-lists');
          S._posLists = listsRes.lists || [];
        } catch (e) {
          alert(e.message || String(e));
        }
      }
    }
    const L = S._posLists.find(l => l.id === listId);
    newSpan.textContent = L ? L.name : cur;
    input.replaceWith(newSpan);
  };

  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
    if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

export async function deletePositionList(listId) {
  const otherName = (S._posLists.find(l => l.id === S.POSITION_LIST_OTHER_ID) || {}).name || 'Other';
  if (!confirm(`Delete this list? Tickers on it will move to “${otherName}”.`)) return;
  try {
    await fetchJson('/api/position-lists/' + listId, { method: 'DELETE' });
    Object.keys(S._posAssignments).forEach(sym => {
      if (S._posAssignments[sym] === listId) {
        S._posAssignments[sym] = S.POSITION_LIST_OTHER_ID;
      }
    });
    const listsRes = await fetchJson('/api/position-lists');
    S._posLists = listsRes.lists || [];
    _renderPositions();
  } catch (e) {
    alert(e.message || String(e));
  }
}
