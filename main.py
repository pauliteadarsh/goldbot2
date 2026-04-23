"""
Gold Bot 2 — Main webhook server
Listens for TradingView alerts and places trades on Capital.com
Signals: buy, sell, sl
"""

import os
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from capital import CapitalClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log")
    ]
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Settings ───────────────────────────────────────────────────
EPIC       = "GOLD"
TRADE_SIZE = float(os.getenv("TRADE_SIZE", "1"))

# ── Capital.com client ─────────────────────────────────────────
def get_capital():
    return CapitalClient(
        api_key    = os.getenv("CAPITAL_API_KEY"),
        password   = os.getenv("CAPITAL_PASSWORD"),
        account_id = os.getenv("CAPITAL_ACCOUNT_ID"),
        env        = os.getenv("CAPITAL_ENV", "demo")
    )


# ══════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "time": datetime.now().isoformat()})


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView sends alerts here.
    Expected payloads:
      {"action": "buy"}
      {"action": "sell"}
      {"action": "sl"}
    """
    data = request.get_json(silent=True)

    if not data or "action" not in data:
        log.warning(f"Invalid webhook payload: {data}")
        return jsonify({"error": "Invalid payload — expected {\"action\": \"buy/sell/sl\"}"}), 400

    action    = data["action"].lower().strip()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"[{timestamp}] Webhook received: action={action}")

    try:
        if action == "buy":
            handle_buy()
        elif action == "sell":
            handle_sell()
        elif action == "sl":
            handle_sl()
        else:
            log.warning(f"Unknown action: {action}")
            return jsonify({"error": f"Unknown action: {action}"}), 400

    except Exception as e:
        log.error(f"Error handling '{action}': {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ok", "action": action})


# ══════════════════════════════════════════════════════════════
# SIGNAL HANDLERS
# ══════════════════════════════════════════════════════════════

def handle_buy():
    capital        = get_capital()
    positions      = capital.get_positions(EPIC)
    sell_positions = [p for p in positions if p["direction"] == "SELL"]

    if sell_positions:
        log.info(f"BUY: closing {len(sell_positions)} SELL position(s) first")
        for pos in sell_positions:
            capital.close_position(pos["dealId"])
            log.info(f"  Closed SELL {pos['dealId']}")

    capital.open_position(EPIC, "BUY", TRADE_SIZE)
    log.info(f"Opened BUY {TRADE_SIZE} x {EPIC}")


def handle_sell():
    capital       = get_capital()
    positions     = capital.get_positions(EPIC)
    buy_positions = [p for p in positions if p["direction"] == "BUY"]

    if buy_positions:
        log.info(f"SELL: closing {len(buy_positions)} BUY position(s) first")
        for pos in buy_positions:
            capital.close_position(pos["dealId"])
            log.info(f"  Closed BUY {pos['dealId']}")

    capital.open_position(EPIC, "SELL", TRADE_SIZE)
    log.info(f"Opened SELL {TRADE_SIZE} x {EPIC}")


def handle_sl():
    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    if not positions:
        log.info("SL signal — no open positions")
        return

    log.info(f"SL signal: closing {len(positions)} position(s)")
    for pos in positions:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed {pos['direction']} {pos['dealId']}")


# ══════════════════════════════════════════════════════════════
# START
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info(f"Gold Bot 2 started on port {port}")
    app.run(host="0.0.0.0", port=port)
