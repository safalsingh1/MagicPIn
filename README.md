# Vera magicpin AI Challenge Submission

## Approach

This bot is a stateful FastAPI implementation of the required Vera endpoints:

- `GET /v1/healthz`
- `GET /v1/metadata`
- `POST /v1/context`
- `POST /v1/tick`
- `POST /v1/reply`

The composer is deterministic. It routes by `trigger.kind`, reads the four contexts
available to Vera, chooses the strongest current signal, and renders one short
message with one CTA. It does not rely on an LLM for the scoring path, which keeps
outputs stable for replay and prevents hallucinated offers, numbers, or capabilities.

## What Changed In v3

- `delta_pct` values such as `-0.50` are normalized to `50%`, not `0.5%`.
- IPL triggers are composed whenever the judge includes them in `available_triggers`,
  even if the wall-clock expiry has passed during replay.
- Customer replies route separately from merchant replies, so Priya is addressed as
  Priya, not as the merchant owner.
- Auto-replies and STOP/hostile replies end immediately to avoid loops.
- If replay starts with a fresh in-memory state, `/reply` can infer the trigger from
  `conversation_id` or `customer_id`.

## Decision Logic

Each trigger family has a dedicated renderer:

- Dentists: clinical language, exact sources, DCI/IOPA dose details, patient recall slots.
- Salons: warm service-at-price offers, bridal timing, local demand questions.
- Restaurants: operator language, covers/delivery decisions, review theme fixes.
- Gyms: no-shame retention, seasonal acquisition dips, trial/session conversion.
- Pharmacies: precise molecule, batch, regulator, refill, and senior-friendly messages.

The output includes `body`, `cta`, `send_as`, `template_name`, `template_params`,
`suppression_key`, and `rationale`. Bodies are capped at 320 characters and URLs are
stripped.

## Running Locally

```bash
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
```

Generate the deterministic sample output:

```bash
python generate_submission.py
```

The bot stores context in memory and treats context pushes as idempotent by
`scope + context_id + version`.
