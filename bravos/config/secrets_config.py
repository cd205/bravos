"""
Bravos Trading System — GCP Secret Manager reader.

All credentials are stored in GCP Secret Manager and never on disk.
Call get_secret() to fetch a single secret, or validate_secrets() at
startup to confirm all required secrets are readable.
"""
from google.cloud import secretmanager

PROJECT_ID = "crafty-water-453519-d7"

REQUIRED_SECRETS = [
    "bravos-site-username",
    "bravos-site-password",
    "bravos-ibkr-username",
    "bravos-ibkr-password",
    "bravos-ibkr-port",
    "bravos-ibkr-clientid",
    "bravos-db-password",
]


def get_secret(secret_id: str, project_id: str = PROJECT_ID) -> str:
    """Fetch the latest version of a secret from GCP Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8")


def validate_secrets(project_id: str = PROJECT_ID) -> None:
    """
    Verify all required secrets are readable at startup.
    Raises RuntimeError listing any unreadable secrets.
    Call this before starting any trading activity.
    """
    failures = []
    for secret_id in REQUIRED_SECRETS:
        try:
            value = get_secret(secret_id, project_id)
            if not value.strip():
                failures.append(f"{secret_id}: empty value")
        except Exception as e:
            failures.append(f"{secret_id}: {e}")

    if failures:
        raise RuntimeError(
            "Secret Manager validation failed — cannot start:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
