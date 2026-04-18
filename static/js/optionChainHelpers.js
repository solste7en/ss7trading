/**
 * Shared helpers for option chain UIs (Trade tab + Strategy tab).
 */

/** Look up a single contract in Schwab-style chain JSON. */
export function findChainContract(chainData, type, strike, expiry) {
  if (!chainData || !expiry) return null;
  const map = type === 'CALL' ? chainData.calls : chainData.puts;
  const contracts = map ? (map[expiry] || []) : [];
  const s = typeof strike === 'number' ? strike : parseFloat(String(strike));
  if (!Number.isFinite(s)) return null;
  return contracts.find(c => c.strike === s) || null;
}

/**
 * Build innerHTML for a strike &lt;select&gt; from call/put maps.
 * @param {'CALL'|'PUT'} type
 * @param {Record<number, object>} callMap strike → contract
 * @param {Record<number, object>} putMap strike → contract
 * @param {number[]} baseStrikes strikes to list (e.g. visible page or full chain)
 * @param {string} currentValue current &lt;select&gt; value before rebuild
 * @param {number[]|null} chainPageStrikes if set, strikes not in this array get an "off-page" hint (strategy pager)
 */
export function makeStrikeSelectOptions(type, callMap, putMap, baseStrikes, currentValue, chainPageStrikes = null) {
  const map = type === 'CALL' ? callMap : putMap;
  let strikes = baseStrikes.slice();
  const curFloat = parseFloat(String(currentValue));
  if (curFloat && !strikes.includes(curFloat)) strikes = [...strikes, curFloat].sort((a, b) => a - b);

  let html = '<option value="">— select strike —</option>';
  strikes.forEach((k) => {
    const c = map[k] || {};
    const sel = curFloat === k ? ' selected' : '';
    const mid = (c.bid != null && c.ask != null) ? ' · mid ' + ((c.bid + c.ask) / 2).toFixed(2) : '';
    const off = chainPageStrikes && !chainPageStrikes.includes(k) ? ' ◀ off-page' : '';
    html += `<option value="${k}"${sel}>$${k.toFixed(2)} (bid ${(c.bid || 0).toFixed(2)} / ask ${(c.ask || 0).toFixed(2)}${mid}${off})</option>`;
  });
  return html;
}
