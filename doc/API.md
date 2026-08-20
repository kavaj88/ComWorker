# comworker/Bridge的API

列出所有Agents信息
curl -s http://127.0.0.1:18080/api/agents

删除1个Agent
curl -s -X DELETE http://127.0.0.1:18080/api/agents/test-agent-123

# Ping 网关
curl -s http://127.0.0.1:8080/api/ping
{"message":"pong","service":"comworker-platform"}%

# 查看容器的状态
curl -s -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmMDUzNjc4NC1kNzJlLTQ1N2EtYTk0NS03ZjdhYTFmZTExYmYiLCJyb2xlIjoidXNlciIsImV4cCI6MTc3Njc0NzI3NSwidHlwZSI6ImFjY2VzcyJ9.59K0jpn2bOcbV6VqNdqvGH9PL28H13iCUnS2DHhBkm0" http://127.0.0.1:8080/api/comworker/container/info
{"container_name":"hermes-user-f0536784","status":"running","docker_id":"3a2399dd01ee82722859b6f728c7f5f9fb36f94a9de446195c234f52c0b00a8c","created_at":"2026-04-20T12:31:57.034631","ports":[{"container_port":"18080/tcp","host_port":"0.0.0.0:55297"}]}%

# Hermes Agent

  1. Hermes 原生 run SSE
  - 发起运行：POST /v1/runs
  - 订阅事件：GET /v1/runs/{run_id}/events
  - 这条链路会真正产出 message.delta / run.completed / run.failed
  - 代码在 hermes-agent/gateway/platforms/api_server.py:1707 和 hermes-agent/gateway/platforms/api_server.py:1885

  核心改动在：

  - platform/app/hermes_client.py:50
  - platform/app/runtime_backends/dedicated_hermes.py:93
  - platform/app/runtime_backends/shared_hermes.py:101
  - platform/app/api_compat/comworker_compat.py:71
