#!/bin/bash
# Docker/Podman entrypoint: bootstrap config files into the mounted volume, then run hermes.
set -e

HERMES_HOME="${HERMES_HOME:-/opt/data}"
INSTALL_DIR="/opt/hermes"

sync_comworker_packaged_skills() {
    if [ "${COMWORKER_PACKAGED_SKILLS_SYNCED:-}" = "1" ]; then
        return 0
    fi

    local src_skills="$INSTALL_DIR/deploy_copy/skills"
    local dst_skills="$HERMES_HOME/skills"
    if [ ! -d "$src_skills" ]; then
        return 0
    fi

    mkdir -p "$dst_skills"
    echo "Syncing ComWorker packaged skills into $dst_skills"
    while IFS= read -r -d '' skill_src; do
        local skill_name
        local skill_dst
        skill_name="$(basename "$skill_src")"
        skill_dst="$dst_skills/$skill_name"
        rm -rf -- "$skill_dst"
        cp -a -- "$skill_src" "$dst_skills/"
        if [ "$(id -u)" = "0" ]; then
            chown -R hermes:hermes "$skill_dst"
        fi
    done < <(find "$src_skills" -mindepth 1 -maxdepth 1 -type d -print0)

    if [ "$(id -u)" = "0" ] && [ -d "$dst_skills" ]; then
        chown -R hermes:hermes "$dst_skills"
    fi
    export COMWORKER_PACKAGED_SKILLS_SYNCED=1
}

sync_comworker_packaged_agents() {
    if [ "${COMWORKER_PACKAGED_AGENTS_SYNCED:-}" = "1" ]; then
        return 0
    fi

    local src_agents="$INSTALL_DIR/deploy_copy/Agents"
    local dst_profiles="$HERMES_HOME/profiles"
    if [ ! -d "$src_agents" ]; then
        return 0
    fi

    mkdir -p "$dst_profiles"
    echo "Syncing ComWorker packaged agents into $dst_profiles"
    while IFS= read -r -d '' agent_src; do
        local agent_name
        local profile_dir
        agent_name="$(basename "$agent_src")"
        profile_dir="$dst_profiles/$agent_name"

        if [ -d "$profile_dir" ]; then
            echo "  Profile '$agent_name' already exists, skipping"
            continue
        fi

        echo "  Creating profile: $agent_name"
        mkdir -p "$profile_dir"/{memories,sessions,skills,skins,logs,plans,workspace,cron,home}

        if [ -f "$agent_src/SOUL.md" ]; then
            cp -a "$agent_src/SOUL.md" "$profile_dir/SOUL.md"
        fi

        for f in IDENTITY.md USER.md AGENTS.md; do
            if [ -f "$agent_src/$f" ]; then
                cp -a "$agent_src/$f" "$profile_dir/workspace/$f"
            fi
        done

        if [ -f "$agent_src/USER.md" ]; then
            cp -a "$agent_src/USER.md" "$profile_dir/memories/USER.md"
        fi

        if [ "$(id -u)" = "0" ]; then
            chown -R hermes:hermes "$profile_dir"
        fi
    done < <(find "$src_agents" -mindepth 1 -maxdepth 1 -type d -print0)

    if [ "$(id -u)" = "0" ] && [ -d "$dst_profiles" ]; then
        chown -R hermes:hermes "$dst_profiles"
    fi
    export COMWORKER_PACKAGED_AGENTS_SYNCED=1
}

