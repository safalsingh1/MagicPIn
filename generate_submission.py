"""
generate_submission.py — Generate submission.jsonl from the 30 canonical test pairs
Run: python generate_submission.py
Output: submission.jsonl (30 lines)
"""

import json
import sys
from pathlib import Path
from bot_standalone import compose

# ── Resolve dataset path ──────────────────────────────────────────────────────
SEARCH_PATHS = [
    Path(__file__).parent.parent / "Downloads" / "magicpin-ai-challenge" / "dataset",
    Path(__file__).parent / "dataset",
    Path(__file__).parent.parent / "magicpin-ai-challenge" / "dataset",
]

def find_dataset() -> Path:
    for p in SEARCH_PATHS:
        if p.exists() and (p / "merchants_seed.json").exists():
            return p
    raise FileNotFoundError(f"Dataset not found. Searched: {SEARCH_PATHS}")

def load_dataset(dataset_dir: Path):
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

    # Try loading expanded dataset if available
    expanded = dataset_dir.parent / "expanded"
    if expanded.exists():
        for subdir, store, key in [
            ("merchants", merchants, "merchant_id"),
            ("triggers", triggers, "id"),
            ("customers", customers, "customer_id"),
        ]:
            sub = expanded / subdir
            if sub.exists():
                for f in sub.glob("*.json"):
                    try:
                        d = json.loads(f.read_text(encoding="utf-8"))
                        if key in d:
                            store[d[key]] = d
                    except Exception:
                        pass

    return cats, merchants, triggers, customers


def build_test_pairs(triggers, merchants):
    """
    Build canonical test pairs by selecting one representative trigger
    per trigger kind per merchant, covering all 5 categories.
    Targets 30 pairs.
    """
    pairs = []
    seen_sup_keys = set()
    
    # Priority: go through all triggers, deduplicate by suppression key
    for trg_id, trg in triggers.items():
        sup_key = trg.get("suppression_key", "")
        if sup_key and sup_key in seen_sup_keys:
            continue
        
        mid = trg.get("merchant_id")
        if not mid or mid not in merchants:
            continue
        
        pairs.append({
            "test_id": f"T{len(pairs)+1:02d}",
            "trigger_id": trg_id,
            "merchant_id": mid,
            "customer_id": trg.get("customer_id"),
        })
        
        if sup_key:
            seen_sup_keys.add(sup_key)
        
        if len(pairs) >= 30:
            break
    
    return pairs


def main():
    print("Loading dataset...")
    dataset_dir = find_dataset()
    cats, merchants, triggers, customers = load_dataset(dataset_dir)
    
    print(f"Loaded: {len(cats)} categories, {len(merchants)} merchants, {len(triggers)} triggers, {len(customers)} customers")

    # Try to load pre-generated test pairs
    pairs_file = dataset_dir.parent / "expanded" / "test_pairs.jsonl"
    
    if pairs_file.exists():
        pairs = []
        with open(pairs_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    pairs.append(json.loads(line))
        print(f"Loaded {len(pairs)} canonical test pairs from {pairs_file}")
    else:
        pairs = build_test_pairs(triggers, merchants)
        print(f"Generated {len(pairs)} test pairs from seed data")

    output_path = Path(__file__).parent / "submission.jsonl"
    
    with open(output_path, "w", encoding="utf-8") as out:
        for i, pair in enumerate(pairs[:30]):
            test_id = pair.get("test_id", f"T{i+1:02d}")
            trg_id = pair.get("trigger_id") or pair.get("trigger", {}).get("id", "")
            mid = pair.get("merchant_id") or pair.get("merchant", {}).get("merchant_id", "")
            cid = pair.get("customer_id")
            
            # Resolve objects
            if isinstance(pair.get("trigger"), dict):
                trg = pair["trigger"]
            else:
                trg = triggers.get(trg_id, {})
            
            if isinstance(pair.get("merchant"), dict):
                merchant = pair["merchant"]
            else:
                merchant = merchants.get(mid, {})
            
            if isinstance(pair.get("customer"), dict):
                customer = pair["customer"]
            else:
                customer = customers.get(cid) if cid else None
            
            cat_slug = merchant.get("category_slug", "")
            cat = cats.get(cat_slug, {})
            
            if not cat or not merchant or not trg:
                print(f"  [SKIP] {test_id}: missing data (cat={bool(cat)}, merchant={bool(merchant)}, trg={bool(trg)})")
                continue
            
            print(f"  Composing {test_id}: {trg.get('kind')} for {merchant.get('identity', {}).get('name', mid)}")
            
            try:
                result = compose(cat, merchant, trg, customer)
                
                line = {
                    "test_id": test_id,
                    "merchant_id": mid,
                    "trigger_id": trg_id,
                    "customer_id": cid,
                    "body": result["body"],
                    "cta": result["cta"],
                    "send_as": result["send_as"],
                    "suppression_key": result["suppression_key"],
                    "rationale": result["rationale"],
                }
                out.write(json.dumps(line, ensure_ascii=False) + "\n")
                print(f"    ✓ [{len(result['body'])} chars] {result['body'][:80]}...")
            except Exception as e:
                print(f"    ✗ Error: {e}")
    
    print(f"\nSubmission written to: {output_path}")
    # Count lines
    with open(output_path, encoding="utf-8") as f:
        n = sum(1 for l in f if l.strip())
    print(f"Total lines: {n}")


if __name__ == "__main__":
    main()
