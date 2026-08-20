# ComWorker 集成 Coding Agent 设计方案

## 0. 结论先行
1. **ComWorker 已经具备 coding agent 的底层能力。** `hermes-agent` 是 Nous Research 的通用自改进 agent，自带 `file_tools` / `terminal_tool` / `code_execution_tool` 以及专门的 `coding` toolset；平台侧也已把"运行 agent"抽象成 `RuntimeBackend` 协议（`platform/app/runtime_backend.py:17`），目前唯一实现是 `DedicatedHermesBackend`（`runtime_backends/dedicated_hermes.py`）。
2. **WorkBuddy（你正在用的产品）的 coding 能力模型** = LLM + 工具集（读写文件 / 跑 shell / 搜索 / WebFetch）+ agent 循环 + 权限门控（Plan/Build）+ 子代理 / 技能 / 记忆。这个模型 hermes 已经等价实现，所以"让项目有 coding agent 能力"≠ 从零造，而是**把这个能力产品化、可插拔、并补上现代 coding agent 的交互体验**。
3. **推荐路径**：让 `RuntimeBackend` 真正可插拔，新增 `CodingAgentBackend`（默认实现 OpenCode），使会话/agent 可按需选择运行时；复用现有 LLM 代理、容器管理、终端/WS 代理、技能目录，前端增加 coding agent 类型与 Plan/Build 切换。

## 1. 现状盘点（为什么能低成本复用）
- 网关 `platform/app/main.py` 是 FastAPI(8080)。`runtime_router.py:13` 当前只支持 `"hermes"` 一种后端。
- `DedicatedHermesBackend` 通过 `HermesClient`（`runtime_backends/hermes_client.py`）调用每用户独立 Docker 容器里的 Hermes REST：`POST /v1/runs`、`/v1/chat/completions`，SSE 事件 `GET /v1/runs/{id}/events`。
- 前端 → gateway（JWT 鉴权）→ 转发到 hermes 容器；WebSocket/终端代理在 `/api/comworker/ws` 与 `/api/comworker/terminal/ws`（`routes/proxy.py`）。
- LLM 统一走 `POST /llm/v1/chat/completions`（底层 litellm，自动注入 key、配额、用量记录），支持 OpenAI / Anthropic / 自定义 base_url。
- 技能 = 含 `SKILL.md` 的目录，挂在容器 `/opt/data/skills`；agent = 预置 profile（`SYSTEM_AGENT_IDS` 含 `main`/`programmer` 等）。

→ 已经存在的可插拔点：① 新 `RuntimeBackend` 实现并在 `runtime_router` 注册；② 复用 `/sessions/{key}/messages`、`/runs/{id}/events`、`/terminal/ws` 通道；③ 模型调用统一走 LLM 代理。

## 2. "Coding Agent 能力"的参考模型
以 WorkBuddy / OpenCode 为参照，一个 coding agent 的本质：
- **核心循环**：用户请求 → LLM 决策 `tool_use` → 执行工具（读/写/编辑文件、跑命令、搜索、LSP 诊断、WebFetch）→ 观察结果 → 回到 LLM，直到任务完成。
- **工具集**：file read/write/edit、bash、grep/glob、LSP diagnostics、webfetch。
- **模式/权限**：Plan（只读、出方案）vs Build（读写、可执行），逐工具审批（allow/ask/deny）。
- **工程化**：会话持久化、Git 快照 undo/redo、多会话并行、记忆/技能/子代理委派。
- **OpenCode 的特征**：client/server 分离（TS+Bun 的 Hono HTTP server，`opencode serve` 无头、`opencode attach <url>` 远程）、Models.dev 75+ provider、自动生成 TypeScript SDK（Stainless）、Plan/Build 双代理、LSP 诊断回灌。

## 3. 目标架构（见随附架构图）
- Gateway 基本不变，仅把 `runtime_router` 从"只认 hermes"扩展为"按 session/agent 选运行时"。
- 新增 `CodingAgentBackend` 实现 `RuntimeBackend` 协议，把 coding 会话路由到 **opencode 容器**（复用 `container/manager.py` 做每用户独立容器，或共享服务 + 多会话）。
- 关键复用：LLM 代理（litellm，key 注入/配额）、终端与 WS 代理、技能卷挂载、DB/审计。
- Hermes 与 OpenCode **并存**：Hermes 做通用 agent，OpenCode 做 coding 专精；同一会话可指定 `runtime: "opencode"`。

