"""
reply_handler.py — Vera multi-turn conversation handler (competition-grade)
===========================================================================
Handles: auto-reply detection, intent commit → action mode,
rejection/hostility → graceful end, out-of-scope → redirect,
and LLM-powered normal conversational flow.
"""

import re
import os
import json
from groq import Groq

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
MODEL = "llama-3.1-8b-instant"

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


# ── Auto-reply patterns ────────────────────────────────────────────────────────
AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"our team will (respond|get back|reply|contact)",
    r"automated (message|assistant|response|reply)",
    r"i am (an |a )?automated",
    r"this is an? auto.?reply",
    r"we (have received|received) your (message|query|request)",
    r"will be in touch shortly",
    r"aapki jaankari ke liye.*shukriya",
    r"bahut.?bahut shukriya.*team.*pahuncha",
    r"main.*automated.*hoon",
    r"hum aapke message.*mil gaya",
    r"your (ticket|request|case) (has been|is) (created|raised|logged)",
    r"reference (number|id|no)\s*[:#]?\s*\d+",
]

# ── Commitment patterns (merchant says 'let's do it') ─────────────────────────
COMMIT_PATTERNS = [
    r"\blet'?s do it\b",
    r"\bgo ahead\b",
    r"\byes\b.*\bproceed\b",
    r"\bproceed\b",
    r"\bconfirm\b",
    r"\bdo it\b",
    r"\byes please\b",
    r"\byes,? go\b",
    r"\bok go\b",
    r"\bwhat'?s next\b",
    r"\bchalo\b",
    r"\bhaan\b.*\bkaro\b",
    r"\byes,? start\b",
    r"\byes,? send\b",
    r"\byes,? draft\b",
    r"\bsend it\b",
    r"\bstart (it|now)\b",
    r"\bokay,? (do it|go|start|proceed)\b",
    r"\bthik hai\b",
    r"\bkaro\b",
]

# ── Rejection/hostility patterns ───────────────────────────────────────────────
REJECT_PATTERNS = [
    r"\bnot interested\b",
    r"\bstop (messaging|sending|contacting|bothering)\b",
    r"\bdon'?t (message|contact|send|bother)\b",
    r"\bno thanks?\b",
    r"\bleave me alone\b",
    r"\bspam\b",
    r"\buseless\b",
    r"\bbothering me\b",
    r"\bblock\b",
    r"\bunsubscribe\b",
    r"\bopt.?out\b",
    r"\bbanda kar\b",
    r"\bnahi chahiye\b",
    r"\bmat bhejo\b",
    r"\bbas karo\b",
]

# ── Out-of-scope patterns ──────────────────────────────────────────────────────
OUT_OF_SCOPE_PATTERNS = [
    r"\bgst (filing|return|advice|help)\b",
    r"\bincome tax\b",
    r"\btax filing\b",
    r"\blegal (advice|help|issue)\b",
    r"\bloan (apply|application|advice)\b",
    r"\binsurance (claim|advice|policy)\b",
    r"\bca (help|advice)\b",
    r"\baccountant\b",
    r"\bstock market\b",
    r"\bproperty (buy|sell|advice)\b",
    r"\breal estate\b",
]


def _is_auto_reply(message: str) -> bool:
    msg = message.lower()
    for p in AUTO_REPLY_PATTERNS:
        if re.search(p, msg, re.IGNORECASE):
            return True
    return False


def _is_commit(message: str) -> bool:
    msg = message.lower()
    for p in COMMIT_PATTERNS:
        if re.search(p, msg, re.IGNORECASE):
            return True
    return False


def _is_reject(message: str) -> bool:
    msg = message.lower()
    for p in REJECT_PATTERNS:
        if re.search(p, msg, re.IGNORECASE):
            return True
    return False


def _is_out_of_scope(message: str) -> bool:
    msg = message.lower()
    for p in OUT_OF_SCOPE_PATTERNS:
        if re.search(p, msg, re.IGNORECASE):
            return True
    return False


