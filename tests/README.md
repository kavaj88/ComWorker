# ComWorker Platform API 测试

ComWorker Platform API 网关（`platform/app/routes`）的集成测试。

这些测试通过对正在运行的网关服务发起真实的 HTTP 请求来验证已部署的 API 端点。

## 前置条件

- Docker Compose 服务必须处于运行状态（`docker compose up -d`）
- 网关必须可访问（默认地址：`http://localhost:8080`）
- 管理员用户必须存在（首次启动时自动创建）

## 快速开始

```bash
# 1. 确保服务正在运行
docker compose ps

# 2. 安装 pytest
pip install pytest

# 3. 运行所有测试
cd tests
pytest -v

# 4. 运行指定测试文件
pytest test_auth.py -v

# 5. 使用自定义 base URL 运行
COMWORKER_BASE_URL=http://127.0.0.1:8080 pytest -v
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `COMWORKER_BASE_URL` | `http://localhost:8080` | API 网关基础地址 |
| `ADMIN_USERNAME` | `admin` | 认证测试用的管理员用户名 |
| `ADMIN_PASSWORD` | `admin123` | 认证测试用的管理员密码 |

## 测试文件组织

每个文件测试 API 的一个功能领域：

| 文件 | 领域 | 覆盖的端点 |
|---|---|---|
| `test_ping.py` | 健康检查 | `GET /api/ping` |
| `test_auth.py` | 认证 | `POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/refresh`、`GET /api/auth/me`、`POST /api/auth/api-token`、`PUT /api/auth/change-password` |
| `test_comworker_dedicated.py` | 专属运行时 | `GET /api/comworker/agents`、`GET /api/comworker/skills`、`POST /api/comworker/marketplaces/skills/search`、`POST /api/comworker/runtime/prewarm`、`GET /api/comworker/sessions`、`GET /api/comworker/commands`、`GET /api/comworker/container/info`、`GET /api/comworker/ping` |
| `test_admin.py` | 管理员管理 | `GET/POST /api/admin/users`、`PUT /api/admin/users/{id}`、`PUT /api/admin/users/{id}/password`、`POST /api/admin/containers/sync`、`GET /api/admin/usage/summary`、`GET /api/admin/usage/history`、`GET /api/admin/audit` |
| `test_filemanager.py` | 文件管理器 | `GET /api/comworker/filemanager/browse`、`POST /api/comworker/filemanager/mkdir`、`DELETE /api/comworker/filemanager/delete` |
| `test_llm.py` | LLM 代理 | `POST /llm/v1/chat/completions` |
| `test_container.py` | 容器管理 | `GET /api/comworker/container/info`、`POST /api/comworker/container/doctor-fix`、`GET /api/comworker/filemanager/download`、`GET /api/comworker/filemanager/serve` |

## 测试模式

所有测试使用 `urllib.request`（Python 标准库），遵循与 `call_agent_api.py` 相同的模式：

- **`conftest.py`** 提供共享辅助函数：`api_url()`、`json_request()`、`auth_headers()`、`admin_token()`、`register_user()`
- 测试通过 `unique_username()` 生成唯一用户名，避免冲突
- 需要认证的端点会同时测试携带有效令牌和不携带令牌的情况
- 覆盖异常场景（错误密码、重复用户、缺少字段）

## 注意事项

- 部分端点需要运行中的专属容器（如聊天消息、文件上传）。这些测试可能需要拥有活跃运行时容器的用户。
- SSE 和 WebSocket 端点不在这些 HTTP 测试的覆盖范围内——它们需要特殊的流式测试基础设施。
- 测试设计为可对同一部署安全地重复运行。
