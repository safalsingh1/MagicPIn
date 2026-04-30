"""
Vera Bot — magicpin AI Challenge
=================================
FastAPI bot implementing the 4-context composition framework.
Endpoint: POST /v1/context | POST /v1/tick | POST /v1/reply | GET /v1/healthz | GET /v1/metadata
"""

import os, time, re, json, uuid, traceback
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from composer import compose_message
from reply_handler import handle_reply

app = FastAPI(title="Vera Bot", version="2.1.0")
START_TIME = time.time()

# ── In-memory state ─────────────────────────────────────────────────────────
contexts: dict[tuple[str, str], dict] = {}          # (scope, context_id) -> {version, payload}
conversations: dict[str, dict] = {}                  # conv_id -> {merchant_id, customer_id, turns[], ended, suppressed}
suppressed_keys: set[str] = set()                    # suppression_key -> skip send
ended_conversations: set[str] = set()

# ── Health & Metadata ────────────────────────────────────────────────────────
@app.get("/v1/healthz")
async def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _) in contexts:
        if scope in counts:
            counts[scope] += 1
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": counts
    }


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "ContextCraft AI",
        "team_members": ["Safal Singh"],
        "model": "llama-3.1-8b-instant via Groq",
        "approach": (
            "Stateful 4-context composer (category + merchant + trigger + customer) "
            "with per-trigger-kind prompt dispatch. "
            "Category-specific voice profiles (dental: clinical citation, "
            "pharmacy: regulatory precision, restaurant: operator jargon, "
            "salon: lifestyle warmth, gym: motivational urgency). "
            "Auto-reply detection via 12-pattern regex with graduated response (wait → end). "
            "Commit-intent detection for instant action-mode switch. "
            "Hindi-English code-mix for regional merchants. "
            "Temperature=0 for full determinism. "
            "320-char body enforcement. "
            "Suppression-key dedup across ticks."
        ),
        "contact_email": "safalsingh76@gmail.com",
        "version": "2.1.0",
        "submitted_at": "2026-04-30T14:30:00Z"
    }


# ── Context Push ────────────────────────────────────────────────────────────
class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


@app.post("/v1/context")
async def push_context(body: CtxBody):
    valid_scopes = {"category", "merchant", "customer", "trigger"}
    if body.scope not in valid_scopes:
        return JSONResponse(status_code=400, content={
            "accepted": False,
            "reason": "invalid_scope",
            "details": f"scope must be one of {valid_scopes}"
        })

    key = (body.scope, body.context_id)
    cur = contexts.get(key)

    if cur and cur["version"] > body.version:
        # Stale version — accept gracefully but don't overwrite
        return {
            "accepted": True,
            "ack_id": f"ack_{body.context_id}_v{body.version}_stale_noop",
            "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        }

    # Store (or update) the context
    contexts[key] = {"version": body.version, "payload": body.payload}
    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }


# ── Tick ─────────────────────────────────────────────────────────────────────
class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []


@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    print(f"[TICK] now={body.now}, triggers={body.available_triggers}, contexts_count={len(contexts)}")

    # Sort triggers by urgency descending
    trigger_items = []
    for trg_id in body.available_triggers:
        trg_data = contexts.get(("trigger", trg_id))
        if not trg_data:
            print(f"[TICK] Trigger {trg_id} not found in contexts")
            continue
        trg = trg_data["payload"]
        trigger_items.append((trg_id, trg, trg.get("urgency", 1)))

    trigger_items.sort(key=lambda x: -x[2])

    for trg_id, trg, urgency in trigger_items:
        if len(actions) >= 20:
            break

        # Suppression check
        sup_key = trg.get("suppression_key", "")
        if sup_key and sup_key in suppressed_keys:
            print(f"[TICK] Trigger {trg_id} suppressed (key={sup_key})")
            continue

        # Expiry check
        exp = trg.get("expires_at")
        if exp:
            try:
                exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                now_dt = datetime.fromisoformat(body.now.replace("Z", "+00:00"))
                if now_dt > exp_dt:
                    print(f"[TICK] Trigger {trg_id} expired")
                    continue
            except Exception:
                pass

        merchant_id = trg.get("merchant_id")
        customer_id = trg.get("customer_id")

        # Merchant lookup — required
        merchant_data = contexts.get(("merchant", merchant_id))
        if not merchant_data:
            print(f"[TICK] Merchant {merchant_id} not found for trigger {trg_id}")
            continue
        merchant = merchant_data["payload"]

        # Category lookup — graceful fallback if missing
        category_slug = merchant.get("category_slug", "")
        category_data = contexts.get(("category", category_slug))
        if category_data:
            category = category_data["payload"]
        else:
            print(f"[TICK] Category '{category_slug}' not found, using minimal fallback")
            category = {"slug": category_slug}

        # Customer lookup — optional
        customer = None
        if customer_id:
            cust_data = contexts.get(("customer", customer_id))
            if cust_data:
                customer = cust_data["payload"]

        # Skip if conversation already ended for this trigger
        conv_id = f"conv_{merchant_id}_{trg_id}"
        if conv_id in ended_conversations:
            continue

        # Compose the message
        try:
            result = compose_message(category, merchant, trg, customer)
            print(f"[TICK] Composed for {trg_id}: {result.get('body', '')[:80]}...")
        except Exception as e:
            print(f"[COMPOSER ERROR] {trg_id}: {e}")
            traceback.print_exc()
            continue

        if not result or not result.get("body"):
            print(f"[TICK] Empty result for {trg_id}")
            continue

        body_text = result["body"]
        # Enforce 320 char limit
        if len(body_text) > 320:
            body_text = body_text[:317] + "..."

        send_as = "merchant_on_behalf" if customer_id else "vera"

        action = {
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": send_as,
            "trigger_id": trg_id,
            "template_name": result.get("template_name", f"vera_{trg.get('kind', 'generic')}_v1"),
            "template_params": result.get("template_params", []),
            "body": body_text,
            "cta": result.get("cta", "open_ended"),
            "suppression_key": sup_key,
            "rationale": result.get("rationale", "")
        }

        # Register conversation
        conversations[conv_id] = {
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "turns": [{"from": "vera", "body": body_text}],
            "ended": False,
            "auto_reply_count": 0,
            "trigger_id": trg_id,
            "category_slug": category_slug,
        }

        # Mark suppression
        if sup_key:
            suppressed_keys.add(sup_key)

        actions.append(action)

    print(f"[TICK] Returning {len(actions)} actions")
    return {"actions": actions}


