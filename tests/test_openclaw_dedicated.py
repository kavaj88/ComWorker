"""Tests for dedicated ComWorker runtime endpoints.

Covers:
- GET  /api/comworker/agents
- GET  /api/comworker/skills
- POST /api/comworker/marketplaces/skills/search
- POST /api/comworker/runtime/prewarm
- GET  /api/comworker/sessions
- GET  /api/comworker/commands
- GET  /api/comworker/container/info
- GET  /api/comworker/ping

Note: /api/comworker/sessions/{key}/messages requires a running dedicated
container, which may not be available in all test environments.
"""

from conftest import admin_token, api_url, auth_headers, json_request


def _token() -> str:
    return admin_token()


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

def test_list_agents():
    try:
        result = json_request(
            api_url("/api/comworker/agents"),
            headers=auth_headers(_token()),
        )
        # Response should be a dict with agents list
        assert result is not None
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "agents" in result, f"Missing 'agents' key, got keys: {list(result.keys())}"
        agents = result["agents"]
        assert isinstance(agents, list), f"Expected list, got {type(agents)}"
        assert len(agents) >= 1, "Expected at least 1 agent"
        # Verify each agent has required fields
        for agent in agents:
            assert "id" in agent, f"Agent missing 'id': {agent}"
            assert "name" in agent, f"Agent missing 'name': {agent}"
            assert "workspace" in agent, f"Agent missing 'workspace': {agent}"
            assert "available" in agent, f"Agent missing 'available': {agent}"
            assert isinstance(agent["available"], bool), f"'available' should be bool"
        # Should have a default agent
        agent_ids = [a["id"] for a in agents]
        assert "main" in agent_ids, f"No 'main' agent found in {agent_ids}"
        # Verify response metadata
        assert "defaultId" in result
        assert result["defaultId"] == "main"
    except RuntimeError as exc:
        # May fail with 500/503 if no container can be created (e.g. Docker unavailable)
        assert "500" in str(exc) or "503" in str(exc)


def test_list_agents_unauthorized():
    try:
        json_request(api_url("/api/comworker/agents"))
        assert False, "Expected 401/403"
    except RuntimeError as exc:
        assert any(str(code) in str(exc) for code in ("401", "403"))


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

def test_list_skills():
    try:
        result = json_request(
            api_url("/api/comworker/skills"),
            headers=auth_headers(_token()),
        )
        assert isinstance(result, (list, dict))
    except RuntimeError as exc:
        # May fail with 503 if no dedicated container is running
        assert "503" in str(exc) or "Hermes runtime is unavailable" in str(exc)


def test_search_skills():
    try:
        result = json_request(
            api_url("/api/comworker/marketplaces/skills/search"),
            method="POST",
            payload={"query": "", "limit": 5},
            headers=auth_headers(_token()),
        )
        assert "results" in result
        assert result["runtime"] == "hermes"
        assert isinstance(result["results"], list)
    except RuntimeError as exc:
        assert "503" in str(exc)


def test_search_skills_with_query():
    try:
        result = json_request(
            api_url("/api/comworker/marketplaces/skills/search"),
            method="POST",
            payload={"query": "code", "limit": 3},
            headers=auth_headers(_token()),
        )
        assert "results" in result
    except RuntimeError as exc:
        assert "503" in str(exc)


# ---------------------------------------------------------------------------
# Prewarm
# ---------------------------------------------------------------------------

def test_prewarm_runtime():
    result = json_request(
        api_url("/api/comworker/runtime/prewarm"),
        method="POST",
        headers=auth_headers(_token()),
    )
    assert "ok" in result


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def test_list_sessions():
    result = json_request(
        api_url("/api/comworker/sessions"),
        headers=auth_headers(_token()),
    )
    # Should return a list (may be empty if no sessions exist)
    assert isinstance(result, (list, dict))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def test_list_commands():
    result = json_request(
        api_url("/api/comworker/commands"),
        headers=auth_headers(_token()),
    )
    # Hermes returns empty commands list; should be a dict
    assert isinstance(result, dict)
    assert "commands" in result


def test_list_commands_with_agent_id():
    result = json_request(
        api_url("/api/comworker/commands?agentId=main"),
        headers=auth_headers(_token()),
    )
    assert "commands" in result


# ---------------------------------------------------------------------------
# Container info
# ---------------------------------------------------------------------------

def test_container_info():
    result = json_request(
        api_url("/api/comworker/container/info"),
        headers=auth_headers(_token()),
    )
    # May return no container if not yet created
    assert "status" in result
    assert result["status"] in ("running", "none", "stopped", "paused", None)


# ---------------------------------------------------------------------------
# Proxy ping
# ---------------------------------------------------------------------------

def test_proxy_ping():
    result = json_request(
        api_url("/api/comworker/ping"),
        headers=auth_headers(_token()),
    )
    assert result["message"] == "pong"
    assert result["service"] == "comworker-proxy"
