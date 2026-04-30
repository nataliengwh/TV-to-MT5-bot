import os
import asyncio
import logging
from datetime import datetime
import pytz
from flask import Flask, request, jsonify
from metaapi_cloud_sdk import MetaApi

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

# Blocked windows: list of (start_hour_min, end_hour_min) in HKT, weekdays only (Mon=0 … Fri=4)
# Times are inclusive-start, exclusive-end, expressed as (hour, minute) tuples.
BLOCKED_WINDOWS = [
    ((21, 30), (22, 0)),   # 21:30 – 22:00 HKT
    ((0,  0),  (8,  0)),   # 00:00 – 08:00 HKT
]

def is_trading_allowed() -> tuple[bool, str]:
    """
    Returns (True, '') if trading is allowed right now,
    or (False, reason_string) if it is blocked.
    """
    now_hkt = datetime.now(HKT)
    weekday = now_hkt.weekday()  # Monday=0, Sunday=6

    # Block all weekend signals (Saturday=5, Sunday=6)
    if weekday >= 5:
        return False, f"Weekend trading blocked ({now_hkt.strftime('%A %H:%M HKT')})"

    # Check each blocked window
    current_hm = (now_hkt.hour, now_hkt.minute)
    for (start_h, start_m), (end_h, end_m) in BLOCKED_WINDOWS:
        start_total = start_h * 60 + start_m
        end_total   = end_h   * 60 + end_m
        current_total = current_hm[0] * 60 + current_hm[1]
        if start_total <= current_total < end_total:
            return False, (
                f"Signal blocked: outside trading hours "
                f"({now_hkt.strftime('%H:%M HKT')} falls in "
                f"{start_h:02d}:{start_m:02d}–{end_h:02d}:{end_m:02d} HKT blackout window)"
            )

    return True, ""

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
METAAPI_TOKEN   = os.environ.get('METAAPI_TOKEN', '')
ACCOUNT_ID      = os.environ.get('METAAPI_ACCOUNT_ID', '')
WEBHOOK_SECRET  = os.environ.get('WEBHOOK_SECRET', 'change_me')

# Strategy configuration
# Offsets are defined in absolute price terms (e.g., $5 for Gold, $40 for BTC)
STRATEGY_CONFIG = {
    "XAUUSD": {
        "tp1_offset": 5.0,
        "tp2_offset": 10.0,
        "sl_offset": 10.0
    },
    "BTCUSD": {
        "tp1_offset": 40.0,
        "tp2_offset": 80.0,
        "sl_offset": 80.0
    }
}

# ---------------------------------------------------------------------------
# MetaApi helpers
# ---------------------------------------------------------------------------

async def _execute_dual_trade(symbol: str, action: str, volume: float, current_price: float) -> dict:
    """
    Connect to MetaApi, calculate TP/SL based on config, and open two market orders.
    Also starts a background task to monitor TP1 and move SL to breakeven for TP2.
    """
    api = MetaApi(METAAPI_TOKEN)
    
    # Get config for symbol
    # We use a fallback mechanism in case the broker uses suffixes like XAUUSD.a
    base_symbol = "XAUUSD" if "XAU" in symbol.upper() else "BTCUSD" if "BTC" in symbol.upper() else None
    
    if not base_symbol or base_symbol not in STRATEGY_CONFIG:
        raise ValueError(f"No strategy configuration found for symbol {symbol}")
        
    config = STRATEGY_CONFIG[base_symbol]
    
    # Calculate prices
    is_buy = action.lower() == 'buy'
    
    if is_buy:
        tp1_price = current_price + config["tp1_offset"]
        tp2_price = current_price + config["tp2_offset"]
        sl_price = current_price - config["sl_offset"]
    else:
        tp1_price = current_price - config["tp1_offset"]
        tp2_price = current_price - config["tp2_offset"]
        sl_price = current_price + config["sl_offset"]

    try:
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)

        if account.state not in ('DEPLOYING', 'DEPLOYED'):
            logger.info("Deploying MetaApi account...")
            await account.deploy()

        logger.info("Waiting for broker connection...")
        await account.wait_connected()

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        logger.info(f"Placing DUAL {action.upper()} {volume} lot(s) on {symbol} @ ~{current_price}")
        logger.info(f"Trade 1: TP={tp1_price}, SL={sl_price}")
        logger.info(f"Trade 2: TP={tp2_price}, SL={sl_price}")

        # Execute Trade 1
        if is_buy:
            res1 = await connection.create_market_buy_order(
                symbol, volume, stop_loss=sl_price, take_profit=tp1_price,
                options={'comment': 'TV-Bot-TP1', 'clientId': 'TV-Bot-TP1'}
            )
            # Execute Trade 2
            res2 = await connection.create_market_buy_order(
                symbol, volume, stop_loss=sl_price, take_profit=tp2_price,
                options={'comment': 'TV-Bot-TP2', 'clientId': 'TV-Bot-TP2'}
            )
        else:
            res1 = await connection.create_market_sell_order(
                symbol, volume, stop_loss=sl_price, take_profit=tp1_price,
                options={'comment': 'TV-Bot-TP1', 'clientId': 'TV-Bot-TP1'}
            )
            # Execute Trade 2
            res2 = await connection.create_market_sell_order(
                symbol, volume, stop_loss=sl_price, take_profit=tp2_price,
                options={'comment': 'TV-Bot-TP2', 'clientId': 'TV-Bot-TP2'}
            )

        logger.info(f"Trade 1 result: {res1.get('stringCode')} (ID: {res1.get('orderId')})")
        logger.info(f"Trade 2 result: {res2.get('stringCode')} (ID: {res2.get('orderId')})")
        
        # Start background monitor for breakeven
        # We pass the actual open price of Trade 2 as the breakeven target
        if 'orderId' in res1 and 'orderId' in res2:
            # We need to get the actual open price of trade 2 to set as breakeven
            # Wait a moment for the order to become a position
            await asyncio.sleep(2)
            try:
                pos2 = await connection.get_position(res2['orderId'])
                open_price = pos2['openPrice']
                
                # Start the monitor task in the background
                asyncio.create_task(_monitor_breakeven(
                    api, ACCOUNT_ID, res1['orderId'], res2['orderId'], open_price
                ))
                logger.info(f"Started breakeven monitor for Trade 2 (ID: {res2['orderId']}) at price {open_price}")
            except Exception as e:
                logger.error(f"Failed to start breakeven monitor: {e}")

        return {
            "trade1": res1,
            "trade2": res2
        }

    finally:
        try:
            await connection.close()
        except Exception:
            pass

