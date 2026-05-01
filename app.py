import os
import asyncio
import logging
import threading
from datetime import datetime
import pytz
from flask import Flask, request, jsonify
from metaapi_cloud_sdk import MetaApi
from dotenv import load_dotenv

# Load .env file automatically
load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)

# ---------------------------------------------------------------------------
# Trading hours filter (HKT = UTC+8)
# ---------------------------------------------------------------------------
HKT = pytz.timezone('Asia/Hong_Kong')

BLOCKED_WINDOWS = [
    ((21, 30), (22, 0)),   # 21:30 – 22:00 HKT
    ((0,  0),  (8,  0)),   # 00:00 – 08:00 HKT
]

def is_trading_allowed() -> tuple:
    now_hkt = datetime.now(HKT)
    weekday = now_hkt.weekday()
    if weekday >= 5:
        return False, f"Weekend trading blocked ({now_hkt.strftime('%A %H:%M HKT')})"
    current_total = now_hkt.hour * 60 + now_hkt.minute
    for (sh, sm), (eh, em) in BLOCKED_WINDOWS:
        if sh * 60 + sm <= current_total < eh * 60 + em:
            return False, (
                f"Signal blocked: outside trading hours "
                f"({now_hkt.strftime('%H:%M HKT')} in "
                f"{sh:02d}:{sm:02d}–{eh:02d}:{em:02d} HKT blackout)"
            )
    return True, ""

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
METAAPI_TOKEN  = os.environ.get('METAAPI_TOKEN', '')
ACCOUNT_ID     = os.environ.get('METAAPI_ACCOUNT_ID', '')
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', 'change_me')

STRATEGY_CONFIG = {
    "XAUUSD": {"tp1_offset": 5.0,  "tp2_offset": 10.0, "sl_offset": 10.0},
    "BTCUSD": {"tp1_offset": 40.0, "tp2_offset": 80.0, "sl_offset": 80.0},
}

# ---------------------------------------------------------------------------
# Persistent async event loop + MetaApi connection
# ---------------------------------------------------------------------------
# A single background thread runs the asyncio event loop for the lifetime of
# the process. The MetaApi connection is established ONCE at startup and reused
# for every webhook — this avoids the 10-30 second reconnect delay that caused
# TradingView's 5-second webhook timeout.
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop = None
_connection = None          # RPC connection (reused)
_api = None                 # MetaApi instance (reused)
_connection_ready = threading.Event()   # set when connection is ready


def _start_background_loop(loop: asyncio.AbstractEventLoop):
    """Run the asyncio loop forever in a daemon thread."""
    asyncio.set_event_loop(loop)
    loop.run_forever()


async def _init_metaapi():
    """Connect to MetaApi once and keep the connection alive."""
    global _api, _connection
    _api = MetaApi(METAAPI_TOKEN)
    account = await _api.metatrader_account_api.get_account(ACCOUNT_ID)
    logger.info(f"Account state: {account.state}")
    logger.info("Waiting for broker connection...")
    await account.wait_connected()
    _connection = account.get_rpc_connection()
    await _connection.connect()
    await _connection.wait_synchronized()
    logger.info("MetaApi connection established and synchronised. Bot is ready.")
    _connection_ready.set()


def start_persistent_connection():
    """Called once at startup: create the background loop and connect."""
    global _loop
    _loop = asyncio.new_event_loop()
    t = threading.Thread(target=_start_background_loop, args=(_loop,), daemon=True)
    t.start()
    # Schedule the async init on the background loop
    future = asyncio.run_coroutine_threadsafe(_init_metaapi(), _loop)
    try:
        future.result(timeout=120)   # wait up to 2 min for initial connection
    except Exception as e:
        logger.error(f"Failed to establish MetaApi connection at startup: {e}")


def run_async(coro):
    """Submit a coroutine to the persistent background loop and wait for result."""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=30)   # 30-second timeout per trade


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

