"""
Label generator (planning.md §3).

generate_label(classification, confidence_score, status="decided") -> dict

Four fields on every label:
  verdict          — short display string
  confidence_text  — one-line qualifier
  explanation      — full plain-language sentence for readers
  appeal_cta       — call-to-action string (ai_generated only, else null)
  appeal_notice    — set when status == "under_review", else null
"""


def generate_label(
    classification: str,
    confidence_score: float,
    status: str = "decided",
) -> dict:
    if classification == "ai_generated":
        pct    = round(confidence_score * 100)
        label  = {
            "verdict":         "AI-generated",
            "confidence_text": f"Our system is {pct}% confident this content was AI-generated.",
            "explanation": (
                "This content shows patterns consistent with AI-generated text. "
                "Our system evaluated it using two independent signals — a language model "
                "classifier and a statistical analyzer — and both indicate AI authorship."
            ),
            "appeal_cta": (
                "Think this is wrong? Creators can contest this classification."
            ),
        }

    elif classification == "human_written":
        pct    = round((1 - confidence_score) * 100)   # invert: express P(human)
        label  = {
            "verdict":         "Likely human-written",
            "confidence_text": f"Our system is {pct}% confident this content was written by a human.",
            "explanation": (
                "This content shows patterns consistent with human authorship. "
                "Our system evaluated it using two independent signals — a language model "
                "classifier and a statistical analyzer — and neither detected significant "
                "markers of AI generation."
            ),
            "appeal_cta": None,
        }

    else:  # uncertain
        label  = {
            "verdict":         "Origin unclear",
            "confidence_text": "Our system could not confidently determine the origin of this content.",
            "explanation": (
                "This content could not be confidently attributed to either a human or an AI. "
                "Our two signals did not agree strongly enough to reach a verdict. "
                "This label should not be treated as an accusation or a clearance."
            ),
            "appeal_cta": None,
        }

    label["appeal_notice"] = (
        "This classification is under review following a creator appeal. "
        "The verdict above may change."
        if status == "under_review" else None
    )

    return label
