# Provenance Guard — Planning

---

## Architecture

In the **submission flow**, a POST /analyze request passes through a rate limiter, then a submission handler that assigns a UUID and timestamp, then two independent detection signals (an LLM classifier via Groq and a local heuristic analyzer) whose scores are weighted and combined into a single confidence float, which drives a label generator that produces plain-language text, after which the full record is written to the audit log and the response is returned to the caller. In the **appeal flow**, a POST /appeal request looks up the existing audit record by content_id, validates that no prior appeal exists, mutates the record's status from `decided` to `under_review` and appends the appeal object, then returns a confirmation — no re-classification occurs. Every decision and every appeal lands in the audit log before any response leaves the server.

```
╔══════════════════════════════════════════════════════════════════╗
║  FLOW 1 — SUBMISSION                                             ║
╚══════════════════════════════════════════════════════════════════╝

POST /analyze
    │
    │  raw request
    ▼
┌─────────────────┐
│  Rate Limiter   │ ──── 429 Too Many Requests
│  10 req/min/IP  │
└────────┬────────┘
         │  raw request (passed)
         ▼
┌─────────────────────┐
│ Submission Handler  │  assigns uuid, submitted_at
└──────────┬──────────┘
           │  text + uuid
           ▼
┌──────────────────────────────────────────────────┐
│               DETECTION PIPELINE                 │
│                                                  │
│   ┌───────────────────┐  ┌────────────────────┐  │
│   │  LLM Classifier   │  │ Heuristic Analyzer │  │
│   │   Groq / Llama    │  │ TTR · var · fillers│  │
│   │   (semantic)      │  │   (statistical)    │  │
│   └────────┬──────────┘  └─────────┬──────────┘  │
│            │ P(AI): 0.0–1.0        │ P(AI): 0.0–1.0
└────────────┼───────────────────────┼─────────────┘
             └──────────┬────────────┘
                        │  score₁ (LLM, 65%) + score₂ (heuristic, 35%)
                        ▼
             ┌──────────────────────┐
             │   Score Combiner     │
             │  65% LLM + 35% heur  │
             └──────────┬───────────┘
                        │  combined score (0.0–1.0) + bucket
                        ▼
             ┌──────────────────────┐
             │   Label Generator    │
             │  AI | Human | Uncert │
             └──────────┬───────────┘
                        │  label {verdict, confidence_text, explanation}
                        ▼
             ┌──────────────────────┐
             │    Audit Logger      │  written before response
             └──────────┬───────────┘
                        │  full record
                        ▼
             ┌──────────────────────┐
             │       Response       │ ──── 200 JSON → caller
             └──────────────────────┘


╔══════════════════════════════════════════════════════════════════╗
║  FLOW 2 — APPEAL                                                 ║
╚══════════════════════════════════════════════════════════════════╝

POST /appeal/<content_id>
    │
    │  content_id + reason
    ▼
┌─────────────┐
│  ID Lookup  │ ──── 404 Not Found
└──────┬──────┘
       │  record ref
       ▼
┌───────────────────┐
│  Status Updater   │ ──── 409 Already Appealed
│ decided →         │
│   under_review    │
└────────┬──────────┘
         │  appeal object {reason, creator_id, appealed_at}
         ▼
┌─────────────────────┐
│    Audit Logger     │  mutates existing record in place
└──────────┬──────────┘
           │  updated record
           ▼
┌─────────────────────────────┐
│          Response           │ ──── 200 JSON → caller
│  {content_id,               │
│   status: under_review,     │
│   appeal_received_at}       │
└─────────────────────────────┘


KEY
───
  text + uuid          validated input with assigned ID and timestamp
  P(AI): 0.0–1.0       each signal's independent AI-probability estimate
  combined score        weighted float (0.0–1.0) + threshold bucket
  label object          {verdict, confidence_text, explanation}
  full record           all of the above + signals_used; stored and returned
  appeal object         {reason, creator_id, appealed_at} appended to record
  ────                 normal data flow
  - - ─               error branch (request terminates here)
```

---

## AI Tool Plan

This section specifies, for each implementation milestone, exactly which planning.md sections to paste into the AI tool prompt, what to ask it to generate, and how to verify the output is correct before moving on. The goal is to never ask the AI tool to generate code from a blank context — every prompt starts from the written spec.

