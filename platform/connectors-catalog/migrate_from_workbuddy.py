#!/usr/bin/env python3
"""Migrate ALL WorkBuddy connectors into this platform's data-driven catalog.

Source : ~/.workbuddy/connectors-marketplace/connectors/<name>/
Target : ./<name>/  (platform/connectors-catalog)

Each connector directory becomes a self-contained package:
  - connector-meta.json : normalized (display_name / description_zh / examples_zh / auth)
  - mcp.json            : mcpServers.<name> entry (MCP connectors); api_key ${KEY} injected
  - cli.json            : native CLI spec (CLI connectors), copied verbatim
  - token-schema.json   : copied as reference for the key-entry form
  - icon.*              : copied if present

Credential strategy is derived from the WorkBuddy layout:
  cli.json (no mcp url)                -> cli
  mcp with url + token-schema          -> api_key   (placeholders injected from schema)
  mcp with url + ${} placeholder       -> api_key   (placeholders already present)
  mcp with url + name in OAUTH_SET     -> oauth     (relay does discovery)
  mcp with url (plain)                 -> none
  mcp with command (stdio) + creds     -> api_key   (env placeholders)
  mcp with command (stdio, no creds)   -> none      (needs binary in container)
  neither mcp.json nor cli.json        -> SKIPPED   (skill-only, not MCP)

Idempotent: re-running overwrites generated files.

NOTE: do NOT run _gen_catalog.py afterwards -- it is a stale hardcoded 10-connector
script and would wipe the migrated catalog.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from collections import Counter
from pathlib import Path

WB_ROOT = Path(os.path.expanduser("~/.workbuddy/connectors-marketplace/connectors"))
CATALOG = Path(__file__).resolve().parent

# Map WorkBuddy dir name -> our existing DB `name` so the 10 already-seeded
# connectors are refreshed in place instead of duplicated.
NAME_MAP = {
    "qq-mail": "qqmail",
    "tencent-docs": "tencent_docs",
    "westock-mcp": "tencent_selfstock",
    "ima-mcp": "ima",
    "lexiang": "lexiang",
    "wecom": "wecom",
    "feishu": "feishu",
    "dingtalk": "dingtalk",
    "tmeet": "tmeet",
    "tdx-connector": "tdx",
}


def load_wb_zh_names() -> dict:
    """Return WB's authoritative Chinese display names keyed by connector `source`.

    WorkBuddy stores the human-readable (often Chinese) names in a central
    index at <marketplace>/.codebuddy-connector/connectors.json, NOT in every
    connector's own connector-meta.json. Connectors lacking a connector-meta.json
    (e.g. dingtalk, baidu-netdisk, feishu) would otherwise fall back to an
    English title-cased slug. This map lets us reuse WB's exact Chinese names.
    """
    p = WB_ROOT.parent / ".codebuddy-connector" / "connectors.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for c in data.get("connectors", []):
        src = c.get("source") or c.get("id")
        nm = c.get("name")
        if src and nm:
            out[src] = nm
    return out


# Known OAuth-discovery MCP servers (relay does RFC8414 discovery).
OAUTH_SET = {"qq-mail", "ima-mcp", "lexiang", "tencent-docs", "tencent-docs-oa"}

REQ_KEY_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def pretty(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").strip().title()


def derive_display(meta: dict, ts: dict | None, dirname: str) -> str:
    if meta.get("name"):
        return meta["name"]
    if ts and ts.get("title"):
        t = ts["title"]
        for suf in (" MCP", " API Key 配置", " 对接配置", " MCP 配置", "配置"):
            if t.endswith(suf):
                t = t[: -len(suf)]
        return t.strip() or pretty(dirname)
    return pretty(dirname)


def derive_description(meta: dict, ts: dict | None, display: str, url: str, is_cli: bool) -> str:
    if meta.get("description_zh"):
        return meta["description_zh"]
    if meta.get("description"):
        return meta["description"]
    if ts and ts.get("description"):
        return ts["description"]
    if is_cli:
        return f"{display} 原生 CLI 连接器。安装后需在容器内完成登录授权，由中继代理与平台转发回调。"
    if url:
        return f"通过 {url} 接入 {display} 的 MCP 服务，由智能体调用其工具完成相关任务。"
    return f"{display} 连接器。"


def derive_examples(meta: dict, display: str) -> list[str]:
    ex = meta.get("examples_zh") or meta.get("examples_en") or []
    if isinstance(ex, str):
        ex = [ex]
    if ex:
        return ex[:6]
    return [
        f"用{display}帮我查一下最新动态",
        f"通过{display}执行一个常见任务",
        f"让{display}总结它可用的能力",
    ]


def inject_api_key(entry: dict, fields: list[dict], transport: str) -> dict:
    """Put ${KEY} placeholders into the mcp entry based on token-schema fields.

    For HTTP transports -> headers / url query. For stdio -> env (propagated to
    the child process).
    """
    url = entry.get("url", "") or ""
    headers = dict(entry.get("headers") or {})
    env = dict(entry.get("env") or {})
    keys = [f for f in fields if f.get("key")]
    multi = len(keys) > 1
    for f in keys:
        K = f["key"]
        desc = " ".join(str(f.get(k, "")) for k in ("description", "description_en", "label", "label_en")).lower()
        is_url_param = ("url" in desc) or ("connection url" in desc) or ("apikey 参数" in desc) or ("api key 参数" in desc)
        if transport == "stdio":
            env[K] = f"${{{K}}}"
            continue
        if is_url_param and url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}apikey=${{{K}}}"
            continue
        # HTTP: single token-like key -> Bearer; otherwise a named X- header.
        if (not multi) and any(t in K.upper() for t in ("TOKEN", "ACCESS", "PAT")):
            headers["Authorization"] = f"Bearer ${{{K}}}"
        else:
            hname = "X-" + K.replace("_", "-").upper()
            headers[hname] = f"${{{K}}}"
    entry["url"] = url
    if headers:
        entry["headers"] = headers
    if env:
        entry["env"] = env
    return entry


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) or {}


def _first_entry(mcp_path: Path) -> dict:
    mcp = _read_json(mcp_path)
    for sv in mcp.get("mcpServers", {}).values():
        if isinstance(sv, dict):
            return sv
    return {}


def _norm_transport(t: str | None) -> str:
    if not t:
        return "streamable_http"
    t = t.lower()
    if "stdio" in t:
        return "stdio"
    if "sse" in t:
        return "sse"
    if "http" in t:
        return "streamable_http"
    return "streamable_http"


def main() -> None:
    if not WB_ROOT.is_dir():
        raise SystemExit(f"WorkBuddy connectors not found at {WB_ROOT}")

    summary: list[tuple] = []
    skipped: list[tuple] = []
    wb_names = load_wb_zh_names()
    for dirname in sorted(os.listdir(WB_ROOT)):
        src = WB_ROOT / dirname
        if not src.is_dir():
            continue

        meta_path = src / "connector-meta.json"
        mcp_path = src / "mcp.json"
        cli_path = src / "cli.json"
        ts_path = src / "token-schema.json"

        meta = _read_json(meta_path) if meta_path.is_file() else {}
        ts = _read_json(ts_path) if ts_path.is_file() else {}
        has_mcp = mcp_path.is_file()
        is_cli = cli_path.is_file() and not (has_mcp and _first_entry(mcp_path).get("url"))

        if is_cli:
            strategy, transport = "cli", "stdio"
        elif has_mcp:
            e = _first_entry(mcp_path)
            has_url = bool(e.get("url"))
            has_cmd = bool(e.get("command"))
            has_ph = bool(REQ_KEY_RE.search(json.dumps(e)))
            if has_url:
                if ts_path.is_file() or has_ph:
                    strategy = "api_key"
                elif dirname in OAUTH_SET:
                    strategy = "oauth"
                else:
                    strategy = "none"
                transport = _norm_transport(e.get("type")) or "streamable_http"
            elif has_cmd:
                if ts_path.is_file() or has_ph:
                    strategy = "api_key"
                else:
                    strategy = "none"
                transport = "stdio"
            else:
                skipped.append((dirname, "mcp.json has neither url nor command"))
                continue
        else:
            skipped.append((dirname, "no mcp.json and no cli.json"))
            continue

        mapped_name = NAME_MAP.get(dirname, dirname)
        dst = CATALOG / mapped_name
        dst.mkdir(parents=True, exist_ok=True)

        wb_zh = wb_names.get(dirname)
        display = derive_display(meta, ts, dirname)
        display_name = wb_zh if (wb_zh and re.search(r"[\u4e00-\u9fff]", wb_zh)) else display
        description = derive_description(meta, ts, display_name, e.get("url", ""), is_cli)
        examples = derive_examples(meta, display_name)

        out_meta = {
            "name": display,
            "source": mapped_name,
            "display_name": display_name,
            "description_zh": description,
            "description_en": description,
            "examples_zh": examples,
            "examples_en": examples,
            "auth": strategy,
        }
        (dst / "connector-meta.json").write_text(
            json.dumps(out_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if strategy == "cli":
            shutil.copyfile(cli_path, dst / "cli.json")
        else:
            entry = dict(e)
            # Most WorkBuddy mcp.json already carry the correct ${KEY} placeholders;
            # only inject from token-schema when none are present.
            if strategy == "api_key" and not REQ_KEY_RE.search(json.dumps(entry)) and ts_path.is_file():
                entry = inject_api_key(entry, ts.get("fields") or [], transport)
            ntype = _norm_transport(e.get("type")) or transport
            entry["type"] = ntype
            entry["transport"] = ntype
            out_mcp = {"mcpServers": {mapped_name: entry}}
            (dst / "mcp.json").write_text(
                json.dumps(out_mcp, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if ts_path.is_file():
                shutil.copyfile(ts_path, dst / "token-schema.json")

        for icon in ("icon.svg", "icon.png", "icon.jpg"):
            if (src / icon).is_file():
                shutil.copyfile(src / icon, dst / icon)

        summary.append((mapped_name, strategy, transport, dirname))
        print(f"  + {mapped_name:22} [{strategy:8}] {transport:14} <- {dirname}")

    print(f"\nMigrated {len(summary)} connectors.")
    if skipped:
        print(f"Skipped {len(skipped)}:")
        for n, why in skipped:
            print(f"  - {n}: {why}")
    print("By strategy:", dict(Counter(s[1] for s in summary)))


if __name__ == "__main__":
    main()
