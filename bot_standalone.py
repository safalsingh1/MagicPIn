"""
bot_standalone.py — Standalone compose function (for submission.jsonl generation)
Uses the same composer as the bot server.
"""

import json
import sys
from pathlib import Path
from composer import compose_message


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None) -> dict:
    """
    Deterministic compose function.
    Inputs: dicts loaded from dataset JSON.
    Returns: dict with keys: body, cta, send_as, suppression_key, rationale
    """
    result = compose_message(category, merchant, trigger, customer)
    return {
        "body": result.get("body", ""),
        "cta": result.get("cta", "open_ended"),
        "send_as": result.get("send_as", "vera"),
        "suppression_key": result.get("suppression_key", trigger.get("suppression_key", "")),
        "rationale": result.get("rationale", ""),
        "template_name": result.get("template_name", "vera_generic_v1"),
        "template_params": result.get("template_params", []),
    }


if __name__ == "__main__":
    # Quick sanity test
    import os
    dataset_dir = Path(__file__).parent / "dataset"

    # Load sample data
    cats = {}
    cat_dir = dataset_dir / "categories"
    if cat_dir.exists():
        for f in cat_dir.glob("*.json"):
            d = json.loads(f.read_text(encoding="utf-8"))
            cats[d.get("slug", f.stem)] = d

    merchants_raw = json.loads((dataset_dir / "merchants_seed.json").read_text(encoding="utf-8"))
    triggers_raw = json.loads((dataset_dir / "triggers_seed.json").read_text(encoding="utf-8"))
    customers_raw = json.loads((dataset_dir / "customers_seed.json").read_text(encoding="utf-8"))

    merchants = {m["merchant_id"]: m for m in merchants_raw.get("merchants", [])}
    triggers = {t["id"]: t for t in triggers_raw.get("triggers", [])}
    customers = {c["customer_id"]: c for c in customers_raw.get("customers", [])}

    # Test with first 3 triggers
    for trg_id, trg in list(triggers.items())[:3]:
        mid = trg.get("merchant_id")
        cid = trg.get("customer_id")
        merchant = merchants.get(mid, {})
        cat = cats.get(merchant.get("category_slug", ""), {})
        customer = customers.get(cid) if cid else None

        print(f"\n{'='*60}")
        print(f"Trigger: {trg_id} (kind={trg.get('kind')})")
        print(f"Merchant: {merchant.get('identity', {}).get('name')}")

        result = compose(cat, merchant, trg, customer)
        print(f"Body ({len(result['body'])} chars): {result['body']}")
        print(f"CTA: {result['cta']}")
        print(f"Send as: {result['send_as']}")
        print(f"Rationale: {result['rationale']}")
