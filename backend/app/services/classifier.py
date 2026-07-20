"""
Small-model-friendly IT issue classifier.

The dataset.jsonl file is used as local supervision data. We first classify
with deterministic matching so the bot works even when Ollama is offline. A
small SLM can still be used downstream to format the KB-grounded response.
"""

import json
import logging
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Tuple

from app.config import (
    CLASSIFICATION_CONFIDENCE_THRESHOLD,
    DATASET_PATH,
    ISSUE_CATEGORIES,
)

logger = logging.getLogger(__name__)


class IssueClassifier:
    """Classify IT issues into predefined intent categories."""

    KEYWORDS: Dict[str, List[str]] = {
        "HEADSET_ISSUE": [
            "headset",
            "headphone",
            "audio",
            "microphone",
            "mic",
            "speaker",
            "sound",
            "voice",
            "jack",
            "headgears",
        ],
        "DISPLAY_ISSUE": [
            "screen",
            "display",
            "monitor",
            "blank",
            "black screen",
            "no display",
            "flicker",
            "resolution",
            "graphics",
            "hdmi",
            "projector",
        ],
        "KEYBOARD_MOUSE_ISSUE": [
            "keyboard",
            "mouse",
            "trackpad",
            "touchpad",
            "cursor",
            "click",
            "typing",
            "keys",
            "not responding",
            "wireless mouse",
            "bluetooth mouse",
        ],
        "NETWORK_ISSUE": [
            "wifi",
            "internet",
            "network",
            "ethernet",
            "router",
            "connection",
            "connectivity",
            "adapter",
            "dns",
        ],
        "TEAMS_ISSUE": [
            "teams",
            "team",
            "microsoft teams",
            "ms teams",
            "meeting",
            "call",
            "conference",
            "video call",
            "chat",
        ],
        "OUTLOOK_ISSUE": [
            "outlook",
            "oulook",  # common misspelling
            "email",
            "mail",
            "inbox",
            "calendar",
            "sync",
            "send",
            "sending",
            "receive",
            "received",
            "outlook.com",
            "exchange",
            "mailbox",
            "e-mail",
            "smtp",
            "imap",
        ],
        "HARDWARE_ISSUE": [
            "printer",
            "drive",
            "hardware",
            "device",
            "external",
            "usb",
            "storage",
        ],
        "SOFTWARE_INSTALLATION": [
            "install",
            "installer",
            "installation",
            "download",
            "downaloding",  # common misspelling
            "downloading",
            "setup",
            "software",
            "office",
            "adobe",
            "google",
            "chrome",
            "firefox",
            "browser",
        ],
    }

    def __init__(self, dataset_path: str = DATASET_PATH):
        self.dataset_path = Path(dataset_path)
        self.examples = self._load_examples()

    def classify(self, user_input: str) -> Tuple[str, float]:
        text = user_input.strip().lower()
        if not text:
            return "UNKNOWN", 0.0

        scores: Dict[str, float] = {}
        for category in ISSUE_CATEGORIES:
            if category == "UNKNOWN":
                continue
            keyword_score = self._keyword_score(text, category)
            example_score = self._example_score(text, category)

            # Guard against false positives from example similarity:
            # only let examples drive classification when similarity is very high OR we already have keyword signal.
            if keyword_score <= 0.0 and example_score < 0.75:
                example_score = 0.0

            scores[category] = max(keyword_score, example_score)

        best_category = max(scores, key=scores.get, default="UNKNOWN")
        confidence = scores.get(best_category, 0.0)

        if confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD:
            # Return special category to signal pipeline to use LLM for classification
            logger.info("Classification below threshold (%.2f): using LLM classification", confidence)
            return "NEEDS_LLM_CLASSIFICATION", confidence

        logger.info("Classification: %s (%.2f)", best_category, confidence)
        return best_category, confidence

    def _load_examples(self) -> Dict[str, List[str]]:
        examples: Dict[str, List[str]] = defaultdict(list)

        if not self.dataset_path.exists():
            logger.warning("Dataset not found: %s", self.dataset_path)
            return examples

        with self.dataset_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                label = item.get("label")
                text = item.get("text")
                if label in ISSUE_CATEGORIES and isinstance(text, str):
                    examples[label].append(text.lower())

        logger.info("Loaded %s classification examples", sum(len(v) for v in examples.values()))
        return examples

    def _keyword_score(self, text: str, category: str) -> float:
        keywords = self.KEYWORDS.get(category, [])
        if not keywords:
            return 0.0

        matches = sum(1 for keyword in keywords if keyword in text)
        if matches == 0:
            # Try fuzzy matching for typos
            fuzzy_matches = self._fuzzy_keyword_match(text, keywords)
            if fuzzy_matches > 0:
                return min(0.85, 0.50 + (fuzzy_matches * 0.15))
            return 0.0

        # Exact match bonus: if the keyword appears as a whole word, give higher score
        exact_matches = sum(1 for keyword in keywords if f" {keyword} " in f" {text} " or text.startswith(f"{keyword} ") or text.endswith(f" {keyword}") or keyword == text)
        
        if exact_matches > 0:
            return min(0.98, 0.85 + (exact_matches * 0.05))
        
        return min(0.98, 0.60 + (matches / max(len(keywords), 1)))

    def _fuzzy_keyword_match(self, text: str, keywords: List[str]) -> int:
        """Count keywords that have fuzzy match in text (for typo handling)."""
        fuzzy_count = 0
        for keyword in keywords:
            # Skip short keywords for fuzzy matching to avoid false positives
            if len(keyword) < 4:
                continue
            # Check if any word in text is similar to keyword
            for word in text.split():
                if len(word) >= 4:
                    ratio = SequenceMatcher(None, keyword, word).ratio()
                    if ratio >= 0.75:  # 75% similarity threshold
                        fuzzy_count += 1
                        break
        return fuzzy_count

    def _example_score(self, text: str, category: str) -> float:
        examples = self.examples.get(category, [])
        if not examples:
            return 0.0

        return max(SequenceMatcher(None, text, example).ratio() for example in examples)


_classifier = None


def get_classifier() -> IssueClassifier:
    global _classifier
    if _classifier is None:
        _classifier = IssueClassifier()
    return _classifier
