# Provenance Guard

An API that accepts text content and returns an attribution analysis: was this written by a human or an AI? It returns a confidence score, a structured audit log, and a plain-language transparency label suitable for display to readers.

---

## Architecture Narrative: The Path of a Piece of Text

A single piece of text flows through the following components in order:

**1. Rate Limiter**
Before any processing, flask-limiter checks the caller's IP against a sliding-window counter. Requests over the limit are rejected with HTTP 429 before any pipeline work begins.

**2. Submission Handler**
The `/analyze` endpoint validates the request body, assigns the content a UUID (`content_id`), and records a `submitted_at` timestamp. It then hands the text to the Detection Pipeline.

**3. Multi-Signal Detection Pipeline**
Two independent signals run against the text:

- **LLM Classifier (Signal 1):** The text is sent to Groq's Llama model with a prompt asking it to estimate the probability the content was AI-generated (0.0–1.0). This signal captures semantic and stylistic patterns — unnaturally even sentence cadence, hedged language, broad topical coherence, and the "polished vagueness" characteristic of LLM output. It sees things a rule system cannot.

- **Heuristic Analyzer (Signal 2):** A local function computes surface-level statistical features without any external call: type-token ratio (lexical diversity), sentence length variance, and frequency of known LLM filler phrases ("it's important to note," "in conclusion," "delve into," etc.). This signal is fast, deterministic, and provides a cross-check on the LLM signal — if both agree, confidence is higher; if they disagree, confidence is lower. It returns its own 0–1 score.

The pipeline combines both scores with a weighted average into a single `confidence_score`.

**4. Confidence Scoring & Threshold Logic**
The combined score maps to one of three classification buckets:
- `>= 0.75` → `ai_generated` (high confidence)
- `<= 0.35` → `human_written` (high confidence)
- Between 0.35–0.75 → `uncertain` (low confidence)

A score of 0.51 and a score of 0.95 both map to "AI-leaning" but produce very different labels. The raw float is preserved in the audit log regardless of bucket.

**5. Transparency Label Generator**
The label generator takes the classification bucket and the raw score and produces a human-readable `label` object. See label variants below.

**6. Audit Logger**
Before the response is returned, a structured JSON record is appended to the audit log: `content_id`, `submitted_at`, classification, confidence score, signals array, label text, and status. This record is the system's permanent account of its decision.

**7. Response**
The caller receives the full structured response: `content_id`, classification, confidence score, signals used, status, and label.

---

## Transparency Label Variants

The label is what a reader would see on the platform. Three variants exist:

**High-confidence AI** (confidence >= 0.75):
> "This content was likely written with AI assistance (confidence: X%). Our system detected patterns consistent with AI-generated text across multiple signals."

**High-confidence Human** (confidence <= 0.35):
> "This content appears to have been written by a human (confidence: X%). Our system found no significant markers of AI generation."

**Uncertain** (confidence between 0.35–0.75):
> "Our system could not confidently determine the origin of this content (confidence: X%). It may have been written by a human, AI, or a combination of both. Treat this label with caution."

The "confidence: X%" shown in each label is derived from the raw score — for AI labels it shows how certain the system is that the content is AI-generated; for human labels it shows certainty of human authorship. A reader seeing 52% uncertain gets a meaningfully weaker signal than a reader seeing 94% AI.

---

## Detection Signals

| Signal | What it captures | Why chosen |
|---|---|---|
| LLM Classifier (Groq/Llama) | Semantic and stylistic patterns, topic coherence, hedging, polished vagueness | LLMs are uniquely good at recognizing LLM output patterns that resist rule-based detection |
| Heuristic Analyzer | Type-token ratio, sentence length variance, filler phrase frequency | Fast, deterministic, interpretable — provides a cross-check and catches surface-level markers without an API call |

Using two signals matters: if the LLM signal alone fired, the system could be fooled by a confident model hallucinating. If the heuristic alone fired, it could flag a formulaic human writer. Agreement between both signals drives confidence up; disagreement drives it down toward the uncertain bucket.

---

## Rate Limits

| Endpoint | Limit | Reasoning |
|---|---|---|
| `POST /submit` | **10 requests / minute / IP** | Each request calls the Groq LLM API, making it non-trivial in cost and latency. A writer submitting their own work would rarely need more than 1–2 requests per minute; 10 is generous for legitimate use while blocking automated scraping. A script flooding the system hits the cap after 10 requests and receives 429s for the rest of the minute. |
| `POST /appeal` | **20 requests / minute / IP** | Appeals involve no LLM call (cheap), but are bounded to prevent a script from flooding a single content record or spamming the audit log. 20/min is more than sufficient for any human workflow. |
| `GET /log` | **No limit** | Read-only, no external calls, no write side-effects. |

### Rate limit evidence

Running 12 rapid requests against `POST /submit` (limit: 10/min):

```
200   ← request 1
200   ← request 2
200   ← request 3
200   ← request 4
200   ← request 5
200   ← request 6
200   ← request 7
200   ← request 8
200   ← request 9
200   ← request 10
429   ← request 11 (limit exceeded)
429   ← request 12 (limit exceeded)
```

429 response body:
```json
{
    "error": "rate_limit_exceeded",
    "message": "Too many requests. Please slow down."
}
```

---

## Appeals Workflow

`POST /appeal/<content_id>` accepts:
```json
{
  "creator_id": "optional",
  "reason": "required — the creator's explanation"
}
```

