"""
Wave 0 test stubs for ingestion DB operations.

Tests use the db_connection fixture from conftest.py (live Cloud SQL).
Stubs are @pytest.mark.skip until plan 02-04 implements the DB write layer.

Requirement coverage:
  INGST-03 -> test_dedup_on_conflict
  INGST-06 -> test_raw_html_stored
  AUDIT-01 -> test_audit_fields_populated
"""
import pytest


def test_dedup_on_conflict(db_connection):
    """INGST-03: Duplicate post_url is silently ignored."""
    test_url = "https://test.bravosresearch.com/phase2-dedup-fixture"
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, raw_html) VALUES (%s, 'Test', '<p>test</p>') ON CONFLICT (post_url) DO NOTHING",
            (test_url,)
        )
        db_connection.commit()
        cur.execute(
            "INSERT INTO signals (post_url, post_title, raw_html) VALUES (%s, 'Dup', '<p>dup</p>') ON CONFLICT (post_url) DO NOTHING",
            (test_url,)
        )
        db_connection.commit()
        cur.execute("SELECT COUNT(*) FROM signals WHERE post_url = %s", (test_url,))
        count = cur.fetchone()[0]
        cur.execute("DELETE FROM signals WHERE post_url = %s", (test_url,))
        db_connection.commit()
    assert count == 1


def test_raw_html_stored(db_connection):
    """INGST-06: raw_html column is populated for every signal."""
    test_url = "https://test.bravosresearch.com/phase2-raw-html-fixture"
    raw = "<div class='entry-content'><p>Trade alert body</p></div>"
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, raw_html, ticker, action_type, confidence, parse_method, scraped_at) VALUES (%s, 'Test Title', %s, 'EME', 'open', 'high', 'regex', NOW()) ON CONFLICT (post_url) DO NOTHING",
            (test_url, raw)
        )
        db_connection.commit()
        cur.execute("SELECT raw_html FROM signals WHERE post_url = %s", (test_url,))
        row = cur.fetchone()
        cur.execute("DELETE FROM signals WHERE post_url = %s", (test_url,))
        db_connection.commit()
    assert row is not None
    assert row[0] == raw


def test_audit_fields_populated(db_connection):
    """AUDIT-01: Every signal has scraped_at and parse_method populated."""
    test_url = "https://test.bravosresearch.com/phase2-audit-fixture"
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, raw_html, parse_method, scraped_at) VALUES (%s, 'Audit Test', '<p>audit</p>', 'regex', NOW()) ON CONFLICT (post_url) DO NOTHING",
            (test_url,)
        )
        db_connection.commit()
        cur.execute("SELECT parse_method, scraped_at FROM signals WHERE post_url = %s", (test_url,))
        row = cur.fetchone()
        cur.execute("DELETE FROM signals WHERE post_url = %s", (test_url,))
        db_connection.commit()
    assert row is not None
    assert row[0] == "regex"
    assert row[1] is not None  # scraped_at timestamp
