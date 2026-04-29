"""
composer.py — Vera 4-context message composer (competition-grade)
=================================================================
Uses Groq llama-3.1-8b-instant (500K TPD free tier).
Dispatches on trigger.kind with case-study-anchored prompt guidance.
"""

import os, json, re
from groq import Groq

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
MODEL = "llama-3.1-8b-instant"

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


# ── Category voice profiles ───────────────────────────────────────────────────
VOICE_PROFILES = {
    "dentists": (
        "Clinical peer-to-peer tone. Address as 'Dr. [FirstName]'. "
        "Technical vocabulary welcome: fluoride varnish, caries, bruxism, IOPA, DCI, RVG, mSv, E-speed. "
        "Source-cite ALL research claims: journal name + year + page ref. "
        "NEVER use: 'guaranteed', 'cure', '100% safe', promotional hype."
    ),
    "salons": (
        "Warm, practical, fellow-operator register. Use owner first name directly. "
        "Highlight services-at-price (not % discounts). "
        "Emojis sparingly (💍 🌸 ✨ max 1). Reference specific stylists, services, or past treatments when known. "
        "Honor Hindi-English code-mix when appropriate."
    ),
    "restaurants": (
        "Operator-to-operator voice. Restaurant jargon: covers, AOV, delivery radius, Swiggy/Zomato, banner, Insta story. "
        "Data-informed and counter-intuitive calls score highest. "
        "Actionable and time-sensitive. Never generic."
    ),
    "gyms": (
        "Coach-to-client + fellow-operator voice. Motivational but evidence-based. "
        "No shame, no guilt-trip. Key terms: ad spend, conversion, member count, retention, HIIT, PT. "
        "Practical specificity beats inspiration. Include time, date, duration for class references."
    ),
    "pharmacies": (
        "Trustworthy, precise, respectful. Use full molecule names (metformin, atorvastatin, telmisartan). "
        "Include batch numbers, regulator names, deadlines when available. "
        "Senior-friendly: 'Namaste' for senior/Hindi audiences. Never alarm unnecessarily. "
        "Include bounded risk framing ('sub-potency, no safety risk')."
    ),
}

