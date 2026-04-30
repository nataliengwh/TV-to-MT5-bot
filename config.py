import os

# MetaTrader 5 Configuration
# Replace these with your actual MT5 account details
MT5_LOGIN = int(os.environ.get('MT5_LOGIN', 12345678))
MT5_PASSWORD = os.environ.get('MT5_PASSWORD', 'your_mt5_password')
MT5_SERVER = os.environ.get('MT5_SERVER', 'YourBroker-Server')

# Path to your MT5 terminal executable (terminal64.exe)
# Example: 'C:\\Program Files\\MetaTrader 5\\terminal64.exe'
MT5_PATH = os.environ.get('MT5_PATH', 'C:\\Program Files\\MetaTrader 5\\terminal64.exe')

# Webhook Security
# This passphrase must be included in the TradingView JSON payload
# to prevent unauthorized trades.
WEBHOOK_PASSPHRASE = os.environ.get('WEBHOOK_PASSPHRASE', 'your_secret_passphrase_here')