# --- Privilege dropping via gosu ---
# When started as root (the default for Docker, or fakeroot in rootless Podman),
# optionally remap the hermes user/group to match host-side ownership, fix volume
# permissions, then re-exec as hermes.
if [ "$(id -u)" = "0" ]; then
    if [ -n "$HERMES_UID" ] && [ "$HERMES_UID" != "$(id -u hermes)" ]; then
        echo "Changing hermes UID to $HERMES_UID"
        usermod -u "$HERMES_UID" hermes
    fi

    if [ -n "$HERMES_GID" ] && [ "$HERMES_GID" != "$(id -g hermes)" ]; then
        echo "Changing hermes GID to $HERMES_GID"
        # -o allows non-unique GID (e.g. macOS GID 20 "staff" may already exist
        # as "dialout" in the Debian-based container image)
        groupmod -o -g "$HERMES_GID" hermes 2>/dev/null || true
    fi

    mkdir -p "$HERMES_HOME"
    if ! command -v python >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
        ln -sf /usr/bin/python3 /usr/local/bin/python
    fi
    sync_comworker_packaged_skills
    sync_comworker_packaged_agents

    # Fix ownership of the data volume. When HERMES_UID remaps the hermes user,
    # files created by previous runs (under the old UID) become inaccessible.
    # Always chown -R when UID was remapped; otherwise only if top-level is wrong.
    actual_hermes_uid=$(id -u hermes)
    needs_chown=false
    if [ -n "$HERMES_UID" ] && [ "$HERMES_UID" != "10000" ]; then
        needs_chown=true
    elif [ "$(stat -c %u "$HERMES_HOME" 2>/dev/null)" != "$actual_hermes_uid" ]; then
        needs_chown=true
    fi
    if [ "$needs_chown" = true ]; then
        echo "Fixing ownership of $HERMES_HOME to hermes ($actual_hermes_uid)"
        # In rootless Podman the container's "root" is mapped to an unprivileged
        # host UID — chown will fail.  That's fine: the volume is already owned
        # by the mapped user on the host side.
        chown -R hermes:hermes "$HERMES_HOME" 2>/dev/null || \
            echo "Warning: chown failed (rootless container?) — continuing anyway"
        # The .venv must also be re-chowned when UID is remapped, otherwise
        # lazy_deps.py cannot install platform packages (discord.py, etc.).
        chown -R hermes:hermes "$INSTALL_DIR/.venv" 2>/dev/null || \
            echo "Warning: chown .venv failed (rootless container?) — continuing anyway"
    fi

    # Ensure config.yaml is readable by the hermes runtime user even if it was
    # edited on the host after initial ownership setup. Must run here (as root)
    # rather than after the gosu drop, otherwise a non-root caller like
    # `docker run -u $(id -u):$(id -g)` hits "Operation not permitted" (#15865).
    if [ -f "$HERMES_HOME/config.yaml" ]; then
        chown hermes:hermes "$HERMES_HOME/config.yaml" 2>/dev/null || true
        chmod 640 "$HERMES_HOME/config.yaml" 2>/dev/null || true
    fi

    echo "Dropping root privileges"
    exec gosu hermes "$0" "$@"
fi

# --- Running as hermes from here ---
source "${INSTALL_DIR}/.venv/bin/activate"
cd "$INSTALL_DIR"
export PYTHONPATH="$INSTALL_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Auto-detect Playwright chromium headless-shell binary path.
# Playwright versions differ in directory layout:
#   - older:  chromium_headless_shell-XXXX/chrome-linux/headless_shell
#   - newer:  chromium_headless_shell-XXXX/chrome-headless-shell-linux64/chrome-headless-shell
# Only override AGENT_BROWSER_EXECUTABLE_PATH if it hasn't been explicitly set.
if [ -z "${AGENT_BROWSER_EXECUTABLE_PATH:-}" ]; then
    pw_root="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
    for hs_dir in "$pw_root"/chromium_headless_shell-*; do
        [ -d "$hs_dir" ] || continue
        # Try newer layout first, then older layout
        for candidate in \
            "$hs_dir/chrome-headless-shell-linux64/chrome-headless-shell" \
            "$hs_dir/chrome-linux/headless_shell"; do
            if [ -x "$candidate" ]; then
                export AGENT_BROWSER_EXECUTABLE_PATH="$candidate"
                echo "Auto-detected browser: $AGENT_BROWSER_EXECUTABLE_PATH"
                break 2
            fi
        done
    done
fi

# Create essential directory structure.  Cache and platform directories
# (cache/images, cache/audio, platforms/whatsapp, etc.) are created on
# demand by the application — don't pre-create them here so new installs
# get the consolidated layout from get_hermes_dir().
# The "home/" subdirectory is a per-profile HOME for subprocesses (git,
# ssh, gh, npm …).  Without it those tools write to /root which is
# ephemeral and shared across profiles.  See issue #4426.
mkdir -p "$HERMES_HOME"/{cron,sessions,logs,hooks,memories,skills,skins,plans,workspace,home}
sync_comworker_packaged_skills
sync_comworker_packaged_agents

