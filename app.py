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
# Timezone
# ---------------------------------------------------------------------------
HKT = pytz.timezone('Asia/Hong_Kong')

# ---------------------------------------------------------------------------
# Trading hours filter (HKT)
# ---------------------------------------------------------------------------
BLOCKED_WINDOWS = [
    ((21, 30), (22, 0)),   # 21:30 – 22:00 HKT  (NY open volatility)
    ((0,  0),  (8,  0)),   # 00:00 – 08:00 HKT  (overnight low liquidity)
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

# Daily auto-close config for XAUUSD (gold market gap protection)
# Set XAUUSD_CLOSE_TIME in .env as "HH:MM" in HKT, e.g. "04:59"
# US summer hours (EDT, Mar–Nov): gap at ~05:00 HKT → close at 04:59
# US winter hours (EST, Nov–Mar): gap at ~06:00 HKT → close at 05:59
XAUUSD_CLOSE_TIME = os.environ.get('XAUUSD_CLOSE_TIME', '04:59')  # HKT

STRATEGY_CONFIG = {
    "XAUUSD": {"tp1_offset": 5.0,  "tp2_offset": 10.0, "sl_offset": 10.0},
    "BTCUSD": {"tp1_offset": 40.0, "tp2_offset": 80.0, "sl_offset": 80.0},
}

# ---------------------------------------------------------------------------
# Persistent async event loop + MetaApi connection
# ---------------------------------------------------------------------------
_loop: asyncio.AbstractEventLoop = None
_connection = None
_api = None
_connection_ready = threading.Event()


def _start_background_loop(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


async def _init_metaapi():
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
    # Start the daily XAUUSD close scheduler
    asyncio.ensure_future(_daily_xauusd_close_scheduler(), loop=_loop)


def start_persistent_connection():
    global _loop
    _loop = asyncio.new_event_loop()
    t = threading.Thread(target=_start_background_loop, args=(_loop,), daemon=True)
    t.start()
    future = asyncio.run_coroutine_threadsafe(_init_metaapi(), _loop)
    try:
        future.result(timeout=120)
    except Exception as e:
        logger.error(f"Failed to establish MetaApi connection at startup: {e}")


def run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=30)


# ---------------------------------------------------------------------------
# Daily XAUUSD close scheduler
# ---------------------------------------------------------------------------

async def _daily_xauusd_close_scheduler():
    """
    Runs forever in the background. Every minute, checks if it is time to
    close all XAUUSD positions (weekdays only, at XAUUSD_CLOSE_TIME HKT).
    This protects against the gold market daily gap (COMEX close ~05:00 HKT
    in US summer hours).

    Configure close time via XAUUSD_CLOSE_TIME in .env (default: "04:59").
    US summer (EDT, Mar–Nov): use "04:59"
    US winter (EST, Nov–Mar): use "05:59"
    """
    try:
        close_h, close_m = [int(x) for x in XAUUSD_CLOSE_TIME.split(':')]
    except Exception:
        logger.error(f"Invalid XAUUSD_CLOSE_TIME format '{XAUUSD_CLOSE_TIME}'. Expected HH:MM. Using default 04:59.")
        close_h, close_m = 4, 59

    logger.info(f"[DailyClose] XAUUSD auto-close scheduler started. Will close positions at {close_h:02d}:{close_m:02d} HKT on weekdays.")
    fired_today = None   # tracks the date we last fired, to avoid double-firing

    while True:
        await asyncio.sleep(30)   # check every 30 seconds
        try:
            now_hkt = datetime.now(HKT)
            today   = now_hkt.date()

            # Only fire on weekdays (Mon–Fri)
            if now_hkt.weekday() >= 5:
                continue

            # Check if it's time (within the target minute) and hasn't fired today
            if (now_hkt.hour == close_h and
                    now_hkt.minute == close_m and
                    fired_today != today):
                fired_today = today
                logger.info(f"[DailyClose] {now_hkt.strftime('%H:%M HKT')} — closing all XAUUSD positions before market gap...")
                await _close_all_xauusd_positions()

        except Exception as e:
            logger.error(f"[DailyClose] Scheduler error: {e}")


async def _close_all_xauusd_positions():
    """Close all open XAUUSD positions at market price."""
    try:
        if not _connection_ready.is_set():
            logger.warning("[DailyClose] Connection not ready — skipping close.")
            return

        positions = await _connection.get_positions()
        xau_positions = [p for p in positions if 'XAU' in p.get('symbol', '').upper()]

        if not xau_positions:
            logger.info("[DailyClose] No open XAUUSD positions to close.")
            return

        logger.info(f"[DailyClose] Found {len(xau_positions)} XAUUSD position(s) to close.")

        for pos in xau_positions:
            pos_id     = pos['id']
            pos_type   = pos['type']   # POSITION_TYPE_BUY or POSITION_TYPE_SELL
            pos_volume = pos['volume']
            pos_symbol = pos['symbol']

            try:
                if pos_type == 'POSITION_TYPE_BUY':
                    result = await _connection.create_market_sell_order(
                        pos_symbol, pos_volume,
                        options={'comment': 'DailyClose'}
                    )
                else:
                    result = await _connection.create_market_buy_order(
                        pos_symbol, pos_volume,
                        options={'comment': 'DailyClose'}
                    )
                logger.info(
                    f"[DailyClose] Closed position {pos_id} ({pos_type} {pos_volume} {pos_symbol}): "
                    f"{result.get('stringCode')}"
                )
            except Exception as e:
                logger.error(f"[DailyClose] Failed to close position {pos_id}: {e}")

        logger.info("[DailyClose] All XAUUSD positions processed.")

    except Exception as e:
        logger.error(f"[DailyClose] Error fetching positions: {e}")


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

async def _execute_dual_trade(symbol: str, action: str, volume: float, current_price: float) -> dict:
    """Place two market orders (TP1 and TP2) using the persistent connection."""
    global _connection

    base_symbol = "XAUUSD" if "XAU" in symbol.upper() else "BTCUSD" if "BTC" in symbol.upper() else None
    if not base_symbol or base_symbol not in STRATEGY_CONFIG:
        raise ValueError(f"No strategy config for symbol: {symbol}")

    config = STRATEGY_CONFIG[base_symbol]
    is_buy = action.lower() == 'buy'

    # Re-synchronise if the connection dropped
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

    # Start breakeven monitor
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
    """
    logger.info(f"[Monitor] Watching TP1 pos={tp1_pos_id}, TP2 pos={tp2_pos_id}")
    max_checks = 48 * 360   # 48 hours

    for _ in range(max_checks):
        await asyncio.sleep(10)
        try:
            positions = await _connection.get_positions()
            pos_ids   = {p['id'] for p in positions}

            if tp1_pos_id not in pos_ids:
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
        "price":   3300.00,
        "filter":  true        // optional — true = apply trading hours filter, false/omit = bypass
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

        apply_filter = str(data.get('filter', 'false')).lower() in ('true', '1', 'yes')
        if apply_filter:
            allowed, reason = is_trading_allowed()
            if not allowed:
                logger.warning(f"Trade blocked by hours filter: {reason}")
                return jsonify({"status": "blocked", "message": reason}), 200
            else:
                logger.info("Trading hours filter: ACTIVE and within allowed window — proceeding")
        else:
            logger.info("Trading hours filter: BYPASSED (filter=false or not set)")

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
    now_hkt = datetime.now(HKT)
    return jsonify({
        "status": "online",
        "metaapi_connected": ready,
        "server_time_hkt": now_hkt.strftime('%Y-%m-%d %H:%M:%S HKT'),
        "xauusd_daily_close_time": f"{XAUUSD_CLOSE_TIME} HKT (weekdays)",
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
