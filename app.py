import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

import audit
from pipeline.llm_signal import run_llm_signal  # noqa: E402

app = Flask(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_submission(data: dict):
    """Return (text, creator_id, error_response). error_response is None on success."""
    if not data or "text" not in data or not data["text"]:
        return None, None, (jsonify({
            "error":   "missing_field",
            "message": "Request body must include a non-empty 'text' field.",
        }), 400)

    text = data["text"]
    if len(text) < 50:
        return None, None, (jsonify({
            "error":   "text_too_short",
            "message": "Text must be at least 50 characters.",
        }), 400)
    if len(text) > 10_000:
        return None, None, (jsonify({
            "error":   "text_too_long",
            "message": "Text must not exceed 10,000 characters.",
        }), 400)

    creator_id = data.get("creator_id") or None

    return text, creator_id, None


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute")
def submit():
    data = request.get_json(silent=True) or {}
    text, creator_id, err = _validate_submission(data)
    if err:
        return err

    content_id = str(uuid.uuid4())
    submitted_at = _now()

    llm_score = run_llm_signal(text)

    # Single-signal confidence: use llm_score directly until heuristic is wired in M4.
    # -1.0 means the LLM call failed; fall back to 0.5 (unknown).
    confidence = llm_score if llm_score >= 0 else 0.5

    if confidence >= 0.75:
        attribution = "ai_generated"
        label = "AI-generated (placeholder — full label in M5)"
    elif confidence <= 0.35:
        attribution = "human_written"
        label = "Likely human-written (placeholder — full label in M5)"
    else:
        attribution = "uncertain"
        label = "Origin unclear (placeholder — full label in M5)"

    signals_used = [
        {"name": "llm_classifier", "score": llm_score},
        {"name": "heuristic",      "score": -1.0},   # wired in M4
    ]

    record = {
        "content_id":   content_id,
        "creator_id":   creator_id,
        "timestamp":    submitted_at,
        "attribution":  attribution,
        "confidence":   round(confidence, 4),
        "llm_score":    llm_score,
        "heuristic_score": -1.0,
        "signals_used": signals_used,
        "label":        label,
        "status":       "decided",
        "appeal":       None,
    }
    audit.append_record(record)

    return jsonify({
        "content_id":   content_id,
        "submitted_at": submitted_at,
        "creator_id":   creator_id,
        "attribution":  attribution,
        "confidence":   round(confidence, 4),
        "label":        label,
        "signals_used": signals_used,
        "status":       "decided",
    }), 200


@app.route("/log", methods=["GET"])
def log():
    status_filter = request.args.get("status") or None
    try:
        limit = int(request.args.get("limit", 50))
        limit = max(1, min(limit, 200))
    except ValueError:
        return jsonify({"error": "invalid_param", "message": "limit must be an integer."}), 400

    entries = audit.get_all(status_filter=status_filter, limit=limit)
    return jsonify({"count": len(entries), "entries": entries}), 200


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "error":   "rate_limit_exceeded",
        "message": "Too many requests. Limit: 10 per minute per IP.",
    }), 429


if __name__ == "__main__":
    app.run(debug=True, port=5001)
