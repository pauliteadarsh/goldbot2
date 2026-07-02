"""
Gold Bot 2 — Webhook server
Listens for TradingView alerts and places trades on Capital.com

Actions (Haddaf N1 spec):
  BUY          — close any opposite SELL, open BUY (no broker SL — indicator
                 handles it internally; bot receives CLOSE_ALL when SL hits)
  SELL         — close any opposite BUY, open SELL
  CLOSE_ALL    — close all open positions (fires on internal SL, Risk Exit, etc.)

Sidecar alerts (independent positions with own TP/SL, each toggleable):
  POWER_BUY    — BUY sidecar: TP 12.5 pts, no SL
  BVB_RC_BUY   — BUY sidecar: TP 10 pts, SL 20 pts
  VB_BUY       — BUY sidecar: TP 25 pts, no SL
  S2           — SELL sidecar: close opposite BUYs, TP 6 pts, no SL
  B2           — BUY sidecar: TP 17 pts, SL 30 pts (re-stack on later bar)

All sidecars are closed automatically on CLOSE_ALL or a parent-direction flip
(BUY sidecars close when SELL fires; S2 closes when BUY fires).
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

# ── Configuration (set via Railway environment variables) ──────────────────────
EPIC       = "GOLD"
TRADE_SIZE = float(os.getenv("TRADE_SIZE", "1"))

# Sidecar sizes — all default to TRADE_SIZE; toggle each on/off independently
POWER_BUY_ENABLED  = os.getenv("POWER_BUY_ENABLED",  "true").lower() == "true"
POWER_BUY_SIZE     = float(os.getenv("POWER_BUY_SIZE",  str(TRADE_SIZE)))
BVB_RC_BUY_ENABLED = os.getenv("BVB_RC_BUY_ENABLED", "true").lower() == "true"
BVB_RC_BUY_SIZE    = float(os.getenv("BVB_RC_BUY_SIZE", str(TRADE_SIZE)))
VB_BUY_ENABLED     = os.getenv("VB_BUY_ENABLED",     "true").lower() == "true"
VB_BUY_SIZE        = float(os.getenv("VB_BUY_SIZE",     str(TRADE_SIZE)))
S2_ENABLED         = os.getenv("S2_ENABLED",          "true").lower() == "true"
S2_SIZE            = float(os.getenv("S2_SIZE",          str(TRADE_SIZE)))
B2_ENABLED         = os.getenv("B2_ENABLED",          "true").lower() == "true"
B2_SIZE            = float(os.getenv("B2_SIZE",          str(TRADE_SIZE)))

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ── Sidecar TP/SL constants — from Haddaf N1; update here if indicator changes ─
_POWER_BUY_TP  = 12.5
_BVB_RC_BUY_TP = 10
_BVB_RC_BUY_SL = 20
_VB_BUY_TP     = 25
_S2_TP         = 6
_B2_TP         = 17
_B2_SL         = 30


# ── Capital.com client ─────────────────────────────────────────────────────────
def get_capital() -> CapitalClient:
    return CapitalClient(
        api_key    = os.getenv("CAPITAL_API_KEY"),
        password   = os.getenv("CAPITAL_PASSWORD"),
        account_id = os.getenv("CAPITAL_ACCOUNT_ID"),
        env        = os.getenv("CAPITAL_ENV", "demo"),
    )


# ── Telegram ───────────────────────────────────────────────────────────────────
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


def _weekly_pnl(capital: CapitalClient) -> str:
    pnl = capital.get_weekly_pnl()
    return f"AED {pnl}" if pnl is not None else "unavailable"


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

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
        elif action == "close_all":
            handle_close_all()
        elif action == "power_buy":
            handle_power_buy()
        elif action == "bvb_rc_buy":
            handle_bvb_rc_buy()
        elif action == "vb_buy":
            handle_vb_buy()
        elif action == "s2":
            handle_s2()
        elif action == "b2":
            handle_b2()
        else:
            log.warning(f"Unknown action: {action!r}")
            return jsonify({"error": f"Unknown action: {action}"}), 400
    except Exception as e:
        log.error(f"Error handling {action!r}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ok", "action": action})


# ══════════════════════════════════════════════════════════════════════════════
# CORE HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def handle_buy():
    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    for pos in [p for p in positions if p["direction"] == "SELL"]:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed SELL {pos['dealId']} size={pos['size']}")

    capital.open_position(EPIC, "BUY", TRADE_SIZE)
    log.info(f"Opened BUY size={TRADE_SIZE}")
    notify(f"🟢 BUY opened\nSize: {TRADE_SIZE}\nWeekly P&L: {_weekly_pnl(capital)}")


def handle_sell():
    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    for pos in [p for p in positions if p["direction"] == "BUY"]:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed BUY {pos['dealId']} size={pos['size']}")

    capital.open_position(EPIC, "SELL", TRADE_SIZE)
    log.info(f"Opened SELL size={TRADE_SIZE}")
    notify(f"🔴 SELL opened\nSize: {TRADE_SIZE}\nWeekly P&L: {_weekly_pnl(capital)}")


def handle_close_all():
    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    if not positions:
        log.info("CLOSE_ALL: no open positions")
        return

    log.info(f"CLOSE_ALL: closing {len(positions)} position(s)")
    for pos in positions:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed {pos['direction']} {pos['dealId']} size={pos['size']}")

    notify(f"❌ CLOSE_ALL: {len(positions)} position(s) closed\nWeekly P&L: {_weekly_pnl(capital)}")


# ══════════════════════════════════════════════════════════════════════════════
# SIDECAR HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def handle_power_buy():
    if not POWER_BUY_ENABLED:
        log.info("POWER_BUY disabled — skipping")
        return

    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    for pos in [p for p in positions if p["direction"] == "SELL"]:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed SELL {pos['dealId']} size={pos['size']}")

    capital.open_position(EPIC, "BUY", POWER_BUY_SIZE, profit_distance=_POWER_BUY_TP)
    log.info(f"Opened POWER_BUY size={POWER_BUY_SIZE} TP={_POWER_BUY_TP}")
    notify(
        f"🟢⚡ POWER_BUY opened\n"
        f"Size: {POWER_BUY_SIZE} | TP: {_POWER_BUY_TP} pts\n"
        f"Weekly P&L: {_weekly_pnl(capital)}"
    )


def handle_bvb_rc_buy():
    if not BVB_RC_BUY_ENABLED:
        log.info("BVB_RC_BUY disabled — skipping")
        return

    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    for pos in [p for p in positions if p["direction"] == "SELL"]:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed SELL {pos['dealId']} size={pos['size']}")

    capital.open_position(
        EPIC, "BUY", BVB_RC_BUY_SIZE,
        profit_distance=_BVB_RC_BUY_TP, stop_distance=_BVB_RC_BUY_SL,
    )
    log.info(f"Opened BVB_RC_BUY size={BVB_RC_BUY_SIZE} TP={_BVB_RC_BUY_TP} SL={_BVB_RC_BUY_SL}")
    notify(
        f"🟢📌 BVB_RC_BUY opened\n"
        f"Size: {BVB_RC_BUY_SIZE} | TP: {_BVB_RC_BUY_TP} pts | SL: {_BVB_RC_BUY_SL} pts\n"
        f"Weekly P&L: {_weekly_pnl(capital)}"
    )


def handle_vb_buy():
    if not VB_BUY_ENABLED:
        log.info("VB_BUY disabled — skipping")
        return

    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    for pos in [p for p in positions if p["direction"] == "SELL"]:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed SELL {pos['dealId']} size={pos['size']}")

    capital.open_position(EPIC, "BUY", VB_BUY_SIZE, profit_distance=_VB_BUY_TP)
    log.info(f"Opened VB_BUY size={VB_BUY_SIZE} TP={_VB_BUY_TP}")
    notify(
        f"🟢🎯 VB_BUY opened\n"
        f"Size: {VB_BUY_SIZE} | TP: {_VB_BUY_TP} pts\n"
        f"Weekly P&L: {_weekly_pnl(capital)}"
    )


def handle_s2():
    if not S2_ENABLED:
        log.info("S2 disabled — skipping")
        return

    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    for pos in [p for p in positions if p["direction"] == "BUY"]:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed BUY {pos['dealId']} size={pos['size']}")

    capital.open_position(EPIC, "SELL", S2_SIZE, profit_distance=_S2_TP)
    log.info(f"Opened S2 SELL size={S2_SIZE} TP={_S2_TP}")
    notify(
        f"🔴⚡ S2 SELL opened\n"
        f"Size: {S2_SIZE} | TP: {_S2_TP} pts\n"
        f"Weekly P&L: {_weekly_pnl(capital)}"
    )


def handle_b2():
    if not B2_ENABLED:
        log.info("B2 disabled — skipping")
        return

    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    for pos in [p for p in positions if p["direction"] == "SELL"]:
        capital.close_position(pos["dealId"])
        log.info(f"  Closed SELL {pos['dealId']} size={pos['size']}")

    capital.open_position(
        EPIC, "BUY", B2_SIZE,
        profit_distance=_B2_TP, stop_distance=_B2_SL,
    )
    log.info(f"Opened B2 BUY size={B2_SIZE} TP={_B2_TP} SL={_B2_SL}")
    notify(
        f"🟢🔁 B2 BUY opened\n"
        f"Size: {B2_SIZE} | TP: {_B2_TP} pts | SL: {_B2_SL} pts\n"
        f"Weekly P&L: {_weekly_pnl(capital)}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# START
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info(f"Gold Bot 2 started on port {port}")
    app.run(host="0.0.0.0", port=port)
