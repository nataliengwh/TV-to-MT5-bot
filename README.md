# TradingView → MetaApi → MT5 Webhook Bot (Dual-Trade Strategy)

A lightweight, self-hosted Python trading bot that runs entirely on a **Linux AWS instance**. It receives webhook alerts from TradingView and executes trades on your MetaTrader 5 account via the [MetaApi](https://metaapi.cloud) cloud service.

## Strategy Logic

This bot is hardcoded to execute a specific **Dual-Trade Strategy** for Gold (XAUUSD) and Bitcoin (BTCUSD).

When a signal is received, the bot simultaneously opens **two trades** with the same volume, but different Take Profit (TP) targets:
*   **Trade 1:** Targets TP1.
*   **Trade 2:** Targets TP2.
*   Both trades share the same initial Stop Loss (SL).
*   **Breakeven Trailing:** The bot runs a background monitor. As soon as Trade 1 hits TP1 and closes, the bot automatically moves the Stop Loss of Trade 2 to the entry price (breakeven).

### Hardcoded Offsets
The offsets are defined in absolute price terms in `app.py`:
*   **XAUUSD:** TP1 = $5, TP2 = $10, SL = $10
*   **BTCUSD:** TP1 = $40, TP2 = $80, SL = $80

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

### 6. Run as a persistent service (24/7)

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

Because the bot calculates the TP and SL automatically based on the current price, your TradingView alert **must send the current price** using TradingView's dynamic variables (`{{close}}`).

### Webhook URL

In the TradingView alert dialog, check **Webhook URL** and enter:

```
http://YOUR_AWS_PUBLIC_IP/webhook
```

### Message (JSON Payload)

Paste the following into the **Message** field of the alert. 

**For a BUY signal:**
```json
{
    "secret":  "change_me_to_something_strong",
    "symbol":  "XAUUSD",
    "action":  "buy",
    "volume":  0.05,
    "price":   {{close}}
}
```

**For a SELL signal:**
```json
{
    "secret":  "change_me_to_something_strong",
    "symbol":  "BTCUSD",
    "action":  "sell",
    "volume":  0.01,
    "price":   {{close}}
}
```

> **Note:** `{{close}}` is a special TradingView placeholder. When the alert triggers, TradingView will automatically replace `{{close}}` with the actual numerical price (e.g., `2345.50`). Do not put quotes around `{{close}}`.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `401 Unauthorized` | The `secret` in your JSON does not match `WEBHOOK_SECRET` in `.env` |
| `Symbol not found` | Check the exact symbol name in your MT5 Market Watch window |
| MetaApi connection timeout | Ensure your account is **Deployed** in the MetaApi dashboard |
| Port 80 not reachable | Check AWS Security Group inbound rules and the OS firewall (`sudo ufw status`) |
