def test_deploy_copy_dir_finds_container_workdir_layout(tmp_path, monkeypatch):
    from app.runtime_backends import hermes_agents

    module_path = tmp_path / "app" / "app" / "runtime_backends" / "hermes_agents.py"
    module_path.parent.mkdir(parents=True)
    deploy_copy = tmp_path / "app" / "deploy_copy"
    deploy_copy.mkdir(parents=True)
    (deploy_copy / "comworker_defaults.json").write_text(
        '{"agents": {"list": [{"id": "doctor"}]}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(hermes_agents, "__file__", str(module_path))

    assert hermes_agents._deploy_copy_dir() == deploy_copy
    assert hermes_agents._configured_agent_ids() == ["doctor"]
