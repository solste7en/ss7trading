/**
 * Shared earnings / catalyst tags: fetch map from /api/earnings, format pills,
 * and detect “within next N trading days” (Mon–Fri only; not exchange holidays).
 */
import { esc } from './utils.js';

export function parseIsoDateUtc(iso) {
  if (!iso || typeof iso !== 'string') return null;
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
}

export function todayUtc() {
  const n = new Date();
  return new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate()));
}

/** Calendar date that is *n* trading days after *startUtc* (weekdays only). */
export function addTradingDaysFrom(startUtc, n) {
  const d = new Date(startUtc);
  let c = 0;
  while (c < n) {
    d.setUTCDate(d.getUTCDate() + 1);
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) c++;
  }
  return d;
}

/** True if earnings falls on or after today and on or before *n* trading days from today. */
export function isEarningsWithinTradingDays(earningsIso, n = 10) {
  const e = parseIsoDateUtc(earningsIso);
  if (!e) return false;
  const t = todayUtc();
  if (e < t) return false;
  const end = addTradingDaysFrom(t, n);
  return e <= end;
}

/** Weekdays from today (inclusive of earnings day) until first session on/after earnings date. */
export function tradingDaysUntil(earningsIso) {
  const target = parseIsoDateUtc(earningsIso);
  const t = todayUtc();
  if (!target || target < t) return null;
  let d = new Date(t);
  let count = 0;
  while (d < target) {
    d.setUTCDate(d.getUTCDate() + 1);
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) count++;
  }
  return count;
}

/**
 * Numeric key for table sort by “how soon is earnings” (fewer trading days = smaller).
 * No upcoming date → +Infinity so those rows sort after real dates when ascending.
 */
export function earningsTradingDaysSortKey(dateIso) {
  if (!dateIso) return Infinity;
  const td = tradingDaysUntil(dateIso);
  if (td == null) return Infinity;
  return td;
}

const CHUNK = 50;

export async function fetchEarningsMap(symbols) {
  const uniq = [...new Set(symbols.map(s => String(s || '').toUpperCase()).filter(Boolean))];
  const out = {};
  for (let i = 0; i < uniq.length; i += CHUNK) {
    const part = uniq.slice(i, i + CHUNK);
    const params = new URLSearchParams({ symbols: part.join(',') });
    try {
      const res = await fetch('/api/earnings?' + params).then(r => r.json());
      if (res && res.error) continue;
      if (res && typeof res === 'object') Object.assign(out, res);
    } catch (_) {
      /* ignore */
    }
  }
  return out;
}

/**
 * @param {string|null|undefined} dateIso YYYY-MM-DD from API
 * @param {{ compact?: boolean }} opts
 */
export function earningsTagHtml(dateIso, opts = {}) {
  if (!dateIso) {
    return '<span class="earn-tag earn-tag-na" title="No upcoming earnings from data source">—</span>';
  }
  const soon = isEarningsWithinTradingDays(dateIso, 10);
  const td = tradingDaysUntil(dateIso);
  const cls = soon ? 'earn-tag earn-tag-soon' : 'earn-tag';
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const [y, mo, d] = dateIso.split('-');
  const label = `${mon[parseInt(mo, 10) - 1]} ${parseInt(d, 10)}`;
  const sub = td != null ? ` · ${td}td` : '';
  const title = `Next earnings: ${dateIso}${td != null ? ` (${td} trading day${td !== 1 ? 's' : ''})` : ''}`;
  return `<span class="${cls}" title="${esc(title)}">${esc(label)}${esc(sub)}</span>`;
}
