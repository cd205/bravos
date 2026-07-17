"""tests/test_db.py — pure-mock tests for bravos.db.get_connection().

No real DB connection is made. psycopg2.connect is monkeypatched to a
MagicMock in every test so these are safe to run against production.
"""
from unittest.mock import MagicMock

import pytest

from bravos import db


def test_raises_when_password_unset(monkeypatch):
    monkeypatch.delenv("BRAVOS_DB_PASSWORD", raising=False)
    mock_connect = MagicMock(side_effect=AssertionError("connect must not be called"))
    monkeypatch.setattr(db.psycopg2, "connect", mock_connect)

    with pytest.raises(RuntimeError, match="BRAVOS_DB_PASSWORD"):
        db.get_connection()

    mock_connect.assert_not_called()


def test_raises_when_password_empty(monkeypatch):
    monkeypatch.setenv("BRAVOS_DB_PASSWORD", "")
    mock_connect = MagicMock(side_effect=AssertionError("connect must not be called"))
    monkeypatch.setattr(db.psycopg2, "connect", mock_connect)

    with pytest.raises(RuntimeError, match="BRAVOS_DB_PASSWORD"):
        db.get_connection()

    mock_connect.assert_not_called()


def test_passes_correct_kwargs(monkeypatch):
    monkeypatch.setenv("BRAVOS_DB_PASSWORD", "secret")
    sentinel_conn = object()
    mock_connect = MagicMock(return_value=sentinel_conn)
    monkeypatch.setattr(db.psycopg2, "connect", mock_connect)

    result = db.get_connection()

    assert result is sentinel_conn
    mock_connect.assert_called_once_with(
        host=db.settings.DB_HOST,
        port=db.settings.DB_PORT,
        dbname=db.settings.DB_NAME,
        user=db.settings.DB_USER,
        password="secret",
    )
