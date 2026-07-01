import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

import audit
from labels import generate_label
from pipeline.pipeline import run_pipeline  # noqa: E402

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


# ── Submission ────────────────────────────────────────────────────────────────

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute")
def submit():
    data = request.get_json(silent=True) or {}
    text, creator_id, err = _validate_submission(data)
    if err:
        return err

    content_id   = str(uuid.uuid4())
    submitted_at = _now()

    result     = run_pipeline(text)
    attribution = result["classification"]
    confidence  = result["confidence_score"]
    signals     = result["signals_used"]

    llm_score  = next(s["score"] for s in signals if s["name"] == "llm_classifier")
    heur_score = next(s["score"] for s in signals if s["name"] == "heuristic")

    label = generate_label(attribution, confidence)

    record = {
        "content_id":      content_id,
        "creator_id":      creator_id,
        "timestamp":       submitted_at,
        "attribution":     attribution,
        "confidence":      confidence,
        "llm_score":       llm_score,
        "heuristic_score": heur_score,
        "signals_used":    signals,
        "label":           label,
        "status":          "decided",
        "appeal":          None,
    }
    audit.append_record(record)

    return jsonify({
        "content_id":   content_id,
        "submitted_at": submitted_at,
        "creator_id":   creator_id,
        "attribution":  attribution,
        "confidence":   confidence,
        "label":        label,
        "signals_used": signals,
        "status":       "decided",
    }), 200


# ── Appeals ───────────────────────────────────────────────────────────────────

def _handle_appeal(content_id: str, reason: str, creator_id: str | None):
    """Shared logic for both appeal routes."""
    if not reason or len(reason) < 10:
        return jsonify({
            "error":   "missing_field",
            "message": "A 'creator_reasoning' or 'reason' field is required (min 10 characters).",
        }), 400
    if len(reason) > 2000:
        return jsonify({
            "error":   "reason_too_long",
            "message": "Reason must not exceed 2,000 characters.",
        }), 400

    record = audit.get_record(content_id)
    if record is None:
        return jsonify({
            "error":   "content_not_found",
            "message": "No content found with that ID.",
        }), 404

    if record.get("appeal") is not None:
        return jsonify({
            "error":   "already_appealed",
            "message": "An appeal has already been submitted for this content.",
        }), 409

    appealed_at = _now()
    audit.update_appeal(content_id, {
        "reason":      reason,
        "creator_id":  creator_id,
        "appealed_at": appealed_at,
    })

    return jsonify({
        "content_id":         content_id,
        "status":             "under_review",
        "appeal_received_at": appealed_at,
    }), 200


@app.route("/appeal", methods=["POST"])
@limiter.limit("20 per minute")
def appeal_flat():
    """Flat appeal endpoint: content_id + creator_reasoning in the request body."""
    data = request.get_json(silent=True) or {}
    content_id = (data.get("content_id") or "").strip()
    if not content_id:
        return jsonify({
            "error":   "missing_field",
            "message": "Request body must include a 'content_id' field.",
        }), 400
    reason = (data.get("creator_reasoning") or data.get("reason") or "").strip()
    creator_id = data.get("creator_id") or None
    return _handle_appeal(content_id, reason, creator_id)


@app.route("/appeal/<content_id>", methods=["POST"])
@limiter.limit("20 per minute")
def appeal(content_id):
    data      = request.get_json(silent=True) or {}
    reason    = (data.get("creator_reasoning") or data.get("reason") or "").strip()
    creator_id = data.get("creator_id") or None
    return _handle_appeal(content_id, reason, creator_id)


# ── Audit log ─────────────────────────────────────────────────────────────────

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


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "error":   "rate_limit_exceeded",
        "message": "Too many requests. Please slow down.",
    }), 429


if __name__ == "__main__":
    app.run(debug=True, port=5001)
