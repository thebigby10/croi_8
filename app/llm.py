import os
from typing import List


def get_directives(instructions: str) -> List[str]:
    """Single LLM call producing a raw (unvalidated) directive list."""
    if not instructions.strip() or not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("LLM unavailable")

    from anthropic import Anthropic  # lazy import: optional dependency at call time

    client = Anthropic()
    resp = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=200,
        messages=[{"role": "user", "content": f"List energy directives for: {instructions}"}],
    )
    return [line.strip() for line in resp.content[0].text.splitlines() if line.strip()]
