#!/usr/bin/env python3
"""Non-interactive Weixin (WeChat personal) QR login for ComWorker.

Runs Hermes's native iLink QR login flow (the same ``qr_login`` the
``hermes gateway setup`` wizard uses), prints the scannable liteapp URL to
stdout (so the web UI can surface it), and persists the returned credentials
to ``~/.hermes/.env`` exactly like the interactive setup wizard does.

This is the real Hermes binding path — no OpenClaw/npm plugin involved.
"""
from __future__ import annotations

import asyncio
import os
import sys

# The gateway reads credentials from $HERMES_HOME/.env (e.g. /opt/data/.env),
# NOT ~/.hermes/.env. Mirror that exactly so a scan actually enables WeChat.
HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
ENV_PATH = os.path.join(HERMES_HOME, ".env")


def _fix_weixin_perms(hermes_home: str) -> None:
    """The gateway drops privileges to the ``hermes`` user (uid 10000) but
    qr_login() above runs as root and creates /opt/data/weixin/... owned by
    root. Make that subtree readable+writable by the gateway so it can write
    its sync temp files (otherwise: Permission denied on *.sync_*.tmp)."""
    weixin_dir = os.path.join(hermes_home, "weixin")
    if not os.path.isdir(weixin_dir):
        return
    hermes_uid = hermes_gid = None
    try:
        import grp
        import pwd
        hermes_uid = pwd.getpwnam("hermes").pw_uid
        hermes_gid = grp.getgrnam("hermes").gr_gid
    except Exception:
        pass
    for root, dirs, files in os.walk(weixin_dir):
        for name in dirs + files:
            p = os.path.join(root, name)
            try:
                os.chmod(p, 0o777 if os.path.isdir(p) else 0o666)
                if hermes_uid is not None:
                    os.chown(p, hermes_uid, hermes_gid)
            except Exception:
                pass
    # Also relax the .env itself so the de-privileged gateway can read it.
    try:
        os.chmod(ENV_PATH, 0o644)
        if hermes_uid is not None:
            os.chown(ENV_PATH, hermes_uid, hermes_gid)
    except Exception:
        pass


def save_env_value(key: str, value: str) -> None:
    """Mirror hermes_cli's save_env_value: set KEY=value, preserving other lines."""
    lines: list[str] = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


async def main() -> int:
    hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    # Imported lazily so the script can be launched from any cwd with the
    # hermes venv on PATH (gateway package lives at /opt/hermes/gateway).
    from gateway.platforms.weixin import qr_login

    print("正在拉取微信扫码二维码...", flush=True)
    credentials = await qr_login(hermes_home)
    if not credentials:
        print("微信扫码登录未完成（超时或已取消）。", flush=True)
        return 1

    # qr_login() wrote the account json as root; relax perms before the
    # de-privileged gateway tries to use it.
    _fix_weixin_perms(hermes_home)

    save_env_value("WEIXIN_ACCOUNT_ID", credentials.get("account_id", ""))
    save_env_value("WEIXIN_TOKEN", credentials.get("token", ""))
    if credentials.get("base_url"):
        save_env_value("WEIXIN_BASE_URL", credentials["base_url"])
    save_env_value(
        "WEIXIN_CDN_BASE_URL",
        os.environ.get("WEIXIN_CDN_BASE_URL") or "https://novac2c.cdn.weixin.qq.com/c2c",
    )
    _fix_weixin_perms(hermes_home)

    print(f"微信凭据已保存到 {ENV_PATH}，重启网关后即可收发消息。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
