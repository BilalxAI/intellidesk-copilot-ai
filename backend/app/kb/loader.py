"""
Knowledge base loading and lookup.

The single source of truth is the root-level kb.json file. The loader accepts
both supported shapes:
- {"INTENT": ["step 1", "step 2"]}
- {"Category": {"issue phrase": ["step 1", "step 2"]}}
- {"Category": {"issue phrase": "solution text"}}
"""

import json
import logging
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

KBCategoryValue = Union[List[str], Dict[str, Any]]


class KnowledgeBase:
    """Load and search IT support knowledge base."""

    def __init__(self, kb_path: str):
        self.kb_path = Path(kb_path)
        # category -> list[str] (legacy) OR category -> {issue_phrase -> steps/solution}
        self.data: Dict[str, KBCategoryValue] = {}
        self.load()

    def load(self):
        """Load KB from JSON file."""
        try:
            with self.kb_path.open("r", encoding="utf-8") as f:
                self.data = self._normalize(json.load(f))

            logger.info("KB loaded from %s", self.kb_path)
            for category, value in self.data.items():
                if isinstance(value, list):
                    logger.info("  %s: %s steps", category, len(value))
                elif isinstance(value, dict):
                    logger.info("  %s: %s entries", category, len(value))

        except FileNotFoundError:
            logger.error("KB file not found: %s", self.kb_path)
            raise
        except json.JSONDecodeError:
            logger.error("Invalid JSON in KB file: %s", self.kb_path)
            raise

    def _normalize(self, raw_data: Dict) -> Dict[str, KBCategoryValue]:
        """Normalize supported KB shapes into category -> list or category -> dict."""
        normalized: Dict[str, KBCategoryValue] = {}

        for category, value in raw_data.items():
            if isinstance(value, list):
                normalized[category] = [str(item) for item in value]
            elif isinstance(value, dict):
                # Keep the mapping so we can match "issue phrase" to the user's input.
                normalized[category] = value
            else:
                logger.warning("Skipping invalid KB category: %s", category)

        return normalized

    def find_solution(self, user_input: str, category: str) -> Tuple[str, float]:
        """
        Find solution steps for a category.

        The classifier owns category detection. The KB returns human-approved
        steps for that category so the model can format, not invent, the answer.
        """
        if category not in self.data:
            logger.warning("Category not found in KB: %s", category)
            return self._format_steps(self.get_steps("UNKNOWN", user_input)), 0.3

        steps = self.get_steps(category, user_input)
        if not steps:
            return self._format_steps(self.get_steps("UNKNOWN", user_input)), 0.3

        readable_category = category.lower().replace("_", " ")
        # Confidence: prefer issue-level match if the category supports it
        value = self.data.get(category)
        if isinstance(value, dict):
            issue_key, issue_score = self._best_issue_match(user_input, value)
            return self._format_steps(steps), max(0.5, issue_score if issue_key else 0.5)

        score = SequenceMatcher(None, user_input.lower(), readable_category).ratio()
        return self._format_steps(steps), max(0.5, score)

    def get_steps(self, category: str, user_input: str = "") -> List[str]:
        """Return raw KB steps for guided troubleshooting.

        If the category value is a dict, we select the best matching issue phrase
        based on user_input, and return its steps.
        """
        value = self.data.get(category)
        if value is None:
            value = self.data.get("UNKNOWN", [])

        if isinstance(value, list):
            return [str(item) for item in value]

        if isinstance(value, dict):
            best_key, _ = self._best_issue_match(user_input, value)
            if not best_key:
                # If we can't match a specific issue, fall back to UNKNOWN steps if present.
                unknown = self.data.get("UNKNOWN")
                if isinstance(unknown, list):
                    return [str(item) for item in unknown]
                return []
            return self._coerce_steps(value.get(best_key))

        return []

    def _format_steps(self, steps: List[str]) -> str:
        if not steps:
            return "Contact IT Support with the issue details."
        return "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))

    def get_all_issues(self) -> Dict[str, List[str]]:
        """Return available categories and their KB steps."""
        # For legacy callers, expose a flattened view.
        flattened: Dict[str, List[str]] = {}
        for category, value in self.data.items():
            if isinstance(value, list):
                flattened[category] = value
            elif isinstance(value, dict):
                # Keep only a representative list (best-effort) for display.
                combined: List[str] = []
                for item in value.values():
                    combined.extend(self._coerce_steps(item))
                flattened[category] = combined
        return flattened

    def _best_issue_match(self, user_input: str, issue_map: Dict[str, Any]) -> Tuple[Optional[str], float]:
        text = (user_input or "").lower().strip()
        if not text or not issue_map:
            return None, 0.0

        best_key: Optional[str] = None
        best_score = 0.0
        for key in issue_map.keys():
            if not isinstance(key, str) or not key.strip():
                continue
            score = SequenceMatcher(None, text, key.lower().strip()).ratio()
            if score > best_score:
                best_score = score
                best_key = key
        return best_key, best_score

    def _coerce_steps(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            # If it's already numbered, keep as one chunk; else split lines.
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return lines if lines else [text]
        return [str(value)]


_kb: Optional[KnowledgeBase] = None


def get_kb(kb_path: str) -> KnowledgeBase:
    """Get or create KB singleton."""
    global _kb
    if _kb is None:
        _kb = KnowledgeBase(kb_path)
    return _kb
