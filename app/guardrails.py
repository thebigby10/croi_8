from typing import List


def validate_and_normalize(raw: List[str]) -> List[str]:
    """Validate + normalize raw LLM/fallback directives, dropping anything malformed."""
    return [d.strip() for d in raw if isinstance(d, str) and d.strip()]