async def _execute_dual_trade(symbol: str, action: str, volume: float, current_price: float) -> dict:
    """Place two market orders (TP1 and TP2) using the persistent connection."""
    global _connection

    base_symbol = "XAUUSD" if "XAU" in symbol.upper() else "BTCUSD" if "BTC" in symbol.upper() else None
    if not base_symbol or base_symbol not in STRATEGY_CONFIG:
        raise ValueError(f"No strategy config for symbol: {symbol}")

    config  = STRATEGY_CONFIG[base_symbol]
    is_buy  = action.lower() == 'buy'

    # Re-synchronise if the connection dropped
    # is_synchronized() is on the underlying connection object, not the instance wrapper
    if not _connection._meta_api_connection.is_synchronized():
        logger.info("Connection not synchronised — re-synchronising...")
        await _connection.wait_synchronized()

    # Fetch live price from broker (ask for BUY, bid for SELL)
    live = await _connection.get_symbol_price(symbol)
    entry_price = live['ask'] if is_buy else live['bid']
    logger.info(f"TV price: {current_price} | Live broker price: {entry_price}")

    # Calculate SL/TP from live price
    if is_buy:
        tp1 = round(entry_price + config["tp1_offset"], 2)
        tp2 = round(entry_price + config["tp2_offset"], 2)
        sl  = round(entry_price - config["sl_offset"],  2)
    else:
        tp1 = round(entry_price - config["tp1_offset"], 2)
        tp2 = round(entry_price - config["tp2_offset"], 2)
        sl  = round(entry_price + config["sl_offset"],  2)

    entry_price = round(entry_price, 2)
    logger.info(f"Placing DUAL {action.upper()} {volume} lot(s) on {symbol} @ {entry_price}")
    logger.info(f"Trade 1 (TP1): TP={tp1}, SL={sl}")
    logger.info(f"Trade 2 (TP2): TP={tp2}, SL={sl}")

    if is_buy:
        res1 = await _connection.create_market_buy_order(
            symbol, volume, stop_loss=sl, take_profit=tp1, options={'comment': 'TP1'})
        res2 = await _connection.create_market_buy_order(
            symbol, volume, stop_loss=sl, take_profit=tp2, options={'comment': 'TP2'})
    else:
        res1 = await _connection.create_market_sell_order(
            symbol, volume, stop_loss=sl, take_profit=tp1, options={'comment': 'TP1'})
        res2 = await _connection.create_market_sell_order(
            symbol, volume, stop_loss=sl, take_profit=tp2, options={'comment': 'TP2'})

    logger.info(f"Trade 1 result: {res1.get('stringCode')} (positionId: {res1.get('positionId')})")
    logger.info(f"Trade 2 result: {res2.get('stringCode')} (positionId: {res2.get('positionId')})")

    # Start breakeven monitor as a background task on the same loop
    pos1_id = res1.get('positionId')
    pos2_id = res2.get('positionId')
    if pos1_id and pos2_id:
        asyncio.ensure_future(
            _monitor_breakeven(pos1_id, pos2_id, entry_price),
            loop=_loop
        )
        logger.info(f"Started breakeven monitor: TP1 pos={pos1_id}, TP2 pos={pos2_id}, BE={entry_price}")

    return {"trade1": res1, "trade2": res2}


async def _monitor_breakeven(tp1_pos_id: str, tp2_pos_id: str, breakeven_price: float):
    """
    Polls every 10 seconds. When TP1 position closes (TP hit), moves TP2 SL to breakeven.
    Runs entirely on the persistent background loop — does NOT block Flask.
    """
    logger.info(f"[Monitor] Watching TP1 pos={tp1_pos_id}, TP2 pos={tp2_pos_id}")
    max_checks = 48 * 360   # 48 hours at 10-second intervals

    for _ in range(max_checks):
        await asyncio.sleep(10)
        try:
            positions = await _connection.get_positions()
            pos_ids   = {p['id'] for p in positions}

            if tp1_pos_id not in pos_ids:
                # TP1 closed
                if tp2_pos_id in pos_ids:
                    tp2_pos = next(p for p in positions if p['id'] == tp2_pos_id)
                    await _connection.modify_position(
                        tp2_pos_id,
                        stop_loss=breakeven_price,
                        take_profit=tp2_pos.get('takeProfit')
                    )
                    logger.info(f"[Monitor] TP1 closed → TP2 SL moved to breakeven {breakeven_price}")
                else:
                    logger.info("[Monitor] Both positions closed. Monitor exiting.")
                return

        except Exception as e:
            logger.error(f"[Monitor] Error: {e}")

    logger.info("[Monitor] 48-hour timeout reached. Monitor exiting.")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    TradingView alert webhook endpoint.

    Expected JSON:
    {
        "secret":  "your_webhook_secret",
        "symbol":  "XAUUSD",
        "action":  "buy",
        "volume":  0.05,
        "price":   3300.00
    }
    """
    try:
        data = request.get_json(force=True)
        logger.info(f"Incoming webhook: {data}")

        if data.get('secret') != WEBHOOK_SECRET:
            logger.warning("Rejected webhook: invalid secret")
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        symbol = data.get('symbol')
        action = data.get('action', '').lower()
        volume = float(data.get('volume', 0.01))
        price  = data.get('price')

        if not symbol:
            return jsonify({"status": "error", "message": "Missing 'symbol'"}), 400
        if action not in ('buy', 'sell'):
            return jsonify({"status": "error", "message": "'action' must be 'buy' or 'sell'"}), 400
        if volume <= 0:
            return jsonify({"status": "error", "message": "'volume' must be > 0"}), 400
        if not price:
            return jsonify({"status": "error", "message": "Missing 'price'"}), 400

        price = float(price)

        allowed, reason = is_trading_allowed()
        if not allowed:
            logger.warning(f"Trade blocked: {reason}")
            return jsonify({"status": "blocked", "message": reason}), 200

        if not _connection_ready.is_set():
            return jsonify({"status": "error", "message": "MetaApi connection not ready yet, try again in a moment"}), 503

        result = run_async(_execute_dual_trade(symbol, action, volume, price))

        return jsonify({
            "status": "success",
            "trade1": result["trade1"].get('stringCode'),
            "trade2": result["trade2"].get('stringCode')
        }), 200

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/', methods=['GET'])
def health():
    ready = _connection_ready.is_set()
    return jsonify({
        "status": "online",
        "metaapi_connected": ready,
        "message": "Bot ready" if ready else "Connecting to MetaApi..."
    }), 200


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    logger.info("Starting persistent MetaApi connection...")
    start_persistent_connection()
    logger.info("Starting Flask server on port 8080...")
    app.run(host='0.0.0.0', port=8080)