The handler:
1. Looks up the original audit log entry by `content_id`
2. Appends an `appeal` object with `reason`, `creator_id`, and `appealed_at`
3. Updates the entry's `status` from `decided` → `under_review`
4. Returns confirmation with the updated status

No automated re-classification occurs. The appeal is a data record for human review.

---

## Audit Log

Every attribution decision is captured as a structured JSON record before the response is returned. Retrieve via `GET /log`. Supports `?status=decided|under_review` and `?limit=N` query params.

Each record contains:

| Field | Type | Description |
|---|---|---|
| `content_id` | string | UUID assigned at submission |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `creator_id` | string\|null | Submitter identifier (optional) |
| `attribution` | string | `ai_generated`, `human_written`, or `uncertain` |
| `confidence` | float | Combined score (0.0–1.0) |
| `llm_score` | float | Groq/Llama signal score (0.0–1.0, or -1.0 if failed) |
| `heuristic_score` | float | Statistical signal score (0.0–1.0) |
| `signals_used` | array | `[{name, score}]` — both signals listed individually |
| `label` | object | Full label shown to readers, including `appeal_notice` after appeal |
| `status` | string | `decided` or `under_review` |
| `appeal` | object\|null | `null` until appealed; then `{reason, creator_id, appealed_at}` |

### Sample `GET /log` output (3 entries)

```json
{
  "count": 3,
  "entries": [
    {
      "appeal": null,
      "attribution": "uncertain",
      "confidence": 0.6162,
      "content_id": "0379e6bc-ab5a-4462-abad-d07a76ecca76",
      "creator_id": "test-academic",
      "heuristic_score": 0.275,
      "label": {
        "appeal_cta": null,
        "appeal_notice": null,
        "confidence_text": "Our system could not confidently determine the origin of this content.",
        "explanation": "This content could not be confidently attributed to either a human or an AI. Our two signals did not agree strongly enough to reach a verdict. This label should not be treated as an accusation or a clearance.",
        "verdict": "Origin unclear"
      },
      "llm_score": 0.8,
      "signals_used": [
        {"name": "llm_classifier", "score": 0.8},
        {"name": "heuristic", "score": 0.275}
      ],
      "status": "decided",
      "timestamp": "2026-07-01T04:49:08.364380+00:00"
    },
    {
      "appeal": null,
      "attribution": "human_written",
      "confidence": 0.2158,
      "content_id": "7e0f7508-5514-42b9-9629-d1987ef42218",
      "creator_id": "test-human",
      "heuristic_score": 0.2267,
      "label": {
        "appeal_cta": null,
        "appeal_notice": null,
        "confidence_text": "Our system is 78% confident this content was written by a human.",
        "explanation": "This content shows patterns consistent with human authorship. Our system evaluated it using two independent signals — a language model classifier and a statistical analyzer — and neither detected significant markers of AI generation.",
        "verdict": "Likely human-written"
      },
      "llm_score": 0.21,
      "signals_used": [
        {"name": "llm_classifier", "score": 0.21},
        {"name": "heuristic", "score": 0.2267}
      ],
      "status": "decided",
      "timestamp": "2026-07-01T04:49:08.255730+00:00"
    },
    {
      "appeal": {
        "appealed_at": "2026-07-01T04:49:08.509752+00:00",
        "creator_id": "test-ai",
        "reason": "I wrote this myself. The structured format reflects my academic background, not AI generation."
      },
      "attribution": "ai_generated",
      "confidence": 0.7743,
      "content_id": "428b2cd1-1000-48da-bae0-e3e991ad06a8",
      "creator_id": "test-ai",
      "heuristic_score": 0.7265,
      "label": {
        "appeal_cta": "Think this is wrong? Creators can contest this classification.",
        "appeal_notice": "This classification is under review following a creator appeal. The verdict above may change.",
        "confidence_text": "Our system is 77% confident this content was AI-generated.",
        "explanation": "This content shows patterns consistent with AI-generated text. Our system evaluated it using two independent signals — a language model classifier and a statistical analyzer — and both indicate AI authorship.",
        "verdict": "AI-generated"
      },
      "llm_score": 0.8,
      "signals_used": [
        {"name": "llm_classifier", "score": 0.8},
        {"name": "heuristic", "score": 0.7265}
      ],
      "status": "under_review",
      "timestamp": "2026-07-01T04:49:08.015551+00:00"
    }
  ]
}
```

---

## API Reference

### `POST /analyze`
Submit text for attribution analysis.

**Request:**
```json
{ "text": "your content here" }
```

**Response:**
```json
{
  "content_id": "uuid",
  "classification": "ai_generated | human_written | uncertain",
  "confidence_score": 0.87,
  "signals_used": [
    {"name": "llm_classifier", "score": 0.91},
    {"name": "heuristic", "score": 0.74}
  ],
  "status": "decided",
  "label": {
    "verdict": "AI-generated",
    "confidence_text": "87% confidence",
    "explanation": "This content was likely written with AI assistance..."
  }
}
```

### `POST /appeal/<content_id>`
Contest a classification.

**Request:**
```json
{ "reason": "I wrote this myself — it reflects my personal experience." }
```

**Response:**
```json
{
  "content_id": "uuid",
  "status": "under_review",
  "appeal_received_at": "2026-06-30T12:00:00Z"
}
```

### `GET /log`
Retrieve the full audit log.

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add your GROQ_API_KEY
python app.py
```