---

### M3 — Submission Endpoint + First Signal

**Spec sections to provide:**
- `## Architecture` (full diagram + 2-sentence narrative)
- `## 1. Detection Signals` → Signal 1 (LLM Classifier) subsection only: what it measures, the exact Groq prompt, output format, and the -1.0 sentinel spec

**What to ask the AI tool to generate:**
1. A Flask app skeleton (`app.py`) with one route: `POST /analyze`. The route should validate the request body (non-empty `text`, 50–10,000 chars), assign a UUID and `submitted_at` timestamp, call a `run_llm_signal(text)` stub that returns a hardcoded 0.5, and return a partial JSON response containing `content_id`, `submitted_at`, and `signals_used` with the stub score. No confidence scoring or labeling yet.
2. The `pipeline/llm_signal.py` module implementing `run_llm_signal(text) -> float`. It should call the Groq API with the exact prompt from the spec, parse the `ai_probability` field from the JSON response, return the float, and return -1.0 on any exception.

**How to verify before wiring:**
- Call `run_llm_signal()` directly in a Python REPL with three inputs: a paragraph of obvious GPT output (expect score > 0.65), a paragraph of casual personal writing with contractions and specific details (expect score < 0.45), and a paragraph of academic writing (expect score somewhere in the middle, not confidently either way).
- Confirm the function returns a float in [0.0, 1.0], not a string or dict.
- Confirm it returns -1.0, not an exception, when the API key is wrong or the network is down.
- Hit `POST /analyze` with curl and confirm the response shape matches the data contract in `api_contract.md`.

---

### M4 — Second Signal + Confidence Scoring

**Spec sections to provide:**
- `## Architecture` (diagram — so the model knows where the combiner sits)
- `## 1. Detection Signals` → Signal 2 (Heuristic Analyzer) subsection: TTR definition, sentence variance definition, filler phrase list, the three sub-feature weights, the heuristic_score formula, and the short-text weight-halving rule
- `## 2. Uncertainty Representation` in full: the combiner formula, the fallback cases, the three threshold buckets with their exact boundaries (0.35, 0.75), and the rate limit on the heuristic weight below 100 words

**What to ask the AI tool to generate:**
1. The `pipeline/heuristic_signal.py` module implementing `run_heuristic_signal(text) -> float`. It must implement sliding-window TTR (window=50), sentence length variance normalized to [0,1], filler phrase density per 100 words normalized to [0,1], and the weighted combination formula. Word count < 100 → halve heuristic weight before returning.
2. The `pipeline/pipeline.py` module implementing `run_pipeline(text) -> dict`. It calls both signal functions, applies `(llm * 0.65) + (heuristic * 0.35)` with -1.0 fallbacks, maps the combined score to a classification bucket, and returns `{classification, confidence_score, signals_used}`.

**What to check — do scores vary meaningfully?**
- Run `run_pipeline()` on at least four inputs and record all signal scores and combined scores:
  - A 300-word ChatGPT essay on climate change (expect combined > 0.72)
  - A 300-word personal blog post with anecdotes, contractions, and typos (expect combined < 0.40)
  - A 300-word academic paper excerpt (expect heuristic fires, LLM uncertain — combined lands 0.45–0.65)
  - A 60-word poem (expect heuristic weight halved, combined closer to LLM score alone)
