import os
import asyncio
import logging
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
# Config (loaded from environment variables — see .env.example)
# ---------------------------------------------------------------------------
METAAPI_TOKEN   = os.environ.get('METAAPI_TOKEN', '')
ACCOUNT_ID      = os.environ.get('METAAPI_ACCOUNT_ID', '')
WEBHOOK_SECRET  = os.environ.get('WEBHOOK_SECRET', 'change_me')

# ---------------------------------------------------------------------------
# MetaApi helpers
# ---------------------------------------------------------------------------

async def _execute_trade(symbol: str, action: str, volume: float,
                         sl: float | None, tp: float | None) -> dict:
    """
    Connect to the MetaApi cloud, open a market order, and return the result.
    A fresh RPC connection is opened per request so the server can stay
    stateless and handle concurrent webhooks safely.
    """
    api = MetaApi(METAAPI_TOKEN)
    try:
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)

        # Deploy / wake the account if it is not already connected
        if account.state not in ('DEPLOYING', 'DEPLOYED'):
            logger.info("Deploying MetaApi account...")
            await account.deploy()

        logger.info("Waiting for broker connection...")
        await account.wait_connected()

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        logger.info(f"Placing {action.upper()} {volume} lot(s) on {symbol} "
                    f"| SL={sl} | TP={tp}")

        kwargs = {}
        if sl is not None:
            kwargs['stop_loss'] = float(sl)
        if tp is not None:
            kwargs['take_profit'] = float(tp)

        if action.lower() == 'buy':
            result = await connection.create_market_buy_order(
                symbol, float(volume), **kwargs,
                options={'comment': 'TV-Bot', 'clientId': 'TV-Webhook-Bot'}
            )
        elif action.lower() == 'sell':
            result = await connection.create_market_sell_order(
                symbol, float(volume), **kwargs,
                options={'comment': 'TV-Bot', 'clientId': 'TV-Webhook-Bot'}
            )
        else:
            raise ValueError(f"Unknown action '{action}'. Must be 'buy' or 'sell'.")

        logger.info(f"Trade result: {result}")
        return result

    finally:
        # Always close the connection to free resources
        try:
            await connection.close()
        except Exception:
            pass


def run_trade(symbol, action, volume, sl, tp):
    """Run the async trade coroutine from synchronous Flask context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            _execute_trade(symbol, action, volume, sl, tp)
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
        "volume":  0.05,           // lot size (optional, default 0.01)
        "sl":      2300.00,        // stop loss price (optional)
        "tp":      2380.00         // take profit price (optional)
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
        sl     = data.get('sl')
        tp     = data.get('tp')

        if not symbol:
            return jsonify({"status": "error", "message": "Missing 'symbol'"}), 400
        if action not in ('buy', 'sell'):
            return jsonify({"status": "error",
                            "message": "'action' must be 'buy' or 'sell'"}), 400
        if volume <= 0:
            return jsonify({"status": "error",
                            "message": "'volume' must be greater than 0"}), 400

        # ── Execute trade ────────────────────────────────────────────────────
        result = run_trade(symbol, action, volume, sl, tp)

        return jsonify({
            "status":      "success",
            "stringCode":  result.get('stringCode'),
            "orderId":     result.get('orderId'),
        }), 200

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "online",
                    "message": "TradingView → MetaApi → MT5 bot is running"}), 200


# ---------------------------------------------------------------------------
# Entry point (development only — use Gunicorn in production)
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
