"""
Heuristic signal: statistical surface features of the text.
Returns P(AI) in [0.0, 1.0]. No external calls.

Three sub-features (from planning.md §1, Signal 2):
  1. Type-Token Ratio (TTR)         weight 0.35  — low diversity → more AI-like
  2. Sentence length variance       weight 0.30  — low variance  → more AI-like
  3. Filler phrase density          weight 0.35  — high density  → more AI-like

Short-text rule: below 100 words, all sub-feature scores are halved before
combining (i.e. the signal pulls toward 0.5, not toward human or AI).
"""

import re
import statistics

# Phrases that appear at elevated rates in LLM output but rarely in
# pre-LLM corpora. Deliberately excludes generic academic transitions.
FILLER_PHRASES = [
    "it's important to note",
    "it is important to note",
    "it's worth noting",
    "it is worth noting",
    "in conclusion",
    "to summarize",
    "delve into",
    "in today's world",
    "in today's rapidly",
    "at its core",
    "let's explore",
    "let us explore",
    "it's worth mentioning",
    "it is worth mentioning",
    "it goes without saying",
    "needless to say",
    "as we can see",
    "as previously mentioned",
]

_TTR_WINDOW = 50          # sliding window size for TTR
_VAR_CAP    = 20.0        # SD cap for sentence-length variance normalisation
_FILL_CAP   = 3.0         # hits-per-100-words cap for filler density


def _sliding_ttr(words: list[str], window: int = _TTR_WINDOW) -> float:
    """Mean type-token ratio over a sliding window. Returns 1.0 for short texts."""
    if len(words) < window:
        return len(set(words)) / len(words) if words else 1.0
    ratios = []
    for i in range(len(words) - window + 1):
        chunk = words[i : i + window]
        ratios.append(len(set(chunk)) / window)
    return statistics.mean(ratios)


def _sentence_variance(text: str) -> float:
    """Normalised [0,1] sentence-length SD. 0 = perfectly uniform, 1 = very varied."""
    sentences = re.split(r"[.!?]+", text)
    lengths = [len(s.split()) for s in sentences if s.strip()]
    if len(lengths) < 2:
        return 0.0
    sd = statistics.stdev(lengths)
    return min(sd / _VAR_CAP, 1.0)


def _filler_density(text: str, word_count: int) -> float:
    """Hits per 100 words, normalised to [0,1] by capping at _FILL_CAP."""
    if word_count == 0:
        return 0.0
    lower = text.lower()
    hits = sum(lower.count(phrase) for phrase in FILLER_PHRASES)
    per_hundred = (hits / word_count) * 100
    return min(per_hundred / _FILL_CAP, 1.0)


def run_heuristic_signal(text: str) -> float:
    """Return P(AI) in [0.0, 1.0]."""
    words = text.split()
    word_count = len(words)

    variance = _sentence_variance(text)
    density  = _filler_density(text, word_count)

    variance_score = 1.0 - variance  # low variance  = more AI-like
    filler_score   = density         # high density  = more AI-like

    if word_count >= _TTR_WINDOW:
        # TTR is only reliable when we have at least one full sliding window.
        ttr       = _sliding_ttr(words)
        ttr_score = 1.0 - ttr        # low diversity = more AI-like
        score = (
            ttr_score      * 0.35 +
            variance_score * 0.30 +
            filler_score   * 0.35
        )
    else:
        # Too short for TTR — redistribute its weight to the reliable signals.
        score = (
            variance_score * 0.45 +
            filler_score   * 0.55
        )

    return round(max(0.0, min(1.0, score)), 4)
