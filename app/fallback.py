import re
from typing import List


def extract_directives(instructions: str) -> List[str]:
    """Regex/rule-based backup for when the LLM call fails or is unavailable."""
    return re.findall(r"\b\w+\b", instructions.lower())
