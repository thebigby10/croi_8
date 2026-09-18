from typing import Dict, List


def build_params(directives: List[str]) -> Dict:
    """Turn normalized directives into optimizer parameters."""
    return {"directives": directives}
