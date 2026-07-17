"""bravos/db.py — single shared DB connection helper (IMPROVEMENTS.md item 1)."""
import os

import psycopg2

from bravos.config import settings


def get_connection():
    """Open a short-lived database connection using settings + BRAVOS_DB_PASSWORD.

    Password is read at CALL time so systemd EnvironmentFile ordering can't
    bite scripts that import before the env is sourced. Raises RuntimeError
    if BRAVOS_DB_PASSWORD is unset/empty (replaces the old silent
    change_me_at_deploy fallback). Caller owns closing the connection.
    """
    password = os.environ.get("BRAVOS_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "BRAVOS_DB_PASSWORD is unset or empty. Set it before connecting "
            "(systemd: /etc/bravos/env; local: export it / source .env). On "
            "the VM it is loaded from GCP Secret Manager."
        )
    return psycopg2.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=password,
    )
