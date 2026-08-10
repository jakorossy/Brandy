"""
Comment-level pipeline.

Handles:
- Spam / irrelevant filtering (keyword-based)
- Sentiment classification of individual comments
- Aggregation at post level
- Aggregation at brand level (for future scoring — deferred)
"""

import logging

from brandy.social.sentiment import get_sentiment_score, classify_sentiment

logger = logging.getLogger(__name__)

# Default spam indicators — override via social_config.yaml
DEFAULT_SPAM_KEYWORDS = [
    "follow me", "follow back", "f4f", "l4l", "check my",
    "dm me", "dm for", "click the link", "link in bio",
    "free followers", "buy followers", "make money",
    "giveaway", "congratulations you won", "claim your",
    "visit my profile", "check out my",
]

# Default irrelevant patterns — single emojis, @mentions only, very short
MIN_RELEVANT_LENGTH = 5


def is_spam(text: str, spam_keywords: list[str] = None) -> bool:
    """Check if a comment is likely spam."""
    if not text:
        return False
    lower = text.lower().strip()
    keywords = spam_keywords or DEFAULT_SPAM_KEYWORDS
    return any(kw in lower for kw in keywords)


def is_irrelevant(text: str, min_length: int = MIN_RELEVANT_LENGTH) -> bool:
    """Check if a comment is too short or substance-free to be useful."""
    if not text:
        return True
    stripped = text.strip()
    # Pure emoji / whitespace / single character
    if len(stripped) < min_length:
        return True
    # Only @mentions
    import re
    without_mentions = re.sub(r'@\w+', '', stripped).strip()
    if len(without_mentions) < min_length:
        return True
    return False


def classify_comment(
    text: str,
    spam_keywords: list[str] = None,
    min_length: int = MIN_RELEVANT_LENGTH,
    pos_threshold: float = 0.05,
    neg_threshold: float = -0.05,
) -> dict:
    """
    Full classification pipeline for a single comment.

    Returns:
        {
            "is_spam": bool,
            "is_irrelevant": bool,
            "sentiment_score": float,
            "sentiment_label": str  # positive|neutral|negative|spam|irrelevant
        }
    """
    if is_spam(text, spam_keywords):
        return {
            "is_spam": True,
            "is_irrelevant": False,
            "sentiment_score": 0.0,
            "sentiment_label": "spam",
        }

    if is_irrelevant(text, min_length):
        return {
            "is_spam": False,
            "is_irrelevant": True,
            "sentiment_score": 0.0,
            "sentiment_label": "irrelevant",
        }

    score = get_sentiment_score(text)
    label = classify_sentiment(score, pos_threshold, neg_threshold)
    return {
        "is_spam": False,
        "is_irrelevant": False,
        "sentiment_score": score,
        "sentiment_label": label,
    }


def aggregate_post_sentiment(classified_comments: list[dict]) -> dict:
    """
    Aggregate comment-level classifications into post-level sentiment.

    Input: list of classify_comment() results.
    Returns dict matching the social_post_sentiment table columns.
    """
    positive = 0
    neutral = 0
    negative = 0
    spam = 0
    irrelevant = 0
    scores = []

    for c in classified_comments:
        label = c.get("sentiment_label", "")
        if label == "positive":
            positive += 1
            scores.append(c["sentiment_score"])
        elif label == "neutral":
            neutral += 1
            scores.append(c["sentiment_score"])
        elif label == "negative":
            negative += 1
            scores.append(c["sentiment_score"])
        elif label == "spam":
            spam += 1
        elif label == "irrelevant":
            irrelevant += 1

    analyzed = positive + neutral + negative
    avg_sentiment = sum(scores) / len(scores) if scores else None

    return {
        "total_comments_analyzed": analyzed,
        "positive_count": positive,
        "neutral_count": neutral,
        "negative_count": negative,
        "spam_count": spam,
        "irrelevant_count": irrelevant,
        "avg_sentiment": avg_sentiment,
    }