# ── Trigger-kind prompt guidance (anchored on case-study patterns) ────────────
TRIGGER_GUIDANCE = {
    "research_digest": (
        "Frame: 'new research just landed relevant to your [patients/customers]'. "
        "MUST include: paper/journal name, trial size (N=X), key finding (X%), source citation (Journal Year p.XX). "
        "Anchor to merchant's own patient cohort from customer_aggregate. "
        "CTA: offer to pull abstract + draft patient-ed content."
    ),
    "regulation_change": (
        "Frame as urgent compliance notice. MUST include: regulator name, circular/doc ref, deadline date, "
        "specific change (e.g. dose limit drop from X to Y mSv). Offer to help prepare checklist/SOP."
    ),
    "recall_due": (
        "Customer-facing recall. Address customer by first name. State exact months since last visit. "
        "Offer 2 specific slot options with day, date, time. Include price from active offer catalog. "
        "Language: match customer language_pref (hi-en mix if hi). CTA: multi-choice slot (Reply 1 for X, 2 for Y)."
    ),
    "perf_dip": (
        "Flag exact dip percentage and metric. Diagnose probable cause from signals. "
        "Propose ONE concrete fix (create offer, reply to reviews, update GBP). Loss aversion framing. "
        "NEVER generic 'your performance dropped' — specific numbers only."
    ),
    "perf_spike": (
        "Celebrate briefly, then immediately: 'here's how to lock in this momentum'. "
        "Propose specific action (GBP post, offer, WhatsApp blast) tied to what caused the spike. "
        "Urgency: 'spikes are 3-5 day windows'."
    ),
    "festival_upcoming": (
        "Days-until number creates urgency. Propose a specific campaign for THIS festival + THIS category combo. "
        "Draft the offer in the message body. Make it easy to say yes."
    ),
    "ipl_match_today": (
        "Counter-intuitive data is gold: SATURDAY IPL = -12% covers (people watch at home); "
        "WEEKNIGHT IPL = +18% covers. Include match details (teams, stadium, time). "
        "Recommend whether to push promo or pivot to delivery based on day. Reference existing offer."
    ),
    "milestone_reached": (
        "Celebrate the milestone. Immediately propose: how to leverage it (GBP post, ad, WhatsApp). "
        "Social proof framing: 'You're in the top X% of merchants on this metric'."
    ),
    "competitor_opened": (
        "Curiosity hook: 'new competitor nearby, here's what they're offering'. "
        "Propose differentiation. Use merchant's existing strength vs competitor's offer."
    ),
    "winback_eligible": (
        "Lead with concrete loss: lapsed customer count + dip % since expiry. "
        "Frame as: 'X customers you earned are drifting'. Low-friction reactivation CTA."
    ),
    "dormant_with_vera": (
        "Re-engage with curiosity, not reminder. Ask an interesting business question. "
        "Reference last topic if known from conv_history. No guilt; no 'you haven't replied'."
    ),
    "review_theme_emerged": (
        "Flag the pattern: exact count + verbatim quote snippet from reviews. "
        "Propose fix: operational change + response template. "
        "Frame as: 'catching this early — here's how to turn it around'."
    ),
    "curious_ask_due": (
        "Low-stakes, high-curiosity question about their business (e.g. 'What's your most asked-for service this week?'). "
        "Offer to convert their answer into a useful artifact (Google post, WhatsApp reply template). "
        "Effort externalization: 'Takes 5 min, I'll do the rest'."
    ),
    "supply_alert": (
        "Urgent compliance. MUST include: batch numbers, molecule name, manufacturer code. "
        "Compute and state affected customer count from merchant's customer_aggregate. "
        "Offer: draft WhatsApp note + replacement pickup workflow."
    ),
    "chronic_refill_due": (
        "Customer-facing refill reminder. MUST include: full molecule names, run-out date. "
        "Compute: total ₹ + savings from active senior/loyalty offer. "
        "Confirm delivery. Two-channel CTA (Reply CONFIRM + phone number)."
    ),
    "customer_lapsed_hard": (
        "Customer win-back. No shame framing ('happens to most members, no judgment'). "
        "Reference their past goal explicitly. Propose a specific new offering matching that goal. "
        "No-commitment trial CTA with date. 'Reply YES — no commitment, no auto-charge'."
    ),
    "gbp_unverified": (
        "Concrete uplift estimate: X% more traffic/discovery after GBP verification. "
        "Simple path: 'takes ~10 min, I'll guide you step by step'. Easy binary commit."
    ),
    "renewal_due": (
        "Anchor on value received: total views, calls, leads in the subscription period. "
        "Days remaining creates urgency. Low-friction renewal path. Loss aversion: 'without Pro, visibility drops'."
    ),
    "active_planning_intent": (
        "Merchant asked for something specific — DELIVER IT, don't ask qualifying questions. "
        "Draft the full artifact IN the message (package tiers, pricing, copy). "
        "Show the output, offer one refinement ask at the end."
    ),
    "trial_followup": (
        "Customer tried a service. Name them. State their exact trial date. "
        "Offer: specific next session (day + date + time). Warmth + conversion offer. Low-friction commit."
    ),
    "category_seasonal": (
        "Seasonal demand shift with specific product/service and % uplift data. "
        "Actionable recommendation: what to restock, what to feature, what to promote this season."
    ),
    "seasonal_perf_dip": (
        "Reframe dip as normal: 'every [category] in [city] sees -X to -Y% in this window'. "
        "Show peer range. Advise: skip ad spend now, save for high-conversion months. "
        "Propose retention action for existing members/customers."
    ),
    "cde_opportunity": (
        "Professional development hook. Include: CDE credits, session details, cost. "
        "Peer social proof: 'other dentists in your vertical are attending'. Low-friction RSVP CTA."
    ),
    "wedding_package_followup": (
        "Bridal customer follow-up. Include: exact days-to-wedding count. "
        "Skin-prep/booking window creates urgency. Reference their preferred slot or trial service. "
        "Personal + warm, merchant_on_behalf voice."
    ),
}


def _build_system_prompt(category: dict, trigger: dict) -> str:
    slug = category.get("slug", "general")
    voice = VOICE_PROFILES.get(slug, "Professional, helpful, specific.")
    trg_kind = trigger.get("kind", "generic")
    trg_guidance = TRIGGER_GUIDANCE.get(trg_kind, "Be specific, useful, grounded in the actual context. No generic messages.")

    return f"""You are Vera, magicpin's AI assistant for merchant growth. You compose WhatsApp messages for Indian merchants.

CATEGORY: {slug}
VOICE RULES:
{voice}

TRIGGER TYPE: {trg_kind}
COMPOSITION GUIDANCE:
{trg_guidance}

HARD CONSTRAINTS (violations = score 0):
1. Body MUST be ≤ 320 characters. Count carefully before responding.
2. NO URLs — ever. Not even shortened ones.
3. EXACTLY ONE CTA. Place it as the final sentence only.
4. NEVER fabricate data not present in the context. Use only what is given.
5. No long preambles. Start with the hook or the data point.

QUALITY SIGNALS (use 1-3 per message):
- Specificity: real numbers, dates, source citations, batch numbers, molecule names
- Loss aversion: "you're missing X", "before this window closes", "drifting away"
- Social proof: "top 10% of merchants", "every gym in HSR sees this"
- Effort externalization: "I've already drafted X — just say go"
- Curiosity gap: "want to see who?", "want the full list?"
- Single binary commit: "Reply YES / Reply CONFIRM / Reply 1 or 2"

LANGUAGE:
- Hindi-English code-mix when merchant/customer language_pref includes 'hi'
- Address merchants by owner_first_name (not "Hi there" or just "Hi")
- For customer-facing: send_as = merchant_on_behalf. For merchant-facing: send_as = vera.
- Never re-introduce yourself after first message.

Respond ONLY with valid JSON (no markdown fences, no extra text):
{{
  "body": "<message text, strictly ≤320 chars>",
  "cta": "<open_ended | binary_yes_no | binary_confirm_cancel | multi_choice_slot | none>",
  "send_as": "<vera | merchant_on_behalf>",
  "template_name": "vera_{trg_kind}_v1",
  "template_params": ["<param1>", "<param2>", "<param3>"],
  "suppression_key": "<copy from trigger context>",
  "rationale": "<1-2 sentences: which specific signal drove this message and the compulsion mechanism used>"
}}"""


