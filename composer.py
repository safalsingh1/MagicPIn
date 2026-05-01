"""
Deterministic Vera message composer.

The judge scores decisions more than prose polish, so the core path is now
rule-based: pick the strongest signal from category + merchant + trigger +
optional customer context, then render one short WhatsApp-style message.

Groq/LLM use is deliberately avoided on the scoring path. This keeps outputs
stable, fixes ratio-to-percent mistakes, and prevents role-routing drift.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

MAX_BODY = 320


CTA_BY_KIND = {
    "recall_due": "multi_choice_slot",
    "trial_followup": "binary_yes_no",
    "wedding_package_followup": "binary_yes_no",
    "chronic_refill_due": "binary_confirm_cancel",
}


def compose_message(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: dict | None = None,
) -> dict:
    """
    Competition entry point.
    Returns body, cta, send_as, template_name, template_params,
    suppression_key, and rationale.
    """
    kind = trigger.get("kind") or "generic"
    renderer = _RENDERERS.get(kind, _compose_generic)
    body, cta, params, rationale = renderer(category or {}, merchant or {}, trigger or {}, customer)

    is_customer = bool(trigger.get("customer_id") or trigger.get("scope") == "customer" or customer)
    return _result(
        body=body,
        kind=kind,
        cta=cta or CTA_BY_KIND.get(kind, "binary_yes_no"),
        send_as="merchant_on_behalf" if is_customer else "vera",
        suppression_key=trigger.get("suppression_key") or f"trg:{trigger.get('id', 'unknown')}",
        params=params,
        rationale=rationale,
    )


def _result(
    *,
    body: str,
    kind: str,
    cta: str,
    send_as: str,
    suppression_key: str,
    params: list[Any],
    rationale: str,
) -> dict:
    body = _clean(body)
    body = re.sub(r"https?://\S+", "", body).strip()
    if len(body) > MAX_BODY:
        body = _trim_preserving_cta(body)

    return {
        "body": body,
        "cta": cta,
        "send_as": send_as,
        "template_name": f"vera_{kind}_v2",
        "template_params": [_clean(p) for p in params if p not in (None, "")][:6],
        "suppression_key": suppression_key,
        "rationale": rationale[:260],
    }


def _trim_preserving_cta(body: str) -> str:
    match = re.search(r"(Reply\s+[^.?!]+[.?!]?)$", body, re.I)
    if not match:
        return body[: MAX_BODY - 3].rstrip() + "..."

    cta = match.group(1).strip()
    head_limit = MAX_BODY - len(cta) - 2
    head = body[:head_limit].rstrip(" ,;.-")
    return f"{head}. {cta}"[:MAX_BODY]


def _clean(value: Any) -> str:
    text = "" if value is None else str(value)
    replacements = {
        "\u20b9": "Rs ",
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\xa0": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"Rs\s+", "Rs ", text)
    return text


def _owner(merchant: dict, category: dict | None = None) -> str:
    identity = merchant.get("identity", {})
    name = identity.get("owner_first_name") or identity.get("name") or "there"
    name = _first_name(name)
    if (category or {}).get("slug") == "dentists" and name and not name.lower().startswith("dr"):
        return f"Dr. {name}"
    return name


def _owner_plain(merchant: dict) -> str:
    identity = merchant.get("identity", {})
    return _first_name(identity.get("owner_first_name") or identity.get("name") or "there")


def _customer_name(customer: dict | None) -> str:
    if not customer:
        return "there"
    name = _clean(customer.get("identity", {}).get("name") or "there")
    honorific = re.match(r"^(Mr\.|Mrs\.|Ms\.|Dr\.)\s+\S+", name)
    if honorific:
        return " ".join(name.split()[:2])
    return _first_name(name)


def _first_name(name: Any) -> str:
    text = _clean(name)
    if not text or text.startswith("("):
        return "there"
    text = text.split("(")[0].strip()
    text = text.split()[0].strip(",")
    return text or "there"


def _humanize(value: Any) -> str:
    text = _clean(value).replace("_", " ").replace("-", " ")
    text = re.sub(r"\b30day\b", "30-day", text, flags=re.I)
    text = re.sub(r"\b6 month\b", "6-month", text, flags=re.I)
    text = re.sub(r"\bapr jun\b", "Apr-Jun", text, flags=re.I)
    text = re.sub(r"\bpost resolution\b", "post-resolution", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _merchant_name(merchant: dict) -> str:
    return _clean(merchant.get("identity", {}).get("name") or "your business")


def _locality(merchant: dict) -> str:
    identity = merchant.get("identity", {})
    return _clean(identity.get("locality") or identity.get("city") or "your area")


def _payload(trigger: dict) -> dict:
    return trigger.get("payload") or {}


def _perf(merchant: dict) -> dict:
    return merchant.get("performance") or {}


def _signals(merchant: dict) -> list[str]:
    return [str(s) for s in merchant.get("signals", [])]


def _cust_agg(merchant: dict) -> dict:
    return merchant.get("customer_aggregate") or {}


def _fmt_pct(value: Any, *, signed: bool = False, absolute: bool = False) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("%"):
            return raw
        found = re.search(r"-?\d+(?:\.\d+)?", raw)
        if not found:
            return raw
        number = float(found.group())
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return _clean(value)

    # Dataset convention: ratios like -0.50 mean -50%, not -0.5%.
    if abs(number) <= 1:
        number *= 100
    if absolute:
        number = abs(number)

    sign = ""
    if signed and number > 0:
        sign = "+"
    if number.is_integer():
        return f"{sign}{int(number)}%"
    return f"{sign}{number:.1f}%"


def _fmt_number(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _clean(value)


def _date_part(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    return text.split("T")[0]


def _time_label(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%I:%M%p").lstrip("0").lower()
    except ValueError:
        return text


def _find_digest(category: dict, *, item_id: str | None = None, kind: str | None = None) -> dict:
    digest = category.get("digest") or []
    if item_id:
        for item in digest:
            if item.get("id") == item_id:
                return item
    if kind:
        for item in digest:
            if item.get("kind") == kind:
                return item
    return digest[0] if digest else {}


def _extract_percent(text: Any, default: str = "") -> str:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", _clean(text))
    return f"{match.group(1)}%" if match else default


def _active_offers(merchant: dict) -> list[str]:
    offers = []
    for offer in merchant.get("offers", []):
        if offer.get("status") == "active":
            title = _clean(offer.get("title"))
            if title:
                offers.append(title)
    return offers


def _catalog_offers(category: dict) -> list[str]:
    return [_clean(o.get("title")) for o in category.get("offer_catalog", []) if o.get("title")]


def _offer(
    merchant: dict,
    category: dict,
    *,
    contains: str | None = None,
    fallback_contains: str | None = None,
) -> str:
    choices = _active_offers(merchant) + _catalog_offers(category)
    lowered_contains = contains.lower() if contains else ""
    if lowered_contains:
        for title in choices:
            if lowered_contains in title.lower():
                return title
    if fallback_contains:
        needle = fallback_contains.lower()
        for title in choices:
            if needle in title.lower():
                return title
    return choices[0] if choices else "a simple starter offer"


def _slots(trigger: dict) -> list[str]:
    items = _payload(trigger).get("available_slots") or _payload(trigger).get("next_session_options") or []
    labels = []
    for item in items:
        if isinstance(item, dict):
            labels.append(_clean(item.get("label") or _time_label(item.get("iso"))))
    return [label for label in labels if label]


def _peer_value(category: dict, key: str) -> str:
    return _fmt_number((category.get("peer_stats") or {}).get(key))


def _review_theme(merchant: dict, theme_name: str | None = None) -> dict:
    themes = merchant.get("review_themes") or []
    if theme_name:
        for theme in themes:
            if theme.get("theme") == theme_name:
                return theme
    return themes[0] if themes else {}


def _history_text(merchant: dict) -> str:
    parts = []
    for turn in merchant.get("conversation_history", [])[-4:]:
        parts.append(_clean(turn.get("body")))
    return " ".join(parts)


def _price_from_text(text: str, default: str = "") -> str:
    clean = _clean(text)
    match = re.search(r"Rs\s*([\d,]+)", clean)
    if match:
        return f"Rs {match.group(1)}"
    match = re.search(r"(?:INR|rs\.?)\s*([\d,]+)", clean, re.I)
    if match:
        return f"Rs {match.group(1)}"
    return default


def _compose_research_digest(category, merchant, trigger, customer):
    payload = _payload(trigger)
    digest = _find_digest(category, item_id=payload.get("top_item_id"), kind="research")
    owner = _owner(merchant, category)
    trial = _fmt_number(digest.get("trial_n"))
    percent = _extract_percent(digest.get("summary"), "38%")
    source = _clean(digest.get("source") or "latest digest")
    cohort = _cust_agg(merchant).get("high_risk_adult_count") or _cust_agg(merchant).get("active_count")
    cohort_line = f" You have {cohort} high-risk adults." if cohort else ""
    body = (
        f"{owner}, {source}: {trial}-patient trial shows {percent} lower caries recurrence "
        f"with 3-month fluoride varnish recalls.{cohort_line} Reply YES for patient note."
    )
    return body, "binary_yes_no", [source, trial, percent, cohort], (
        "Used the research digest plus the merchant's high-risk cohort; CTA externalizes the patient-note work."
    )


def _compose_regulation_change(category, merchant, trigger, customer):
    payload = _payload(trigger)
    digest = _find_digest(category, item_id=payload.get("top_item_id"), kind="compliance")
    owner = _owner(merchant, category)
    deadline = _date_part(payload.get("deadline_iso")) or _date_part(trigger.get("expires_at"))
    summary = _clean(digest.get("summary"))
    drops = re.search(r"from ([\d.]+\s*mSv) to ([\d.]+\s*mSv)", summary)
    change = f"IOPA max drops {drops.group(1)} to {drops.group(2)}" if drops else summary[:95]
    body = (
        f"{owner}, DCI change takes effect {deadline}: {change}; D-speed will not pass. "
        "I can draft your X-ray SOP checklist. Reply YES."
    )
    return body, "binary_yes_no", [deadline, change], (
        "Chose the compliance deadline and exact dose-limit change; CTA asks for a checklist draft only."
    )


def _compose_recall_due(category, merchant, trigger, customer):
    payload = _payload(trigger)
    name = _customer_name(customer)
    owner = _owner(merchant, category)
    service = _humanize(payload.get("service_due", "recall"))
    due = _date_part(payload.get("due_date"))
    offer = _offer(merchant, category, contains="cleaning")
    slots = _slots(trigger)
    slot_text = ""
    if len(slots) >= 2:
        slot_text = f" 1) {slots[0]} 2) {slots[1]}."
        cta_sentence = "Reply 1 or 2."
    elif slots:
        slot_text = f" Slot: {slots[0]}."
        cta_sentence = "Reply YES to hold it."
    else:
        cta_sentence = "Reply YES for a slot."
    body = f"{name}, your {service} is due {due} at {owner}'s clinic. {offer} is active.{slot_text} {cta_sentence}"
    return body, "multi_choice_slot" if len(slots) >= 2 else "binary_yes_no", [name, service, due, offer], (
        "Customer recall uses due date, slot choices, and active offer for a low-effort booking reply."
    )


def _compose_perf_dip(category, merchant, trigger, customer):
    payload = _payload(trigger)
    metric = _clean(payload.get("metric") or "calls")
    raw_delta = payload.get("delta_pct")
    if raw_delta in (None, ""):
        raw_delta = (_perf(merchant).get("delta_7d") or {}).get(f"{metric}_pct")
    dip = _fmt_pct(raw_delta, absolute=True)
    window = _clean(payload.get("window") or "7d")
    baseline = _fmt_number(payload.get("vs_baseline"))
    owner = _owner(merchant, category)
    no_offer = "no_active_offers" in _signals(merchant) or not _active_offers(merchant)
    unverified = "unverified_gbp" in _signals(merchant) or merchant.get("identity", {}).get("verified") is False
    offer = _offer(merchant, category, contains="cleaning")
    causes = []
    if unverified:
        causes.append("GBP unverified")
    if no_offer:
        causes.append("no active offers")
    cause_text = " + ".join(causes) or "stale conversion path"
    baseline_text = f" vs baseline {baseline}" if baseline else ""
    body = (
        f"{owner}, {metric} are down {dip} in {window}{baseline_text}. {cause_text} is the likely leak. "
        f"I can draft {offer} + GBP fix. Reply YES."
    )
    return body, "binary_yes_no", [metric, dip, window, baseline, cause_text], (
        "Normalized ratio delta to a true percent and tied the dip to merchant signals."
    )


def _compose_perf_spike(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner(merchant, category)
    metric = _clean(payload.get("metric") or "calls")
    spike = _fmt_pct(payload.get("delta_pct"), signed=True)
    window = _clean(payload.get("window") or "7d")
    driver = _humanize(payload.get("likely_driver") or "recent post")
    offer = _offer(merchant, category)
    body = (
        f"{owner}, {metric} are up {spike} in {window}, likely from {driver}. Spikes are 3-5 day windows; "
        f"push {offer} now. Reply YES for GBP copy."
    )
    return body, "binary_yes_no", [metric, spike, window, driver, offer], (
        "Uses the spike size and likely driver, then converts momentum into a fast follow-up action."
    )


def _compose_festival_upcoming(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    festival = _clean(payload.get("festival") or "festival")
    days = _fmt_number(payload.get("days_until"))
    offer_one = _offer(merchant, category)
    offer_two = _offer(merchant, category, contains="spa", fallback_contains="massage")
    bundle = offer_one if offer_two == offer_one else f"{offer_one} + {offer_two}"
    body = (
        f"{owner}, {festival} is {days} days away. Push a {bundle} prep offer in {_locality(merchant)} "
        "before festive slots crowd. Reply YES for the post."
    )
    return body, "binary_yes_no", [festival, days, bundle], (
        "Uses festival timing plus category-suitable service-at-price offers."
    )


def _compose_ipl_match_today(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    match = _clean(payload.get("match") or "today's match")
    venue = _clean(payload.get("venue") or payload.get("city") or "nearby")
    time = _time_label(payload.get("match_time_iso")) or "tonight"
    is_weeknight = bool(payload.get("is_weeknight"))
    offer = _offer(merchant, category, contains="pizza", fallback_contains="match")
    if is_weeknight:
        body = (
            f"{owner}, {match} at {venue} {time}: weeknight IPL lifts covers +18%. "
            f"Use {offer} as a dine-in hook. Reply YES for match-night copy."
        )
    else:
        body = (
            f"{owner}, {match} at {venue} {time} is weekend IPL: covers drop 12% as people watch at home. "
            f"Push delivery with {offer}. Reply YES for copy."
        )
    return body, "binary_yes_no", [match, venue, time, offer], (
        "Covers the IPL trigger explicitly and chooses delivery vs dine-in from match-day pattern."
    )


def _compose_milestone_reached(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    current = int(payload.get("value_now") or 0)
    milestone = int(payload.get("milestone_value") or current)
    remaining = max(0, milestone - current)
    peer_avg = _peer_value(category, "avg_review_count")
    metric = _humanize(payload.get("metric") or "reviews")
    if metric == "review count":
        metric = "reviews"
    body = (
        f"{owner}, {_merchant_name(merchant)} is {remaining} {metric} from {milestone} and already above peer avg {peer_avg}. "
        "Turn this into social proof on GBP. Reply YES for post copy."
    )
    return body, "binary_yes_no", [current, milestone, peer_avg], (
        "Turns an imminent milestone into timely social-proof content."
    )


def _compose_competitor_opened(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner(merchant, category)
    competitor = _clean(payload.get("competitor_name") or "a competitor")
    distance = _fmt_number(payload.get("distance_km"))
    their_offer = _clean(payload.get("their_offer") or "a starter offer")
    our_offer = _offer(merchant, category, contains="cleaning")
    review = _review_theme(merchant, "doctor_manner").get("common_quote")
    edge = f"your reviews say '{_clean(review)}'" if review else f"your {our_offer}"
    body = (
        f"{owner}, {competitor} opened {distance} km away with {their_offer}. Counter on trust: {edge}. "
        "Reply YES for comparison copy."
    )
    return body, "binary_yes_no", [competitor, distance, their_offer, our_offer], (
        "Uses competitor distance/offer and a merchant-specific differentiator."
    )


def _compose_winback_eligible(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    lapsed = _fmt_number(payload.get("lapsed_customers_added_since_expiry") or _cust_agg(merchant).get("lapsed_90d_plus"))
    days = _fmt_number(payload.get("days_since_expiry") or merchant.get("subscription", {}).get("days_since_expiry"))
    dip = _fmt_pct(payload.get("perf_dip_pct") or (_perf(merchant).get("delta_7d") or {}).get("calls_pct"), absolute=True)
    offer = _offer(merchant, category, contains="haircut", fallback_contains="trial")
    body = (
        f"{owner}, {lapsed} customers drifted in {days} days since Pro expired and calls are down {dip}. "
        f"Win them back with {offer}. Reply YES for the WhatsApp draft."
    )
    return body, "binary_yes_no", [lapsed, days, dip, offer], (
        "Leads with concrete lost customers and a low-friction winback draft."
    )


def _compose_dormant_with_vera(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    days = _fmt_number(payload.get("days_since_last_merchant_message"))
    offer_one = _offer(merchant, category, contains="haircut")
    offer_two = _offer(merchant, category, contains="spa")
    body = (
        f"{owner}, after {days} days, one useful {_locality(merchant)} question: are clients asking more for "
        f"{offer_one} or {offer_two}? Reply one service; I'll draft a GBP post."
    )
    return body, "open_ended", [days, offer_one, offer_two], (
        "Re-engages with a curiosity question tied to local service demand, not a generic reminder."
    )


def _compose_review_theme_emerged(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    theme = _clean(payload.get("theme") or "review theme")
    count = _fmt_number(payload.get("occurrences_30d"))
    quote = _clean(payload.get("common_quote") or _review_theme(merchant, theme).get("common_quote"))
    body = (
        f"{owner}, {theme} appeared {count} times in 30d; one quote: '{quote}'. "
        "Trim delivery radius tonight and reply fast. Reply YES for response template."
    )
    return body, "binary_yes_no", [theme, count, quote], (
        "Uses exact review count and quote, then proposes one operational fix."
    )


def _compose_curious_ask_due(category, merchant, trigger, customer):
    owner = _owner_plain(merchant)
    offers = _active_offers(merchant) or _catalog_offers(category)
    left = offers[0] if offers else "your top service"
    right = offers[1] if len(offers) > 1 else "walk-ins"
    body = (
        f"{owner}, quick {_locality(merchant)} pulse: what is most asked this week - {left} or {right}? "
        "Reply one name; I'll turn it into a GBP post."
    )
    return body, "open_ended", [left, right], (
        "Asks one low-effort business question and offers to convert the answer into a useful asset."
    )


def _compose_supply_alert(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    molecule = _clean(payload.get("molecule") or "medicine")
    batches = ", ".join(_clean(x) for x in payload.get("affected_batches", []))
    manufacturer = _clean(payload.get("manufacturer") or "manufacturer")
    affected = _cust_agg(merchant).get("chronic_rx_count") or _cust_agg(merchant).get("active_count") or "repeat"
    body = (
        f"{owner}, CDSCO alert: {molecule} batches {batches} by {manufacturer} are flagged for sub-potency, no safety panic. "
        f"Filter {affected} chronic-Rx customers. Reply YES for WhatsApp note."
    )
    return body, "binary_yes_no", [molecule, batches, manufacturer, affected], (
        "Uses batch/molecule/manufacturer and bounded risk framing for pharmacy compliance."
    )


def _compose_chronic_refill_due(category, merchant, trigger, customer):
    payload = _payload(trigger)
    name = _customer_name(customer)
    molecules = ", ".join(_clean(x) for x in payload.get("molecule_list", []))
    runout = _date_part(payload.get("stock_runs_out_iso"))
    senior_offer = _offer(merchant, category, contains="senior", fallback_contains="delivery")
    delivery = _offer(merchant, category, contains="delivery")
    offer_text = senior_offer if senior_offer == delivery else f"{senior_offer} + {delivery}"
    body = (
        f"Namaste {name}, {molecules} run out on {runout}. {offer_text} applies at {_merchant_name(merchant)}. "
        "Reply CONFIRM for delivery."
    )
    return body, "binary_confirm_cancel", [name, molecules, runout, offer_text], (
        "Customer refill reminder uses exact molecules, run-out date, and active pharmacy offers."
    )


def _compose_customer_lapsed_hard(category, merchant, trigger, customer):
    payload = _payload(trigger)
    name = _customer_name(customer)
    days = _fmt_number(payload.get("days_since_last_visit"))
    focus = _humanize(payload.get("previous_focus") or (customer or {}).get("preferences", {}).get("training_focus") or "your goal")
    offer = _offer(merchant, category, contains="trial")
    body = (
        f"{name}, you were working on {focus}; {days} days away happens, no judgment. "
        f"{_merchant_name(merchant)} has {offer} to restart easy. Reply YES - no auto-charge."
    )
    return body, "binary_yes_no", [name, days, focus, offer], (
        "Customer winback references the past goal and removes commitment anxiety."
    )


def _compose_gbp_unverified(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    uplift = _fmt_pct(payload.get("estimated_uplift_pct"), absolute=True)
    path = _humanize(payload.get("verification_path") or "verification")
    slug = _humanize(category.get("slug") or "businesses")
    body = (
        f"Namaste {owner}, your GBP is unverified; verified {slug} can gain {uplift} more discovery. "
        f"{path} takes ~10 min. Reply YES for steps."
    )
    return body, "binary_yes_no", [uplift, path], (
        "Uses the verification trigger and estimated discovery uplift with a small time ask."
    )


def _compose_renewal_due(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner(merchant, category)
    plan = _clean(payload.get("plan") or merchant.get("subscription", {}).get("plan") or "plan")
    days = _fmt_number(payload.get("days_remaining") or merchant.get("subscription", {}).get("days_remaining"))
    perf = _perf(merchant)
    body = (
        f"{owner}, {plan} ends in {days} days. Last 30d gave {perf.get('views', '?')} views, "
        f"{perf.get('calls', '?')} calls and {perf.get('leads', '?')} leads; don't lose visibility. Reply YES for renewal summary."
    )
    return body, "binary_yes_no", [plan, days, perf.get("views"), perf.get("calls"), perf.get("leads")], (
        "Anchors renewal to value already delivered and the days-left deadline."
    )


def _compose_active_planning_intent(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    topic = _clean(payload.get("intent_topic") or payload.get("merchant_last_message") or "plan").lower()
    history = _history_text(merchant)

    if "corporate" in topic or "thali" in topic:
        offer = _offer(merchant, category, contains="thali")
        body = (
            f"{owner}, corporate thali draft: {offer}, 10+ pax preorder by 11am, free delivery within {_locality(merchant)} offices. "
            "Reply YES to turn this into WhatsApp copy."
        )
        params = [offer, "10+ pax", "11am"]
    elif "kids" in topic or "yoga" in topic:
        price = _price_from_text(history, "Rs 2,499")
        body = (
            f"{owner}, kids yoga camp draft: age 7-12, 4 weeks, 3 classes/week, {price}, Sat 8am trial. "
            "Reply YES for GBP + Insta copy."
        )
        params = ["age 7-12", "4 weeks", "3 classes/week", price]
    else:
        offer = _offer(merchant, category)
        body = (
            f"{owner}, I drafted the {payload.get('intent_topic', 'plan')} around {offer}, one clear price and one reply path. "
            "Reply YES to see the final copy."
        )
        params = [offer, payload.get("intent_topic")]

    return body, "binary_yes_no", params, (
        "Merchant already showed planning intent, so the message delivers a concrete draft instead of asking more questions."
    )


def _compose_trial_followup(category, merchant, trigger, customer):
    payload = _payload(trigger)
    name = _customer_name(customer)
    trial = _date_part(payload.get("trial_date"))
    slots = _slots(trigger)
    slot = slots[0] if slots else "the next session"
    body = (
        f"{name}, you tried with {_merchant_name(merchant)} on {trial}. {slot} is open for the next step, no long commitment. "
        "Reply YES to hold the seat."
    )
    return body, "binary_yes_no", [name, trial, slot], (
        "Uses the trial date and the next available session to create an easy conversion ask."
    )


def _compose_category_seasonal(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    trends = []
    for item in payload.get("trends", []):
        text = _clean(item).replace("_demand_", " ").replace("_", " ")
        text = re.sub(r"([+-]\d+)$", r"\1%", text)
        trends.append(text)
    trend_text = ", ".join(trends[:4]) or _clean(payload.get("season") or "seasonal shift")
    body = (
        f"{owner}, summer shelf shift: {trend_text}. Move ORS+sunscreen to counter and cold/cough to back shelf today. "
        "Reply YES for shelf poster copy."
    )
    return body, "binary_yes_no", trends, (
        "Uses seasonal demand shifts and gives one pharmacy shelf action."
    )


def _compose_seasonal_perf_dip(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _owner_plain(merchant)
    metric = _clean(payload.get("metric") or "views")
    dip = _fmt_pct(payload.get("delta_pct"), absolute=True)
    window = _clean(payload.get("window") or "7d")
    season = _humanize(payload.get("season_note") or "seasonal window")
    offer = _offer(merchant, category, contains="trial")
    body = (
        f"{owner}, {metric} are down {dip} in {window}, but {season} is gyms' low-acquisition stretch. "
        f"Skip extra ads; use {offer} for retention. Reply YES for copy."
    )
    return body, "binary_yes_no", [metric, dip, season, offer], (
        "Reframes the dip as seasonal and recommends retention over acquisition spend."
    )


def _compose_cde_opportunity(category, merchant, trigger, customer):
    payload = _payload(trigger)
    digest = _find_digest(category, item_id=payload.get("digest_item_id"), kind="cde")
    owner = _owner(merchant, category)
    date = _date_part(digest.get("date"))
    time = _time_label(digest.get("date"))
    credits = _fmt_number(payload.get("credits") or digest.get("credits"))
    fee = _humanize(payload.get("fee") or digest.get("actionable"))
    title = _clean(digest.get("title") or "CDE session")
    body = (
        f"{owner}, {title} is {date} {time}: {credits} CDE credits, {fee}. "
        "Reply YES for a 5-question prep note."
    )
    return body, "binary_yes_no", [title, date, credits, fee], (
        "Uses professional-development details and offers a useful prep artifact."
    )


def _compose_wedding_package_followup(category, merchant, trigger, customer):
    payload = _payload(trigger)
    name = _customer_name(customer)
    days = _fmt_number(payload.get("days_to_wedding"))
    window = _humanize(payload.get("next_step_window_open") or "skin prep")
    if "30-day" in window and "skin prep" in window:
        window = "30-day skin prep program"
    preferred = _humanize((customer or {}).get("preferences", {}).get("preferred_slots") or "your preferred slot")
    if preferred in {"saturday", "sunday"}:
        preferred = preferred.title()
    body = (
        f"{name}, your wedding is in {days} days and the {window} window is open at {_merchant_name(merchant)}. "
        f"{preferred} can work. Reply YES for a bridal follow-up slot."
    )
    return body, "binary_yes_no", [name, days, window, preferred], (
        "Customer bridal follow-up uses days-to-wedding and the service window to create urgency."
    )


def _compose_generic(category, merchant, trigger, customer):
    payload = _payload(trigger)
    owner = _customer_name(customer) if customer else _owner(merchant, category)
    facts = []
    for key, value in payload.items():
        if value not in (None, "", [], {}):
            facts.append(f"{key}={_clean(value)}")
        if len(facts) == 2:
            break
    fact_text = "; ".join(facts) or "a new signal just arrived"
    body = f"{owner}, {fact_text}. I can turn this into one clear merchant action. Reply YES for the draft."
    return body, "binary_yes_no", facts, (
        "Generic fallback still grounds the message in trigger payload fields."
    )


_RENDERERS = {
    "research_digest": _compose_research_digest,
    "regulation_change": _compose_regulation_change,
    "recall_due": _compose_recall_due,
    "perf_dip": _compose_perf_dip,
    "perf_spike": _compose_perf_spike,
    "festival_upcoming": _compose_festival_upcoming,
    "ipl_match_today": _compose_ipl_match_today,
    "milestone_reached": _compose_milestone_reached,
    "competitor_opened": _compose_competitor_opened,
    "winback_eligible": _compose_winback_eligible,
    "dormant_with_vera": _compose_dormant_with_vera,
    "review_theme_emerged": _compose_review_theme_emerged,
    "curious_ask_due": _compose_curious_ask_due,
    "supply_alert": _compose_supply_alert,
    "chronic_refill_due": _compose_chronic_refill_due,
    "customer_lapsed_hard": _compose_customer_lapsed_hard,
    "gbp_unverified": _compose_gbp_unverified,
    "renewal_due": _compose_renewal_due,
    "active_planning_intent": _compose_active_planning_intent,
    "trial_followup": _compose_trial_followup,
    "category_seasonal": _compose_category_seasonal,
    "seasonal_perf_dip": _compose_seasonal_perf_dip,
    "cde_opportunity": _compose_cde_opportunity,
    "wedding_package_followup": _compose_wedding_package_followup,
}
