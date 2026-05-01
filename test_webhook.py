#!/usr/bin/env python3
"""
test_webhook.py
---------------
Mimics a TradingView webhook POST request to your local or remote bot.

Usage:
    # Test against local bot (same machine):
    python3 test_webhook.py

    # Test against your live AWS bot:
    python3 test_webhook.py --host http://3.25.226.172

    # Custom symbol, action, volume, price:
    python3 test_webhook.py --symbol BTCUSD --action buy --volume 0.01 --price 76173.00

    # Run all built-in test cases:
    python3 test_webhook.py --all-tests
"""

import argparse
import json
import requests
from datetime import datetime

# ── Default config ────────────────────────────────────────────────────────────
DEFAULT_HOST   = "http://localhost:8080"
DEFAULT_SECRET = "tvtvtvsecret!@#$"   # must match WEBHOOK_SECRET in your .env

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def colour(text, c):
    return f"{c}{text}{RESET}"

# ── Core send function ────────────────────────────────────────────────────────
def send_webhook(host, payload, label=""):
    url = f"{host}/webhook"
    print(f"\n{'='*60}")
    print(f"  {colour('TEST', YELLOW)}: {label or 'Custom signal'}")
    print(f"  URL    : {url}")
    print(f"  Payload: {json.dumps(payload, indent=4)}")
    print(f"  Sent at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    try:
        resp = requests.post(url, json=payload, timeout=120)
        body = resp.json()
        status = body.get("status", "unknown")

        if status == "success":
            print(colour(f"  RESULT : SUCCESS (HTTP {resp.status_code})", GREEN))
            print(f"  Trade 1: {body.get('trade1')}")
            print(f"  Trade 2: {body.get('trade2')}")
        elif status == "blocked":
            print(colour(f"  RESULT : BLOCKED (HTTP {resp.status_code})", YELLOW))
            print(f"  Reason : {body.get('message')}")
        else:
            print(colour(f"  RESULT : ERROR (HTTP {resp.status_code})", RED))
            print(f"  Message: {body.get('message')}")

        return resp.status_code, body

    except requests.exceptions.ConnectionError:
        print(colour(f"  RESULT : CONNECTION REFUSED — is the bot running on {host}?", RED))
        return None, None
    except requests.exceptions.Timeout:
        print(colour("  RESULT : TIMEOUT — MetaApi connection may be slow, check the bot log", YELLOW))
        return None, None
    except Exception as e:
        print(colour(f"  RESULT : UNEXPECTED ERROR — {e}", RED))
        return None, None

# ── Health check ──────────────────────────────────────────────────────────────
def check_health(host):
    try:
        resp = requests.get(host, timeout=5)
        if resp.status_code == 200:
            print(colour(f"  Health check PASSED — bot is reachable at {host}", GREEN))
        else:
            print(colour(f"  Health check WARNING — HTTP {resp.status_code}", YELLOW))
    except Exception:
        print(colour(f"  Health check FAILED — bot is not reachable at {host}", RED))

# ── Built-in test suite ───────────────────────────────────────────────────────
def run_all_tests(host, secret):
    print(f"\n{colour('Running full test suite', YELLOW)}")
    check_health(host)

    tests = [
        {
            "label": "XAUUSD SELL — normal signal",
            "payload": {
                "secret": secret,
                "symbol": "XAUUSD",
                "action": "sell",
                "volume": 0.01,
                "price": 3300.00
            }
        },
        {
            "label": "XAUUSD BUY — normal signal",
            "payload": {
                "secret": secret,
                "symbol": "XAUUSD",
                "action": "buy",
                "volume": 0.01,
                "price": 3300.00
            }
        },
        {
            "label": "BTCUSD SELL — normal signal",
            "payload": {
                "secret": secret,
                "symbol": "BTCUSD",
                "action": "sell",
                "volume": 0.01,
                "price": 95000.00
            }
        },
        {
            "label": "BTCUSD BUY — normal signal",
            "payload": {
                "secret": secret,
                "symbol": "BTCUSD",
                "action": "buy",
                "volume": 0.01,
                "price": 95000.00
            }
        },
        {
            "label": "Wrong secret — should return 401",
            "payload": {
                "secret": "wrong_secret",
                "symbol": "XAUUSD",
                "action": "buy",
                "volume": 0.01,
                "price": 3300.00
            }
        },
        {
            "label": "Missing price — should return 400",
            "payload": {
                "secret": secret,
                "symbol": "XAUUSD",
                "action": "buy",
                "volume": 0.01
            }
        },
        {
            "label": "Invalid action — should return 400",
            "payload": {
                "secret": secret,
                "symbol": "XAUUSD",
                "action": "hold",
                "volume": 0.01,
                "price": 3300.00
            }
        },
    ]

    for t in tests:
        send_webhook(host, t["payload"], label=t["label"])

    print(f"\n{colour('Test suite complete.', GREEN)}\n")

# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradingView webhook simulator")
    parser.add_argument("--host",       default=DEFAULT_HOST,   help="Bot base URL (default: http://localhost:8080)")
    parser.add_argument("--secret",     default=DEFAULT_SECRET, help="Webhook secret")
    parser.add_argument("--symbol",     default="XAUUSD",       help="Symbol (XAUUSD or BTCUSD)")
    parser.add_argument("--action",     default="sell",         help="buy or sell")
    parser.add_argument("--volume",     default=0.01, type=float, help="Lot size")
    parser.add_argument("--price",      default=3300.00, type=float, help="Current price")
    parser.add_argument("--all-tests",  action="store_true",    help="Run the full built-in test suite")
    args = parser.parse_args()

    if args.all_tests:
        run_all_tests(args.host, args.secret)
    else:
        check_health(args.host)
        payload = {
            "secret": args.secret,
            "symbol": args.symbol,
            "action": args.action,
            "volume": args.volume,
            "price":  args.price
        }
        send_webhook(args.host, payload, label=f"{args.symbol} {args.action.upper()} @ {args.price}")