## 4. OpenCode 接入细节
- 容器化：Dockerfile 基于 `oven/bun`，装 `opencode`，挂载用户代码工作区与技能目录；`opencode serve --address 0.0.0.0:PORT` 起 HTTP。
- Gateway 调用：`CodingAgentBackend` 用 httpx 调 opencode 的 session/chat API（或 `opencode` 自动生成的 SDK）；事件流走 SSE 复用现有 `/runs/{id}/events` 形态，或适配 opencode 的 streaming 端点。
- 模型接入：opencode 的 provider 指向平台 LLM 代理 `http://gateway/llm/v1`（OpenAI 兼容），实现统一配额/key 管理。
- 权限对齐：把 opencode 的 Plan/Build 与 hermes 的 approval 机制对齐，前端在会话页露出"只读规划 / 执行"切换。

## 5. 其它 coding agent（Pi / Codex / Aider）
- 同一 `RuntimeBackend` 接口即可包成独立 backend；"Pi Agent" 具体指哪个需你确认（Inflection 的 Pi 并非 coding agent，可能是指某内部/其他产品）。
- 更轻量的低成本方案：把 opencode / aider 作为 **MCP server** 暴露，用 hermes 已有的 `mcp_tool` 接入，无需改核心循环（参考 hermes 已集成的 Codex provider 路线 `agent/codex_runtime.py`）。

## 6. 分阶段实施
- **P0** 容器化 opencode + `opencode serve` 验证，确认 HTTP API 与 SSE。
- **P1** 实现 `CodingAgentBackend` + `runtime_router` 注册 + 容器模板；管理端可把某 agent 标记为 `runtime: "opencode"`。
- **P2** 前端：agent 列表增加 coding 类型；会话页支持 Plan/Build 切换、LSP 诊断展示、复用终端 WS。
- **P3** 技能目录挂载进容器、配额/审计对齐、undo/redo（Git 快照）体验。
- **P4** 多 coding agent 可插拔（pi / codex 等），管理端统一开关。

## 7. 风险与待确认
- opencode 为 TS/Bun，`opencode serve` 镜像体积与冷启动时间需评估（对比 hermes 的 Python 容器）。
- 并存 vs 替代：建议**并存**（通用 + 专精），避免破坏现有 hermes 技能生态。
- 权限模型如何与现有 approval 对齐；LSP 诊断的前端呈现。
- "Pi Agent" 具体指哪个产品？（请确认，以便判断是否值得单独做一个 backend）

## 8. 调研对比：OpenCode vs Pi Agent vs Hermes/DeepSeek
> 来源：pi.dev/docs/latest、opencode.ai/docs、Pi 架构拆解文章（2026-08）。结论：首选 OpenCode 作为第一个接入的 coding agent。

### 三者定位
| 维度 | OpenCode | Pi Agent (Pi Agent Harness) | Hermes（现状）+ DeepSeek |
|---|---|---|---|
| 作者/协议 | SST/anomalyco，MIT | Mario Zechner(badlogic)，MIT，~23k★ | Nous Research，已集成 |
| 形态 | 成品 coding agent（TUI/桌面/IDE） | 极简 agent harness（CLI/SDK/RPC） | 通用自改进 agent |
| 远程/无头 | ✅ `opencode serve` HTTP + SSE + 自动生成 TS SDK | ⚠️ 远程 protocol/server 仍实验；默认 JSONL 本地会话 | ✅ 已有 HTTP REST + SSE（/v1/runs） |
| 默认工具 | read/write/edit、bash、grep/glob、LSP、webfetch | 极简 4 个（read/bash/edit/write）+ 可选 grep/find/ls | file/terminal/code_exec + coding toolset |
| Plan/Build | ✅ 内置 Tab 切换 | ❌ 核心不内置，需 Extension/Package 自装 | approval 机制（非 Plan/Build UX） |
| LSP 诊断 | ✅ 内置回灌 | ❌ 需扩展 | ❌ |
| 模型 | Models.dev 75+ provider，OpenAI 兼容 | 22+ provider（含 DeepSeek/Ollama/vLLM） | 经 litellm 多 provider |
| 多会话/子代理 | ✅ 多会话并行 | ✅ 子代理（teammate）是扩展能力 | ✅ delegate 子代理 |
| 接入成本（到 ComWorker） | 低：HTTP/SSE 与 Hermes 同构，复用 proxy/终端/LLM | 中高：需 TS sidecar 包 SDK/RPC，自补 Plan/权限/LSP | 0（已集成） |
| 与 ComWorker 契合 | 高：现成"coding 专精"运行时，补齐现代 UX | 高（理念）：极简核心 + ComWorker 当产品壳，但需自建 UX | 已是通用底座 |

