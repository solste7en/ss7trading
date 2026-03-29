"""
app.py — ss7trading dashboard
Run: python app.py
Visit: http://127.0.0.1:5050
"""
import json
import traceback
from flask import Flask, jsonify, render_template_string
import schwab
from auth import get_client

app = Flask(__name__)

@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

# ── helpers ────────────────────────────────────────────────────────────────────

def _clean_positions(accounts_data):
    """Parse the Schwab account response into a flat list of position dicts."""
    positions = []
    for acct in accounts_data:
        acct_info = acct.get("securitiesAccount", {})
        acct_number = acct_info.get("accountNumber", "")
        for pos in acct_info.get("positions", []):
            instrument  = pos.get("instrument", {})
            asset_type  = instrument.get("assetType", "")
            symbol      = instrument.get("symbol", "")
            description = instrument.get("description", symbol)
            qty         = pos.get("longQuantity", 0) - pos.get("shortQuantity", 0)
            avg_price   = pos.get("averagePrice")
            mkt_value   = pos.get("marketValue")
            cost_basis  = pos.get("longOpenProfitLoss") # unrealized P&L vs cost
            day_pl      = pos.get("currentDayProfitLoss")
            day_pl_pct  = pos.get("currentDayProfitLossPercentage")

            positions.append({
                "account":     acct_number[-4:],   # last 4 digits only
                "symbol":      symbol,
                "description": description,
                "asset_type":  asset_type,
                "quantity":    qty,
                "avg_price":   avg_price,
                "market_value": mkt_value,
                "unrealized_pl": cost_basis,
                "day_pl":      day_pl,
                "day_pl_pct":  day_pl_pct,
            })
    # Sort: equities first, then options, then cash
    order = {"EQUITY": 0, "ETF": 1, "OPTION": 2, "CASH_EQUIVALENT": 3}
    positions.sort(key=lambda p: (order.get(p["asset_type"], 9), p["symbol"]))
    return positions


def _clean_quotes(quotes_data):
    """Parse the Schwab quote response into a flat list."""
    result = []
    for symbol, data in quotes_data.items():
        q = data.get("quote", {})
        ref = data.get("reference", {})
        result.append({
            "symbol":       symbol,
            "description":  ref.get("description", symbol),
            "last":         q.get("lastPrice"),
            "bid":          q.get("bidPrice"),
            "ask":          q.get("askPrice"),
            "change":       q.get("netChange"),
            "change_pct":   q.get("netPercentChange"),
            "volume":       q.get("totalVolume"),
            "52w_high":     q.get("52WeekHigh"),
            "52w_low":      q.get("52WeekLow"),
        })
    result.sort(key=lambda x: x["symbol"])
    return result


# ── API routes ─────────────────────────────────────────────────────────────────

