import logging
from waitress import serve
from app import app, init_mt5

if __name__ == '__main__':
    # Initialize MT5 before starting the server
    logging.info("Starting TradingView to MT5 Webhook Bot...")
    init_mt5()
    
    # Run the production WSGI server
    logging.info("Server listening on port 80. Waiting for webhooks...")
    serve(app, host='0.0.0.0', port=80)