def _build_user_prompt(category: dict, merchant: dict, trigger: dict, customer) -> str:
    identity = merchant.get("identity", {})
    perf = merchant.get("performance", {})
    delta = perf.get("delta_7d", {})
    offers = merchant.get("offers", [])
    active_offers = [f"{o['title']} (₹{o.get('price','?')})" if o.get('price') else o['title']
                     for o in offers if o.get("status") == "active"]
    sigs = merchant.get("signals", [])
    conv_hist = merchant.get("conversation_history", [])
    cust_agg = merchant.get("customer_aggregate", {})
    sub = merchant.get("subscription", {})
    rev_themes = merchant.get("review_themes", [])

    digest = category.get("digest", [])
    peer_stats = category.get("peer_stats", {})
    seasonal = category.get("seasonal_beats", [])
    trends = category.get("trend_signals", [])
    offer_catalog = category.get("offer_catalog", [])

    trg_payload = trigger.get("payload", {})

    # Pre-compute key numbers so the LLM doesn't have to derive them
    views_delta = delta.get("views_pct", 0) or 0
    calls_delta = delta.get("calls_pct", 0) or 0
    days_remaining = sub.get("days_remaining", "?")
    sub_status = sub.get("status", "unknown")
    lapsed_count = cust_agg.get("lapsed_count", 0)
    active_count = cust_agg.get("active_count", 0)

    trend_parts = []
    for t in trends[:3]:
        q = t.get("query", "")
        d = t.get("delta_yoy", 0) or 0
        trend_parts.append(f"{q} +{int(d*100)}% YoY")

    lines = [
        "=== CATEGORY CONTEXT ===",
        f"Slug: {category.get('slug')}",
        f"Peer stats: avg_rating={peer_stats.get('avg_rating')}, avg_ctr={peer_stats.get('avg_ctr')}, scope={peer_stats.get('scope', '')}",
        f"Offer catalog examples: {[o.get('title') for o in offer_catalog[:4]]}",
        f"Seasonal beats: {[s.get('note') for s in seasonal[:3]]}",
        f"Trend signals: {trend_parts}",
        f"Digest items: {json.dumps(digest[:3], ensure_ascii=False)}",
        "",
        "=== MERCHANT CONTEXT ===",
        f"Name: {identity.get('name')}",
        f"Owner first name: {identity.get('owner_first_name')} (USE THIS when addressing the merchant)",
        f"City / Locality: {identity.get('city')}, {identity.get('locality')}",
        f"Verified GBP: {identity.get('verified')}",
        f"Languages spoken: {identity.get('languages', [])}",
        f"Subscription: status={sub_status}, plan={sub.get('plan')}, days_remaining={days_remaining}",
        f"Performance (30d): views={perf.get('views')}, calls={perf.get('calls')}, ctr={perf.get('ctr')}, leads={perf.get('leads')}",
        f"7-day delta: views={views_delta:+.1f}%, calls={calls_delta:+.1f}%",
        f"Active offers (use these, do not invent offers): {active_offers}",
        f"Customer aggregate: active={active_count}, lapsed={lapsed_count}, full={json.dumps(cust_agg, ensure_ascii=False)}",
        f"Signals: {sigs}",
        f"Review themes (use verbatim if referencing): {json.dumps(rev_themes[:3], ensure_ascii=False)}",
        f"Recent conversation history (last 3 turns): {json.dumps(conv_hist[-3:], ensure_ascii=False)}",
        "",
        "=== TRIGGER CONTEXT ===",
        f"ID: {trigger.get('id')}",
        f"Kind: {trigger.get('kind')}",
        f"Urgency: {trigger.get('urgency')} / 5",
        f"Suppression key (copy this into your response): {trigger.get('suppression_key')}",
        f"Expires: {trigger.get('expires_at')}",
        f"Payload (all data from trigger — USE these numbers): {json.dumps(trg_payload, ensure_ascii=False)}",
    ]

    if customer:
        cust_id = customer.get("identity", {})
        rel = customer.get("relationship", {})
        prefs = customer.get("preferences", {})
        consent = customer.get("consent", {}).get("scope", [])

        # Compute days/months since last visit if available
        last_visit = rel.get("last_visit", "")
        months_hint = ""
        if last_visit:
            try:
                from datetime import datetime
                lv = datetime.fromisoformat(last_visit.replace("Z", "+00:00"))
                now = datetime.now(lv.tzinfo)
                months_ago = round((now - lv).days / 30)
                months_hint = f" (~{months_ago} months ago)"
            except Exception:
                pass

        lines += [
            "",
            "=== CUSTOMER CONTEXT (direct outreach — send_as = merchant_on_behalf) ===",
            f"Name: {cust_id.get('name')} (address by first name)",
            f"Language preference: {cust_id.get('language_pref')} (HONOR THIS — mix Hindi-English if 'hi')",
            f"Age band: {cust_id.get('age_band')}",
            f"State: {customer.get('state')}",
            f"Last visit: {last_visit}{months_hint}",
            f"Total visits: {rel.get('visits_total')}",
            f"Services received: {rel.get('services_received', [])}",
            f"Lifetime value: ₹{rel.get('lifetime_value', 0)}",
            f"Slot preferences: {prefs.get('slot_preferences', [])}",
            f"Consent scope: {consent}",
        ]

    lines.append("")
    lines.append(
        "NOW COMPOSE. Use the trigger payload numbers directly. "
        "Reference the merchant by owner_first_name. "
        "Stay strictly ≤320 chars. Return JSON only."
    )

    return "\n".join(lines)


