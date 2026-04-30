# TradingView to MT5 Webhook Bot

A lightweight, self-hosted Python trading bot that receives webhook alerts from TradingView and executes trades directly on MetaTrader 5 (MT5).

This bot is designed to run on a Windows VPS alongside your MT5 terminal. It uses Flask to listen for incoming webhooks and the official `MetaTrader5` Python library to place trades.

## Features
*   **Direct Execution:** No third-party bridges or monthly fees.
*   **JSON Payload Parsing:** Extracts Symbol, Action (Buy/Sell), Volume, Stop Loss, and Take Profit from TradingView alerts.
*   **Security:** Uses a secret passphrase to prevent unauthorized trade execution.
*   **Production Ready:** Includes a `waitress` WSGI server script for stable 24/7 operation.

## Prerequisites
1.  A **Windows VPS** (or local Windows machine running 24/7).
2.  **MetaTrader 5** installed and logged into your broker account.
3.  **Python 3.8+** installed on the Windows machine.
4.  A public IP address or a tunneling service (like Ngrok or Cloudflare Tunnels) to expose port 80 to the internet so TradingView can reach it.

## Installation & Setup

### 1. Clone the Repository
Open Command Prompt or PowerShell on your Windows VPS and clone this repository:
```bash
git clone https://github.com/nataliengwh/TV-to-MT5-bot.git
cd TV-to-MT5-bot
```

### 2. Install Dependencies
Install the required Python packages:
```bash
pip install -r requirements.txt
```

### 3. Configure the Bot
Open `config.py` in a text editor and update the following variables with your actual details:
*   `MT5_LOGIN`: Your MT5 account number.
*   `MT5_PASSWORD`: Your MT5 password.
*   `MT5_SERVER`: Your broker's server name (e.g., 'ICMarkets-MT5-Live01').
*   `MT5_PATH`: The absolute path to your `terminal64.exe` file.
*   `WEBHOOK_PASSPHRASE`: Create a strong, unique password. You will use this in your TradingView alerts.

### 4. Enable Algo Trading in MT5
1. Open your MT5 terminal.
2. Go to **Tools** -> **Options** -> **Expert Advisors**.
3. Check the box for **"Allow algorithmic trading"**.
4. Click **OK**.

### 5. Run the Server
To start the bot in production mode, run:
```bash
python run_server.py
```
You should see logs indicating that it successfully connected to MT5 and is listening on port 80.

## TradingView Alert Configuration

When creating an alert in TradingView, you must configure it to send a Webhook URL and a specific JSON payload.

### Webhook URL
In the TradingView alert settings, check the "Webhook URL" box and enter your VPS's public IP address or tunnel URL:
`http://YOUR_VPS_IP/webhook`

### Message (JSON Payload)
In the "Message" box of the TradingView alert, paste the following JSON structure. 

**Example for a BUY order on Gold (XAUUSD):**
```json
{
    "passphrase": "your_secret_passphrase_here",
    "symbol": "XAUUSD",
    "action": "buy",
    "volume": 0.05,
    "sl": 2300.50,
    "tp": 2350.00
}
```

**Example for a SELL order on Bitcoin (BTCUSD) without SL/TP:**
```json
{
    "passphrase": "your_secret_passphrase_here",
    "symbol": "BTCUSD",
    "action": "sell",
    "volume": 0.1
}
```

*Note: Ensure the `symbol` exactly matches the symbol name used by your specific broker in MT5 (e.g., some brokers use `XAUUSD.a` or `BTCUSDm`).*

## Troubleshooting
*   **MT5 Connection Failed:** Double-check your login credentials and the `MT5_PATH` in `config.py`. Ensure MT5 is actually installed at that location.
*   **Symbol Not Found:** Check exactly how the symbol is spelled in your MT5 "Market Watch" window. It is case-sensitive.
*   **Unauthorized:** Ensure the `passphrase` in your TradingView JSON exactly matches the `WEBHOOK_PASSPHRASE` in `config.py`.
*   **TradingView Webhook Not Reaching Server:** Ensure port 80 is open on your Windows Firewall. If you are behind a NAT router, you may need to use a service like Ngrok or Cloudflare Tunnels to expose the local port to the internet.