async def _monitor_breakeven(api, account_id, tp1_id, tp2_id, breakeven_price):
    """
    Background task that polls the account to see if TP1 position is closed (hit TP).
    If it is, it moves the SL of TP2 position to breakeven_price.
    """
    try:
        account = await api.metatrader_account_api.get_account(account_id)
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()
        
        logger.info(f"[Monitor] Tracking TP1 ({tp1_id}) and TP2 ({tp2_id})")
        
        # Monitor for up to 48 hours (adjust as needed)
        max_iterations = 48 * 60 * 6  # 48 hours, checking every 10 seconds
        
        for _ in range(max_iterations):
            await asyncio.sleep(10)
            
            try:
                # Check if TP1 position still exists
                positions = await connection.get_positions()
                position_ids = [p['id'] for p in positions]
                
                if tp1_id not in position_ids:
                    # TP1 is gone (likely hit TP or SL)
                    # Check if it hit TP by looking at history
                    # For simplicity, if TP1 is closed and TP2 is still open, we move TP2 to breakeven
                    
                    if tp2_id in position_ids:
                        logger.info(f"[Monitor] TP1 ({tp1_id}) closed. Moving TP2 ({tp2_id}) SL to breakeven ({breakeven_price})")
                        
                        # Get current TP2 position to keep its TP
                        tp2_pos = next((p for p in positions if p['id'] == tp2_id), None)
                        if tp2_pos:
                            await connection.modify_position(
                                tp2_id, 
                                stop_loss=breakeven_price, 
                                take_profit=tp2_pos.get('takeProfit')
                            )
                            logger.info(f"[Monitor] Successfully moved SL to breakeven for {tp2_id}")
                    else:
                        logger.info(f"[Monitor] Both TP1 and TP2 are closed. Stopping monitor.")
                        
                    break # Exit monitor loop
                    
            except Exception as e:
                logger.error(f"[Monitor] Error checking positions: {e}")
                
    except Exception as e:
        logger.error(f"[Monitor] Fatal error: {e}")
    finally:
        try:
            await connection.close()
        except Exception:
            pass

def run_trade(symbol, action, volume, current_price):
    """Run the async trade coroutine from synchronous Flask context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            _execute_dual_trade(symbol, action, volume, current_price)
        )
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Receives TradingView alert webhooks.

    Expected JSON payload:
    {
        "secret":  "your_webhook_secret",
        "symbol":  "XAUUSD",
        "action":  "buy",          // or "sell"
        "volume":  0.05,           // lot size per trade
        "price":   2300.00         // current price from TV
    }
    """
    try:
        data = request.get_json(force=True)
        logger.info(f"Incoming webhook: {data}")

        # ── Security check ──────────────────────────────────────────────────
        if data.get('secret') != WEBHOOK_SECRET:
            logger.warning("Rejected webhook: invalid secret")
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        # ── Parameter validation ─────────────────────────────────────────────
        symbol = data.get('symbol')
        action = data.get('action', '').lower()
        volume = float(data.get('volume', 0.01))
        price  = data.get('price')

        if not symbol:
            return jsonify({"status": "error", "message": "Missing 'symbol'"}), 400
        if action not in ('buy', 'sell'):
            return jsonify({"status": "error", "message": "'action' must be 'buy' or 'sell'"}), 400
        if volume <= 0:
            return jsonify({"status": "error", "message": "'volume' must be greater than 0"}), 400
        if not price:
            return jsonify({"status": "error", "message": "Missing 'price'. TV must send current price."}), 400

        price = float(price)

        # ── Trading hours check ──────────────────────────────────────────────
        allowed, reason = is_trading_allowed()
        if not allowed:
            logger.warning(f"Trade blocked: {reason}")
            return jsonify({"status": "blocked", "message": reason}), 200

        # ── Execute trade ────────────────────────────────────────────────────
        result = run_trade(symbol, action, volume, price)

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
    return jsonify({"status": "online", "message": "TradingView → MetaApi → MT5 bot is running"}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