### 结论（推荐）
- **首选接入 OpenCode 作为第一个 coding agent**：自带无头 HTTP + SSE + SDK，与现有 `DedicatedHermesBackend` 几乎同构，直接复用网关代理、终端/WS 代理、LLM 代理与配额；Plan/Build + LSP 开箱即用，立刻补上"现代 coding UX"。
- **Pi 作为第二阶段可插拔 backend**："极简核心 + 扩展装配"理念与"ComWorker 当产品壳"契合，但远程服务实验态，需 TS sidecar 包 SDK/RPC，并把 Plan/权限/LSP 作为扩展接进来。适合要"可深度定制的 harness"而非"开箱产品"时。
- **Hermes 不替代**：保留通用 agent 底座（技能生态、MCP、ACP、Codex provider）。coding 专精体验交给 OpenCode，通用任务仍走 Hermes。
- **DeepSeek 是"模型"不是"agent 框架"**：应在任意 backend 内作为被调用的模型（走 `/llm/v1`），不与 OpenCode/Pi 并列比较。

## 9. 深入：RuntimeBackend 改造与 CodingAgentBackend（OpenCode）接口设计
（本块是集成链路中最关键、对 OpenCode 与 Pi 都通用的一环）

### 9.1 让 runtime_router 可插拔
现状：`platform/app/runtime_router.py:13` 仅支持 `"hermes"`。改为注册表：

```python
# platform/app/runtime_router.py（示意）
_BACKENDS: dict[str, type[RuntimeBackend]] = {
    "hermes": DedicatedHermesBackend,
    "opencode": OpenCodeBackend,   # 新增
}

def get_backend(runtime: str, **kwargs) -> RuntimeBackend:
    if runtime not in _BACKENDS:
        raise ValueError(f"unknown runtime: {runtime}")
    return _BACKENDS[runtime](**kwargs)
```
会话/agent 记录增加 `runtime` 字段（默认 `"hermes"`）；管理端可把某 agent 标记为 `runtime: "opencode"`。前端建会话时带 `runtime`，网关据此选 backend。

### 9.2 CodingAgentBackend 实现 RuntimeBackend 协议
`RuntimeBackend`（`runtime_backend.py:17`）核心方法：`send_message`、`wait_run`、`stream_run_events`、`list_skills`（及 agents 相关）。`OpenCodeBackend` 镜像 `dedicated_hermes.py:169` 结构，但把对 Hermes 容器的 REST 换成对 opencode 容器的 HTTP：

| RuntimeBackend 方法 | OpenCode 映射（P0 需验证真实端点） |
|---|---|
| `send_message(session_key, message)` | 复用 opencode session；`POST /sessions/{id}/messages`（或 SDK `client.session.chat`）→ 返回 run/turn id |
| `stream_run_events(run_id)` | 订阅 opencode SSE（`/sessions/{id}/events` 或 SDK stream）→ 转成与 Hermes 事件同构的 `{type, data}` |
| `wait_run(run_id)` | 阻塞轮询直到 turn 结束（或读 SSE 直到 done） |
| `list_skills(...)` | 读挂载进容器的技能目录（与 Hermes 共用 `/opt/data/skills` 卷），或返回 opencode 的 skills/packages 列表 |
| `list_agents(...)` | 返回该 runtime 下的 agent profile（默认 + 自定义） |

关键点：事件流形状要与前端 `/runs/{id}/events` 消费端对齐（文本增量、tool_use、tool_result、done）。P0 验证 opencode 真实 SSE 字段后做适配层。

### 9.3 opencode 容器
- Dockerfile：`FROM oven/bun`，`bun install -g opencode-ai`（锁版本），装 git/LSP 依赖；暴露 `opencode serve --address 0.0.0.0:PORT`。
- 挂载：用户代码工作区（按 Plan/Build 决定只读/读写）、技能卷（复用 Hermes 的 `/opt/data/skills`）、opencode 配置目录。
- 模型：opencode provider 指向平台 LLM 代理 `http://gateway/llm/v1`（OpenAI 兼容），统一配额/key。
- 复用 `container/manager.py` 的 per-user 生命周期（ensure_running、网络、清理）。

### 9.4 权限/Plan-Build 对齐
- opencode Plan 模式 ↔ 前端"只读规划"开关；Build ↔ 执行。逐工具审批挂到 ComWorker 现有 approval 流程（参考 Hermes approval）。
- 终端/WS 直接复用 `/api/comworker/terminal/ws`，无需新建。

### 9.5 风险
- opencode 镜像体积 / Bun 冷启动 vs Hermes(Python)；P0 实测。
- opencode 真实 HTTP/SSE 端点需 P0 抓包确认（文档偏 TUI，服务端 API 以 `opencode serve` + SDK 为准）。
- 事件流字段映射需适配层；差异大则在 backend 内做 translator。