# ── Reply ─────────────────────────────────────────────────────────────────────
class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv_id = body.conversation_id
    message = body.message
    print(f"[REPLY] conv={conv_id}, from={body.from_role}, msg={message[:80]}")

    # Get or create conversation state
    conv = conversations.get(conv_id, {
        "merchant_id": body.merchant_id,
        "customer_id": body.customer_id,
        "turns": [],
        "ended": False,
        "auto_reply_count": 0,
        "trigger_id": None,
        "category_slug": None,
    })

    # If conversation already ended, just return end
    if conv.get("ended"):
        return {"action": "end", "rationale": "Conversation already closed."}

    # Record turn
    conv["turns"].append({"from": body.from_role, "body": message})
    conversations[conv_id] = conv

    merchant_id = conv.get("merchant_id") or body.merchant_id
    customer_id = conv.get("customer_id") or body.customer_id

    # Load contexts
    merchant = contexts.get(("merchant", merchant_id), {}).get("payload", {})
    category_slug = conv.get("category_slug") or merchant.get("category_slug", "")
    category = contexts.get(("category", category_slug), {}).get("payload", {"slug": category_slug})
    customer = None
    if customer_id:
        cust_data = contexts.get(("customer", customer_id))
        if cust_data:
            customer = cust_data["payload"]

    trigger_id = conv.get("trigger_id")
    trigger = contexts.get(("trigger", trigger_id), {}).get("payload", {}) if trigger_id else {}

    try:
        result = handle_reply(
            conversation=conv,
            message=message,
            merchant=merchant,
            category=category,
            trigger=trigger,
            customer=customer,
        )
    except Exception as e:
        print(f"[REPLY ERROR] {e}")
        traceback.print_exc()
        result = {"action": "send", "body": "Got it — let me check and get back to you.", "cta": "open_ended", "rationale": "Fallback reply"}

    # Post-process
    action = result.get("action", "send")
    print(f"[REPLY] action={action}")

    if action == "end":
        conv["ended"] = True
        conversations[conv_id] = conv
        ended_conversations.add(conv_id)
        return {"action": "end", "rationale": result.get("rationale", "Conversation closed.")}

    if action == "wait":
        # Save updated state (auto_reply_count must persist)
        conversations[conv_id] = conv
        return {
            "action": "wait",
            "wait_seconds": result.get("wait_seconds", 3600),
            "rationale": result.get("rationale", "Waiting for merchant response.")
        }

    # Send
    resp_body = result.get("body", "")
    if len(resp_body) > 320:
        resp_body = resp_body[:317] + "..."

    conv["turns"].append({"from": "vera", "body": resp_body})
    conversations[conv_id] = conv

    return {
        "action": "send",
        "body": resp_body,
        "cta": result.get("cta", "open_ended"),
        "rationale": result.get("rationale", "")
    }


# ── Optional teardown ──────────────────────────────────────────────────────
@app.post("/v1/teardown")
async def teardown():
    contexts.clear()
    conversations.clear()
    suppressed_keys.clear()
    ended_conversations.clear()
    return {"status": "wiped"}