- Confirm that a 0.51 score and a 0.91 score produce meaningfully different labels downstream (they won't yet — label generation is M5 — but verify the bucket assignment: 0.51 → `uncertain`, 0.91 → `ai_generated`).
- Confirm that `signals_used` records both raw scores individually, not just the combined score.

---

### M5 — Production Layer (Labels + Appeals + Audit Log)

**Spec sections to provide:**
- `## Architecture` (both flows — submission and appeal — the combiner section is already wired)
- `## 3. Transparency Label Design` in full: all three variant templates with exact strings, the `{pct}` substitution rule including the human-label inversion (`1 - score`), the `{direction}` logic for uncertain labels, and the contested-label suffix
- `## 4. Appeals Workflow` in full: who can appeal, the request/response shapes, the four steps the handler takes, the 409 duplicate guard, and the reviewer-facing log format
- `## 8. Data Contracts` (all three response shapes as reference)

**What to ask the AI tool to generate:**
1. `labels.py` implementing `generate_label(classification, confidence_score) -> dict`. It must handle all three classification values, apply the correct `{pct}` formula per variant (including the human-label inversion), substitute `{direction}` for uncertain labels, and append the contested suffix when called with `status="under_review"`.
2. `audit.py` implementing an in-memory audit log with `append_record(record)`, `get_record(content_id) -> dict | None`, `update_appeal(content_id, appeal_obj)`, and `get_all(status_filter=None) -> list`. Thread safety is not required.
3. The `POST /appeal/<content_id>` route in `app.py`: validate `reason` (non-empty, 10–2000 chars), look up by content_id (404 if missing), check for existing appeal (409 if present), build appeal object, call `update_appeal`, return the confirmation shape from the data contract.
4. The `GET /log` route in `app.py` with optional `?status=` and `?limit=` query params.

**How to verify all three label variants are reachable and appeals work:**
- Manually call `generate_label()` with three inputs: `("ai_generated", 0.89)`, `("human_written", 0.12)`, `("uncertain", 0.58)`. Confirm the `explanation` text matches the exact strings in the spec, the `{pct}` values are correct (89%, 88%, 58%), and the human label reads "88% confidence" not "12% confidence".
- Submit a text via `POST /analyze`, copy the `content_id`, then `POST /appeal/<content_id>` with a reason. Confirm the response status is `under_review`. Hit `GET /log` and confirm the entry shows the appeal object and `status: under_review`.
- `POST /appeal/<content_id>` a second time on the same ID. Confirm a 409 is returned and the audit record is unchanged.
- `GET /log?status=under_review` — confirm only the appealed entry appears.
- `GET /log?status=decided` — confirm the appealed entry does not appear.

---

## 1. Detection Signals

### Signal 1 — LLM Classifier (Groq / Llama 3)

**What it measures:** Holistic semantic and stylistic probability of AI authorship. The model reads the full text and estimates how likely it is that the text was produced by a language model. It attends to: consistent quality throughout without any drop in energy or focus, characteristic hedging phrases, unnaturally clean topic structure, and the "polished vagueness" of text optimized for general-audience readability rather than a specific human voice.

**Output:** A float in [0.0, 1.0] where 1.0 = certain AI. Extracted from a structured JSON response. If the model fails to return a parseable float, the signal is marked as failed and assigned a sentinel value of -1.0. The combiner handles this gracefully.

**Prompt used:**
```
You are an AI-authorship classifier. Analyze the following text and return ONLY
a JSON object with one key: "ai_probability" (float 0.0 to 1.0, where 1.0 means
you are certain the text was AI-generated and 0.0 means you are certain it was
written by a human). Consider: sentence rhythm consistency, hedging language,
structural cleanliness, vocabulary range, and presence of personal voice.
Return nothing but the JSON.
```

**Weight in combiner:** 65%

**Blind spots:** Formulaic human writing (corporate, academic, legal) scores high. Heavily edited AI drafts score low. Calibration varies across non-English text. Cannot be treated as ground truth — it is a probabilistic estimate from one model about another model's output patterns.

---

### Signal 2 — Heuristic Analyzer (local, no API call)

**What it measures:** Three surface-level statistical features computed entirely on the text without any external call:

1. **Type-Token Ratio (TTR):** `unique_words / total_words`, computed over a sliding window of 50 words to control for text length. Range: 0.0–1.0. Low TTR (repetitive vocabulary) → higher AI suspicion.

2. **Sentence Length Variance:** Standard deviation of sentence lengths in words, normalized to [0, 1] by capping at 20 words SD. Low variance (consistently similar sentence lengths) → higher AI suspicion.

3. **Filler Phrase Density:** Count of known LLM filler phrases per 100 words. Phrases: "it's important to note", "it is important to note", "in conclusion", "to summarize", "delve into", "in today's world", "at its core", "let's explore", "it's worth noting", "it is worth noting", "fundamentally", "as we can see". Normalized to [0, 1] by capping at 3 hits per 100 words.

**Combining the three sub-features into one signal score:**
```
heuristic_score = (
    (1 - normalized_TTR) * 0.35 +
    (1 - normalized_variance) * 0.30 +
    filler_density * 0.35
)
```

High heuristic_score = more AI-like surface statistics.

**Output:** A float in [0.0, 1.0].

**Weight in combiner:** 35%

**Blind spots:** Trivially defeated by instructing an LLM to vary sentence length and avoid fillers. Academic and legal writing has low TTR and low variance by convention. Short texts (< 100 words) produce unstable TTR and variance estimates — heuristic weight is halved below this threshold.

---

### Combining Signals into a Confidence Score

```
confidence_score = (llm_score * 0.65) + (heuristic_score * 0.35)
```

**Fallback when LLM signal fails (score = -1.0):**
```
confidence_score = heuristic_score
```
The `signals_used` array in the response records `"score": -1.0` for the failed signal so reviewers can see the decision was made on one signal.

**Fallback when both signals fail:**
```
classification = "uncertain"
confidence_score = 0.5
```
Return a 502 with the partial record logged.

---

## 2. Uncertainty Representation

### What a confidence score of 0.6 means

0.6 means: the two signals together lean AI-generated, but not convincingly. The LLM classifier and heuristic may be pointing in opposite directions (e.g., LLM says 0.72, heuristic says 0.38 → combined ≈ 0.60), or both may be near the middle of their range. At 0.6, the system has a real opinion — it suspects AI — but not enough signal to make an accusation. This is the zone where a false positive has the highest cost.

### Threshold map

| Range | Classification | Meaning |
|---|---|---|
| 0.75 – 1.00 | `ai_generated` | High-confidence AI. Both signals likely agree. |
| 0.35 – 0.75 | `uncertain` | Insufficient signal. Signals may disagree. |
| 0.00 – 0.35 | `human_written` | High-confidence human. Both signals likely agree. |

### Why these thresholds

The uncertain band is deliberately wide (0.40 wide) rather than narrow. The false positive cost — a human creator publicly labeled as AI — is much higher than the cost of an uncertain label. Narrowing the band to e.g. 0.45–0.65 would produce more confident labels but more confident wrong labels. The asymmetry favors caution.

The 0.75 floor for AI and 0.35 ceiling for human are not symmetric by accident — they are symmetric. A score of 0.74 and a score of 0.26 are equidistant from 0.5 and both fall in uncertain. This is intentional: the system treats "not sure it's AI" and "not sure it's human" with equal caution.

### Displaying confidence to readers

For `ai_generated` labels: show `round(confidence_score * 100)`% — this is P(AI).
For `human_written` labels: show `round((1 - confidence_score) * 100)`% — this is P(human), not P(AI). A score of 0.10 shows as "90% confidence" on a human label, which is correct for a non-technical reader.
For `uncertain` labels: show the raw percent but frame it as "X% leaning AI" or "X% leaning human" depending on which side of 0.5 the score falls.

---

## 3. Transparency Label Design

Three variants. These are the exact strings the system will produce. `{pct}` is substituted at render time.

The `label` object has four fields:
- `verdict` — short status word/phrase, displayed prominently
- `confidence_text` — one-line confidence qualifier, displayed under the verdict
- `explanation` — full plain-language sentence shown to the reader
- `appeal_cta` — call-to-action string, only present on `ai_generated` labels; `null` on all others

Keeping `appeal_cta` as its own field (rather than appended to `explanation`) lets a frontend render it as a separate interactive element without string-parsing.

---

### Variant A — High-confidence AI (confidence_score >= 0.75)

**verdict:** `"AI-generated"`
**confidence_text:** `"Our system is {pct}% confident this content was AI-generated."`
**explanation:**
> "This content shows patterns consistent with AI-generated text. Our system evaluated it using two independent signals — a language model classifier and a statistical analyzer — and both indicate AI authorship."

**appeal_cta:** `"Think this is wrong? Creators can contest this classification."`

`{pct}` = `round(confidence_score * 100)`

**Why:** The explanation names both signals so a reader understands the verdict isn't a single algorithm's guess. The CTA is separated so it can be rendered as a button or link, not buried in prose.

---

### Variant B — High-confidence Human (confidence_score <= 0.35)

**verdict:** `"Likely human-written"`
**confidence_text:** `"Our system is {pct}% confident this content was written by a human."`
**explanation:**
> "This content shows patterns consistent with human authorship. Our system evaluated it using two independent signals — a language model classifier and a statistical analyzer — and neither detected significant markers of AI generation."

**appeal_cta:** `null`

`{pct}` = `round((1 - confidence_score) * 100)`

**Why:** Symmetric with Variant A — same signal framing, same structure. The `{pct}` inversion is critical: a score of 0.10 must display as "90% confident this content was written by a human," not "10%."

---

### Variant C — Uncertain (0.35 < confidence_score < 0.75)

**verdict:** `"Origin unclear"`
**confidence_text:** `"Our system could not confidently determine the origin of this content."`
**explanation:**
> "This content could not be confidently attributed to either a human or an AI. Our two signals did not agree strongly enough to reach a verdict. This label should not be treated as an accusation or a clearance."

**appeal_cta:** `null`

**Why this is better than the old version:** The old `confidence_text` was `"{pct}% leaning AI"` — a percentage that communicates a weak lean while sounding like a real measurement. Removed entirely. A non-technical reader seeing "58% leaning AI" may treat it as 58% confidence in an accusation. The new `confidence_text` is a plain statement of inability, which is the honest message. The explanation adds "not an accusation or a clearance" — the two things a reader most needs to know.

There is no `{pct}` in this variant. The raw score is available in the API response for technical consumers; it is not surfaced to readers.

---

### Contested label (status = "under_review")

When an appeal has been filed, the `label` object gains a fifth field:

**appeal_notice:** `"This classification is under review following a creator appeal. The verdict above may change."`

The original `verdict`, `confidence_text`, and `explanation` are unchanged — the verdict is marked as contested, not withdrawn. `appeal_notice` is `null` when `status = "decided"`.

---

## 4. Appeals Workflow

### Who can submit an appeal

Anyone who knows the `content_id`. There is no authentication. In a production system this would be gated to the verified content creator, but for this implementation the appeal endpoint is open. The `creator_id` field is optional freeform text — it lets creators self-identify but is not validated.

### What information they provide

```json
{
  "reason": "required string, min 10 chars, max 2000 chars",
  "creator_id": "optional freeform string"
}
```

`reason` is the creator's plain-language explanation of why the classification is wrong. It is stored verbatim — the system does not parse or evaluate it.

### What the system does on receipt

1. Look up the audit record by `content_id`. Return 404 if not found.
2. Check if `appeal` is already non-null on the record. Return 409 if so.
3. Build the appeal object: `{reason, creator_id, appealed_at: now()}`.
4. Mutate the audit record: append the appeal object, flip `status` from `decided` to `under_review`.
5. Return 200 with `{content_id, status: "under_review", appeal_received_at}`.

No re-classification. No notification. No queue. The appeal is a data record only.

### What a human reviewer sees in the appeal queue (GET /log?status=under_review)

Each entry in the filtered log shows:

```
content_id:       <uuid>
submitted_at:     <timestamp>
classification:   ai_generated  ← the original decision
confidence_score: 0.83          ← the raw score that triggered it
signals_used:
  llm_classifier: 0.89          ← what each signal said individually
  heuristic:      0.71
status:           under_review
appeal:
  reason:         "I wrote this myself — the structured format reflects
                   my journalism training, not AI generation."
  creator_id:     "maya_k"
  appealed_at:    <timestamp>
```

The reviewer can see whether the signals agreed (both high → likely correct classification) or disagreed (one high, one low → uncertain classification that may have been wrong). The `reason` gives them the creator's context. Nothing is automated from here — a human decides.

---

## 5. Anticipated Edge Cases

### Edge Case 1: Formal academic writing by a human

A professor submits a section of a paper they wrote. Academic writing has: low type-token ratio (terminology repeated for precision), nearly uniform sentence length (discipline convention), and transition phrases ("as demonstrated above," "this suggests that") that pattern-match against LLM filler phrases. The heuristic signal may fire at 0.65–0.75. If the LLM classifier also reads the careful hedging ("it may be argued," "the evidence suggests") as LLM hedging, the combined score could push above 0.75 and produce a high-confidence AI label.

**Mitigation in implementation:** The filler phrase list should exclude academic transition phrases. "As demonstrated above" and "this suggests" are not LLM-specific. The list should be restricted to phrases that are nearly absent from pre-LLM corpora — "delve into," "it's important to note," "at its core."

---

### Edge Case 2: Very short text (< 100 words)

A creator submits a 60-word poem. The heuristic's TTR and sentence variance are meaningless at this length — a 60-word poem might have 50 unique words (TTR near 1.0) or 30 (TTR 0.5) without that difference meaning anything about AI authorship. The LLM classifier may also be poorly calibrated on very short texts.

**Mitigation in implementation:** Below 100 words, halve the heuristic weight in the combiner. Below 50 words, return a 400 error with `"text_too_short"` — the system cannot produce a meaningful signal on fragments.

---

### Edge Case 3: Human-edited AI draft

A creator generates a blog post draft with ChatGPT, then rewrites 40% of it: adds personal anecdotes, changes the structure, roughens the vocabulary. The resulting text is a hybrid. The LLM classifier may or may not catch the remaining AI scaffolding. The heuristic may see enough human noise to score low. The combined result is likely to land in the uncertain bucket — which is actually the correct outcome. A hybrid text genuinely has uncertain authorship.

**This is not a bug — it is correct behavior.** The label should say uncertain. The system's job is not to determine legal authorship; it is to signal to a reader how much to trust the provenance.

---

### Edge Case 4: A non-native English writer with a flat voice

A writer whose first language is not English has learned written English from formal sources. Their prose is grammatically correct but rhythmically flat: consistent sentence length, careful word choices that avoid idiom, no contractions or colloquialisms. This looks like LLM output to both signals. The LLM classifier reads "polished non-idiomatic English" as a pattern it associates with AI output. The heuristic sees low variance and low TTR.

**No good mitigation is available at the signal level.** This is a fundamental limitation: the signals measure properties that correlate with AI output, and those same properties can arise from certain human writing contexts. The appeal workflow exists for exactly this case.

---

## 6. Component Map

| Component | File | Responsibility |
|---|---|---|
| Rate Limiter | `app.py` | Reject over-limit callers before pipeline |
| Submission Handler | `app.py` | Validate input, assign UUID, timestamp |
| LLM Signal | `pipeline/llm_signal.py` | Groq API call → AI probability float |
| Heuristic Signal | `pipeline/heuristic_signal.py` | TTR + variance + filler density → AI probability float |
| Score Combiner | `pipeline/pipeline.py` | Weighted merge + fallback handling → final score + bucket |
| Label Generator | `labels.py` | Bucket + score → `{verdict, confidence_text, explanation, appeal_cta, appeal_notice}` |
| Audit Logger | `audit.py` | Append-only in-memory store; serve `GET /log` |
| Appeals Handler | `app.py` | Validate, mutate audit record, return status |

---

## 7. Architecture Diagram

### Flow 1 — Submission

```
POST /analyze
    │
    │  raw request
    ▼
┌─────────────────┐
│  Rate Limiter   │ ──── 429 Too Many Requests
│  10 req/min/IP  │
└────────┬────────┘
         │  raw request (passed)
         ▼
┌─────────────────────┐
│ Submission Handler  │  assigns uuid, submitted_at
└──────────┬──────────┘
           │  text + uuid
           ▼
┌──────────────────────────────────────────────────┐
│               DETECTION PIPELINE                 │
│                                                  │
│   ┌───────────────────┐  ┌────────────────────┐  │
│   │  LLM Classifier   │  │ Heuristic Analyzer │  │
│   │   Groq / Llama    │  │ TTR · var · fillers│  │
│   └────────┬──────────┘  └─────────┬──────────┘  │
│            │ P(AI): 0.0–1.0        │ P(AI): 0.0–1.0
└────────────┼───────────────────────┼─────────────┘
             └──────────┬────────────┘
                        │  score₁ (LLM), score₂ (heuristic)
                        ▼
             ┌──────────────────────┐
             │   Score Combiner     │
             │  65% LLM + 35% heur  │
             └──────────┬───────────┘
                        │  combined score (0.0–1.0) + bucket
                        ▼
             ┌──────────────────────┐
             │   Label Generator    │
             │  AI | Human | Uncert │
             └──────────┬───────────┘
                        │  label object {verdict, confidence_text, explanation}
                        ▼
             ┌──────────────────────┐
             │    Audit Logger      │  appends full record before response
             └──────────┬───────────┘
                        │  full record
                        ▼
             ┌──────────────────────┐
             │       Response       │──── 200 JSON → caller
             └──────────────────────┘
```

### Flow 2 — Appeal

```
POST /appeal/<content_id>
    │
    │  content_id + reason
    ▼
┌─────────────┐
│  ID Lookup  │ ──── 404 Not Found
└──────┬──────┘
       │  record ref
       ▼
┌───────────────────┐
│  Status Updater   │ ──── 409 Already Appealed
│ decided →         │
│   under_review    │
└────────┬──────────┘
         │  appeal object {reason, creator_id, appealed_at}
         ▼
┌─────────────────────┐
│    Audit Logger     │  mutates existing record in place
└──────────┬──────────┘
           │  updated record
           ▼
┌─────────────────────────────┐
│          Response           │──── 200 JSON → caller
│  {content_id,               │
│   status: under_review,     │
│   appeal_received_at}       │
└─────────────────────────────┘
```

---

## 8. Data Contracts

### POST /analyze — response
```json
{
  "content_id":       "uuid",
  "submitted_at":     "ISO8601",
  "classification":   "ai_generated | human_written | uncertain",
  "confidence_score": 0.83,
  "signals_used": [
    {"name": "llm_classifier", "score": 0.89},
    {"name": "heuristic",      "score": 0.71}
  ],
  "status": "decided",
  "label": {
    "verdict":         "AI-generated",
    "confidence_text": "Our system is 83% confident this content was AI-generated.",
    "explanation":     "This content shows patterns consistent with AI-generated text. Our system evaluated it using two independent signals — a language model classifier and a statistical analyzer — and both indicate AI authorship.",
    "appeal_cta":      "Think this is wrong? Creators can contest this classification.",
    "appeal_notice":   null
  }
}
```

Example `label` for `human_written` (score 0.12):
```json
{
  "verdict":         "Likely human-written",
  "confidence_text": "Our system is 88% confident this content was written by a human.",
  "explanation":     "This content shows patterns consistent with human authorship. Our system evaluated it using two independent signals — a language model classifier and a statistical analyzer — and neither detected significant markers of AI generation.",
  "appeal_cta":      null,
  "appeal_notice":   null
}
```

Example `label` for `uncertain` (score 0.58):
```json
{
  "verdict":         "Origin unclear",
  "confidence_text": "Our system could not confidently determine the origin of this content.",
  "explanation":     "This content could not be confidently attributed to either a human or an AI. Our two signals did not agree strongly enough to reach a verdict. This label should not be treated as an accusation or a clearance.",
  "appeal_cta":      null,
  "appeal_notice":   null
}
```

Example `label` after appeal filed (any classification):
```json
{
  "appeal_notice": "This classification is under review following a creator appeal. The verdict above may change."
}
```
(all other fields unchanged; `appeal_notice` flips from `null` to the string above)

### POST /appeal — response
```json
{
  "content_id":         "uuid",
  "status":             "under_review",
  "appeal_received_at": "ISO8601"
}
```

### GET /log — response
```json
{
  "count": 3,
  "entries": [
    {
      "content_id":       "uuid",
      "submitted_at":     "ISO8601",
      "classification":   "ai_generated",
      "confidence_score": 0.83,
      "signals_used": [
        {"name": "llm_classifier", "score": 0.89},
        {"name": "heuristic",      "score": 0.71}
      ],
      "label": { "verdict": "...", "confidence_text": "...", "explanation": "..." },
      "status": "under_review",
      "appeal": {
        "reason":      "I wrote this myself.",
        "creator_id":  "maya_k",
        "appealed_at": "ISO8601"
      }
    }
  ]
}
```

---

## 9. Rate Limit Reasoning

| Endpoint | Limit | Reasoning |
|---|---|---|
| `POST /analyze` | 10 req / min / IP | Calls external LLM API; each request has real latency and cost. Generous enough for interactive use, low enough to block scraping. |
| `POST /appeal/<id>` | 20 req / min / IP | No LLM call, but bounded to prevent flooding a single content record or the audit log. |
| `GET /log` | none | Read-only, no external calls, no write side effects. |
