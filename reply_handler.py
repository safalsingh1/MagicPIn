"""
Deterministic Vera reply handler.

Replay scoring punishes role drift: a customer reply must not be answered as if
the customer were the merchant. This module routes STOP, auto-replies, customer
messages, merchant commits, and trigger-specific follow-ups before any generic
fallback can run.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


AUTO_REPLY_PATTERNS = [
    r"thank you for (contacting|reaching out|your message|writing|messaging)",
    r"thanks for (contacting|reaching out|your message|writing|messaging)",
    r"our team will (respond|get back|reply|contact|reach out|revert)",
    r"we('ll| will) (get back|respond|reply|reach out|revert)",
    r"automated (message|assistant|response|reply|system)",
    r"auto.?reply|auto.?response|auto.?generated",
    r"we (have received|received|got) your (message|query|request|inquiry|mail|email)",
    r"will be in touch (shortly|soon)",
    r"out of office|currently unavailable|business hours|working hours",
    r"your (ticket|request|case|inquiry|query) (has been|is|was) (created|raised|logged|received|noted|registered)",
    r"reference (number|id|no)\s*[:#]?\s*\d+",
    r"we appreciate your (patience|message|inquiry)",
    r"our (representative|agent|executive|team member) will",
    r"aapki jaankari ke liye.*shukriya",
    r"bahut.?bahut shukriya.*team.*pahuncha",
    r"main.*automated.*hoon",
    r"hum.*message.*mil gaya",
    r"hum jaldi.*jawab denge",
    r"dhanyavaad.*sampark",
    r"kripya pratiksha karein",
]

STOP_PATTERNS = [
    r"^\s*stop\s*$",
    r"^\s*end\s*$",
    r"^\s*cancel\s*$",
    r"^\s*unsubscribe\s*$",
    r"^\s*opt.?out\s*$",
    r"\bstop\b.*\b(message|sending|contact|text|spam)\b",
]

COMMIT_PATTERNS = [
    r"\blet'?s do it\b",
    r"\bgo ahead\b",
    r"\byes\b.*\b(proceed|start|send|draft|do)\b",
    r"\b(proceed|confirm|do it|send it)\b",
    r"\byes please\b",
    r"^\s*(yes|ok|okay|sure|haan)\s*$",
    r"\bchalo\b|\bkaro\b|\bthik hai\b",
]

REJECT_PATTERNS = [
    r"\bnot interested\b",
    r"\bdon'?t (message|contact|send|bother|text|ping)\b",
    r"\bno thanks?\b",
    r"\bleave me alone\b",
    r"\bspam\b",
    r"\buseless\b",
    r"\bbothering me\b",
    r"\bblock\b",
    r"\bunsubscribe\b",
    r"\bopt.?out\b",
    r"\bnahi chahiye\b",
    r"\bmat bhejo\b",
    r"\bbas karo\b",
    r"\bwaste of time\b",
    r"\bgo away\b",
    r"\bfuck off\b",
    r"\bshut up\b",
    r"\bnonsense\b",
    r"\bscam\b|\bfraud\b",
]

OUT_OF_SCOPE_PATTERNS = [
    r"\bgst (filing|return|advice|help)\b",
    r"\bincome tax\b",
    r"\btax filing\b",
    r"\blegal (advice|help|issue)\b",
    r"\bloan (apply|application|advice)\b",
    r"\binsurance (claim|advice|policy)\b",
    r"\bca (help|advice)\b",
    r"\bstock market\b",
    r"\breal estate\b",
]


def handle_reply(
    conversation: dict,
    message: str,
    merchant: dict,
    category: dict,
    trigger: dict,
    customer: dict | None,
    from_role: str = "merchant",
) -> dict:
    """Return {action, body?, cta?, rationale, wait_seconds?}."""
    turns = conversation.get("turns", [])
    role = (from_role or "merchant").lower()
    print(f"[REPLY_HANDLER] role={role}, msg='{message[:60]}', turns={len(turns)}")

    if _matches(message, STOP_PATTERNS):
        return {"action": "end", "rationale": "STOP/opt-out detected; ending immediately."}

    if _matches(message, REJECT_PATTERNS):
        return {"action": "end", "rationale": "User rejected or expressed hostility; ending conversation."}

    if _is_auto_reply(message):
        conversation["auto_reply_count"] = conversation.get("auto_reply_count", 0) + 1
        return {
            "action": "end",
            "rationale": "Detected canned auto-reply; ending to avoid a wait loop.",
        }

    if role == "customer":
        return _handle_customer_reply(message, merchant, category, trigger, customer)

    if _matches(message, OUT_OF_SCOPE_PATTERNS):
        topic = _trigger_topic(trigger)
        return {
            "action": "send",
            "body": _limit(
                f"That is outside Vera's scope. Coming back to {topic}, I can draft the merchant-growth next step from the data here. Reply YES."
            ),
            "cta": "binary_yes_no",
            "rationale": "Out-of-scope request declined and redirected to the active Vera task.",
        }

    if _matches(message, COMMIT_PATTERNS):
        return _handle_merchant_commit(message, merchant, category, trigger, customer)

    return _handle_merchant_followup(message, merchant, category, trigger)


def _handle_customer_reply(message: str, merchant: dict, category: dict, trigger: dict, customer: dict | None) -> dict:
    kind = trigger.get("kind") or ""
    name = _customer_name(customer)
    merchant_name = _merchant_name(merchant)
    payload = trigger.get("payload") or {}

    if kind == "chronic_refill_due":
        molecules = ", ".join(_clean(x) for x in payload.get("molecule_list", [])) or "your refill"
        return {
            "action": "send",
            "body": _limit(
                f"Namaste {name}, noted. {merchant_name} will prepare {molecules} and confirm delivery availability. Reply CONFIRM."
            ),
            "cta": "binary_confirm_cancel",
            "rationale": "Customer refill reply handled as customer-facing confirmation.",
        }

    if kind in {"recall_due", "trial_followup", "wedding_package_followup", "customer_lapsed_hard"} or _looks_like_booking(message):
        slot = _requested_slot(message, trigger)
        service = _service_name(trigger)
        slot_text = f"slot noted: {slot}." if slot else "noted."
        return {
            "action": "send",
            "body": _limit(
                f"{name}, {slot_text} {service} at {merchant_name}; the team will confirm availability. Reply CONFIRM to keep it."
            ),
            "cta": "binary_confirm_cancel",
            "rationale": "Customer booking reply uses the customer name and requested slot, not the merchant owner name.",
        }

    if re.search(r"\b(price|cost|kitna|charges?)\b", message, re.I):
        offer = _best_offer(merchant, category)
        return {
            "action": "send",
            "body": _limit(f"{name}, the current offer is {offer} at {merchant_name}. Reply YES if you want me to hold this option."),
            "cta": "binary_yes_no",
            "rationale": "Customer asked about price; answered with the active/catalog offer only.",
        }

    return {
        "action": "send",
        "body": _limit(f"{name}, noted. {merchant_name} will confirm the next step from their side. Reply CONFIRM to proceed."),
        "cta": "binary_confirm_cancel",
        "rationale": "Generic customer reply kept customer-facing and bounded.",
    }


def _handle_merchant_commit(message: str, merchant: dict, category: dict, trigger: dict, customer: dict | None) -> dict:
    kind = trigger.get("kind") or ""
    owner = _owner(merchant, category)
    payload = trigger.get("payload") or {}

    if kind == "regulation_change":
        dose = _dose_change(category, payload)
        body = (
            f"{owner}, D-speed is the red flag: {dose}; E-speed/RVG is the safer SOP path. "
            "I'll draft a checklist for film type, exposure log and patient note. Reply CONFIRM."
        )
        return _send(body, "binary_confirm_cancel", "Merchant accepted compliance help; answered with exact audit path.")

    if kind == "perf_dip":
        metric = _clean(payload.get("metric") or "calls")
        dip = _fmt_pct(payload.get("delta_pct"), absolute=True)
        body = (
            f"{owner}, the leak is {metric} down {dip} plus {_signal_summary(merchant)}. "
            f"I'll draft one offer + GBP fix for review. Reply CONFIRM."
        )
        return _send(body, "binary_confirm_cancel", "Merchant committed to performance fix; moved to concrete action.")

    if kind in {"festival_upcoming", "ipl_match_today"}:
        body = f"{owner}, on it. I'll draft the campaign copy using the current offer and show it before launch. Reply CONFIRM."
        return _send(body, "binary_confirm_cancel", "Merchant committed to a time-sensitive campaign.")

    if kind in {"supply_alert", "chronic_refill_due"}:
        body = f"{owner}, on it. I'll draft the customer WhatsApp note and replacement/refill workflow from this alert. Reply CONFIRM."
        return _send(body, "binary_confirm_cancel", "Merchant committed to a pharmacy workflow.")

    if kind in {"winback_eligible", "customer_lapsed_hard", "trial_followup", "recall_due"}:
        body = f"{owner}, on it. I'll draft the winback/follow-up message and keep it ready for your review. Reply CONFIRM."
        return _send(body, "binary_confirm_cancel", "Merchant committed; switched to draft-review mode.")

    if kind == "active_planning_intent":
        body = f"{owner}, perfect. I'll convert the plan into final WhatsApp + GBP copy now. Reply CONFIRM."
        return _send(body, "binary_confirm_cancel", "Merchant planning intent confirmed.")

    body = f"{owner}, great. I'll draft the next merchant action from this trigger and show it before anything goes out. Reply CONFIRM."
    return _send(body, "binary_confirm_cancel", "Merchant committed; using bounded action mode.")


def _handle_merchant_followup(message: str, merchant: dict, category: dict, trigger: dict) -> dict:
    kind = trigger.get("kind") or ""
    owner = _owner(merchant, category)
    payload = trigger.get("payload") or {}

    if kind == "regulation_change" or re.search(r"\b(d-speed|x-?ray|radiograph|iopa|rvg)\b", message, re.I):
        dose = _dose_change(category, payload)
        body = (
            f"{owner}, yes - D-speed is the issue. {dose}; E-speed/RVG passes. "
            "I'll draft an SOP checklist, not a medical opinion. Reply CONFIRM."
        )
        return _send(body, "binary_confirm_cancel", "Answered the merchant's X-ray audit concern with bounded compliance help.")

    if kind == "perf_dip":
        metric = _clean(payload.get("metric") or "calls")
        dip = _fmt_pct(payload.get("delta_pct"), absolute=True)
        body = f"{owner}, start with the sharpest leak: {metric} down {dip} and {_signal_summary(merchant)}. Reply YES for the exact fix copy."
        return _send(body, "binary_yes_no", "Follow-up keeps the perf dip decision focused on one leak.")

    if re.search(r"\bwhy|kaise|how|what\b", message, re.I):
        body = f"{owner}, I am using the trigger payload plus your live merchant signals, not outside data. I can draft the exact next message now. Reply YES."
        return _send(body, "binary_yes_no", "Explained grounding and returned to action.")

    body = f"{owner}, got it. I can fold that detail into the draft and keep the ask to one reply. Reply YES."
    return _send(body, "binary_yes_no", "Generic merchant follow-up preserves action momentum.")


def _send(body: str, cta: str, rationale: str) -> dict:
    return {"action": "send", "body": _limit(body), "cta": cta, "rationale": rationale}


def _matches(message: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, message or "", re.I) for pattern in patterns)


def _is_auto_reply(message: str) -> bool:
    msg = (message or "").lower().strip()
    if _matches(msg, AUTO_REPLY_PATTERNS):
        return True
    return len(msg) > 80 and all(word in msg for word in ["thank", "team"]) and any(
        word in msg for word in ["received", "shortly", "automated", "business hours"]
    )


def _looks_like_booking(message: str) -> bool:
    return bool(re.search(r"\b(book|booking|slot|appointment|reserve|hold|schedule)\b", message, re.I))


def _requested_slot(message: str, trigger: dict) -> str:
    msg = _clean(message)
    slots = []
    for item in (trigger.get("payload") or {}).get("available_slots", []) + (trigger.get("payload") or {}).get("next_session_options", []):
        if isinstance(item, dict) and item.get("label"):
            slots.append(_clean(item["label"]))

    if re.fullmatch(r"\s*[12]\s*", msg) and slots:
        idx = int(msg.strip()) - 1
        if 0 <= idx < len(slots):
            return slots[idx]

    patterns = [
        r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+\d{1,2}\s+[a-z]{3,9},?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
        r"\b\d{1,2}\s+[a-z]{3,9},?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
        r"\b(?:today|tomorrow)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
    ]
    for pattern in patterns:
        found = re.search(pattern, msg, re.I)
        if found:
            return found.group(0)
    return slots[0] if slots else ""


def _service_name(trigger: dict) -> str:
    payload = trigger.get("payload") or {}
    if payload.get("service_due"):
        return _humanize(payload["service_due"])
    if payload.get("next_step_window_open"):
        return _humanize(payload["next_step_window_open"])
    if payload.get("trial_date"):
        return "the next session"
    return "the appointment"


def _dose_change(category: dict, payload: dict) -> str:
    item_id = payload.get("top_item_id") or payload.get("digest_item_id")
    digest = _find_digest(category, item_id)
    summary = _clean(digest.get("summary"))
    match = re.search(r"from ([\d.]+\s*mSv) to ([\d.]+\s*mSv)", summary)
    if match:
        return f"IOPA max drops from {match.group(1)} to {match.group(2)}"
    return "the new IOPA limit is stricter"


def _find_digest(category: dict, item_id: str | None) -> dict:
    for item in category.get("digest", []) or []:
        if item_id and item.get("id") == item_id:
            return item
    for item in category.get("digest", []) or []:
        if item.get("kind") == "compliance":
            return item
    return {}


def _signal_summary(merchant: dict) -> str:
    signals = {str(s) for s in merchant.get("signals", [])}
    parts = []
    if "unverified_gbp" in signals or merchant.get("identity", {}).get("verified") is False:
        parts.append("GBP unverified")
    if "no_active_offers" in signals or not [o for o in merchant.get("offers", []) if o.get("status") == "active"]:
        parts.append("no active offers")
    if not parts:
        parts.append("conversion friction")
    return " + ".join(parts)


def _trigger_topic(trigger: dict) -> str:
    kind = _clean(trigger.get("kind") or "the active trigger").replace("_", " ")
    payload = trigger.get("payload") or {}
    if payload.get("metric"):
        return f"{kind} on {payload.get('metric')}"
    if payload.get("festival"):
        return f"{payload.get('festival')} planning"
    return kind


def _best_offer(merchant: dict, category: dict) -> str:
    for offer in merchant.get("offers", []):
        if offer.get("status") == "active" and offer.get("title"):
            return _clean(offer["title"])
    for offer in category.get("offer_catalog", []) or []:
        if offer.get("title"):
            return _clean(offer["title"])
    return "the current starter offer"


def _owner(merchant: dict, category: dict) -> str:
    name = _first_name(merchant.get("identity", {}).get("owner_first_name") or merchant.get("identity", {}).get("name") or "there")
    if category.get("slug") == "dentists" and not name.lower().startswith("dr"):
        return f"Dr. {name}"
    return name


def _customer_name(customer: dict | None) -> str:
    if not customer:
        return "there"
    name = _clean(customer.get("identity", {}).get("name") or "there")
    if re.match(r"^(Mr\.|Mrs\.|Ms\.|Dr\.)\s+\S+", name):
        return " ".join(name.split()[:2])
    return _first_name(name)


def _merchant_name(merchant: dict) -> str:
    return _clean(merchant.get("identity", {}).get("name") or "the merchant")


def _first_name(value: Any) -> str:
    text = _clean(value)
    if not text or text.startswith("("):
        return "there"
    return (text.split("(")[0].strip().split() or ["there"])[0].strip(",")


def _fmt_pct(value: Any, *, absolute: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _clean(value or "")
    if abs(number) <= 1:
        number *= 100
    if absolute:
        number = abs(number)
    return f"{int(number)}%" if number.is_integer() else f"{number:.1f}%"


def _clean(value: Any) -> str:
    text = "" if value is None else str(value)
    for old, new in {
        "\u20b9": "Rs ",
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\xa0": " ",
    }.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def _humanize(value: Any) -> str:
    text = _clean(value).replace("_", " ").replace("-", " ")
    text = re.sub(r"\b6 month\b", "6-month", text, flags=re.I)
    text = re.sub(r"\b30day\b", "30-day", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _limit(body: str) -> str:
    body = re.sub(r"https?://\S+", "", _clean(body)).strip()
    if len(body) <= 320:
        return body
    match = re.search(r"(Reply\s+[^.?!]+[.?!]?)$", body, re.I)
    if not match:
        return body[:317].rstrip() + "..."
    cta = match.group(1)
    return f"{body[: 318 - len(cta)].rstrip(' ,;.-')}. {cta}"[:320]
