import os
from typing import List


def get_directives(instructions: str) -> List[str]:
    """Single LLM call producing a raw (unvalidated) directive list."""
    if not instructions.strip() or not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("LLM unavailable")

    from google import genai  # lazy import: optional dependency at call time

    client = genai.Client()
    resp = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=f"List energy directives for: {instructions}",
    )
    return [line.strip() for line in resp.text.splitlines() if line.strip()]