# .env
if [ ! -f "$HERMES_HOME/.env" ]; then
    if [ -f "$INSTALL_DIR/.env.example" ]; then
        cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
    else
        touch "$HERMES_HOME/.env"
    fi
fi
chmod 600 "$HERMES_HOME/.env"

# config.yaml
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
    cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
fi
chmod 600 "$HERMES_HOME/config.yaml"

# COMWORKER Platform Integration: If COMWORKER_PROXY__URL is set, configure custom provider
# This allows the container to use the platform's LLM gateway as a proxy.
if [ -n "$COMWORKER_PROXY__URL" ] && [ -n "$COMWORKER_PROXY__TOKEN" ]; then
    echo "Configuring COMWORKER platform LLM proxy: $COMWORKER_PROXY__URL"

    # Inject platform proxy configuration into config.yaml using Python
    # Uses hermes-agent's native custom_providers format which accepts api_key.
    "$INSTALL_DIR/.venv/bin/python" << 'PYTHON_EOF'
import os
import sys
from pathlib import Path
import yaml

hermes_home = Path(os.environ.get('HERMES_HOME', '/opt/data'))
config_path = hermes_home / 'config.yaml'

# Read current config
try:
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f) or {}
except Exception as e:
    print(f"Error reading config.yaml: {e}", file=sys.stderr)
    sys.exit(1)

# Get platform proxy settings from environment
proxy_url = os.environ.get('COMWORKER_PROXY__URL', '').strip()
proxy_token = os.environ.get('COMWORKER_PROXY__TOKEN', '').strip()
default_model = os.environ.get('COMWORKER_AGENTS__DEFAULTS__MODEL', '').strip()
api_toolsets_raw = os.environ.get('HERMES_API_TOOLSETS', '').strip()
reasoning_effort_raw = os.environ.get('HERMES_REASONING_EFFORT', '').strip()
service_tier_raw = os.environ.get('HERMES_SERVICE_TIER', '').strip()

if not proxy_url or not proxy_token:
    sys.exit(0)

# Create custom_providers entry using hermes-agent's native format
# This will be picked up by the model provider resolution logic
custom_provider_entry = {
    'name': 'platform-gateway',
    'base_url': proxy_url,
    'api_key': proxy_token,
}

# Initialize custom_providers list if it doesn't exist
if 'custom_providers' not in config:
    config['custom_providers'] = []
elif not isinstance(config['custom_providers'], list):
    print(f"Error: custom_providers must be a list, got {type(config['custom_providers'])}", file=sys.stderr)
    sys.exit(1)

# Remove any existing 'platform-gateway' entry to avoid duplicates
config['custom_providers'] = [
    p for p in config['custom_providers']
    if not (isinstance(p, dict) and p.get('name') == 'platform-gateway')
]

# Add the new entry
config['custom_providers'].append(custom_provider_entry)

# Ensure model section exists and set provider.
# Only default to platform-gateway when no user-defined provider is already
# configured — otherwise user-added custom_providers (e.g. deepseek) with
# their own API keys would be ignored and all traffic would still go through
# the platform proxy.
existing_provider = (config.get('model') or {}).get('provider', '') if config.get('model') else ''
if not existing_provider or existing_provider in ('platform-gateway', 'custom'):
    config['model']['provider'] = 'platform-gateway'
else:
    print(f"✓ Preserving user-defined model.provider: {existing_provider}")

# Set default model only when the user hasn't already chosen one in config.yaml.
# The platform-wide default is a seed for first run; user selections in the UI
# write to config.yaml and must survive container restarts/rebuilds.
existing_default = (config.get('model') or {}).get('default', '') if isinstance(config.get('model'), dict) else ''
if default_model and not existing_default:
    config['model']['default'] = default_model

def parse_api_toolsets(raw):
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in {'none', 'off', 'false', '0'}:
        return []
    if lowered in {'full', 'default', 'hermes-api-server'}:
        return ['hermes-api-server']
    return [part.strip() for part in raw.replace(';', ',').split(',') if part.strip()]

api_toolsets = parse_api_toolsets(api_toolsets_raw)
if api_toolsets is not None:
    config.setdefault('platform_toolsets', {})['api_server'] = api_toolsets

