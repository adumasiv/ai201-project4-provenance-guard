import json
import os
from groq import Groq

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


SYSTEM_PROMPT = (
    "You are an AI-authorship classifier. Analyze the following text and return ONLY "
    "a JSON object with one key: \"ai_probability\" (float 0.0 to 1.0, where 1.0 means "
    "you are certain the text was AI-generated and 0.0 means you are certain it was "
    "written by a human). Consider: sentence rhythm consistency, hedging language, "
    "structural cleanliness, vocabulary range, and presence of personal voice. "
    "Return nothing but the JSON."
)


def run_llm_signal(text: str) -> float:
    """Return P(AI) in [0.0, 1.0]. Returns -1.0 on any failure."""
    try:
        response = _get_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=64,
        )
        raw = response.choices[0].message.content.strip()
        # strip markdown code fences if the model wraps the JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        score = float(data["ai_probability"])
        return max(0.0, min(1.0, score))
    except Exception:
        return -1.0