@app.route("/api/test")
def api_test():
    """Quick connectivity test — returns raw account numbers from Schwab."""
    try:
        client = get_client()
        resp = client.get_account_numbers()
        resp.raise_for_status()
        return jsonify({"status": "ok", "data": resp.json()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/positions")
def api_positions():
    try:
        client = get_client()
        resp = client.get_accounts(fields=[schwab.client.Client.Account.Fields.POSITIONS])
        resp.raise_for_status()
        return jsonify(_clean_positions(resp.json()))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/quotes")
def api_quotes():
    """Returns quotes for all symbols currently held."""
    try:
        client = get_client()
        resp = client.get_accounts(fields=[schwab.client.Client.Account.Fields.POSITIONS])
        resp.raise_for_status()
        positions = _clean_positions(resp.json())

        symbols = set()
        for p in positions:
            sym = p["symbol"]
            if p["asset_type"] == "OPTION":
                underlying = sym.split()[0] if " " in sym else sym
                symbols.add(underlying)
            elif p["asset_type"] not in ("CASH_EQUIVALENT",):
                symbols.add(sym)

        if not symbols:
            return jsonify([])

        resp = client.get_quotes(list(symbols))
        resp.raise_for_status()
        return jsonify(_clean_quotes(resp.json()))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/quote/<symbol>")
def api_quote_single(symbol):
    """Quote a single symbol."""
    try:
        client = get_client()
        resp = client.get_quotes([symbol.upper()])
        resp.raise_for_status()
        data = _clean_quotes(resp.json())
        return jsonify(data[0] if data else {})
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Dashboard UI ───────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ss7trading · Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f1117; color: #e2e8f0; font-size: 13px; }

.topbar { background: #1a1d2e; border-bottom: 1px solid #2d3148;
          padding: 14px 24px; display: flex; align-items: center; gap: 16px; }
.topbar h1 { font-size: 17px; font-weight: 600; }
.topbar .sub { color: #64748b; font-size: 12px; }
.refresh-btn { margin-left: auto; background: #312e81; color: #a5b4fc;
               border: 1px solid #4338ca; border-radius: 6px; padding: 6px 14px;
               cursor: pointer; font-size: 12px; }
.refresh-btn:hover { background: #3730a3; }

.tabs { display: flex; gap: 2px; padding: 12px 24px 0;
        border-bottom: 1px solid #1e2235; }
.tab { padding: 8px 18px; border-radius: 6px 6px 0 0; cursor: pointer;
       color: #64748b; font-size: 12px; font-weight: 500; }
.tab.active { background: #1a1d2e; color: #e2e8f0;
              border: 1px solid #2d3148; border-bottom: none; }

.panel { display: none; padding: 20px 24px; }
.panel.active { display: block; }

/* Quote search */
.quote-search { display: flex; gap: 8px; margin-bottom: 16px; }
.quote-search input { background: #1a1d2e; border: 1px solid #2d3148;
  border-radius: 6px; color: #e2e8f0; padding: 8px 12px; font-size: 13px;
  width: 200px; outline: none; text-transform: uppercase; }
.quote-search input:focus { border-color: #6366f1; }
.quote-search button { background: #312e81; color: #a5b4fc;
  border: 1px solid #4338ca; border-radius: 6px; padding: 8px 16px;
  cursor: pointer; font-size: 12px; }
.quote-card { background: #1a1d2e; border: 1px solid #2d3148; border-radius: 8px;
  padding: 16px 20px; display: inline-grid; min-width: 260px;
  grid-template-columns: 1fr 1fr; gap: 8px 24px; margin-bottom: 12px; }
.quote-card .sym { font-size: 22px; font-weight: 700; grid-column: 1/-1; }
.quote-card .last { font-size: 28px; font-weight: 700; grid-column: 1/-1; }
.quote-card label { color: #64748b; font-size: 11px; text-transform: uppercase; }
.quote-card .val  { color: #e2e8f0; }

/* Table */
table { width: 100%; border-collapse: collapse; }
th { background: #1a1d2e; color: #94a3b8; font-weight: 500; font-size: 11px;
     text-transform: uppercase; letter-spacing: 0.05em; padding: 10px 12px;
     text-align: left; border-bottom: 1px solid #2d3148; white-space: nowrap; }
td { padding: 9px 12px; border-bottom: 1px solid #1e2235; white-space: nowrap; }
tr:hover td { background: #1a1d2e; }
.pos { color: #34d399; } .neg { color: #f87171; }
.badge { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; }
.badge-EQUITY  { background: #1e3a5f; color: #7dd3fc; }
.badge-ETF     { background: #1e3a5f; color: #93c5fd; }
.badge-OPTION  { background: #312e81; color: #a5b4fc; }
.badge-CASH_EQUIVALENT { background: #1c2a1c; color: #86efac; }
.loading { color: #64748b; padding: 40px; text-align: center; }
.error   { color: #f87171; padding: 20px; }
</style>
</head>
<body>

<div class="topbar">
  <div>
    <h1>📈 ss7trading</h1>
    <div class="sub" id="lastUpdated">Loading…</div>
  </div>
  <button class="refresh-btn" onclick="loadAll()">↻ Refresh</button>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('positions')">Positions</div>
  <div class="tab" onclick="switchTab('quotes')">Quote Lookup</div>
</div>

<!-- Positions Tab -->
<div class="panel active" id="tab-positions">
  <div class="loading" id="pos-loading">Loading positions…</div>
  <div id="pos-error" class="error" style="display:none"></div>
  <div id="pos-table" style="display:none">
    <table>
      <thead><tr>
        <th>Symbol</th><th>Type</th><th>Qty</th>
        <th>Avg Price</th><th>Mkt Value</th>
        <th>Unrealized P&L</th><th>Day P&L</th><th>Day %</th>
      </tr></thead>
      <tbody id="pos-tbody"></tbody>
    </table>
  </div>
</div>

<!-- Quotes Tab -->
<div class="panel" id="tab-quotes">
  <div class="quote-search">
    <input type="text" id="quoteInput" placeholder="NVDA" maxlength="10"
           onkeydown="if(event.key==='Enter') fetchQuote()">
    <button onclick="fetchQuote()">Get Quote</button>
  </div>
  <div id="quote-result"></div>
  <hr style="border-color:#1e2235; margin: 20px 0">
  <div style="color:#64748b; font-size:12px; margin-bottom:12px;">
    Live quotes for all held positions:
  </div>
  <div class="loading" id="q-loading">Loading quotes…</div>
  <div id="q-error" class="error" style="display:none"></div>
  <table style="display:none" id="q-table">
    <thead><tr>
      <th>Symbol</th><th>Last</th><th>Bid</th><th>Ask</th>
      <th>Change</th><th>Change %</th><th>Volume</th>
      <th>52W High</th><th>52W Low</th>
    </tr></thead>
    <tbody id="q-tbody"></tbody>
  </table>
</div>

<script>
const fmt  = (v, d=2) => v == null ? '—' : Number(v).toLocaleString('en-US', {minimumFractionDigits:d, maximumFractionDigits:d});
const fmtD = (v, d=2) => v == null ? '—' : (v>=0?'+':'') + fmt(v,d);
const cls  = (v) => v == null ? '' : (v >= 0 ? 'pos' : 'neg');

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => {
    const names = ['positions','quotes'];
    t.classList.toggle('active', names[i] === name);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
}

async function loadPositions() {
  try {
    const data = await fetch('/api/positions').then(r => r.json());
    const tbody = document.getElementById('pos-tbody');
    tbody.innerHTML = data.map(p => `
      <tr>
        <td><b>${p.symbol}</b></td>
        <td><span class="badge badge-${p.asset_type}">${p.asset_type}</span></td>
        <td>${fmt(p.quantity, p.quantity % 1 === 0 ? 0 : 2)}</td>
        <td>${p.avg_price != null ? '$'+fmt(p.avg_price,4) : '—'}</td>
        <td>${p.market_value != null ? '$'+fmt(p.market_value) : '—'}</td>
        <td class="${cls(p.unrealized_pl)}">${p.unrealized_pl != null ? fmtD(p.unrealized_pl) : '—'}</td>
        <td class="${cls(p.day_pl)}">${p.day_pl != null ? '$'+fmtD(p.day_pl) : '—'}</td>
        <td class="${cls(p.day_pl_pct)}">${p.day_pl_pct != null ? fmtD(p.day_pl_pct)+'%' : '—'}</td>
      </tr>`).join('');
    document.getElementById('pos-loading').style.display = 'none';
    document.getElementById('pos-table').style.display = 'block';
  } catch(e) {
    document.getElementById('pos-loading').style.display = 'none';
    document.getElementById('pos-error').style.display = 'block';
    document.getElementById('pos-error').textContent = 'Error loading positions: ' + e.message;
  }
}

async function loadQuotes() {
  try {
    const data = await fetch('/api/quotes').then(r => r.json());
    const tbody = document.getElementById('q-tbody');
    tbody.innerHTML = data.map(q => `
      <tr>
        <td><b>${q.symbol}</b></td>
        <td>$${fmt(q.last)}</td>
        <td>${q.bid != null ? '$'+fmt(q.bid) : '—'}</td>
        <td>${q.ask != null ? '$'+fmt(q.ask) : '—'}</td>
        <td class="${cls(q.change)}">${q.change != null ? '$'+fmtD(q.change) : '—'}</td>
        <td class="${cls(q.change_pct)}">${q.change_pct != null ? fmtD(q.change_pct)+'%' : '—'}</td>
        <td>${q.volume != null ? Number(q.volume).toLocaleString() : '—'}</td>
        <td>${q['52w_high'] != null ? '$'+fmt(q['52w_high']) : '—'}</td>
        <td>${q['52w_low'] != null ? '$'+fmt(q['52w_low']) : '—'}</td>
      </tr>`).join('');
    document.getElementById('q-loading').style.display = 'none';
    document.getElementById('q-table').style.display = 'table';
  } catch(e) {
    document.getElementById('q-loading').style.display = 'none';
    document.getElementById('q-error').style.display = 'block';
    document.getElementById('q-error').textContent = 'Error loading quotes: ' + e.message;
  }
}

async function fetchQuote() {
  const sym = document.getElementById('quoteInput').value.trim().toUpperCase();
  if (!sym) return;
  const div = document.getElementById('quote-result');
  div.innerHTML = '<div class="loading">Loading ' + sym + '…</div>';
  try {
    const q = await fetch('/api/quote/' + sym).then(r => r.json());
    if (!q.symbol) { div.innerHTML = '<div class="error">No data for ' + sym + '</div>'; return; }
    const chgCls = cls(q.change);
    div.innerHTML = `
      <div class="quote-card">
        <div class="sym">${q.symbol}</div>
        <div class="last">$${fmt(q.last)}</div>
        <div><label>Bid</label><div class="val">$${fmt(q.bid)}</div></div>
        <div><label>Ask</label><div class="val">$${fmt(q.ask)}</div></div>
        <div><label>Change</label><div class="val ${chgCls}">${q.change!=null?'$'+fmtD(q.change):'—'}</div></div>
        <div><label>Change %</label><div class="val ${chgCls}">${q.change_pct!=null?fmtD(q.change_pct)+'%':'—'}</div></div>
        <div><label>Volume</label><div class="val">${q.volume!=null?Number(q.volume).toLocaleString():'—'}</div></div>
        <div><label>52W High</label><div class="val">$${fmt(q['52w_high'])}</div></div>
        <div><label>52W Low</label><div class="val">$${fmt(q['52w_low'])}</div></div>
      </div>`;
  } catch(e) {
    div.innerHTML = '<div class="error">Error: ' + e.message + '</div>';
  }
}

function loadAll() {
  document.getElementById('lastUpdated').textContent =
    'Updated ' + new Date().toLocaleTimeString();
  loadPositions();
  loadQuotes();
}

loadAll();
</script>
</body>
</html>"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  ss7trading dashboard")
    print("  http://127.0.0.1:5050")
    print("=" * 50)
    print()
    app.run(host="127.0.0.1", port=5050, debug=True, use_reloader=False)
