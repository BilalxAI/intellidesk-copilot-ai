"""
Text cleaning and preprocessing utilities.
"""

import re
import logging

logger = logging.getLogger(__name__)


def clean_text(text: str) -> str:
    """
    Clean and normalize user input text
    
    Args:
        text: Raw user input
        
    Returns:
        Cleaned text
    """
    
    if not text:
        return ""
    
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Convert to lowercase for analysis (but keep original for context)
    # Actually, we keep it as-is since we do case-insensitive matching
    
    # Remove special characters but keep readability
    text = text.replace('\n', ' ').replace('\r', '')
    
    # Remove multiple punctuation
    text = re.sub(r'[!?]{2,}', '!', text)
    
    return text


def extract_keywords(text: str) -> list:
    """Extract important keywords from text"""
    
    # Remove common stop words
    stop_words = {
        'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for',
        'from', 'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on',
        'that', 'the', 'to', 'was', 'will', 'with', 'my', 'i'
    }
    
    # Split and clean
    words = text.lower().split()
    keywords = [w for w in words if w not in stop_words and len(w) > 2]
    
    return keywords


def is_valid_input(text: str) -> bool:
    """Check if input is valid"""
    
    if not text or len(text.strip()) < 3:
        return False
    
    # Check if it's mostly gibberish (too many special chars)
    special_ratio = len([c for c in text if not c.isalnum() and c.isascii()]) / len(text)
    if special_ratio > 0.5:
        return False
    
    return True
