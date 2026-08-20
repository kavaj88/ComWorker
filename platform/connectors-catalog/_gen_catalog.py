#!/usr/bin/env python3
"""Generate the platform connector catalog from WorkBuddy's package layout.

Each connector is a directory with:
  - connector-meta.json : display + examples + ``auth`` (credential strategy)
  - mcp.json            : the mcpServers.<name> runtime entry (MCP connectors)
  - cli.json            : native CLI auth/install spec (CLI connectors)

This mirrors ~/.workbuddy/connectors-marketplace/connectors/<name>/ exactly so
the catalog stays data-driven (drop a directory => a new connector, no code).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# (dir, meta, runtime_file, runtime_content)
CATALOG: list[tuple[str, dict, str, dict]] = []


def mcp(name, url, typ="streamable_http", timeout=30000, extra=None):
    cfg = {"url": url, "type": typ, "timeout": timeout}
    if extra:
        cfg.update(extra)
    return {"mcpServers": {name: cfg}}


# ── MCP, open (no auth) ───────────────────────────────────────────────────
CATALOG.append((
    "tdx",
    {
        "name": "通达信股票",
        "source": "tdx",
        "display_name": "通达信",
        "description_zh": "通过通达信 MCP 查询全球股票行情数据、条件选股、研究报告、公告资讯和宏观信息。支持个股基本面分析、同行业对比和智能选股筛查。",
        "description_en": "Query global stock data via Tongdaxin MCP, with screening and research support.",
        "examples_zh": ["帮我查询贵州茅台(600519)最近的研究报告", "筛选市盈率低于20、ROE高于15%的全球股票", "查询近期宏观经济政策动向", "分析半导体行业龙头股的基本面数据"],
        "examples_en": ["Find recent research reports for Kweichow Moutai (600519)", "Screen global stocks with P/E below 20 and ROE above 15%"],
        "auth": "none",
    },
    "mcp.json",
    mcp("tdx", "https://txmcp.tdx.com.cn:3001/txmcp", "streamable-http"),
))

CATALOG.append((
    "tencent_selfstock",
    {
        "name": "腾讯自选股",
        "source": "tencent_selfstock",
        "display_name": "腾讯自选股",
        "description_zh": "直连腾讯自选股，实时掌握毫秒级行情与资金动态，用自然语言分析自选数据、设置股价提醒、管理模拟交易，轻松搞定盯盘与投资决策。",
        "description_en": "Directly connect to WeStock for real-time market data, watchlist management, price alerts, and paper trading.",
        "examples_zh": ["把宁德时代加到我的自选", "帮我找半导体相关的股票", "看看我有哪些自选股票", "查一下腾讯控股的资金流向", "茅台和宁德时代今天行情怎么样"],
        "examples_en": ["Add CATL to my watchlist", "Find semiconductor-related stocks"],
        "auth": "none",
    },
    "mcp.json",
    mcp("tencent_selfstock", "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp", "streamableHttp"),
))

# ── MCP, OAuth-protected (弹授权页/二维码) ─────────────────────────────────
CATALOG.append((
    "qqmail",
    {
        "name": "QQ 邮箱",
        "source": "qqmail",
        "display_name": "QQ 邮箱",
        "description_zh": "QQ 邮箱 MCP 连接器。让智能体读取邮件、搜索邮件、发送邮件、管理文件夹与草稿。基于腾讯邮箱开放接口，需用户 OAuth 授权（安装时会弹出授权登录页/二维码）。",
        "description_en": "QQ Mail MCP connector. Requires the user's OAuth authorization.",
        "examples_zh": ["帮我看看今天的重要邮件", "把这份报告发到 xxx@qq.com", "搜索上周关于项目启动的邮件", "整理我的收件箱未读邮件"],
        "examples_en": ["Show me today's important emails", "Send this report to xxx@qq.com"],
        "auth": "oauth",
    },
    "mcp.json",
    mcp("qqmail", "https://api.mail.qq.com/mcp"),
))

CATALOG.append((
    "ima",
    {
        "name": "ima 知识库",
        "source": "ima",
        "display_name": "ima 知识库",
        "description_zh": "ima 知识库 MCP 连接器。将用户个人或团队的 ima 知识库接入智能体，支持检索知识库内容、追问、引用来源。需配置 ima 开放接口地址与用户 OAuth 授权（安装时弹授权页）。",
        "description_en": "ima knowledge-base MCP connector. Requires user OAuth authorization.",
        "examples_zh": ["在公司知识库里查一下 VPN 配置", "总结上周的产品评审纪要", "ima 里有没有关于入职流程的文档"],
        "examples_en": ["Look up the VPN config in the company knowledge base"],
        "auth": "oauth",
    },
    "mcp.json",
    mcp("ima", "https://ima.qq.com/mcp", "streamableHttp"),
))

CATALOG.append((
    "lexiang",
    {
        "name": "乐享知识库",
        "source": "lexiang",
        "display_name": "乐享知识库",
        "description_zh": "乐享知识库 MCP 连接器。把企业乐享知识库的内容检索与问答能力接入智能体，支持按空间、标签、关键词检索文档并引用原文。需 OAuth 凭证（安装时弹授权页）。",
        "description_en": "Lexiang knowledge-base MCP connector. Requires OAuth authorization.",
        "examples_zh": ["在乐享里搜一下 Q3 财报解读", "总结一下技术分享会的纪要", "乐享上有没有新人培训资料"],
        "examples_en": ["Search the Q3 earnings readout in Lexiang"],
        "auth": "oauth",
    },
    "mcp.json",
    mcp("lexiang", "https://mcp.lexiang-app.com/mcp"),
))

CATALOG.append((
    "tencent_docs",
    {
        "name": "腾讯文档",
        "source": "tencent_docs",
        "display_name": "腾讯文档",
        "description_zh": "腾讯文档 MCP 连接器。让智能体创建、读取、编辑、搜索腾讯文档（文档/表格/幻灯片），支持把对话结果直接生成在线协作文档。需用户 OAuth 授权访问其腾讯文档空间（安装时弹授权页）。",
        "description_en": "Tencent Docs MCP connector. Requires user OAuth authorization.",
        "examples_zh": ["帮我新建一个会议纪要文档", "把这份表格导出成腾讯文档", "读取我共享给我的季度规划文档", "在文档里插入一张表格"],
        "examples_en": ["Create a meeting-notes doc for me", "Read the quarterly plan doc shared with me"],
        "auth": "oauth",
    },
    "mcp.json",
    mcp("tencent_docs", "https://docs.qq.com/openapi/mcp"),
))

# ── CLI connectors (需容器内安装 CLI + 交互登录弹二维码) ───────────────────
def cli(meta_runtime: dict) -> dict:
    return meta_runtime


CATALOG.append((
    "tmeet",
    {
        "name": "腾讯会议",
        "source": "tmeet",
        "display_name": "腾讯会议",
        "description_zh": "腾讯会议 MCP 连接器。把会议创建、查询、预订、会议纪要等能力接入智能体，支持『帮我预约明天下午 3 点和小组的会』『总结刚结束会议的重点』。需在容器内安装 CLI 并完成交互授权（弹二维码/授权页）。",
        "description_en": "Tencent Meeting MCP connector. Requires in-container CLI install + interactive auth.",
        "examples_zh": ["帮我预约明天下午 3 点和小组的会", "总结刚结束会议的重点", "查一下我今天的会议安排"],
        "examples_en": ["Book a meeting with the team at 3pm tomorrow"],
        "auth": "cli",
    },
    "cli.json",
    {
        "runtime": {"type": "node", "version": ">=18"},
        "init": {"darwin": "npm install -g @tmeet/cli", "linux": "npm install -g @tmeet/cli", "win32": "npm install -g @tmeet/cli"},
        "versionCheck": {"command": {"darwin": "tmeet --version", "linux": "tmeet --version"}, "minVersion": "1.0.0"},
        "auth": {"darwin": "tmeet login", "linux": "tmeet login"},
        "unAuth": {"darwin": "tmeet logout", "linux": "tmeet logout"},
        "status": {"darwin": "tmeet whoami", "linux": "tmeet whoami"},
        "authUrlDomain": "meeting.tencent.com",
        "authWaitForExit": True,
        "authQrModal": True,
    },
))

CATALOG.append((
    "wecom",
    {
        "name": "企业微信",
        "source": "wecom",
        "display_name": "企业微信",
        "description_zh": "企业微信 MCP 连接器。把企业微信的消息收发、通讯录、日程、审批等能力接入智能体，支持企业内网场景下的自动化协作。需在容器内安装 CLI 并完成交互授权（弹二维码）。",
        "description_en": "WeCom MCP connector. Requires in-container CLI install + interactive QR auth.",
        "examples_zh": ["给张三发条消息", "查一下今天的日程", "帮我发起一个请假审批"],
        "examples_en": ["Send a message to Zhang San", "Check today's schedule"],
        "auth": "cli",
    },
    "cli.json",
    {
        "runtime": {"type": "node", "version": ">=18"},
        "init": {"darwin": "npm install -g @wecom/cli", "linux": "npm install -g @wecom/cli", "win32": "npm install -g @wecom/cli"},
        "versionCheck": {"command": {"darwin": "wecom-cli --version", "linux": "wecom-cli --version"}, "minVersion": "0.1.9"},
        "auth": {"darwin": "wecom-cli init --noninteractive --no-open", "linux": "wecom-cli init --noninteractive --no-open"},
        "unAuth": {"darwin": "rm -rf \"$HOME/.config/wecom\"", "linux": "rm -rf \"$HOME/.config/wecom\""},
        "status": {"darwin": "wecom-cli auth show", "linux": "wecom-cli auth show"},
        "statusMatch": "\"id\"\\s*:\\s*\"",
        "authUrlDomain": "work.weixin.qq.com",
        "authWaitForExit": True,
        "authQrModal": True,
    },
))

CATALOG.append((
    "feishu",
    {
        "name": "飞书",
        "source": "feishu",
        "display_name": "飞书",
        "description_zh": "飞书 MCP 连接器。把飞书的消息、云文档、多维表格、日历、审批等能力接入智能体，支持跨应用自动化。需在容器内安装 CLI 并完成两步交互授权（弹二维码/授权页）。",
        "description_en": "Feishu (Lark) MCP connector. Requires in-container CLI install + 2-step interactive auth.",
        "examples_zh": ["在飞书群里发个通知", "新建一篇云文档", "查一下我今天的日历"],
        "examples_en": ["Post a notice in the Feishu group", "Create a new cloud doc"],
        "auth": "cli",
    },
    "cli.json",
    {
        "runtime": {"type": "node", "version": ">=18"},
        "init": {"darwin": "npm install -g @larksuite/cli", "linux": "npm install -g @larksuite/cli", "win32": "npm install -g @larksuite/cli"},
        "versionCheck": {"command": {"darwin": "lark-cli --version", "linux": "lark-cli --version"}, "minVersion": "1.0.79"},
        "auth": [
            {"command": {"darwin": "lark-cli config init --new --lang en", "linux": "lark-cli config init --new --lang en"}, "skipIf": {"darwin": "lark-cli config show", "linux": "lark-cli config show"}, "authWaitForExit": True, "authUrlDomain": "open.feishu.cn"},
            {"command": {"darwin": "lark-cli auth login --recommend", "linux": "lark-cli auth login --recommend"}, "authWaitForExit": True, "authUrlDomain": "accounts.feishu.cn"},
        ],
        "unAuth": {"darwin": "lark-cli auth logout", "linux": "lark-cli auth logout"},
        "status": {"darwin": "lark-cli auth status", "linux": "lark-cli auth status"},
        "statusMatchJson": {"identity": "user"},
    },
))

CATALOG.append((
    "dingtalk",
    {
        "name": "钉钉",
        "source": "dingtalk",
        "display_name": "钉钉",
        "description_zh": "钉钉 MCP 连接器。把钉钉的待办、审批、公告、群消息、智能办公等能力接入智能体，支持企业协同自动化。需在容器内安装 CLI 并完成交互授权（弹二维码/授权页）。",
        "description_en": "DingTalk MCP connector. Requires in-container CLI install + interactive auth.",
        "examples_zh": ["发一条钉钉工作通知", "查一下我的待办", "帮我发起一个审批"],
        "examples_en": ["Send a DingTalk work notice", "Check my to-dos"],
        "auth": "cli",
    },
    "cli.json",
    {
        "runtime": {"type": "node", "version": ">=18"},
        "init": {"darwin": "npm install -g @dingtalk/cli", "linux": "npm install -g @dingtalk/cli", "win32": "npm install -g @dingtalk/cli"},
        "versionCheck": {"command": {"darwin": "dingtalk-cli --version", "linux": "dingtalk-cli --version"}, "minVersion": "1.0.0"},
        "auth": {"darwin": "dingtalk-cli login", "linux": "dingtalk-cli login"},
        "unAuth": {"darwin": "dingtalk-cli logout", "linux": "dingtalk-cli logout"},
        "status": {"darwin": "dingtalk-cli whoami", "linux": "dingtalk-cli whoami"},
        "authUrlDomain": "login.dingtalk.com",
        "authWaitForExit": True,
        "authQrModal": True,
    },
))


def main() -> None:
    for name, meta, runtime_file, runtime in CATALOG:
        d = ROOT / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "connector-meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (d / runtime_file).write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"wrote {name}/{{connector-meta.json,{runtime_file}}}")


if __name__ == "__main__":
    # DEPRECATED: this script only regenerates the original 10 hardcoded
    # connectors and would WIPE the 74 connectors migrated from WorkBuddy by
    # migrate_from_workbuddy.py. Do not run it. To refresh the catalog, re-run
    # migrate_from_workbuddy.py instead.
    import sys
    print(
        "REFUSING TO RUN: _gen_catalog.py is deprecated and would overwrite the "
        "migrated catalog. Use migrate_from_workbuddy.py to (re)generate connectors.",
        file=sys.stderr,
    )
    sys.exit(1)
