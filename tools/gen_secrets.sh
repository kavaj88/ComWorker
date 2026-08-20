#!/usr/bin/env bash
# Generate strong random secrets for the ComWorker gateway and write them
# into ./secrets/ as docker-compose file-based secrets.
#
# Idempotent — re-running overwrites any existing files with fresh values.
# Operators who want to rotate one secret should run `tools/rotate_secret.sh
# <name>` instead so that downstream tokens / encrypted DB rows keep working.
#
# Usage:
#   bash tools/gen_secrets.sh                 # create missing secrets
#   bash tools/gen_secrets.sh --force        # overwrite all secrets
#
# After generation:
#   - secrets/jwt_secret                  (PLATFORM_JWT_SECRET)
#   - secrets/model_keys_master_key        (PLATFORM_MODEL_KEYS_MASTER_KEY)
#   - secrets/admin_password               (PLATFORM_ADMIN_PASSWORD)
#
# ⚠ Rotating model_keys_master_key INVALIDATES all previously-encrypted
#   LLM provider API keys in the database. Re-enter them via the admin UI
#   after a rotation.

set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p secrets
chmod 700 secrets

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force|-f) FORCE=1 ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

write_secret() {
  local name="$1"
  local path="secrets/$name"
  if [ -f "$path" ] && [ "$FORCE" -eq 0 ]; then
    echo "  • $name exists, skipping (use --force to overwrite)"
    return
  fi
  # 64 bytes → 86 url-safe base64 chars. Well above the 32-byte minimum.
  python -c 'import secrets; print(secrets.token_urlsafe(64))' > "$path"
  chmod 600 "$path"
  echo "  • $name generated"
}

echo "==> Generating ComWorker gateway secrets in ./secrets/"
write_secret jwt_secret
write_secret model_keys_master_key

# admin_password is optional — if it stays empty the platform auto-generates
# a strong random password at startup and prints it in the logs. Generate
# one only when --force is set so existing local development passwords are
# preserved.
if [ "$FORCE" -eq 1 ] || [ ! -f secrets/admin_password ]; then
  if [ ! -f secrets/admin_password ] || [ "$FORCE" -eq 1 ]; then
    if [ "$FORCE" -eq 0 ]; then
      # First-run: skip generating admin_password so the platform auto-
      # generates one and shows it in the gateway container logs.
      echo "  • admin_password: leaving empty (platform will auto-generate at startup)"
      : > secrets/admin_password
      chmod 600 secrets/admin_password
    else
      python -c 'import secrets; print(secrets.token_urlsafe(18))' > secrets/admin_password
      chmod 600 secrets/admin_password
      echo "  • admin_password generated (overwritten)"
    fi
  fi
fi

echo
echo "Done. Next steps:"
echo "  1. docker compose up -d"
echo "  2. docker logs comworker-gateway | grep -A6 'INITIAL ADMIN'"
echo "  3. Visit http://localhost:3081 and log in with the printed credentials"