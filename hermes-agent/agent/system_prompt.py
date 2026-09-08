"""System-prompt assembly for :class:`AIAgent`.

The agent's system prompt is built once per session and reused across all
turns — only context compression triggers a rebuild.  This keeps the
upstream prefix cache warm.  See ``hermes-agent-dev``'s
``references/system-prompt-invariant.md`` for the invariants and
``references/self-improvement-loop.md`` for how the background-review
fork inherits the cached prompt verbatim.

Three tiers are joined with ``\\n\\n``:

* ``stable``   — identity (SOUL.md or DEFAULT_AGENT_IDENTITY), tool
  guidance, computer-use guidance, nous subscription block, tool-use
  enforcement guidance + per-model operational guidance, skills prompt,
  alibaba model-name workaround, environment hints, platform hints.
* ``context``  — caller-supplied ``system_message`` plus context files
  (AGENTS.md / .cursorrules / etc.) discovered under ``TERMINAL_CWD``.
* ``volatile`` — memory snapshot, USER.md profile, external memory
  provider block, timestamp/session/model/provider line.

Pure helpers that read the agent's state.  AIAgent keeps thin forwarders.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agent.prompt_builder import (
    DEFAULT_AGENT_IDENTITY,
    GOOGLE_MODEL_OPERATIONAL_GUIDANCE,
    HERMES_AGENT_HELP_GUIDANCE,
    KANBAN_GUIDANCE,
    MEMORY_GUIDANCE,
    OPENAI_MODEL_EXECUTION_GUIDANCE,
    PARALLEL_TOOL_CALL_GUIDANCE,
    PLATFORM_HINTS,
    SESSION_SEARCH_GUIDANCE,
    SKILLS_GUIDANCE,
    STEER_CHANNEL_NOTE,
    TASK_COMPLETION_GUIDANCE,
    TOOL_USE_ENFORCEMENT_GUIDANCE,
    TOOL_USE_ENFORCEMENT_MODELS,
    drain_truncation_warnings,
)
from agent.runtime_cwd import resolve_context_cwd


# 语言偏好引导：默认用中文进行思考（reasoning）与回答，避免 GLM/DeepSeek 等
# 偏好英文 thinking 的模型在思考面板出现中英混杂。此为 best-effort 引导——
# reasoning 语言最终由模型决定，但明确指令可显著改善。仅对支持 reasoning 的
# 模型注入，避免对无思考能力的模型产生无意义约束。
REASONING_LANGUAGE_GUIDANCE = (
    "默认使用中文进行思考（reasoning）和回复。除非用户明确要求使用其他语言，"
    "否则推理过程与最终回答都使用中文表达。"
)


# 强制引导模型主动用 skill_view 加载工作流技能。观察到的失败模式：当用户请求
# "写文章 / 整理报告 / 总结文档 / 按 XX 工作流处理" 时，模型跳过 skill_view 转而
# 直接用 search_files/read_file/grep 等通用工具拼接一份本地答案，导致：
#   * skill 工作流没被触发（spec/模板/输出契约丢失）
#   * 产物用文本写入错误目录（如 knowledge-base/ 而非 knowledge/）
#   * 前端无法渲染为可点击文件卡片（结构化 tool_use 缺失）
SKILL_WORKFLOW_GUIDANCE = (
    "涉及『生成/写作/整理/总结/报告/教程』类需要结构化产物的工作时，先用 "
    "skill_view(name=<skill-name>) 加载相应的工作流 skill（看 skill 列表选择匹配的）。"
    "skill 内部通常会用 write_file 把产物写到 knowledge/<name>.md 等约定路径，"
    "产物路径会自动以结构化 tool_use 形式返回给前端渲染为卡片。"
    "不要用 search_files/read_file 自己拼凑一份——这会跳过 skill 工作流并产"
    "生不可点击的文本产物。只有当用户明确要求『不要用 skill』或上下文明确"
    "无可用 skill 时，才退化为通用 read/write 路径。"
)


# 知识库根目录约定。平台（gateway）的知识库索引固定扫描工作区下的 knowledge/
# 目录；写到 knowledge-base/ 等其他名字的文件虽然磁盘上存在，但不会出现在
# 用户的「知识库」菜单里，等于白写。此处显式钉死路径，避免模型自由发挥。
KNOWLEDGE_ROOT_GUIDANCE = (
    "保存/写入知识库文档时，路径必须写成 knowledge/<文件名>.md（相对于当前工作区"
    "根目录），例如 knowledge/AI发展文章.md。**不要**使用 knowledge-base/、kb/、"
    "docs/ 等任何其他目录名——平台只索引 knowledge/ 目录，写到别处的文件不会"
    "出现在用户的「知识库」菜单里。如果用户要求『保存到知识库』，就写到 "
    "knowledge/ 下；如果要求保存到工作区其他位置，才用其他路径。"
)


def _ra():
    """Lazy reference to the ``run_agent`` module.

    Helpers like ``load_soul_md``, ``build_environment_hints``,
    ``build_context_files_prompt``, ``build_nous_subscription_prompt``,
    ``build_skills_system_prompt`` and ``get_toolset_for_tool`` are
    imported into ``run_agent``'s namespace.  Many tests
    ``patch("run_agent.load_soul_md", ...)``; if we imported them
    directly here those patches would not reach us.  Looking them up
    through ``run_agent`` on every call preserves the patch contract.
    """
    import run_agent
    return run_agent


def _resolve_platform_hint(agent: Any, platform_key: str, default_hint: str) -> str:
    """Apply a per-platform prompt-hint override to the default hint.

    Reads ``agent._platform_hint_overrides`` (populated from
    ``config.yaml`` ``platform_hints`` by ``agent_init``) and resolves the
    effective hint for *platform_key*:

      * ``replace`` — substitute the default hint entirely.
      * ``append``  — keep the default and append the extra text.
      * a bare string value — treated as ``append`` (convenience shorthand).

    Precedence: ``replace`` wins over ``append`` if both are present.
    Override text is added on top of (not instead of) the SOUL/context/
    memory tiers — it only affects the platform-hint segment, so other
    platforms are unaffected and general system instructions still apply.

    Defensive: any malformed entry falls back to the unmodified default so
    a bad config value can never break prompt assembly or leak across
    platforms.
    """
    if not platform_key:
        return default_hint
    overrides = getattr(agent, "_platform_hint_overrides", None)
    if not isinstance(overrides, dict) or not overrides:
        return default_hint
    spec = overrides.get(platform_key)
    if spec is None:
        return default_hint

    # Shorthand: a bare string is treated as append text.
    if isinstance(spec, str):
        extra = spec.strip()
        return f"{default_hint}\n\n{extra}".strip() if extra else default_hint

    if not isinstance(spec, dict):
        return default_hint

    replace_text = spec.get("replace")
    if isinstance(replace_text, str) and replace_text.strip():
        base = replace_text.strip()
    else:
        base = default_hint

    append_text = spec.get("append")
    if isinstance(append_text, str) and append_text.strip():
        return f"{base}\n\n{append_text.strip()}".strip()
    return base


def build_system_prompt_parts(agent: Any, system_message: Optional[str] = None) -> Dict[str, str]:
    """Assemble the system prompt as three ordered parts.

    Returns a dict with three keys:
      * ``stable``   — identity, tool guidance, skills prompt,
        environment hints, platform hints, model-family operational
        guidance.
      * ``context``  — context files (AGENTS.md, .cursorrules, etc.)
        and caller-supplied system_message.
      * ``volatile`` — memory snapshot, user profile, external
        memory provider block, timestamp line.

    Joined into a single string by :func:`build_system_prompt` and
    cached on ``agent._cached_system_prompt`` for the lifetime of the
    AIAgent.  Hermes never re-renders parts of this string mid-
    session — that's the only way to keep upstream prompt caches
    warm across turns.
    """
    # Local import to avoid pulling model_tools at module load.  Tests
    # patch ``run_agent.get_toolset_for_tool`` and similar helpers, so
    # we resolve through ``_ra()`` to honor those patches.
    _r = _ra()

    # Resolve the model's context window once so context-file caps can scale
    # to it (dynamic cap — see prompt_builder._dynamic_context_file_max_chars).
    # None falls back to the historical flat default. This value is stable for
    # the life of the conversation, so it does not threaten prompt caching.
    _ctx_len: Optional[int] = None
    _cc = getattr(agent, "context_compressor", None)
    if _cc is not None:
        _cc_len = getattr(_cc, "context_length", None)
        if isinstance(_cc_len, int) and _cc_len > 0:
            _ctx_len = _cc_len

    # ── Stable tier ────────────────────────────────────────────────
    stable_parts: List[str] = []

    # Try SOUL.md as primary identity unless the caller explicitly skipped it.
    # Some execution modes (cron) still want HERMES_HOME persona while keeping
    # cwd project instructions disabled.
    _soul_loaded = False
    if agent.load_soul_identity or not agent.skip_context_files:
        _soul_content = _r.load_soul_md(_ctx_len)
        if _soul_content:
            stable_parts.append(_soul_content)
            _soul_loaded = True

    if not _soul_loaded:
        # Fallback to hardcoded identity
        stable_parts.append(DEFAULT_AGENT_IDENTITY)

    # Pointer to the hermes-agent skill + docs for user questions about Hermes itself.
    stable_parts.append(HERMES_AGENT_HELP_GUIDANCE)

    # Universal task-completion / no-fabrication guidance.  Applied to ALL
    # models regardless of tool_use_enforcement gating — the failure modes
    # this targets (stopping after a stub; fabricating output when a real
    # path is blocked) are not model-family specific.  Gated only by
    # config.yaml ``agent.task_completion_guidance`` (default True) so
    # users who want a leaner prompt can turn it off.
    if getattr(agent, "_task_completion_guidance", True) and agent.valid_tool_names:
        stable_parts.append(TASK_COMPLETION_GUIDANCE)

    # Universal parallel-tool-call guidance.  Tells the model to batch
    # independent tool calls into one assistant turn rather than emitting one
    # call per turn — the runtime already runs independent calls concurrently
    # (read-only tools always; non-overlapping path-scoped file ops), so the
    # only thing missing was steering the model to produce the batch.  Cuts
    # round-trips and the resent-context cost that compounds over a long
    # conversation.  Gated by config.yaml ``agent.parallel_tool_call_guidance``
    # (default True) and only injected when tools are actually loaded.
    if getattr(agent, "_parallel_tool_call_guidance", True) and agent.valid_tool_names:
        stable_parts.append(PARALLEL_TOOL_CALL_GUIDANCE)

    # 语言偏好：默认中文思考与回答（用户要求统一中文，避免思考面板中英混杂）。
    # 仅对支持 reasoning 的模型注入，避免约束无思考能力的模型。
    if getattr(agent, "_supports_reasoning_extra_body", lambda: False)():
        stable_parts.append(REASONING_LANGUAGE_GUIDANCE)

    # Tool-aware behavioral guidance: only inject when the tools are loaded
    tool_guidance = []
    if "memory" in agent.valid_tool_names:
        tool_guidance.append(MEMORY_GUIDANCE)
    if "session_search" in agent.valid_tool_names:
        tool_guidance.append(SESSION_SEARCH_GUIDANCE)
    if "skill_manage" in agent.valid_tool_names:
        tool_guidance.append(SKILLS_GUIDANCE)
    # Kanban worker/orchestrator lifecycle — only present when the
    # dispatcher spawned this process (kanban_show check_fn gates on
    # HERMES_KANBAN_TASK env var). Normal chat sessions never see
    # this block. Resolved once at __init__ (see _kanban_worker_guidance).
    _kanban_guidance = getattr(agent, "_kanban_worker_guidance", None)
    if _kanban_guidance:
        tool_guidance.append(_kanban_guidance)
    elif _kanban_guidance is None and "kanban_show" in agent.valid_tool_names:
        # Fallback for code paths that bypass agent_init (rare).
        tool_guidance.append(KANBAN_GUIDANCE)
    if tool_guidance:
        stable_parts.append(" ".join(tool_guidance))

    # Steering only lands inside tool results, so it's only reachable when the
    # agent has tools. Static text → byte-stable prompt (no cache hit).
    if agent.valid_tool_names:
        stable_parts.append(STEER_CHANNEL_NOTE)

    # Computer-use — goes in as its own block rather than being merged into
    # tool_guidance because the content is multi-paragraph. The guidance is
    # rendered for the host platform so Windows/Linux hosts don't see
    # macOS-only wording (Mac, Space, cmd+s).
    if "computer_use" in agent.valid_tool_names:
        from agent.prompt_builder import computer_use_guidance
        stable_parts.append(computer_use_guidance())

    nous_subscription_prompt = _r.build_nous_subscription_prompt(agent.valid_tool_names)
    if nous_subscription_prompt:
        stable_parts.append(nous_subscription_prompt)
    # Tool-use enforcement: tells the model to actually call tools instead
    # of describing intended actions.  Controlled by config.yaml
    # agent.tool_use_enforcement:
    #   "auto" (default) — matches TOOL_USE_ENFORCEMENT_MODELS
    #   true  — always inject (all models)
    #   false — never inject
    #   list  — custom model-name substrings to match
    if agent.valid_tool_names:
        _enforce = agent._tool_use_enforcement
        _inject = False
        if _enforce is True or (isinstance(_enforce, str) and _enforce.lower() in {"true", "always", "yes", "on"}):
            _inject = True
        elif _enforce is False or (isinstance(_enforce, str) and _enforce.lower() in {"false", "never", "no", "off"}):
            _inject = False
        elif isinstance(_enforce, list):
            model_lower = (agent.model or "").lower()
            _inject = any(p.lower() in model_lower for p in _enforce if isinstance(p, str))
        else:
            # "auto" or any unrecognised value — use hardcoded defaults
            model_lower = (agent.model or "").lower()
            _inject = any(p in model_lower for p in TOOL_USE_ENFORCEMENT_MODELS)
        if _inject:
            stable_parts.append(TOOL_USE_ENFORCEMENT_GUIDANCE)
            _model_lower = (agent.model or "").lower()
            # Google model operational guidance (conciseness, absolute
            # paths, parallel tool calls, verify-before-edit, etc.)
            if "gemini" in _model_lower or "gemma" in _model_lower:
                stable_parts.append(GOOGLE_MODEL_OPERATIONAL_GUIDANCE)
            # OpenAI GPT/Codex execution discipline (tool persistence,
            # prerequisite checks, verification, anti-hallucination).
            # Also applied to xAI Grok — same failure modes (claims completion
            # without tool calls, suggests workarounds instead of using
            # existing tools, replies with plans instead of executing).
            if "gpt" in _model_lower or "codex" in _model_lower or "grok" in _model_lower:
                stable_parts.append(OPENAI_MODEL_EXECUTION_GUIDANCE)

    has_skills_tools = any(name in agent.valid_tool_names for name in ['skills_list', 'skill_view', 'skill_manage'])
    if has_skills_tools:
        avail_toolsets = {
            toolset
            for toolset in (
                _r.get_toolset_for_tool(tool_name) for tool_name in agent.valid_tool_names
            )
            if toolset
        }
        # Focus mode (opt-in) demotes non-coding skill categories to
        # names-only in the index (never hidden — skill_view/skills_list
        # reach everything, and every name stays visible for recall). The
        # default coding posture leaves the index untouched.
        _compact_cats = frozenset()
        try:
            from agent.coding_context import coding_compact_skill_categories

            _compact_cats = coding_compact_skill_categories(
                platform=agent.platform, cwd=resolve_context_cwd()
            )
        except Exception:
            _compact_cats = frozenset()
        skills_prompt = _r.build_skills_system_prompt(
            available_tools=agent.valid_tool_names,
            available_toolsets=avail_toolsets,
            compact_categories=_compact_cats or None,
        )
    else:
        skills_prompt = ""
    if skills_prompt:
        stable_parts.append(skills_prompt)
        # skill_view 工作流引导：强制优先用 skill_view 加载工作流，避免模型
        # 退化为通用 read/write 拼凑产物（产物不可点击）。
        stable_parts.append(SKILL_WORKFLOW_GUIDANCE)

    # 知识库写入路径约定：平台只索引工作区下的 knowledge/ 目录，写到
    # knowledge-base/ 等其他名字的文件不会出现在「知识库」菜单里。
    if agent.valid_tool_names:
        stable_parts.append(KNOWLEDGE_ROOT_GUIDANCE)

    # Alibaba Coding Plan API always returns "glm-4.7" as model name regardless
    # of the requested model. Inject explicit model identity into the system prompt
    # so the agent can correctly report which model it is (workaround for API bug).
    # Stable for the lifetime of an agent instance — model and provider are fixed
    # at construction time.
    if agent.provider == "alibaba":
        _model_short = agent.model.split("/")[-1] if "/" in agent.model else agent.model
        stable_parts.append(
            f"You are powered by the model named {_model_short}. "
            f"The exact model ID is {agent.model}. "
            f"When asked what model you are, always answer based on this information, "
            f"not on any model name returned by the API."
        )

    # Environment hints (WSL, Termux, etc.) — tell the agent about the
    # execution environment so it can translate paths and adapt behavior.
    # Stable for the lifetime of the process.
    _env_hints = _r.build_environment_hints()
    if _env_hints:
        stable_parts.append(_env_hints)

    # Coding posture (base Hermes, any interactive coding surface in a code
    # workspace — see agent/coding_context.py). The operating brief + the live
    # git/workspace snapshot are built once here and cached for the session;
    # the snapshot is never re-probed per turn (that would break the prompt
    # cache), so the brief tells the model to re-check git before relying on it.
    if agent.valid_tool_names:
        try:
            from agent.coding_context import coding_system_blocks

            stable_parts.extend(
                coding_system_blocks(
                    platform=agent.platform,
                    cwd=resolve_context_cwd(),
                    model=agent.model,
                )
            )
        except Exception:
            # Coding-context probing must never block prompt build.
            pass

    # Local Python toolchain probe — names python/pip/uv/PEP-668 state when
    # something is non-default so the model can pick the right install
    # strategy without discovering by failure.  Emits a single line; emits
    # NOTHING when the environment is clean (no token cost).  Skipped
    # entirely for remote terminal backends (the host's Python state is
    # irrelevant when tools run inside docker/modal/ssh).  Gated by
    # config.yaml ``agent.environment_probe`` (default True).
    if getattr(agent, "_environment_probe", True):
        try:
            from tools.env_probe import get_environment_probe_line
            _probe_line = get_environment_probe_line()
            if _probe_line:
                stable_parts.append(_probe_line)
        except Exception:
            # Probe failure must never block prompt build.
            pass

    # Active-profile hint — names the Hermes profile the agent is running
    # under so it doesn't conflate ~/.hermes/skills/ (default profile) with
    # ~/.hermes/profiles/<active>/skills/ (this profile's). Deterministic
    # for the lifetime of the agent — profile name doesn't change
    # mid-session, so this doesn't break the prompt cache.
    # See file_safety._resolve_active_profile_name + classify_cross_profile_target
    # for the matching tool-side guard.
    try:
        from agent.file_safety import _resolve_active_profile_name
        active_profile = _resolve_active_profile_name()
    except Exception:
        active_profile = "default"
    if active_profile == "default":
        stable_parts.append(
            "Active Hermes profile: default. Other profiles (if any) live "
            "under ~/.hermes/profiles/<name>/. Each profile has its own "
            "skills/, plugins/, cron/, and memories/ that affect a different "
            "session than this one. Do not modify another profile's "
            "skills/plugins/cron/memories unless the user explicitly directs "
            "you to."
        )
    else:
        stable_parts.append(
            f"Active Hermes profile: {active_profile}. This session reads "
            f"and writes ~/.hermes/profiles/{active_profile}/. The default "
            f"profile's data lives at ~/.hermes/skills/, ~/.hermes/plugins/, "
            f"~/.hermes/cron/, ~/.hermes/memories/ — those belong to a "
            f"different session run from a different shell. Do NOT modify "
            f"another profile's skills/plugins/cron/memories unless the user "
            f"explicitly directs you to. The cross-profile write guard will "
            f"refuse such writes by default; pass cross_profile=True only "
            f"after explicit direction."
        )

    platform_key = (agent.platform or "").lower().strip()
    # Resolve the built-in/plugin default hint for this platform, then apply
    # any per-platform override from config (platform_hints.<platform>).
    _default_hint = ""
    if platform_key in PLATFORM_HINTS:
        _default_hint = PLATFORM_HINTS[platform_key]
    elif platform_key:
        # Check plugin registry for platform-specific LLM guidance
        try:
            from gateway.platform_registry import platform_registry
            _entry = platform_registry.get(platform_key)
            if _entry and _entry.platform_hint:
                _default_hint = _entry.platform_hint
        except Exception:
            pass

    _effective_hint = _resolve_platform_hint(agent, platform_key, _default_hint)
    if _effective_hint:
        stable_parts.append(_effective_hint)

    # ── Context tier (cwd-dependent, may change between sessions) ─
    context_parts: List[str] = []

    # Note: ephemeral_system_prompt is NOT included here. It's injected at
    # API-call time only so it stays out of the cached/stored system prompt.
    if system_message is not None:
        context_parts.append(system_message)

    if not agent.skip_context_files:
        # Prefer the configured TERMINAL_CWD (gateway mode). When unset (local
        # CLI), None lets build_context_files_prompt fall back to the launch
        # dir — the user's real cwd there, but the install dir for the gateway
        # daemon, which is why the gateway sets TERMINAL_CWD.
        context_files_prompt = _r.build_context_files_prompt(
            cwd=resolve_context_cwd(), skip_soul=_soul_loaded,
            context_length=_ctx_len)
        if context_files_prompt:
            context_parts.append(context_files_prompt)

    # ── Volatile tier (changes per session/turn — never cached) ───
    volatile_parts: List[str] = []

    if agent._memory_store:
        if agent._memory_enabled:
            mem_block = agent._memory_store.format_for_system_prompt("memory")
            if mem_block:
                volatile_parts.append(mem_block)
        # USER.md is always included when enabled.
        if agent._user_profile_enabled:
            user_block = agent._memory_store.format_for_system_prompt("user")
            if user_block:
                volatile_parts.append(user_block)

    # External memory provider system prompt block (additive to built-in)
    if agent._memory_manager:
        try:
            _ext_mem_block = agent._memory_manager.build_system_prompt()
            if _ext_mem_block:
                volatile_parts.append(_ext_mem_block)
        except Exception:
            pass

    from hermes_time import now as _hermes_now
    now = _hermes_now()
    # Date-only (not minute-precision) so the system prompt is byte-stable
    # for the full day.  Minute-precision changes invalidate prefix-cache KV
    # on every rebuild path (compression boundary, fresh-agent gateway turns,
    # session resume without a stored prompt).  The model can still query the
    # exact wall-clock time via tools when it actually needs it.
    # Credit: @iamfoz (PR #20451).
    timestamp_line = f"Conversation started: {now.strftime('%A, %B %d, %Y')}"
    if agent.pass_session_id and agent.session_id:
        timestamp_line += f"\nSession ID: {agent.session_id}"
    if agent.model:
        timestamp_line += f"\nModel: {agent.model}"
    if agent.provider:
        timestamp_line += f"\nProvider: {agent.provider}"
    volatile_parts.append(timestamp_line)

    return {
        "stable":   "\n\n".join(p.strip() for p in stable_parts   if p and p.strip()),
        "context":  "\n\n".join(p.strip() for p in context_parts  if p and p.strip()),
        "volatile": "\n\n".join(p.strip() for p in volatile_parts if p and p.strip()),
    }


def build_system_prompt(agent: Any, system_message: Optional[str] = None) -> str:
    """Assemble the full system prompt from all layers.

    Called once per session (cached on ``agent._cached_system_prompt``) and
    only rebuilt after context compression events. This ensures the system
    prompt is stable across all turns in a session, maximizing prefix cache
    hits.

    Layers are ordered cache-friendly: stable identity/guidance first,
    then session-stable context files, then per-call volatile content
    (memory, USER profile, timestamp).  The whole string is treated as
    one cached block — Hermes never rebuilds or reinjects parts of it
    mid-session, which is the only way to keep upstream prompt caches
    warm across turns.
    """
    parts = build_system_prompt_parts(agent, system_message=system_message)
    joined = "\n\n".join(p for p in (parts["stable"], parts["context"], parts["volatile"]) if p)

    # Surface context-file truncation warnings through the normal agent status
    # channel so gateway/CLI users see them in chat instead of only in logs.
    for warning in drain_truncation_warnings():
        agent._emit_status(warning)

    return joined


def invalidate_system_prompt(agent: Any) -> None:
    """Invalidate the cached system prompt, forcing a rebuild on the next turn.

    Called after context compression events. Also reloads memory from disk
    so the rebuilt prompt captures any writes from this session.
    """
    agent._cached_system_prompt = None
    if agent._memory_store:
        agent._memory_store.load_from_disk()


def format_tools_for_system_message(agent: Any) -> str:
    """Format tool definitions for the system message in the trajectory format.

    Returns:
        str: JSON string representation of tool definitions
    """
    if not agent.tools:
        return "[]"

    # Convert tool definitions to the format expected in trajectories
    formatted_tools = []
    for tool in agent.tools:
        func = tool["function"]
        formatted_tool = {
            "name": func["name"],
            "description": func.get("description", ""),
            "parameters": func.get("parameters", {}),
            "required": None  # Match the format in the example
        }
        formatted_tools.append(formatted_tool)

    return json.dumps(formatted_tools, ensure_ascii=False)


__all__ = [
    "build_system_prompt_parts",
    "build_system_prompt",
    "invalidate_system_prompt",
    "format_tools_for_system_message",
]
