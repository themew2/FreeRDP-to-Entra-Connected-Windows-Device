#!/usr/bin/env bash
set -euo pipefail

DEFAULT_HOST="your-vm-hostname"
DEFAULT_USER="youruser@yourdomain.com"
TENANT_ID="your-entra-tenant-id"
FREERDP_BIN="$HOME/FreeRDP/build/client/SDL/SDL3/sdl-freerdp"
RES_WIDTH="2560"
RES_HEIGHT="1440"

HOST="${1:-$DEFAULT_HOST}"
USERNAME="${2:-$DEFAULT_USER}"

if [[ ! -x "$FREERDP_BIN" ]]; then
    echo "Error: FreeRDP binary not found at $FREERDP_BIN"
    exit 1
fi

if ! getent hosts "$HOST" > /dev/null 2>&1; then
    echo "Warning: '$HOST' does not resolve via DNS or /etc/hosts." >&2
fi

echo "Connecting to $HOST as $USERNAME ..."

exec "$FREERDP_BIN" \
    /v:"$HOST" \
    /sec:aad \
    /azure:tenantid:"$TENANT_ID" \
    /u:"$USERNAME" \
    /cert:ignore \
    /f \
    /w:"$RES_WIDTH" \
    /h:"$RES_HEIGHT" \
    /clipboard \
    /microphone:sys:pulse \
    /sound:sys:pulse
