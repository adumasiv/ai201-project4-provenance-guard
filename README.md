# Provenance Guard

A REST API that analyzes text content and returns an AI-authorship attribution: was this written by a human or an AI? It runs two independent detection signals, combines them into a calibrated confidence score, generates a plain-language transparency label for readers, and maintains a structured audit log of every decision — including appeals.

---

## How to Run

**Requirements:** Python 3.10+, a [Groq API key](https://console.groq.com) (free).

```bash
# Install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Add your key to .env
echo "GROQ_API_KEY=your_key_here" > .env

# Start the server
python app.py
# Running on http://localhost:5001
```

Open a second terminal tab for curl commands while the server runs in the first.

---

## Architecture

A single piece of text flows through seven components in order:

```
POST /submit
    │
    ▼
[Rate Limiter]  10 req/min/IP ──── 429 if exceeded
    │
    ▼
[Submission Handler]  assigns UUID + timestamp
    │
    ├─────────────────────────────┐
    ▼                             ▼
[LLM Classifier]         [Heuristic Analyzer]
 Groq/Llama · semantic    TTR · variance · fillers
    │ P(AI) 0–1               │ P(AI) 0–1
    └──────────┬──────────────┘
               ▼
        [Score Combiner]  65% LLM + 35% heuristic
               │
               ▼ combined score + bucket
        [Label Generator]  AI | Human | Uncertain
               │
               ▼
        [Audit Logger]  written before response
               │
               ▼
           [Response]  200 JSON → caller
```

**Appeal flow:**
```
POST /appeal  →  [ID Lookup]  →  [Status Updater]  →  [Audit Logger]  →  200
                     │                   │
                  404 if missing      409 if already appealed
```

---

## Detection Signals

### Why two signals?

A single signal can't handle the false-positive problem alone. The writers most at risk of being misclassified — academics, non-native English speakers, heavy editors — tend to fool one signal but not both. When the two signals disagree, that disagreement itself is evidence of genuine uncertainty, and the system returns an uncertain label rather than making a confident wrong call.

### Signal 1 — LLM Classifier (Groq / Llama 3, weight: 65%)

**What it measures:** The probability that the text was produced by a language model, as judged by another language model. Llama reads the full text and attends to semantic and stylistic patterns: unnaturally consistent quality throughout, characteristic hedging phrases ("it is important to note," "it becomes clear that"), clean topic structure with no tangents, and the "polished vagueness" of text optimized for general-audience readability rather than a specific human voice.

**Why this signal:** LLMs are uniquely good at recognizing LLM output patterns that resist rule-based detection. A language model has seen enormous quantities of AI-generated text and can sense the fingerprint even when surface statistics look normal.

**Why it gets 65% weight:** It captures deeper semantic structure that the heuristic misses. It's also harder to spoof — you can instruct an LLM to avoid filler phrases, but it's much harder to instruct it to not "feel like" an LLM to another LLM.

**Blind spots:** Formulaic human writing (corporate communications, legal boilerplate, five-paragraph essays) scores high. Heavily edited AI drafts score low. The model's calibration varies across non-English text and writing styles underrepresented in its training data.

### Signal 2 — Heuristic Analyzer (local, no API call, weight: 35%)

**What it measures:** Three surface-level statistical features computed entirely on the text without any external call:

- **Type-Token Ratio (TTR):** Unique words ÷ total words, over a sliding window of 50 words. Low diversity → more AI-like. Only used when the text has ≥50 words; below that, TTR is meaningless because almost every word in a short text is unique by definition.
- **Sentence length variance:** Standard deviation of sentence lengths, normalized to [0,1]. Consistently similar sentence lengths → more AI-like.
- **Filler phrase density:** Frequency of phrases that appear at elevated rates in LLM output but rarely in pre-LLM corpora: "delve into," "it is important to note," "at its core," "in conclusion," "needless to say," etc. Normalized per 100 words.

**Why this signal:** It's fast, deterministic, interpretable, and costs nothing. More importantly, it acts as a counterweight: when the LLM classifier over-fires on formal human writing, the heuristic often correctly reads zero filler phrases and moderate variance and pulls the combined score down toward the uncertain bucket.

**Why it gets 35% weight:** It's trivially defeated — anyone who knows these heuristics can prompt an LLM to avoid them. It's a useful cross-check on naive AI output, not a robust standalone signal.

**Blind spots:** Academic writing has low TTR (terminology must be repeated) and low variance (disciplinary convention) by design. This signal will over-flag it. Short texts produce unreliable TTR and variance — the signal drops TTR entirely for texts under 50 words and redistributes its weight to filler density and variance.

---

## Confidence Scoring

### Combination formula

```
confidence_score = (llm_score × 0.65) + (heuristic_score × 0.35)
```

Fallbacks:
- If LLM call fails (returns -1.0): `confidence = heuristic_score`
- If both fail: `confidence = 0.5`, classification = `uncertain`

### Threshold map

| Score range | Classification | What it means |
|---|---|---|
| ≥ 0.75 | `ai_generated` | Both signals likely agree. High enough to show a public label. |
| 0.35 – 0.75 | `uncertain` | Insufficient signal, or signals disagree. |
| ≤ 0.35 | `human_written` | Both signals likely agree on human authorship. |

### Why this scoring approach is meaningful (not a binary flip at 0.5)

The uncertain band is deliberately wide — 0.40 units. A score of 0.51 and a score of 0.94 are both "AI-leaning" but they carry completely different levels of confidence. The 0.51 case lands in the uncertain bucket and the reader sees "our system could not determine the origin." The 0.94 case lands in `ai_generated` and the reader sees "77% confident." These are meaningfully different labels, not the same verdict at different intensities.

The width also reflects the false-positive cost asymmetry: labeling a human writer's work as AI-generated is a serious accusation. The system is calibrated to make that accusation only when both signals agree strongly.

### Example: high-confidence AI (score 0.807)

**Input:**
> "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment."

**Scores:**
```
llm_classifier:  0.850  (Groq reads uniform structure, hedging, filler phrases)
heuristic:       0.727  (high filler density: "it is important to note",
                          low sentence variance, consistent rhythm)
combined:        (0.850 × 0.65) + (0.727 × 0.35) = 0.807
classification:  ai_generated
```

**Label shown to reader:**
> "Our system is 81% confident this content was AI-generated."

Both signals agree strongly. The gap between them (0.850 vs 0.727) is small, which is why the combined score lands well above the 0.75 threshold rather than near it.

---

### Example: low-confidence / uncertain (score 0.616)

**Input:**
> "The relationship between monetary policy and asset price inflation has been extensively studied in the literature. Central banks face a fundamental tension between their mandate for price stability and the unintended consequences of prolonged low interest rates on equity and real estate valuations."

**Scores:**
```
llm_classifier:  0.800  (reads formal hedging and academic register as AI-like)
heuristic:       0.275  (no filler phrases; low TTR due to terminology reuse
                          is expected in academic writing, not AI-specific)
combined:        (0.800 × 0.65) + (0.275 × 0.35) = 0.616
classification:  uncertain
```

**Label shown to reader:**
> "Our system could not confidently determine the origin of this content."

The two signals disagree by 0.525 — the largest divergence observed in testing. The LLM over-fires on academic hedging; the heuristic correctly reads no AI-specific filler and pulls the combined score down. The disagreement is the signal: when the two signals are this far apart, the system has genuinely insufficient evidence to make an accusation, and the uncertain label is the honest result.

**This is the system working correctly, not a failure.** A single-signal system would have classified this as `ai_generated` at 0.80. The second signal prevented a false positive.

---

### How scores vary across all four test inputs

| Input | LLM | Heuristic | Combined | Classification |
|---|---|---|---|---|
| GPT filler prose | 0.850 | 0.727 | **0.807** | `ai_generated` |
| Casual personal writing | 0.210 | 0.233 | **0.218** | `human_written` |
| Academic writing | 0.800 | 0.275 | **0.616** | `uncertain` |
| Lightly edited AI | 0.420 | 0.313 | **0.383** | `uncertain` |

The spread from 0.218 to 0.807 — a range of 0.589 — across clearly different inputs demonstrates that the scoring produces meaningful variation. The high-confidence AI case (0.807) and the low-confidence uncertain case (0.616) differ by 0.191 and produce completely different labels.

### What I'd change for a real deployment

- **Calibrate against labeled data.** The 0.65/0.35 weights and 0.35/0.75 thresholds were chosen by reasoning, not by fitting to a labeled dataset. With even a few hundred labeled examples, you could optimize both the weights and the thresholds.
- **Replace the filler phrase list with a trained classifier.** The current list was hand-curated. A lightweight classifier trained on pre- and post-LLM text corpora would be more robust and easier to update as LLM writing evolves.
- **Expose calibration data.** The current `confidence_score` is a weighted average, not a calibrated probability. A Platt scaling layer would make "77% confident" mean something statistically defensible.
- **Add a third signal for longer texts.** Perplexity scores (how "surprising" each token is to a language model) are a well-studied signal for AI detection. The current pipeline focuses on stylistic surface features; perplexity would add a deeper syntactic layer.

---

## Transparency Labels

Three variants, shown to readers based on the confidence bucket:

### Variant A — AI-generated (score ≥ 0.75)

```
verdict:          "AI-generated"
confidence_text:  "Our system is {pct}% confident this content was AI-generated."
explanation:      "This content shows patterns consistent with AI-generated text.
                   Our system evaluated it using two independent signals — a language
                   model classifier and a statistical analyzer — and both indicate
                   AI authorship."
appeal_cta:       "Think this is wrong? Creators can contest this classification."
```

### Variant B — Likely human-written (score ≤ 0.35)

```
verdict:          "Likely human-written"
confidence_text:  "Our system is {pct}% confident this content was written by a human."
explanation:      "This content shows patterns consistent with human authorship.
                   Our system evaluated it using two independent signals — a language
                   model classifier and a statistical analyzer — and neither detected
                   significant markers of AI generation."
appeal_cta:       null
```

Note: `{pct}` for human labels is `round((1 - confidence_score) × 100)`. A score of 0.12 displays as "88% confident this content was written by a human" — not 12%. The percentage expresses confidence in human authorship, not AI probability.

### Variant C — Uncertain (0.35 < score < 0.75)

```
verdict:          "Origin unclear"
confidence_text:  "Our system could not confidently determine the origin of this content."
explanation:      "This content could not be confidently attributed to either a human
                   or an AI. Our two signals did not agree strongly enough to reach a
                   verdict. This label should not be treated as an accusation or a clearance."
appeal_cta:       null
```

No percentage is shown to readers on uncertain labels. A percentage on an uncertain verdict would imply a precision the system doesn't have.

### Contested label (after appeal filed)

All variants gain an additional field when `status = "under_review"`:

```
appeal_notice:    "This classification is under review following a creator appeal.
                   The verdict above may change."
```

---

## Known Limitations

### 1. Non-native English writers will be over-flagged

A writer whose first language is not English, and who learned written English from formal sources, tends to produce prose that is grammatically correct but rhythmically flat: consistent sentence lengths, careful word choices that avoid idiom, no contractions or colloquialisms. This pattern triggers both signals simultaneously for the wrong reasons.

The heuristic sees low sentence length variance (all sentences are roughly the same length, because the writer is working carefully) and a moderate type-token ratio (vocabulary is correct but not idiomatic). The LLM classifier reads "polished non-idiomatic English" as a pattern it associates with AI output — because AI output is also polished and non-idiomatic.

There is no fix available at the signal level. Both signals are measuring real properties of the text; they just happen to be properties that arise from both AI generation and from writing in a second language. The appeal workflow exists for exactly this case, but that puts the burden on the writer to contest a label they shouldn't have received in the first place.

### 2. Adversarially prompted AI text will evade detection

The heuristic signal is trivially defeated. Anyone who knows what it measures can prompt an LLM to produce output that evades it:

> "Write this blog post with varied sentence lengths, no transition phrases like 'it is important to note' or 'in conclusion,' and a high vocabulary diversity."

The resulting text will score near 0.0 on the heuristic — no filler phrases, high TTR, variable sentence lengths. The LLM classifier will still fire, but at 65% weight it can only push the combined score to ~0.65, which lands in the uncertain bucket rather than `ai_generated`. A motivated bad actor can reliably suppress the verdict to uncertain.

This is a fundamental property of rule-based and statistical signals: once the rules are known, they can be gamed. The LLM classifier is harder to defeat because it reasons about the full text rather than counting features, but it can be nudged by writing AI output that emphasizes personal voice, specific details, and deliberate imperfections.

### 3. Short texts (under 50 words) produce unreliable scores

For texts shorter than the sliding-window size (50 words), the TTR sub-feature is dropped entirely and the heuristic redistributes its weight to filler density and sentence variance. This degrades gracefully, but a 40-word paragraph with a single filler phrase will score differently than a 500-word essay with the same phrase density — not because the shorter text is more or less AI-like, but because there isn't enough signal to be confident either way.

The minimum submission length is 50 characters (roughly 8–12 words), which is well below the threshold where any of the signals are statistically reliable. A haiku, a tweet-length post, or a single sentence cannot be meaningfully attributed with this pipeline.

### 4. The LLM classifier is not calibrated

When the system returns "81% confident this content was AI-generated," that percentage is a weighted average of signal scores, not a calibrated probability. It does not mean that 81% of texts scoring this way are actually AI-generated. The thresholds (0.35 / 0.75) were chosen by reasoning about the false-positive cost, not by fitting to a labeled dataset. A real deployment would need Platt scaling or isotonic regression over a labeled corpus to make the confidence percentages statistically meaningful.

---

## Appeals Workflow

Anyone with a `content_id` can submit an appeal. The endpoint accepts `creator_reasoning` or `reason` as the field name (both work).

**`POST /appeal`** (body) or **`POST /appeal/<content_id>`** (path):
```json
{
  "content_id": "uuid",
  "creator_reasoning": "I wrote this myself from personal experience.",
  "creator_id": "optional"
}
```

**Response:**
```json
{
  "content_id": "uuid",
  "status": "under_review",
  "appeal_received_at": "2026-07-01T04:41:28Z"
}
```

What happens internally:
1. Looks up the audit record by `content_id` → 404 if not found
2. Checks if an appeal already exists → 409 if so (one appeal per content record)
3. Appends `{reason, creator_id, appealed_at}` to the record
4. Flips `status` from `decided` → `under_review`
5. Regenerates the label so `appeal_notice` is populated in subsequent log reads
6. Returns confirmation

No automated re-classification occurs. The appeal is a record for human review.

---

## Rate Limits

| Endpoint | Limit | Reasoning |
|---|---|---|
| `POST /submit` | **10 req / min / IP** | Each request calls the Groq LLM API — real cost and latency. A writer submitting their own work needs at most a few requests per session; 10/min is generous for legitimate use and blocks automated scraping. |
| `POST /appeal` | **20 req / min / IP** | No LLM call, so cheaper. Still bounded to prevent a script from flooding a single content record. 20/min is more than sufficient for any human workflow. |
| `GET /log` | **No limit** | Read-only, no external calls, no write side-effects. |

### Evidence — rate limit in action

12 rapid requests against `POST /submit` (limit: 10/min):

```
200  200  200  200  200  200  200  200  200  200  429  429
```

429 response:
```json
{ "error": "rate_limit_exceeded", "message": "Too many requests. Please slow down." }
```

---

## Audit Log

Every attribution decision is written to the log before the response is returned. Retrieve via `GET /log`. Supports `?status=decided|under_review` and `?limit=N`.

| Field | Description |
|---|---|
| `content_id` | UUID assigned at submission |
| `timestamp` | ISO 8601 UTC |
| `creator_id` | Submitter identifier (optional, freeform) |
| `attribution` | `ai_generated`, `human_written`, or `uncertain` |
| `confidence` | Combined score 0.0–1.0 |
| `llm_score` | Groq/Llama raw score (−1.0 if call failed) |
| `heuristic_score` | Statistical signal raw score |
| `signals_used` | Array of `{name, score}` for each signal |
| `label` | Full label object including `appeal_notice` |
| `status` | `decided` or `under_review` |
| `appeal` | `null`, or `{reason, creator_id, appealed_at}` |

### Sample output — `GET /log`

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

### `POST /submit`

```json
// Request
{
  "text": "required, 50–10,000 characters",
  "creator_id": "optional"
}

// Response 200
{
  "content_id": "uuid",
  "submitted_at": "ISO8601",
  "creator_id": "string | null",
  "attribution": "ai_generated | human_written | uncertain",
  "confidence": 0.807,
  "signals_used": [
    {"name": "llm_classifier", "score": 0.85},
    {"name": "heuristic", "score": 0.727}
  ],
  "label": {
    "verdict": "AI-generated",
    "confidence_text": "Our system is 81% confident this content was AI-generated.",
    "explanation": "...",
    "appeal_cta": "Think this is wrong? Creators can contest this classification.",
    "appeal_notice": null
  },
  "status": "decided"
}
```

Errors: `400` missing/short/long text · `429` rate limit exceeded · `502` Groq API unavailable

### `POST /appeal` or `POST /appeal/<content_id>`

```json
// Request
{
  "content_id": "uuid",         // required when using POST /appeal (flat)
  "creator_reasoning": "...",   // required, 10–2000 chars (also accepts "reason")
  "creator_id": "optional"
}

// Response 200
{
  "content_id": "uuid",
  "status": "under_review",
  "appeal_received_at": "ISO8601"
}
```

Errors: `400` missing reason · `404` content not found · `409` already appealed

### `GET /log`

```
?status=decided|under_review   filter by status
?limit=N                        max entries (1–200, default 50)
```

---

## AI Usage

This project was built with Claude (claude-sonnet-4-6) as a pair programmer across all six milestones. The following are two specific instances where AI output required meaningful revision.

---

### Instance 1 — Short-text heuristic dampening

**What I directed the AI to do:** After the heuristic signal gave a suspiciously low score on a 43-word clearly-AI text (0.275 vs. the LLM's 0.98), I asked Claude to diagnose the mismatch and propose a fix. I pointed it at the per-signal scores and the `run_heuristic_signal` implementation.

**What it produced:** Claude identified that the sliding-window TTR was returning a near-perfect diversity score (≈0.91) on short text because almost every word in a 43-word sample is unique by definition. Its proposed fix was to add a dampening rule — "below 100 words, halve the heuristic weight before combining" — pulling the signal toward 0.5 to reduce overconfidence.

**What I revised:** I accepted the diagnosis but rejected the fix. Dampening the entire heuristic signal treats filler phrase density (which is equally reliable at 40 words or 400) the same as TTR (which is statistically meaningless below the window size). Applying a blanket penalty would suppress a good signal to compensate for a bad one. I overrode the dampening approach and instead dropped TTR entirely below 50 words, redistributing its weight to variance (0.45) and filler density (0.55). The final score on the same text jumped to 0.807 — correctly crossing the 0.75 `ai_generated` threshold.

---

### Instance 2 — Human label confidence percentage

**What I directed the AI to do:** I asked Claude to implement the three label variants from `planning.md`, including the `human_written` label that should express confidence as a percentage.

**What it produced:** Claude generated the label with `pct = round(confidence_score * 100)`, producing strings like "Our system is 12% confident this content was written by a human" for a text with a 0.12 AI-probability score.

**What I revised:** A 12% confidence display is misleading for a human label — the system is actually 88% confident it is human. The confidence score tracks P(AI), so the human label needs to invert it: `pct = round((1 - confidence_score) * 100)`. I caught this before any tests ran, corrected the formula, and added a comment noting the inversion. The final label for a 0.12 score correctly reads "88% confident this content was written by a human."

---

## Spec Reflection

**One way the spec helped:** The M4 AI Tool Plan in `planning.md` specified exact verification inputs with expected score ranges — including "clearly AI-generated text should score above 0.75." When the first test of a 43-word AI-written sample came back 0.7282, that concrete target made the failure unambiguous. Without it, 0.7282 might have looked acceptable. The spec also required logging per-signal scores alongside the combined score, which immediately showed that the LLM signal (0.98) and heuristic signal (0.275) were far apart — pointing straight at the heuristic as the culprit rather than leaving a diffuse "the system is wrong" diagnosis.

**One way the implementation diverged:** The spec defined the short-text rule as "below 100 words, halve the heuristic weight" — a blanket dampening intended to reduce overconfidence on short inputs. The implementation ended up doing something different: below 50 words, TTR is dropped entirely and its weight is redistributed to variance (0.45) and filler density (0.55). The spec's dampening approach proved wrong in practice because it treated all three sub-features as equally unreliable on short text. But filler phrase density is just as diagnostic on a 40-word text as on a 400-word one — the problem was only TTR, which is statistically meaningless below the sliding window size (50 words). Dampening filler density alongside TTR suppressed a good signal to fix a bad one. The correct fix was to remove only the unreliable sub-feature and redistribute its weight to the ones that remained valid.

---

## File Structure

```
app.py                   Flask routes: /submit, /appeal, /log
audit.py                 In-memory audit log: append, lookup, update
labels.py                Label generator: maps score → label object
pipeline/
  llm_signal.py          Groq/Llama signal: returns P(AI) float
  heuristic_signal.py    Statistical signal: TTR + variance + fillers
  pipeline.py            Score combiner: weighted merge + bucket assignment
planning.md              Architecture decisions, signal design, AI tool plan
api_contract.md          Full endpoint contracts and invariants
```
