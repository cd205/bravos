#!/usr/bin/env bash
# Start IB Gateway (PAPER) via IBC on display :99
# Credentials must be injected before calling this script by populating
# /opt/ibcalpha/current/config.ini from GCP Secret Manager.
# See bravos/config/secrets_config.py for the injection pattern.

export DISPLAY=:99
BASE=/opt/ibcalpha/current

echo "$(date) ▶ Starting Gateway (paper, display $DISPLAY)"
"$BASE/gatewaystart.sh" -inline &
wait
