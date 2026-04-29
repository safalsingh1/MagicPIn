import requests
import json
import uuid
import time
from datetime import datetime, timezone

BOT_URL = "https://magicpin-production.up.railway.app"

def run_query():
    # Use epoch seconds as version so it always increments
    ver = int(time.time())
    unique = uuid.uuid4().hex[:8]

    # 1. Push Category Context
    r = requests.post(f"{BOT_URL}/v1/context", json={
        "version": ver,
        "scope": "category",
        "context_id": "gyms",
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "slug": "gyms",
            "peer_stats": {"avg_rating": 4.5, "avg_ctr": 2.1}
        }
    })
    print(f"Category push: {r.status_code}")

    # 2. Push Merchant Context
    r = requests.post(f"{BOT_URL}/v1/context", json={
        "version": ver,
        "scope": "merchant",
        "context_id": "test_merchant_001",
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "identity": {
                "name": "PowerHouse Fitness",
                "owner_first_name": "Karthik",
                "city": "Bangalore"
            },
            "category_slug": "gyms",
            "offers": [{"title": "Monthly Sub @ ₹999", "status": "active"}],
            "customer_aggregate": {"active_count": 245, "lapsed_count": 10}
        }
    })
    print(f"Merchant push: {r.status_code}")

    # 3. Push Trigger Context (unique suppression key + unique trigger ID each run)
    trg_id = f"trg_test_{unique}"
    r = requests.post(f"{BOT_URL}/v1/context", json={
        "version": ver,
        "scope": "trigger",
        "context_id": trg_id,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "id": trg_id,
            "merchant_id": "test_merchant_001",
            "kind": "perf_dip",
            "urgency": 5,
            "suppression_key": f"test_sup_{unique}",
            "payload": {
                "views_delta_pct": -30,
                "reason": "seasonal drop"
            }
        }
    })
    print(f"Trigger push: {r.status_code}")

    # 4. Trigger the tick
    print("\nGenerating message... (3-5 seconds)")
    tick_req = requests.post(f"{BOT_URL}/v1/tick", json={
        "now": datetime.now(timezone.utc).isoformat(),
        "available_triggers": [trg_id]
    })

    if tick_req.status_code == 200:
        resp = tick_req.json()
        if resp.get("actions"):
            action = resp["actions"][0]
            print(f"\n{'='*60}")
            print(f"🤖 VERA says:")
            print(f"{'='*60}")
            print(f"Body ({len(action['body'])} chars):")
            print(f"  {action['body']}")
            print(f"CTA: {action['cta']}")
            print(f"Send as: {action['send_as']}")
            print(f"Rationale: {action['rationale']}")
        else:
            print("No actions generated.")
            print("Full response:", json.dumps(resp, indent=2))
    else:
        print(f"Error {tick_req.status_code}: {tick_req.text}")

if __name__ == "__main__":
    run_query()
