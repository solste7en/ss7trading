"""
app.py — ss7trading dashboard
Run: python app.py
Visit: http://127.0.0.1:5050
"""

import logging

from flask import Flask, jsonify, render_template

from blueprints.analytics import bp as analytics_bp
from blueprints.balance import bp as balance_bp
from blueprints.income import bp as income_bp
from blueprints.options import bp as options_bp
from blueprints.orders import bp as orders_bp
from blueprints.positions import bp as positions_bp
from blueprints.quotes import bp as quotes_bp
from blueprints.sync import bp as sync_bp
from blueprints.transactions import bp as transactions_bp
from blueprints.watchlists import bp as watchlists_bp

app = Flask(__name__)
log = logging.getLogger(__name__)

app.register_blueprint(analytics_bp)
app.register_blueprint(positions_bp)
app.register_blueprint(balance_bp)
app.register_blueprint(quotes_bp)
app.register_blueprint(watchlists_bp)
app.register_blueprint(transactions_bp)
app.register_blueprint(orders_bp)
app.register_blueprint(options_bp)
app.register_blueprint(sync_bp)
app.register_blueprint(income_bp)


@app.errorhandler(Exception)
def handle_exception(e):
    log.exception("Unhandled error")
    return jsonify({"error": str(e)}), 500


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  ss7trading dashboard")
    print("  http://127.0.0.1:5050")
    print("=" * 50)
    print()
    app.run(host="127.0.0.1", port=5050, debug=True, use_reloader=True)
