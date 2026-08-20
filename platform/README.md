# Hermes Platform

Hermes Platform 是一个基于 FastAPI 的多租户网关服务，用于管理用户、配额和 runtime 容器。当前默认 runtime backend 是 Hermes Agent，同时保留 ComWorker-compatible API 路径和 ComWorker fallback 配置。

## 功能特性

- **用户管理** - 注册、登录、JWT 认证、角色权限（admin/user）
- **配额管理** - 免费/基础/专业三级配额，限制每日 token 使用量
- **容器管理** - 为每个用户创建和管理独立的 Docker 容器
- **LLM 代理** - 统一路由 LLM 请求，平台端存储 API Keys，容器内不暴露密钥
- **使用统计** - 记录并统计每个用户的 LLM token 使用量

## 技术栈

- **Web 框架**: FastAPI + Uvicorn
- **数据库**: PostgreSQL + SQLAlchemy (async) + Alembic
- **认证**: JWT (python-jose) + bcrypt
- **容器**: Docker SDK for Python

## 架构概览

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────────┐
│   Client    │────▶│  Platform API   │────▶│  Runtime Container│
│  (Frontend) │     │   (port 8080)    │     │  (Hermes default) │
└─────────────┘     └────────┬────────┘     └──────────────────┘
                             │
                             ▼
                    ┌────────────────┐
                    │   PostgreSQL    │
                    └────────────────┘
```

## API 端点

| 路由 | 描述 |
|------|------|
| `GET /api/ping` | 健康检查 |
| `POST /api/auth/register` | 用户注册 |
| `POST /api/auth/login` | 用户登录 |
| `POST /api/auth/refresh` | 刷新 Token |
| `POST /api/auth/container` | 获取用户容器访问信息 |
| `POST /api/llm/v1/*` | LLM 代理接口 |
| `GET /api/admin/users` | 管理员获取用户列表 |
| `PUT /api/admin/users/{user_id}` | 管理员更新用户 |
| `DELETE /api/admin/users/{user_id}/container` | 管理员删除用户容器 |
| `GET /api/admin/usage/summary` | 平台使用统计 |
| `/api/comworker/*` | dedicated 兼容 API，默认转到 Hermes runtime backend |
| `/api/shared-comworker/*` | shared 兼容 API，默认转到 Hermes shared runtime |

## 配置说明

通过环境变量配置（以 `PLATFORM_` 为前缀）：

| 变量 | 默认值 | 描述 |
|------|--------|------|
| `PLATFORM_DATABASE_URL` | `postgresql+asyncpg://comworker:comworker@localhost:5432/comworker_platform` | 数据库连接 |
| `PLATFORM_JWT_SECRET` | `change-me-in-production` | JWT 密钥 |
| `PLATFORM_DEFAULT_MODEL` | `claude-sonnet-4-5` | 新用户默认模型 |
| `PLATFORM_DEDICATED_RUNTIME_BACKEND` | `hermes` | dedicated 用户容器 runtime，可显式切回 `comworker` |
| `PLATFORM_SHARED_RUNTIME_BACKEND` | `hermes` | shared runtime backend，可显式切回 `comworker` |
| `PLATFORM_HERMES_IMAGE` | `comworker-hermes-agent:latest` | dedicated Hermes runtime Docker 镜像 |
| `PLATFORM_COMWORKER_IMAGE` | `comworker:latest` | ComWorker fallback Docker 镜像 |
| `PLATFORM_SHARED_HERMES_URL` | `http://shared-comworker:8080` | shared Hermes runtime API 地址 |
| `PLATFORM_CONTAINER_MEMORY_LIMIT` | `2g` | 容器内存限制 |
| `PLATFORM_QUOTA_FREE` | `20000000` | 免费用户每日配额 |

## 数据模型

- **User** - 用户账户（username, email, password_hash, role, quota_tier）
- **Container** - 用户容器元数据（docker_id, status, internal_host, internal_port）
- **UsageRecord** - LLM 使用记录（model, input_tokens, output_tokens）
- **AuditLog** - 操作审计日志

## 快速开始

```bash
# 安装依赖
cd platform
pip install -e .

# 启动服务（需要 PostgreSQL）
export PLATFORM_DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/comworker_platform"
python -m app.main
```

## Docker 部署

```bash
# 使用 docker-compose（参考项目根目录的 docker-compose.yml）
docker-compose up -d platform
```

## 前端确定用户容器是否启动的原理

请求处理流程

当前端请求 `http://<host>:8080/api/comworker/sessions/web%3Adefault` 时：

1. **入口：ComWorker-compatible API**
   已适配的 chat/session/run 请求首先到达 `platform/app/api_compat/comworker_compat.py`，再由 `platform/app/runtime_router.py` 按用户 runtime mode 和配置选择 Hermes 或 ComWorker backend。未适配的 legacy 路径仍会落到 `platform/app/routes/proxy.py`。

   ```python
   @router.post("/api/comworker/sessions/{session_key:path}/messages")
   async def send_dedicated_message(...):
       backend = get_runtime_backend(user)  # <-- 关键步骤
   ```

2. **dedicated 容器状态检查：ensure_running**
   dedicated Hermes backend 会调用 `ensure_running` 函数（`platform/app/container/manager.py`），这个函数会：

   1. 从数据库查询容器记录 - 检查该用户是否有容器记录
   2. 根据状态处理：
      - None → 创建新容器
      - paused → unpause 恢复运行
      - archived → 重新创建
      - running → 验证容器实际运行状态

3. **Docker API 实际检查**
   在 `ensure_running` 中：

   ```python
   elif record.status == "running":
       try:
           c = client.containers.get(record.docker_id)
           if c.status != "running":
               c.start()  # 如果状态不是 running，启动它
       except DockerNotFound:
           # 容器被外部删除，重新创建
           return await create_container(db, user_id)
   ```
