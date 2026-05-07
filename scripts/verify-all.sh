#!/usr/bin/env bash
# Bravos Trading System — Infrastructure Verification Script
# Run on bravos-vm1 to confirm all Phase 1 components are operational.
# Usage: BRAVOS_DB_PASSWORD=<password> bash scripts/verify-all.sh

set -e
cd "$(dirname "$0")/.."

echo "=== Bravos Infrastructure Verification ==="
echo ""

echo "1. Python environment..."
python3 --version
python3 -c "import ibapi, psycopg2, selenium, fastapi, structlog, alembic; print('   All packages OK')"

echo ""
echo "2. Cloud SQL Proxy..."
systemctl is-active cloud-sql-proxy && echo "   cloud-sql-proxy: active" || echo "   WARNING: cloud-sql-proxy not running"

echo ""
echo "3. Xvfb..."
systemctl is-active xvfb@99 && echo "   xvfb@99: active" || echo "   WARNING: xvfb@99 not running"

echo ""
echo "4. Chrome headless..."
python3 scripts/verify_chrome.py

echo ""
echo "5. GCP Secret Manager..."
gcloud secrets versions access latest --secret=bravos-ibkr-port --project=crafty-water-453519-d7 > /dev/null && echo "   Secrets readable OK"

echo ""
echo "6. Test suite..."
~/miniconda3/bin/pytest tests/test_infrastructure.py -q

echo ""
echo "=== All checks complete ==="
