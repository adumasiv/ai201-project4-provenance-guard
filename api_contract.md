# Provenance Guard — API Contract

This document defines the full API surface. No implementation exists yet.
All code must implement these contracts exactly. If implementation requires
changing a contract, update this document first.

---

## Endpoints

### 1. POST /analyze

Submit a piece of text for attribution analysis.

**Rate limit:** 10 requests / minute / IP

**Request body (JSON):**
```
{
  "text": string          // required. The content to analyze. Min 50 chars, max 10,000 chars.
}
```

**Success response — 200 OK:**
```
{
  "content_id":       string,   // UUID assigned at submission time
  "submitted_at":     string,   // ISO 8601 UTC timestamp
  "classification":   string,   // "ai_generated" | "human_written" | "uncertain"
  "confidence_score": number,   // float 0.0–1.0. For ai_generated: P(AI). For human_written: P(human). For uncertain: distance from 0.5.
  "signals_used": [
    {
      "name":  string,          // "llm_classifier" | "heuristic"
      "score": number           // float 0.0–1.0, signal's raw P(AI) estimate
    }
  ],
  "status": string,             // "decided" always on first response
  "label": {
    "verdict":          string, // "AI-generated" | "Likely human" | "Uncertain"
    "confidence_text":  string, // e.g. "87% confidence" — derived from confidence_score
    "explanation":      string  // the full plain-language sentence shown to readers
  }
}
```

**Error responses:**
```
400 Bad Request
{
  "error": "missing_field",
  "message": "Request body must include a non-empty 'text' field."
}

400 Bad Request
{
  "error": "text_too_short",
  "message": "Text must be at least 50 characters."
}

400 Bad Request
{
  "error": "text_too_long",
  "message": "Text must not exceed 10,000 characters."
}

429 Too Many Requests
{
  "error": "rate_limit_exceeded",
  "message": "Too many requests. Limit: 10 per minute per IP.",
  "retry_after_seconds": number
}

502 Bad Gateway
{
  "error": "upstream_failure",
  "message": "LLM classifier unavailable. Please retry.",
  "content_id": string   // still assigned so partial state can be recovered
}
```

---

### 2. POST /appeal/<content_id>

Contest a classification. The content's status moves to "under_review".
No re-classification occurs automatically.

**Rate limit:** 20 requests / minute / IP

**Path parameter:**
```
content_id   string   // UUID from the /analyze response
```

**Request body (JSON):**
```
{
  "reason":     string,   // required. Creator's explanation. Min 10 chars, max 2,000 chars.
  "creator_id": string    // optional. Identifier for the creator (freeform, not validated).
}
```

**Success response — 200 OK:**
```
{
  "content_id":         string,   // echoed back
  "status":             string,   // "under_review" always on success
  "appeal_received_at": string    // ISO 8601 UTC timestamp
}
```

**Error responses:**
```
400 Bad Request
{
  "error": "missing_field",
  "message": "Request body must include a non-empty 'reason' field."
}

404 Not Found
{
  "error": "content_not_found",
  "message": "No content found with that ID."
}

409 Conflict
{
  "error": "already_appealed",
  "message": "An appeal has already been submitted for this content."
}
```

---

### 3. GET /log

Retrieve the full audit log, newest entries first.

**Rate limit:** None (read-only, no external calls)

**Query parameters (all optional):**
```
limit    integer   // max entries to return. Default 50, max 200.
status   string    // filter by "decided" | "under_review"
```

**Success response — 200 OK:**
```
{
  "count": number,
  "entries": [
    {
      "content_id":       string,
      "submitted_at":     string,    // ISO 8601 UTC
      "classification":   string,    // "ai_generated" | "human_written" | "uncertain"
      "confidence_score": number,    // raw float, always preserved
      "signals_used": [
        { "name": string, "score": number }
      ],
      "label": {
        "verdict":         string,
        "confidence_text": string,
        "explanation":     string
      },
      "status": string,              // "decided" | "under_review"
      "appeal": null | {
        "reason":            string,
        "creator_id":        string | null,
        "appealed_at":       string  // ISO 8601 UTC
      }
    }
  ]
}
```

**Error responses:**
```
400 Bad Request
{
  "error": "invalid_param",
  "message": "limit must be an integer between 1 and 200."
}
```

---

## Invariants

These are constraints the implementation must never violate:

1. Every `content_id` that appears in a `/appeal` response or `/log` entry was
   first created by `/analyze`. There is no other way to create a content record.

2. `confidence_score` is always the raw combined float (0.0–1.0), regardless of
   classification bucket. It is never rounded or clamped to the bucket boundary.

3. `signals_used` always contains exactly two entries: `llm_classifier` and
   `heuristic`, in that order. Both are always present even if one failed
   (failed signal score is -1.0 and classification falls back to the other signal alone).

4. A content record's `status` transitions in one direction only:
   `decided` → `under_review`. There is no transition back.

5. An appeal can only be filed once per `content_id`. The 409 error enforces this.

6. `submitted_at` and `appeal.appealed_at` are set server-side. The client
   never supplies a timestamp.

7. The audit log is append-only from the caller's perspective. `/log` never
   returns a record that contradicts an earlier `/analyze` or `/appeal` response
   for the same `content_id`.

---

## Label Text — Exact Strings

The `explanation` field uses these templates, with `{pct}` replaced by
`round(confidence_score * 100)`:

**ai_generated (confidence_score >= 0.75):**
> "This content was likely written with AI assistance ({pct}% confidence).
>  Our system detected patterns consistent with AI-generated text across
>  multiple signals. Think this is wrong? Creators can contest this classification."

**human_written (confidence_score <= 0.35):**
> "This content appears to have been written by a human ({pct}% confidence).
>  Our system found no significant markers of AI generation across multiple signals."

**uncertain (0.35 < confidence_score < 0.75):**
> "Our system could not confidently determine the origin of this content
>  ({pct}% confidence). It may have been written by a human, AI, or a
>  combination of both. Treat this label with caution."

Note: for `human_written`, `{pct}` is `round((1 - confidence_score) * 100)` —
it expresses confidence in human authorship, not AI probability.

---

## What is NOT in this API

- No authentication. All endpoints are public.
- No DELETE or UPDATE on content records. The log is append-only.
- No re-classification on appeal. Appeal only changes status.
- No pagination cursor. `/log` uses limit/offset-style filtering only.
- No webhook or async callback. All responses are synchronous.
