import json
import logging
from flask import Flask, request, jsonify
import MetaTrader5 as mt5
from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH, WEBHOOK_PASSPHRASE

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)

app = Flask(__name__)

def init_mt5():
    """Initialize connection to MetaTrader 5."""
    logging.info("Initializing MT5 connection...")
    
    # Initialize MT5
    if not mt5.initialize(path=MT5_PATH):
        logging.error(f"MT5 initialization failed, error code: {mt5.last_error()}")
        return False
        
    # Login to account
    authorized = mt5.login(
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER
    )
    
    if authorized:
        logging.info(f"Successfully connected to MT5 account: {MT5_LOGIN}")
        return True
    else:
        logging.error(f"Failed to connect to MT5 account: {MT5_LOGIN}, error code: {mt5.last_error()}")
        return False

def execute_trade(symbol, action, volume, sl=None, tp=None):
    """Execute a trade on MT5."""
    # Ensure MT5 is connected
    if not init_mt5():
        return False, "MT5 connection failed"
        
    # Prepare symbol
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        logging.error(f"Symbol {symbol} not found")
        return False, f"Symbol {symbol} not found"
        
    # Ensure symbol is visible in Market Watch
    if not symbol_info.visible:
        logging.info(f"Symbol {symbol} is not visible, trying to select it...")
        if not mt5.symbol_select(symbol, True):
            logging.error(f"Failed to select symbol {symbol}")
            return False, f"Failed to select symbol {symbol}"
            
    # Determine order type and price
    if action.lower() == 'buy':
        order_type = mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(symbol).ask
    elif action.lower() == 'sell':
        order_type = mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(symbol).bid
    else:
        logging.error(f"Invalid action: {action}")
        return False, f"Invalid action: {action}"
        
    # Prepare order request
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": 234000,
        "comment": "TradingView Webhook Bot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    # Add Stop Loss and Take Profit if provided
    if sl is not None:
        request["sl"] = float(sl)
    if tp is not None:
        request["tp"] = float(tp)
        
    # Send order to MT5
    logging.info(f"Sending order: {request}")
    result = mt5.order_send(request)
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logging.error(f"Order failed, retcode: {result.retcode}, comment: {result.comment}")
        return False, f"Order failed: {result.comment}"
        
    logging.info(f"Order successfully placed! Ticket: {result.order}")
    return True, f"Order placed successfully. Ticket: {result.order}"

@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint to receive TradingView webhooks."""
    try:
        # Parse JSON payload
        data = request.get_json()
        logging.info(f"Received webhook payload: {data}")
        
        # Verify passphrase for security
        if data.get('passphrase') != WEBHOOK_PASSPHRASE:
            logging.warning("Unauthorized webhook attempt: Invalid passphrase")
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
            
        # Extract trade parameters
        symbol = data.get('symbol')
        action = data.get('action')
        volume = data.get('volume', 0.01) # Default to 0.01 lots if not specified
        sl = data.get('sl')
        tp = data.get('tp')
        
        if not symbol or not action:
            logging.error("Missing required parameters (symbol or action)")
            return jsonify({"status": "error", "message": "Missing required parameters"}), 400
            
        # Execute trade
        success, message = execute_trade(symbol, action, volume, sl, tp)
        
        if success:
            return jsonify({"status": "success", "message": message}), 200
        else:
            return jsonify({"status": "error", "message": message}), 500
            
    except Exception as e:
        logging.error(f"Error processing webhook: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/', methods=['GET'])
def health_check():
    """Simple health check endpoint."""
    return jsonify({"status": "online", "message": "TradingView to MT5 Webhook Bot is running"}), 200

if __name__ == '__main__':
    # Initialize MT5 on startup
    init_mt5()
    
    # Run Flask app
    # Note: In production, use a WSGI server like Waitress or Gunicorn
    logging.info("Starting Flask webhook server on port 80...")
    app.run(host='0.0.0.0', port=80)