def compose_message(category: dict, merchant: dict, trigger: dict, customer=None) -> dict:
    """
    Core compose function — the competition entry point.
    Returns: {body, cta, send_as, template_name, template_params, suppression_key, rationale}
    """
    system = _build_system_prompt(category, trigger)
    user = _build_user_prompt(category, merchant, trigger, customer)

    client = _get_client()

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=600,
        )
        raw = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[GROQ ERROR] {e}")
        return _fallback_compose(merchant, trigger)

    result = _parse_json_response(raw)
    if not result:
        return _fallback_compose(merchant, trigger)

    # Enforce constraints
    body = result.get("body", "")
    body = re.sub(r'https?://\S+', '', body).strip()
    if len(body) > 320:
        body = body[:317] + "..."
    result["body"] = body

    if not result.get("suppression_key"):
        result["suppression_key"] = trigger.get("suppression_key", f"trg:{trigger.get('id', 'unknown')}")

    return result


def _parse_json_response(raw: str):
    """Extract JSON from LLM response robustly."""
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Strip markdown fences if present
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


def _fallback_compose(merchant: dict, trigger: dict) -> dict:
    """
    Meaningful fallback — uses the trigger kind to produce a relevant message
    rather than a totally generic one, so the bot still scores something.
    """
    identity = merchant.get("identity", {})
    name = identity.get("owner_first_name") or identity.get("name", "there")
    kind = trigger.get("kind", "update")
    sup_key = trigger.get("suppression_key", f"fallback:{trigger.get('id', 'x')}")
    payload = trigger.get("payload", {})

    # Kind-specific fallbacks
    if kind == "perf_dip":
        views_pct = payload.get("views_delta_pct") or payload.get("calls_delta_pct", "")
        metric = f"{views_pct}%" if views_pct else "in your key metrics"
        body = f"{name}, there's been a dip {metric} this week. Want me to look at your active offers and suggest one fix? Reply YES."
    elif kind == "renewal_due":
        days = trigger.get("payload", {}).get("days_remaining", "soon")
        body = f"{name}, your magicpin subscription expires in {days} days. Want a quick look at your value stats before deciding? Reply YES."
    elif kind == "research_digest":
        body = f"Dr. {name}, new research relevant to your patients just landed. Want me to pull the abstract and draft a patient-ed note? Reply YES."
    elif kind == "recall_due":
        body = f"Your patient's recall window is open. Want me to draft a WhatsApp reminder with available slots? Reply YES."
    elif kind == "supply_alert":
        body = f"{name}, there's an urgent supply alert affecting some of your customers. Want me to draft their notification? Reply YES."
    else:
        body = f"{name}, I have an update relevant to your business. Want me to share the details? Reply YES."

    if len(body) > 320:
        body = body[:317] + "..."

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "template_name": f"vera_{kind}_v1",
        "template_params": [name],
        "suppression_key": sup_key,
        "rationale": f"Fallback message for {kind}. LLM unavailable — using kind-specific template."
    }
