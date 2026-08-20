from types import SimpleNamespace

from app.capabilities import build_capability_plan, capability_registry, capability_states, parse_hermes_api_toolsets


def _settings(**over):
    base = {"hermes_api_toolsets": "terminal,file,skills", "tavily_api_key": ""}
    base.update(over)
    return SimpleNamespace(**base)


def _uc(enabled):
    return SimpleNamespace(enabled=enabled)


def _pd(default_inject):
    return SimpleNamespace(default_inject=default_inject)


# --- parse_hermes_api_toolsets ---

def test_parse_toolsets_empty_and_none():
    assert parse_hermes_api_toolsets("") == []
    assert parse_hermes_api_toolsets("none") == []
    assert parse_hermes_api_toolsets("off") == []


def test_parse_toolsets_full_keyword():
    assert parse_hermes_api_toolsets("full") == ["hermes-api-server"]


def test_parse_toolsets_list_normalizes():
    assert parse_hermes_api_toolsets("terminal,file,skills") == ["terminal", "file", "skills"]
    assert parse_hermes_api_toolsets("terminal, file , skills") == ["terminal", "file", "skills"]
    assert parse_hermes_api_toolsets("terminal;file") == ["terminal", "file"]


# --- backward-compat invariant ---

def test_empty_tables_no_key_reproduces_baseline():
    plan = build_capability_plan(_settings())
    assert plan.enabled_toolsets == ["terminal", "file", "skills"]
    assert plan.env == {}
    assert plan.config_overrides == {}


def test_platform_key_alone_without_grant_is_dormant():
    # A configured key must NOT enable anything without a grant.
    plan = build_capability_plan(_settings(tavily_api_key="tvly-x"))
    assert plan.enabled_toolsets == ["terminal", "file", "skills"]
    assert plan.env == {}
    assert plan.config_overrides == {}


# --- conditional mounting ---

def test_grant_with_platform_key_mounts_web():
    plan = build_capability_plan(
        _settings(tavily_api_key="tvly-x"),
        platform_defaults={"web_search": _pd(True)},
    )
    assert "web" in plan.enabled_toolsets
    assert plan.env == {"TAVILY_API_KEY": "tvly-x"}
    assert plan.config_overrides == {"web": {"backend": "tavily"}}


def test_grant_without_key_does_not_mount_web():
    # Granted but no usable key -> conditional mounting skips the toolset.
    plan = build_capability_plan(
        _settings(tavily_api_key=""),
        platform_defaults={"web_search": _pd(True)},
    )
    assert "web" not in plan.enabled_toolsets
    assert plan.env == {}
    assert plan.config_overrides == {}


# --- precedence ---

def test_per_user_grant_overrides_default_off():
    plan = build_capability_plan(
        _settings(tavily_api_key="tvly-x"),
        user_caps={"web_search": _uc(True)},
        platform_defaults={"web_search": _pd(False)},
    )
    assert "web" in plan.enabled_toolsets


def test_per_user_revoke_overrides_default_on():
    plan = build_capability_plan(
        _settings(tavily_api_key="tvly-x"),
        user_caps={"web_search": _uc(False)},
        platform_defaults={"web_search": _pd(True)},
    )
    assert "web" not in plan.enabled_toolsets
    assert plan.env == {}


def test_default_on_when_no_per_user():
    plan = build_capability_plan(
        _settings(tavily_api_key="tvly-x"),
        platform_defaults={"web_search": _pd(True)},
    )
    assert "web" in plan.enabled_toolsets


def test_off_when_neither_grant_nor_default():
    plan = build_capability_plan(_settings(tavily_api_key="tvly-x"))
    assert "web" not in plan.enabled_toolsets


# --- key precedence ---

def test_user_key_overrides_platform_key():
    plan = build_capability_plan(
        _settings(tavily_api_key="plat-key"),
        user_caps={"web_search": _uc(True)},
        user_keys={"web_search": "user-key"},
    )
    assert plan.env["TAVILY_API_KEY"] == "user-key"


def test_platform_key_used_when_no_user_key():
    plan = build_capability_plan(
        _settings(tavily_api_key="plat-key"),
        user_caps={"web_search": _uc(True)},
    )
    assert plan.env["TAVILY_API_KEY"] == "plat-key"


