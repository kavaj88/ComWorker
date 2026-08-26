from app.container import manager


def _skill_mount(mounts):
    return next((mount for mount in mounts if mount["Target"] == "/opt/hermes/skills"), None)


def test_runtime_mounts_prefer_packaged_builtin_skills_volume(monkeypatch):
    monkeypatch.setattr(manager, "_runtime_backend", lambda: "hermes")
    monkeypatch.setattr(manager, "_resolve_builtin_skills_host_dir", lambda: None)
    monkeypatch.setenv("BUILTIN_SKILLS_VOLUME", "comworker-builtin-skills")

    mount = _skill_mount(manager._build_runtime_mounts("user-data", "abcd1234"))

    assert mount is not None
    assert mount["Type"] == "volume"
    assert mount["Source"] == "comworker-builtin-skills"
    assert mount["ReadOnly"] is True


def test_runtime_mounts_keep_host_bind_fallback(monkeypatch):
    monkeypatch.setattr(manager, "_runtime_backend", lambda: "hermes")
    monkeypatch.setattr(
        manager,
        "_resolve_builtin_skills_host_dir",
        lambda: "/srv/comworker/hermes-agent/skills",
    )
    monkeypatch.delenv("BUILTIN_SKILLS_VOLUME", raising=False)

    mount = _skill_mount(manager._build_runtime_mounts("user-data", "abcd1234"))

    assert mount is not None
    assert mount["Type"] == "bind"
    assert mount["Source"] == "/srv/comworker/hermes-agent/skills"
    assert mount["ReadOnly"] is True
