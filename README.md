# Vera â€” magicpin AI Challenge Submission

## Approach

**Vera Composer** is a stateful FastAPI bot implementing the 4-context message composition framework from the challenge brief.

### Architecture

```
Judge Harness â”€â”€â–º POST /v1/context  â”€â”€â–º in-memory store (scope, context_id) â†’ {version, payload}
                  POST /v1/tick     â”€â”€â–º tick engine â†’ composer â†’ LLM (Groq llama-3.3-70b)
                  POST /v1/reply    â”€â”€â–º reply handler â†’ auto-reply detect / intent route / LLM
                  GET  /v1/healthz  â”€â”€â–º liveness + context count
                  GET  /v1/metadata â”€â”€â–º bot identity
```

### Key design decisions

**1. Trigger-kind dispatch**
Every trigger kind (`research_digest`, `recall_due`, `perf_dip`, `festival_upcoming`, etc.) gets its own prompt guidance block. This ensures the LLM frames the message correctly for each situation â€” clinical source-citation for research digests, urgency framing for compliance alerts, no-shame warmth for customer winbacks.

**2. Deterministic at temperature=0**
Groq API calls use `temperature=0.0`. Same input â†’ same output on every run.

**3. Auto-reply detection (pattern + count)**
The reply handler uses 10+ regex patterns to detect WhatsApp Business auto-replies. On first detection: gentle owner-directed nudge. On second: `wait` 4 hours. On third: `end`. Matches the production Vera pattern from the case studies.

**4. Intent transition routing**
Commit phrases ("let's do it", "yes please", "go ahead", "haan", "chalo") are detected with regex before the LLM sees the reply. On commit: the bot switches immediately from pitch mode to action mode â€” drafting, confirming, launching. No qualifying questions after a yes.

**5. Suppression dedup**
Every action's `suppression_key` is recorded in memory. Duplicate triggers with the same key are skipped on subsequent ticks.

**6. Body length enforcement**
Both composer and reply handler strip URLs and truncate to â‰¤320 chars post-LLM. The system prompt instructs the LLM to target 150-200 chars.

**7. Category voice profiles**
Five distinct voice profiles (dentists, salons, restaurants, gyms, pharmacies) are injected into the system prompt. Dentists get clinical vocabulary + source citation requirements. Pharmacies get precision + regulatory framing. Restaurants get operator jargon. This directly addresses the "category fit" scoring dimension.

**8. Hindi-English code-mix**
The system prompt instructs code-mix for merchants/customers with `hi` in their language preference. The LLM naturally produces mixed-language output matching Indian WhatsApp norms.

### Model choice

**Groq llama-3.3-70b-versatile** â€” fast (sub-2s), capable of following strict JSON output format, good at Hindi-English code-mix. Free tier adequate for the challenge volume. Temperature=0 for determinism.

### What additional context would help most

1. **Real slot availability** â€” without live clinic schedules, slot offers are constructed from trigger payload data only.
2. **Live offer catalog** â€” merchant offers are taken from `MerchantContext.offers`; a real-time catalog would enable fresher pricing.
3. **Merchant's actual customer list** for aggregate-based derived counts (e.g., "22 of your 240 chronic-Rx customers").

## Running locally

```bash
cd Desktop/magicpin
pip install -r requirements.txt
set GROQ_API_KEY=your_api_key_here
uvicorn bot:app --host 0.0.0.0 --port 8080 --reload
```

Then run the judge simulator:

```bash
# In the challenge directory
cd Downloads/magicpin-ai-challenge
python judge_simulator.py
```

## Generating submission.jsonl

```bash
cd Desktop/magicpin
python generate_submission.py
```

## File structure

```
magicpin/
â”œâ”€â”€ bot.py                  # FastAPI server (5 endpoints)
â”œâ”€â”€ composer.py             # LLM composer (4-context â†’ message)
â”œâ”€â”€ reply_handler.py        # Multi-turn reply handler
â”œâ”€â”€ bot_standalone.py       # compose() function for JSONL generation
â”œâ”€â”€ generate_submission.py  # Generates submission.jsonl
â”œâ”€â”€ requirements.txt
â””â”€â”€ README.md
```
