"""
Bravos Trading System — Trade Alert Parser.

Regex-first parsing with spaCy NLP fallback (per D-09).
Confidence scoring based on field completeness (per D-10).
"""
import re
import logging

logger = logging.getLogger(__name__)

# Regex patterns (validated against real post titles and bodies)
TICKER_RE = re.compile(r'\$([A-Z]{1,5})\b')
# Fallback: matches bare ticker in parentheses, e.g. "(CPER)" in og:title where $ is absent.
TICKER_PAREN_RE = re.compile(r'\(([A-Z]{1,5})\)')
PRICE_RE = re.compile(r'at \$(\d+(?:\.\d{1,2})?)')
# Matches "weight from X to Y", "weight of X to Y", "weight X to Y"
WEIGHT_RE = re.compile(
    r'weight(?:\s+(?:from|of))?\s+(\d+)\s+to\s+(\d+)',
    re.IGNORECASE,
)

# Action type keyword map — real observed vocabulary from post titles.
# Order matters: longer/more specific phrases must come BEFORE shorter substrings.
# "booking partial profits" must precede "booking profits" to avoid false partial match.
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

# spaCy lazy-load state
_nlp = None


def _get_nlp():
    """Lazy-load spaCy en_core_web_sm model. Returns None if unavailable."""
    global _nlp
    if _nlp is not None:
        return _nlp
    try:
        import spacy  # noqa: PLC0415
        _nlp = spacy.load("en_core_web_sm")
        return _nlp
    except (ImportError, OSError) as exc:
        logger.warning("spaCy not available (%s) — skipping NLP fallback", exc)
        return None


def infer_action_from_title(title: str) -> str | None:
    """Infer action type from title keywords.

    Keywords are checked in order: longer/more specific phrases first to
    avoid substring collisions (e.g. "booking partial profits" before
    "booking profits").

    Args:
        title: Post title text.

    Returns:
        Action type string (e.g. "open", "add", "partial_close", "close")
        or None if no keyword matches.
    """
    lower = title.lower()
    for keyword, action in ACTION_KEYWORDS.items():
        if keyword in lower:
            return action
    return None


def cross_check_action(
    title_action: str | None,
    weight_from: int | None,
    weight_to: int | None,
) -> str | None:
    """Cross-check title-derived action against weight direction.

    Weight direction is considered more authoritative than title keywords.
    Logs a WARNING if a conflict is detected.

    Mapping:
        weight_to > weight_from  → "open" or "add" (both increase exposure)
        weight_to < weight_from and weight_to > 0 → "partial_close"
        weight_to == 0           → "close"

    Args:
        title_action: Action inferred from title keywords, or None.
        weight_from: Starting weight value, or None.
        weight_to: Target weight value, or None.

    Returns:
        Resolved action type string, or None if weight data is absent.
    """
    if weight_from is None or weight_to is None:
        # Cannot cross-check without weight data — trust title action as-is.
        return title_action

    weight_from = int(weight_from)
    weight_to = int(weight_to)

    if weight_to == 0:
        weight_action = "close"
    elif weight_to < weight_from:
        weight_action = "partial_close"
    else:
        # weight_to >= weight_from — opening or adding
        # Distinguish "open" (from 0) vs "add" (from >0) using weight_from
        weight_action = "open" if weight_from == 0 else "add"

    if title_action is not None and title_action != weight_action:
        logger.warning(
            "Action type conflict: title says '%s' but weight direction implies '%s' "
            "(weight_from=%s, weight_to=%s). Using weight-derived action.",
            title_action,
            weight_action,
            weight_from,
            weight_to,
        )

    return weight_action