if reasoning_effort_raw:
    config.setdefault('agent', {})['reasoning_effort'] = reasoning_effort_raw

if service_tier_raw:
    config.setdefault('agent', {})['service_tier'] = service_tier_raw

# Save updated config
try:
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    print(f"✓ Updated config.yaml with platform-gateway provider")
except Exception as e:
    print(f"Error writing config.yaml: {e}", file=sys.stderr)
    sys.exit(1)

PYTHON_EOF
fi

# SOUL.md
if [ ! -f "$HERMES_HOME/SOUL.md" ]; then
    cp "$INSTALL_DIR/docker/SOUL.md" "$HERMES_HOME/SOUL.md"
fi
chmod 644 "$HERMES_HOME/SOUL.md"

# Active Agent config injection (controlled by HERMES_ACTIVE_AGENT env var)
# Defaults to "main" when not set.
ACTIVE_AGENT="${HERMES_ACTIVE_AGENT:-main}"
ACTIVE_AGENT_DIR="$INSTALL_DIR/deploy_copy/Agents/$ACTIVE_AGENT"
if [ -d "$ACTIVE_AGENT_DIR" ]; then
    # SOUL.md — overwrite with active agent version
    if [ -f "$ACTIVE_AGENT_DIR/SOUL.md" ]; then
        cp "$ACTIVE_AGENT_DIR/SOUL.md" "$HERMES_HOME/SOUL.md"
        chmod 644 "$HERMES_HOME/SOUL.md"
    fi
    # AGENTS.md, IDENTITY.md → workspace/
    for f in AGENTS.md IDENTITY.md; do
        if [ -f "$ACTIVE_AGENT_DIR/$f" ]; then
            cp "$ACTIVE_AGENT_DIR/$f" "$HERMES_HOME/workspace/$f"
            chmod 644 "$HERMES_HOME/workspace/$f"
        fi
    done
    # USER.md → memories/
    if [ -f "$ACTIVE_AGENT_DIR/USER.md" ]; then
        cp "$ACTIVE_AGENT_DIR/USER.md" "$HERMES_HOME/memories/USER.md"
        chmod 644 "$HERMES_HOME/memories/USER.md"
    fi
    echo "✓ ${ACTIVE_AGENT} agent config injected"
else
    echo "⚠ Agent directory not found: $ACTIVE_AGENT_DIR"
fi

# auth.json: bootstrap from env on first boot only.  Used by orchestrators
# that need to seed the OAuth refresh credential non-interactively.
if [ ! -f "$HERMES_HOME/auth.json" ] && [ -n "$HERMES_AUTH_JSON_BOOTSTRAP" ]; then
    printf '%s' "$HERMES_AUTH_JSON_BOOTSTRAP" > "$HERMES_HOME/auth.json"
    chmod 600 "$HERMES_HOME/auth.json"
fi

# Sync bundled skills (manifest-based so user edits are preserved)
if [ -d "$INSTALL_DIR/skills" ]; then
    "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/tools/skills_sync.py"
fi

# Optionally start `hermes dashboard` as a side-process.
case "${HERMES_DASHBOARD:-}" in
    1|true|TRUE|True|yes|YES|Yes)
        dash_host="${HERMES_DASHBOARD_HOST:-0.0.0.0}"
        dash_port="${HERMES_DASHBOARD_PORT:-9119}"
        dash_args=(--host "$dash_host" --port "$dash_port" --no-open)
        if [ "$dash_host" != "127.0.0.1" ] && [ "$dash_host" != "localhost" ]; then
            dash_args+=(--insecure)
        fi
        echo "Starting hermes dashboard on ${dash_host}:${dash_port} (background)"
        (
            stdbuf -oL -eL "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/comworker_hermes.py" dashboard "${dash_args[@]}" 2>&1 \
                | sed -u 's/^/[dashboard] /'
        ) &
        ;;
esac

# Avoid relying on editable-install console-script metadata at runtime.
# Launch through the ComWorker wrapper so compatibility overlays are installed
# before Hermes dispatches subcommands such as "gateway run".
case "${1:-}" in
    bash|sh|sleep|tail|python|python3|node|npm|uv)
        exec "$@"
        ;;
esac

exec "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/comworker_hermes.py" "$@"
