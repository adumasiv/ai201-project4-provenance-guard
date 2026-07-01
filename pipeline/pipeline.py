"""
Score combiner (planning.md §1 and §2).

Weights:   LLM classifier 65%,  heuristic 35%
Thresholds:
  >= 0.75  →  ai_generated
  <= 0.35  →  human_written
  else     →  uncertain

Fallbacks:
  LLM failed (-1.0)  → confidence = heuristic score alone
  heuristic failed   → confidence = LLM score alone
  both failed        → confidence = 0.5, classification = uncertain
"""

from pipeline.llm_signal       import run_llm_signal
from pipeline.heuristic_signal import run_heuristic_signal

_LLM_WEIGHT  = 0.65
_HEUR_WEIGHT = 0.35

_AI_THRESHOLD    = 0.75
_HUMAN_THRESHOLD = 0.35


def _classify(score: float) -> str:
    if score >= _AI_THRESHOLD:
        return "ai_generated"
    if score <= _HUMAN_THRESHOLD:
        return "human_written"
    return "uncertain"


def run_pipeline(text: str) -> dict:
    """
    Run both signals and return:
      classification, confidence_score, signals_used
    """
    llm_score  = run_llm_signal(text)
    heur_score = run_heuristic_signal(text)

    llm_failed  = llm_score  < 0
    heur_failed = heur_score < 0

    if llm_failed and heur_failed:
        confidence = 0.5
    elif llm_failed:
        confidence = heur_score
    elif heur_failed:
        confidence = llm_score
    else:
        confidence = (llm_score * _LLM_WEIGHT) + (heur_score * _HEUR_WEIGHT)

    confidence = round(max(0.0, min(1.0, confidence)), 4)

    return {
        "classification":  _classify(confidence),
        "confidence_score": confidence,
        "signals_used": [
            {"name": "llm_classifier", "score": llm_score},
            {"name": "heuristic",      "score": heur_score},
        ],
    }
