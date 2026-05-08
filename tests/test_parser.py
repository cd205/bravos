"""
Wave 0 test stubs for bravos.ingestion.parser.

Tests feed known-format title+body strings and assert field extraction.
Stubs are @pytest.mark.skip until plan 02-02 implements the parser.

Requirement coverage:
  INGST-04 -> test_extract_ticker, test_extract_action_type, test_extract_weight, test_extract_price
  INGST-05 -> test_confidence_high, test_confidence_medium, test_confidence_low
"""
import pytest


def test_extract_ticker():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Initiating Long on EMCOR Group, Inc. ($EME) | Breakout",
        body="We are initiating a long position in EMCOR Group at $42.50. We are increasing weight from 0 to 5."
    )
    assert result["ticker"] == "EME"


def test_extract_action_type_open():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Initiating Long on EMCOR Group, Inc. ($EME) | Breakout",
        body="We are initiating a long position in EMCOR Group at $42.50. We are increasing weight from 0 to 5."
    )
    assert result["action_type"] == "open"


def test_extract_action_type_partial_close():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Booking Partial Profits on Meta Platforms ($META) | 8.32% Profit",
        body="We are booking partial profits on Meta Platforms. Reducing weight from 8 to 4 at current market price."
    )
    assert result["action_type"] == "partial_close"


def test_extract_action_type_close():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Closing ProShares UltraShort 20+ Year Treasury ($TBT)",
        body="Closing our position in $TBT. Moving weight from 3 to 0."
    )
    assert result["action_type"] == "close"


def test_extract_weight():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Initiating Long on EMCOR Group, Inc. ($EME) | Breakout",
        body="We are initiating a long position in EMCOR Group at $42.50. We are increasing weight from 0 to 5."
    )
    assert result["weight_from"] == 0
    assert result["weight_to"] == 5


def test_extract_price():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Initiating Long on EMCOR Group, Inc. ($EME) | Breakout",
        body="We are initiating a long position in EMCOR Group at $42.50. We are increasing weight from 0 to 5."
    )
    assert result["reference_price"] == 42.50


def test_confidence_high():
    from bravos.ingestion.parser import score_confidence
    assert score_confidence("EME", "open", 0, 5) == "high"


def test_confidence_medium():
    from bravos.ingestion.parser import score_confidence
    assert score_confidence("META", "partial_close", None, None) == "medium"


def test_confidence_low():
    from bravos.ingestion.parser import score_confidence
    assert score_confidence(None, None, None, None) == "low"


def test_multi_ticker_forces_low_confidence():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Portfolio Update: Increasing TLT, GLD; Closing IYT, XLK, XLY",
        body="Multiple position updates..."
    )
    assert result["confidence"] == "low"


def test_weight_direction_cross_check():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Increasing Exposure to Acme ($ACM) | Technical Strength",
        body="We are increasing our weight from 3 to 6."
    )
    assert result["action_type"] == "add"
    assert result["weight_from"] == 3
    assert result["weight_to"] == 6


def test_spacy_fallback():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Initiating Long on EMCOR Group | Breakout",
        body="We are initiating a long position in EMCOR Group at $42.50. Weight from 0 to 5."
    )
    # No $TICKER in title — spaCy should attempt ORG extraction
    assert result["parse_method"] == "spacy"
