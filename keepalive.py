"""
keepalive.py — Pings the Railway bot every 10 minutes to prevent cold-start sleep.
Run this locally while the judge is evaluating: python keepalive.py
"""
import time
import urllib.request as r
import urllib.error as ue
from datetime import datetime

BOT_URL = "https://magicpin-production.up.railway.app"
INTERVAL_SECONDS = 600  # 10 minutes


def ping():
    try:
        resp = r.urlopen(BOT_URL + "/v1/healthz", timeout=15)
        data = resp.read().decode()
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] OK: {data[:60]}")
    except ue.URLError as e:
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] WARN: {e.reason}")
    except Exception as e:
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] ERROR: {e}")


print(f"Keepalive started — pinging every {INTERVAL_SECONDS // 60} minutes.")
print(f"Target: {BOT_URL}/v1/healthz")
print("Press Ctrl+C to stop.\n")

while True:
    ping()
    time.sleep(INTERVAL_SECONDS)