def handle_reply(
    conversation: dict,
    message: str,
    merchant: dict,
    category: dict,
    trigger: dict,
    customer,
) -> dict:
    """
    Main entry point. Returns: {action, body?, cta?, rationale, wait_seconds?}
    """
    turns = conversation.get("turns", [])
    auto_count = conversation.get("auto_reply_count", 0)

    # ── 1. Rejection / hostility → END immediately ─────────────────────────────
    if _is_reject(message):
        return {
            "action": "end",
            "rationale": (
                "Merchant explicitly opted out or expressed frustration. "
                "Closing conversation and suppressing this merchant for 30 days."
            )
        }

    # ── 2. Auto-reply detection ────────────────────────────────────────────────
    if _is_auto_reply(message):
        auto_count += 1
        conversation["auto_reply_count"] = auto_count

        if auto_count == 1:
            owner = merchant.get("identity", {}).get("owner_first_name", "there")
            return {
                "action": "wait",
                "wait_seconds": 14400,   # 4 hours — owner not at phone
                "rationale": (
                    "Detected auto-reply (canned phrasing). Waiting 4 hours for owner to become available."
                )
            }
        else:
            # Second+ auto-reply: end conversation cleanly
            return {
                "action": "end",
                "rationale": (
                    "Auto-reply detected again — owner is not engaging. Closing conversation."
                )
            }

    # ── 3. Commitment intent → action mode ────────────────────────────────────
    if _is_commit(message):
        return _handle_commit(merchant, category, trigger, customer, turns)

    # ── 4. Out-of-scope → polite redirect ────────────────────────────────────
    if _is_out_of_scope(message):
        kind = trigger.get("kind", "update")
        last_vera = next(
            (t["body"] for t in reversed(turns) if t.get("from") == "vera"), ""
        )
        topic_hint = last_vera[:60] if last_vera else "what we were discussing"
        return {
            "action": "send",
            "body": (
                f"That's a bit outside what I can help with — best to check with "
                f"a specialist for that. Coming back to: {topic_hint}... want to proceed?"
            )[:320],
            "cta": "binary_yes_no",
            "rationale": "Out-of-scope ask politely declined. Redirecting to original trigger topic."
        }

    # ── 5. Normal multi-turn → LLM ───────────────────────────────────────────
    return _llm_reply(conversation, message, merchant, category, trigger, customer)


def _handle_commit(merchant, category, trigger, customer, turns) -> dict:
    """Merchant said 'let's do it' → switch to action mode immediately."""
    identity = merchant.get("identity", {})
    name = identity.get("owner_first_name") or identity.get("name", "there")
    kind = trigger.get("kind", "")
    offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]
    cust_agg = merchant.get("customer_aggregate", {})

    if kind == "research_digest":
        high_risk = cust_agg.get("high_risk_adult_count") or cust_agg.get("active_count", "")
        for_str = f" for your {high_risk} high-risk patients" if high_risk else ""
        body = (
            f"On it{for_str}! Pulling the abstract now + drafting a patient-ed WhatsApp "
            f"you can share. Ready in ~2 min. Reply CONFIRM to schedule a GBP post too."
        )
        cta = "binary_confirm_cancel"

    elif kind in ("recall_due", "trial_followup"):
        body = (
            f"Sending now! Draft message prepared. "
            f"Reply CONFIRM and I'll dispatch it from your number."
        )
        cta = "binary_confirm_cancel"

    elif kind == "chronic_refill_due":
        body = (
            f"Dispatching the refill reminder now. "
            f"Reply CONFIRM to send it, or tell me if any dosage changed."
        )
        cta = "binary_confirm_cancel"

    elif kind in ("perf_dip", "seasonal_perf_dip"):
        body = (
            f"Great call, {name}! Drafting the retention campaign now — "
            f"I'll show you the copy before anything goes out. 60 seconds."
        )
        cta = "open_ended"

    elif kind == "active_planning_intent":
        body = (
            f"Perfect, {name}! Finalizing the draft — "
            f"I'll share the full plan in a moment. Reply CONFIRM to proceed."
        )
        cta = "binary_confirm_cancel"

    elif kind in ("festival_upcoming", "ipl_match_today"):
        offer_str = f" using your '{offers[0]}'" if offers else ""
        body = (
            f"On it{offer_str}! Drafting the campaign copy + banner now. "
            f"Live in 10 min. Reply CONFIRM to launch."
        )
        cta = "binary_confirm_cancel"

    elif kind == "supply_alert":
        affected = cust_agg.get("chronic_rx_count") or cust_agg.get("active_count", "some")
        body = (
            f"Pulling the affected customer list now ({affected} patients). "
            f"Drafting their WhatsApp note + replacement workflow — ready in ~2 min. Reply CONFIRM."
        )
        cta = "binary_confirm_cancel"

    elif kind == "renewal_due":
        body = (
            f"Great, {name}! Sending you the renewal link now. "
            f"Your views and leads stay protected. Reply CONFIRM to lock in your plan."
        )
        cta = "binary_confirm_cancel"

    elif kind in ("winback_eligible", "customer_lapsed_hard"):
        body = (
            f"On it! Drafting the win-back message now. "
            f"Reply CONFIRM and I'll send it from your number."
        )
        cta = "binary_confirm_cancel"

    else:
        body = (
            f"Great, {name}! Starting now — I'll have the draft ready for your review "
            f"in about 2 minutes. Reply CONFIRM to proceed."
        )
        cta = "binary_confirm_cancel"

    if len(body) > 320:
        body = body[:317] + "..."

    return {
        "action": "send",
        "body": body,
        "cta": cta,
        "rationale": (
            f"Merchant committed ('let's do it'). Switched to action mode immediately. "
            f"Trigger kind: {kind}."
        )
    }


