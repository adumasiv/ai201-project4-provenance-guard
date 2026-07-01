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
| `POST /analyze` | 10 requests / minute / IP | Each request calls an external LLM API, making it non-trivial in cost and latency. 10/min is generous for interactive use while blocking automated scraping or abuse. |
| `POST /appeal/<content_id>` | 20 requests / minute / IP | Appeals are cheaper (no LLM call) but still bounded to prevent flooding a single content record. |

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

Every attribution decision is captured as a structured record. Retrieve via `GET /log`.

Each record contains:
- `content_id` — UUID assigned at submission
- `submitted_at` — ISO 8601 timestamp
- `classification` — `ai_generated`, `human_written`, or `uncertain`
- `confidence_score` — raw float (0.0–1.0)
- `signals_used` — array of `{name, score}` for each signal
- `label` — the full label object shown to the reader
- `status` — `decided` or `under_review`
- `appeal` — `null` or `{reason, creator_id, appealed_at}`

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