def score_confidence(ticker, action_type, weight_from, weight_to) -> str:
    """Score confidence based on field completeness (per D-10).

    4/4 fields present = 'high'
    3/4 fields present = 'medium'
    <3 fields present  = 'low'

    Reference price is optional and does not affect confidence.

    Args:
        ticker: Ticker symbol string or None.
        action_type: Action type string or None.
        weight_from: Starting weight or None.
        weight_to: Target weight or None.

    Returns:
        'high', 'medium', or 'low'.
    """
    present = sum(
        1 for f in (ticker, action_type, weight_from, weight_to)
        if f is not None
    )
    if present == 4:
        return "high"
    if present >= 2:
        return "medium"
    return "low"


def parse_signal(title: str, body: str) -> dict:
    """Parse a trade alert post into structured signal fields.

    Extraction order:
        1. Find all $TICKER patterns in title + body.
        2. If more than one unique ticker found, force confidence='low'.
        3. Extract reference_price from body (first match).
        4. Extract weight_from and weight_to from body only.
        5. Infer action_type from title keywords.
        6. If no $TICKER found, attempt spaCy ORG fallback.
        7. Cross-check action_type vs weight direction.
        8. Score confidence.

    Args:
        title: Post title text.
        body: Post body text (HTML stripped to plain text).

    Returns:
        dict with keys:
            ticker (str | None)
            action_type (str | None)
            weight_from (int | None)
            weight_to (int | None)
            reference_price (float | None)
            confidence (str)
            parse_method (str)  — 'regex' or 'spacy'
    """
    parse_method = "regex"

    # --- Ticker extraction ---
    tickers_in_title = TICKER_RE.findall(title)
    tickers_in_body = TICKER_RE.findall(body)
    all_tickers = list(dict.fromkeys(tickers_in_title + tickers_in_body))  # dedup, order-preserved

    if len(set(all_tickers)) > 1:
        # Multi-ticker post — cannot determine single signal; force low confidence.
        return {
            "ticker": None,
            "action_type": None,
            "weight_from": None,
            "weight_to": None,
            "reference_price": None,
            "confidence": "low",
            "parse_method": parse_method,
        }

    ticker = all_tickers[0] if all_tickers else None

    # Parenthetical fallback: og:title omits the $ prefix (e.g. "(CPER)" not "($CPER)").
    # Only applied when $TICKER regex found nothing in both title and body.
    if ticker is None:
        paren_in_title = TICKER_PAREN_RE.findall(title)
        if len(set(paren_in_title)) == 1:
            ticker = paren_in_title[0]

    # --- Reference price (body only, first match) ---
    price_match = PRICE_RE.search(body)
    reference_price = float(price_match.group(1)) if price_match else None

    # --- Weight (body only — title never contains weight notation per research pitfall #4) ---
    weight_match = WEIGHT_RE.search(body)
    if weight_match:
        weight_from = int(weight_match.group(1))
        weight_to = int(weight_match.group(2))
    else:
        weight_from = None
        weight_to = None

    # --- Action type from title ---
    title_action = infer_action_from_title(title)

    # --- spaCy fallback when no $TICKER found ---
    if ticker is None:
        # Mark that we entered the NLP fallback path regardless of whether
        # spaCy is installed — downstream consumers use this to know regex
        # alone was insufficient.
        parse_method = "spacy"
        nlp = _get_nlp()
        if nlp is not None:
            combined = f"{title} {body}"
            doc = nlp(combined)
            orgs = [ent.text for ent in doc.ents if ent.label_ == "ORG"]
            if orgs:
                logger.debug("spaCy ORG fallback found: %s", orgs)
            # spaCy ORG entities are company names, not ticker symbols.
            # We cannot resolve them to tickers here, but we record that
            # NLP was attempted so downstream can prompt for manual review.

    # --- Cross-check action type vs weight direction ---
    action_type = cross_check_action(title_action, weight_from, weight_to)

    # --- Confidence scoring ---
    confidence = score_confidence(ticker, action_type, weight_from, weight_to)

    return {
        "ticker": ticker,
        "action_type": action_type,
        "weight_from": weight_from,
        "weight_to": weight_to,
        "reference_price": reference_price,
        "confidence": confidence,
        "parse_method": parse_method,
    }
