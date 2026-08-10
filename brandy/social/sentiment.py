"""
Sentiment classification.

Extends the original sentiment2.py VADER wrapper with:
- Label classification (positive / neutral / negative)
- Configurable thresholds
- Batch processing for comment lists

The core VADER logic is preserved from the original.
"""

import logging

import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)

# Ensure VADER lexicon is available (from sentiment2.py)
nltk.download("vader_lexicon", quiet=True)

_analyzer = SentimentIntensityAnalyzer()

# Default thresholds — can be overridden via config
POSITIVE_THRESHOLD = 0.05
NEGATIVE_THRESHOLD = -0.05


def get_sentiment_score(text: str) -> float:
    """
    Return VADER compound sentiment score for a text string.
    Reused from sentiment2.py.
    """
    if not isinstance(text, str) or not text.strip():
        return 0.0
    return _analyzer.polarity_scores(text)["compound"]


def classify_sentiment(
    score: float,
    pos_threshold: float = POSITIVE_THRESHOLD,
    neg_threshold: float = NEGATIVE_THRESHOLD,
) -> str:
    """
    Classify a compound score into a label.
    Returns: 'positive', 'neutral', or 'negative'.
    """
    if score >= pos_threshold:
        return "positive"
    elif score <= neg_threshold:
        return "negative"
    else:
        return "neutral"


def analyze_comment(
    text: str,
    pos_threshold: float = POSITIVE_THRESHOLD,
    neg_threshold: float = NEGATIVE_THRESHOLD,
) -> dict:
    """
    Analyze a single comment: score + label.
    Returns {"sentiment_score": float, "sentiment_label": str}.
    """
    score = get_sentiment_score(text)
    label = classify_sentiment(score, pos_threshold, neg_threshold)
    return {"sentiment_score": score, "sentiment_label": label}


def analyze_comments_batch(
    comments: list[dict],
    pos_threshold: float = POSITIVE_THRESHOLD,
    neg_threshold: float = NEGATIVE_THRESHOLD,
) -> list[dict]:
    """
    Analyze a list of comment dicts in place.
    Each dict should have a "text" key.
    Adds "sentiment_score" and "sentiment_label" to each.
    Returns the same list (mutated).
    """
    for comment in comments:
        text = comment.get("text", "")
        result = analyze_comment(text, pos_threshold, neg_threshold)
        comment.update(result)
    return comments
