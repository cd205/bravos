"""
Bravos Trading System — Trade Alert Parser.

Regex-first parsing with spaCy NLP fallback (per D-09).
Confidence scoring based on field completeness (per D-10).
"""
import re
import logging

logger = logging.getLogger(__name__)

# Regex patterns (from CLAUDE.md, validated against real post titles)
TICKER_RE = re.compile(r'\$([A-Z]{1,5})\b')
PRICE_RE = re.compile(r'at \$(\d+(?:\.\d{1,2})?)')
WEIGHT_RE = re.compile(r'weight(?:\s+of)?\s+(\d+)\s+to\s+(\d+)', re.IGNORECASE)

# Action type keyword map (from real observed post titles — NOT CLAUDE.md approximations)
ACTION_KEYWORDS = {
    "booking partial profits": "partial_close",
    "booking profits": "partial_close",
    "partial profit": "partial_close",
    "initiating long": "open",
    "initiating a long": "open",
    "increasing exposure": "add",
    "adding to": "add",
    "closing": "close",
}


def parse_signal(title: str, body: str) -> dict:
    """Parse a trade alert post into structured signal fields.

    Args:
        title: Post title text.
        body: Post body text (inner HTML stripped to text).

    Returns:
        dict with keys: ticker, action_type, weight_from, weight_to,
        reference_price, confidence, parse_method.
    """
    raise NotImplementedError("Stub — implemented in plan 02-02")


def score_confidence(ticker, action_type, weight_from, weight_to) -> str:
    """Score confidence based on field completeness (per D-10).

    4/4 fields = 'high', 3/4 = 'medium', <3 = 'low'.
    Reference price is optional and does not affect confidence.
    """
    raise NotImplementedError("Stub — implemented in plan 02-02")