def test_user_key_without_grant_is_ignored():
    # A user key alone (no grant) must not mount the capability.
    plan = build_capability_plan(
        _settings(tavily_api_key=""),
        user_keys={"web_search": "user-key"},
    )
    assert "web" not in plan.enabled_toolsets
    assert plan.env == {}

def test_capability_registry_reports_web_search_and_key_status():
    reg = capability_registry(_settings(tavily_api_key=""))
    ws = [c for c in reg if c["capability"] == "web_search"][0]
    assert ws["label"] == "联网搜索"
    assert ws["toolsets"] == ["web"]
    assert ws["env_key"] == "TAVILY_API_KEY"
    assert ws["requires_key"] is True
    assert ws["platform_key_configured"] is False

    reg2 = capability_registry(_settings(tavily_api_key="tvly-x"))
    assert [c for c in reg2 if c["capability"] == "web_search"][0]["platform_key_configured"] is True


def _state(states, cap):
    return [s for s in states if s["capability"] == cap][0]


def test_capability_states_per_user_grant_overrides_default_off():
    states = capability_states(
        _settings(tavily_api_key="x"),
        user_caps={"web_search": _uc(True)},
        platform_defaults={"web_search": _pd(False)},
    )
    st = _state(states, "web_search")
    assert st["effective_enabled"] is True
    assert st["source"] == "user"
    assert st["user_override"] is True


def test_capability_states_per_user_revoke_overrides_default_on():
    states = capability_states(
        _settings(tavily_api_key="x"),
        user_caps={"web_search": _uc(False)},
        platform_defaults={"web_search": _pd(True)},
    )
    st = _state(states, "web_search")
    assert st["effective_enabled"] is False
    assert st["source"] == "user"


def test_capability_states_default_when_no_override():
    states = capability_states(
        _settings(tavily_api_key="x"),
        platform_defaults={"web_search": _pd(True)},
    )
    st = _state(states, "web_search")
    assert st["effective_enabled"] is True
    assert st["source"] == "default"
    assert st["user_override"] is False


def test_capability_states_off_when_neither():
    states = capability_states(_settings(tavily_api_key="x"))
    st = _state(states, "web_search")
    assert st["effective_enabled"] is False
    assert st["source"] == "off"


# --- placeholder capabilities ---

def test_placeholder_never_mounted_even_if_granted():
    from app.capabilities import build_capability_plan
    # Grant browser + a real default; browser must stay inert.
    plan = build_capability_plan(
        _settings(tavily_api_key="tvly-x"),
        user_caps={"browser": _uc(True), "web_search": _uc(True)},
        platform_defaults={"image_gen": _pd(True)},
    )
    assert "web" in plan.enabled_toolsets          # real capability still works
    # placeholders contribute no toolsets/env/config
    assert plan.env == {"TAVILY_API_KEY": "tvly-x"}
    assert plan.config_overrides == {"web": {"backend": "tavily"}}


def test_placeholder_in_registry_marked_not_implemented():
    from app.capabilities import capability_registry
    reg = {c["capability"]: c for c in capability_registry(_settings())}
    assert reg["browser"]["placeholder"] is True
    assert reg["image_gen"]["placeholder"] is True
    assert reg["browser"]["requires_key"] is False
    assert reg["browser"]["platform_key_configured"] is False
    assert reg["web_search"]["placeholder"] is False


def test_placeholder_states_always_inert():
    from app.capabilities import capability_states
    # Even with a grant + default, placeholders report inert.
    states = capability_states(
        _settings(tavily_api_key="x"),
        user_caps={"browser": _uc(True)},
        platform_defaults={"image_gen": _pd(True)},
    )
    by = {s["capability"]: s for s in states}
    assert by["browser"]["effective_enabled"] is False
    assert by["browser"]["source"] == "placeholder"
    assert by["image_gen"]["effective_enabled"] is False
    assert by["image_gen"]["source"] == "placeholder"


def test_env_key_map_excludes_placeholders():
    from app.capabilities import capability_by_env_key
    assert capability_by_env_key() == {"TAVILY_API_KEY": "web_search"}
