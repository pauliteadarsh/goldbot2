"""
Gold Bot 2 — Webhook server
Listens for TradingView alerts and places trades on Capital.com

Actions:
  buy           — close any opposite SELL, open BUY
  sell          — close any opposite BUY, open SELL
  partial close — close PARTIAL_CLOSE_PCT of each open position, re-open remainder
  close         — close all open positions
"""

import os
import logging
import requests
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
        logging.FileHandler("bot.log"),
    ],
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Configuration (set via Railway environment variables) ──────
EPIC              = os.getenv("EPIC", "GOLD")
TRADE_SIZE        = float(os.getenv("TRADE_SIZE", "1"))
SL_DISTANCE       = float(os.getenv("SL_DISTANCE", "80"))
PARTIAL_CLOSE_PCT = float(os.getenv("PARTIAL_CLOSE_PCT", "0.70"))  # fraction to close, e.g. 0.70 = 70%
MIN_TRADE_SIZE    = float(os.getenv("MIN_TRADE_SIZE", "0.10"))      # don't re-open below this size
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")


# ── Capital.com client ─────────────────────────────────────────
def get_capital() -> CapitalClient:
    return CapitalClient(
        api_key    = os.getenv("CAPITAL_API_KEY"),
        password   = os.getenv("CAPITAL_PASSWORD"),
        account_id = os.getenv("CAPITAL_ACCOUNT_ID"),
        env        = os.getenv("CAPITAL_ENV", "demo"),
    )


# ── Telegram ───────────────────────────────────────────────────
def notify(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=5,
        )
    except Exception as e:
        log.warning(f"Telegram failed: {e}")


def _daily_pnl(capital: CapitalClient) -> str:
    pnl = capital.get_daily_pnl()
    return f"AED {pnl}" if pnl is not None else "unavailable"


# ══════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "time": datetime.now().isoformat()})


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data or "action" not in data:
        log.warning(f"Bad payload: {data}")
        return jsonify({"error": "Expected JSON with 'action' key"}), 400

    action    = data["action"].lower().strip()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"[{timestamp}] action={action!r}")

    try:
        if action == "buy":
            handle_buy()
        elif action == "sell":
            handle_sell()
        elif action == "partial close":
            handle_partial_close()
        elif action == "close":
            handle_close()
        else:
            log.warning(f"Unknown action: {action!r}")
            return jsonify({"error": f"Unknown action: {action}"}), 400
    except Exception as e:
        log.error(f"Error handling {action!r}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ok", "action": action})


# ══════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════

def handle_buy():
    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    for pos in [p for p in positions if p["direction"] == "SELL"]:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed SELL {pos['dealId']} size={pos['size']}")

    capital.open_position(EPIC, "BUY", TRADE_SIZE, SL_DISTANCE)
    log.info(f"Opened BUY size={TRADE_SIZE} SL={SL_DISTANCE}")
    notify(f"🟢 BUY opened\nSize: {TRADE_SIZE}\nDaily P&L: {_daily_pnl(capital)}")


def handle_sell():
    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    for pos in [p for p in positions if p["direction"] == "BUY"]:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed BUY {pos['dealId']} size={pos['size']}")

    capital.open_position(EPIC, "SELL", TRADE_SIZE, SL_DISTANCE)
    log.info(f"Opened SELL size={TRADE_SIZE} SL={SL_DISTANCE}")
    notify(f"🔴 SELL opened\nSize: {TRADE_SIZE}\nDaily P&L: {_daily_pnl(capital)}")


def handle_partial_close():
    """
    Close PARTIAL_CLOSE_PCT of each open position and re-open the remainder.
    If the remaining size would be below MIN_TRADE_SIZE the position is fully closed.
    """
    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    if not positions:
        log.info("Partial close: no open positions")
        return

    close_pct = PARTIAL_CLOSE_PCT
    keep_pct  = round(1.0 - close_pct, 10)
    log.info(f"Partial close {close_pct*100:.0f}% across {len(positions)} position(s)")

    for pos in positions:
        direction     = pos["direction"]
        original_size = float(pos["size"])
        keep_size     = round(original_size * keep_pct, 2)

        capital.close_position(pos["dealId"])
        log.info(f"  Closed {direction} {pos['dealId']} (original size={original_size})")

        if keep_size >= MIN_TRADE_SIZE:
            capital.open_position(EPIC, direction, keep_size, SL_DISTANCE)
            log.info(f"  Re-opened {direction} size={keep_size} (kept {keep_pct*100:.0f}%)")
            notify(
                f"⚡ Partial close {close_pct*100:.0f}%\n"
                f"Direction: {direction}\n"
                f"Closed: {original_size} — Remaining: {keep_size}\n"
                f"Daily P&L: {_daily_pnl(capital)}"
            )
        else:
            log.info(f"  Remaining size {keep_size} < min {MIN_TRADE_SIZE} — fully closed")
            notify(
                f"⚡ Partial close {close_pct*100:.0f}% (fully closed — remaining {keep_size} < min {MIN_TRADE_SIZE})\n"
                f"Direction: {direction}\n"
                f"Daily P&L: {_daily_pnl(capital)}"
            )


def handle_close():
    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    if not positions:
        log.info("Close: no open positions")
        return

    log.info(f"Closing all {len(positions)} position(s)")
    for pos in positions:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed {pos['direction']} {pos['dealId']} size={pos['size']}")

    notify(f"❌ All positions closed\nCount: {len(positions)}\nDaily P&L: {_daily_pnl(capital)}")


# ══════════════════════════════════════════════════════════════
# START
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info(f"Gold Bot 2 started on port {port}")
    app.run(host="0.0.0.0", port=port)
