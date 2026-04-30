# TradingView → MetaApi → MT5 Webhook Bot

A lightweight, self-hosted Python trading bot that runs entirely on a **Linux AWS instance**. It receives webhook alerts from TradingView and executes trades on your MetaTrader 5 account via the [MetaApi](https://metaapi.cloud) cloud service.

```
TradingView Alert
      │  HTTPS POST (JSON)
      ▼
AWS Linux Server  ──  Flask app (this repo)
      │  MetaApi Cloud SDK (Python)
      ▼
MetaApi Cloud  ──  manages the MT5 connection on your behalf
      │
      ▼
MT5 Broker Account  (GO Markets, IC Markets, Pepperstone, etc.)
```

## Features

- Runs 100% on Linux — no Windows machine required
- No third-party bridge subscriptions
- Validates every incoming webhook with a secret passphrase
- Supports `buy` and `sell` market orders with optional Stop Loss and Take Profit
- Structured JSON logging to file and stdout
- Systemd service file included for 24/7 auto-start

---

## Prerequisites

| Requirement | Details |
|---|---|
| AWS EC2 (Linux) | Ubuntu 22.04 LTS recommended |
| Python 3.10+ | Pre-installed on Ubuntu 22.04 |
| MetaApi account | Free tier available at [metaapi.cloud](https://metaapi.cloud) |
| MT5 broker account | GO Markets, IC Markets, Pepperstone, Exness, etc. |
| TradingView account | Essential plan or above (webhooks require a paid plan) |

---

## Installation

### 1. SSH into your AWS instance and clone the repo

```bash
git clone https://github.com/nataliengwh/TV-to-MT5-bot.git
cd TV-to-MT5-bot
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Add your MT5 account to MetaApi

1. Sign up at [https://app.metaapi.cloud](https://app.metaapi.cloud)
2. Go to **Accounts** → **Add Account**
3. Fill in your MT5 broker login, password, and server name (e.g. `GOMarkets-Live`)
4. Select platform **MT5** and click **Deploy**
5. Copy your **Account ID** (shown on the accounts page)
6. Go to **API Tokens** and copy your **API Token**

### 4. Configure environment variables

```bash
cp .env.example .env
nano .env
```

Fill in the three values:

```env
METAAPI_TOKEN=your_metaapi_token_here
METAAPI_ACCOUNT_ID=your_account_id_here
WEBHOOK_SECRET=change_me_to_something_strong
```

### 5. Open port 80 on your AWS Security Group

In the AWS Console → **EC2 → Security Groups → Inbound Rules**, add:

| Type | Protocol | Port | Source |
|---|---|---|---|
| HTTP | TCP | 80 | `52.89.214.238/32` |
| HTTP | TCP | 80 | `34.212.75.30/32` |
| HTTP | TCP | 80 | `54.218.53.128/32` |
| HTTP | TCP | 80 | `52.32.178.7/32` |

These are the four official TradingView webhook IP addresses. Restricting to these IPs prevents anyone else from hitting your endpoint.

### 6. Test the server manually

```bash
# Load env vars and start the server
export $(cat .env | xargs)
python app.py
```

In a second terminal, send a test request:

```bash
curl -X POST http://localhost:80/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "change_me_to_something_strong",
    "symbol": "XAUUSD",
    "action": "buy",
    "volume": 0.01
  }'
```

You should see `{"status": "success", ...}` and a new position in your MT5 account.

### 7. Run as a persistent service (24/7)

```bash
# Copy the systemd unit file
sudo cp tv-mt5-bot.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable tv-mt5-bot
sudo systemctl start tv-mt5-bot

# Check status
sudo systemctl status tv-mt5-bot

# View live logs
sudo journalctl -u tv-mt5-bot -f
```

---

## TradingView Alert Setup

### Webhook URL

In the TradingView alert dialog, check **Webhook URL** and enter:

```
http://YOUR_AWS_PUBLIC_IP/webhook
```

> **Note:** TradingView requires 2-Factor Authentication (2FA) to be enabled on your account before webhooks can be used.

### Message (JSON Payload)

Paste the following into the **Message** field of the alert. Adjust the values to match your strategy.

**BUY signal with SL and TP:**
```json
{
    "secret":  "change_me_to_something_strong",
    "symbol":  "XAUUSD",
    "action":  "buy",
    "volume":  0.05,
    "sl":      3200.00,
    "tp":      3280.00
}
```

**SELL signal without SL/TP:**
```json
{
    "secret":  "change_me_to_something_strong",
    "symbol":  "BTCUSD",
    "action":  "sell",
    "volume":  0.01
}
```

> **Tip:** Check the exact symbol name your broker uses in MT5 (e.g. some brokers use `XAUUSD.` or `BTCUSDm`). The `symbol` field must match exactly.

---

## Payload Reference

| Field | Type | Required | Description |
|---|---|---|---|
| `secret` | string | Yes | Must match `WEBHOOK_SECRET` in your `.env` |
| `symbol` | string | Yes | Instrument name as shown in MT5 Market Watch |
| `action` | string | Yes | `buy` or `sell` |
| `volume` | float | No | Lot size. Defaults to `0.01` |
| `sl` | float | No | Stop Loss price |
| `tp` | float | No | Take Profit price |

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `401 Unauthorized` | The `secret` in your JSON does not match `WEBHOOK_SECRET` in `.env` |
| `Symbol not found` | Check the exact symbol name in your MT5 Market Watch window |
| MetaApi connection timeout | Ensure your account is **Deployed** in the MetaApi dashboard |
| Port 80 not reachable | Check AWS Security Group inbound rules and the OS firewall (`sudo ufw status`) |
| Gunicorn permission denied on port 80 | Run `sudo setcap 'cap_net_bind_service=+ep' /usr/bin/python3` or change the port to 8080 and use a reverse proxy |