def _llm_reply(conversation, message, merchant, category, trigger, customer) -> dict:
    """LLM-powered contextual reply for normal multi-turn flow."""
    turns = conversation.get("turns", [])
    identity = merchant.get("identity", {})

    history = []
    for t in turns[-6:]:
        role = "Vera" if t.get("from") == "vera" else "Merchant"
        history.append(f"{role}: {t.get('body', '')}")
    history.append(f"Merchant: {message}")
    history_str = "\n".join(history)

    active_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]
    cust_agg = merchant.get("customer_aggregate", {})

    system = f"""You are Vera, magicpin's AI assistant. Active conversation with merchant.
Category: {category.get('slug', 'general')}
Merchant: {identity.get('name')} — {identity.get('locality')}, {identity.get('city')}
Owner first name: {identity.get('owner_first_name')}
Languages: {identity.get('languages', [])}
Active offers: {active_offers}
Customer aggregate: {json.dumps(cust_agg)}
Trigger: kind={trigger.get('kind')}, payload={json.dumps(trigger.get('payload', {}))}

RULES:
- ≤150 chars for conversational replies (WhatsApp-natural)
- No URLs. One CTA. Match their language (Hindi-English mix if they're using it).
- If they asked a question, answer it directly.
- If they gave new info, use it to propose a concrete next step.
- DO NOT ask another qualifying question if they already said yes or gave info.
- Be warm and collegial, not corporate.
- Never fabricate offers or data not in the context above.

JSON only: {{"action": "send", "body": "<reply ≤150 chars>", "cta": "<open_ended|binary_yes_no|binary_confirm_cancel|none>", "rationale": "<why>"}}"""

    user = f"Conversation:\n{history_str}\n\nCompose Vera's next reply. ≤150 chars. JSON only."

    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        result = _parse_json(raw)
        if result and result.get("body"):
            body = re.sub(r'https?://\S+', '', result["body"]).strip()
            result["body"] = body[:320] if len(body) > 320 else body
            return result
    except Exception as e:
        print(f"[LLM REPLY ERROR] {e}")

    # Fallback
    owner = identity.get("owner_first_name", "there")
    return {
        "action": "send",
        "body": f"Got it, {owner}! Working on it — I'll have an update for you shortly.",
        "cta": "open_ended",
        "rationale": "LLM reply fallback."
    }


def _parse_json(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        pass
    raw_clean = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.MULTILINE)
    raw_clean = re.sub(r'\s*```$', '', raw_clean.strip(), flags=re.MULTILINE)
    try:
        return json.loads(raw_clean)
    except Exception:
        pass
    match = re.search(r'\{[\s\S]*\}', raw)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None
