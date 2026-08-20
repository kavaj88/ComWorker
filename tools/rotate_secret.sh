#!/usr/bin/env bash
# Rotate a single ComWorker gateway secret without disturbing the others.
#
# Usage:
#   bash tools/rotate_secret.sh jwt_secret
#   bash tools/rotate_secret.sh model_keys_master_key    # ⚠ invalidates encrypted API keys
#   bash tools/rotate_secret.sh admin_password
#
# The new value is written to ./secrets/<name> with 0600 permissions.
# After running this you MUST restart the gateway container:
#   docker compose restart gateway
#
# ⚠ Rotating model_keys_master_key INVALIDATES every LLM provider API key
#   stored encrypted in the database. Re-enter them via the admin UI after
#   the gateway comes back up — provider records will show up as empty.

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <jwt_secret|model_keys_master_key|admin_password>" >&2
  exit 2
fi

NAME="$1"
case "$NAME" in
  jwt_secret|model_keys_master_key|admin_password) ;;
  *) echo "Unknown secret name: $NAME" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/.."
mkdir -p secrets
chmod 700 secrets

if [ "$NAME" = "model_keys_master_key" ]; then
  echo "⚠ Rotating model_keys_master_key will INVALIDATE all encrypted LLM" >&2
  echo "  provider API keys in the database. Continue? [y/N]" >&2
  read -r ans
  if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then
    echo "Aborted."
    exit 1
  fi
fi

python -c 'import secrets; print(secrets.token_urlsafe(64))' > "secrets/$NAME"
chmod 600 "secrets/$NAME"
echo "✓ secrets/$NAME rotated. Run: docker compose restart gateway"