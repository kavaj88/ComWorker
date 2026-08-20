# 配置向导

> MCP 连接配置、Token 管理、连接验证、故障排查。

---

## 🚀 快速开始

访问 https://lexiangla.com/mcp 获取：
- **COMPANY_FROM**：企业标识
- **LEXIANG_TOKEN**：访问令牌（`lxmcp_xxx` 格式）

---

## 自动配置步骤

### Step 1: 获取用户参数

向用户询问 `COMPANY_FROM` 和 `LEXIANG_TOKEN`。两个参数都不能为空。

### Step 2: 确定 mcp.json 路径

| 客户端 | 路径 |
|--------|------|
| WorkBuddy | `~/.workbuddy/mcp.json` |
| 通用（mcporter） | `~/.mcporter/mcporter.json` |
| Windows | `%USERPROFILE%\.mcporter\mcporter.json` |

### Step 3: 写入 mcp.json

如果配置文件已存在且包含其他 mcpServers 条目，应**合并**而非覆盖。

```json
{
  "mcpServers": {
    "lexiang": {
      "url": "https://mcp.lexiang-app.com/mcp?company_from=实际COMPANY_FROM",
      "transportType": "streamable-http",
      "headers": {
        "Authorization": "Bearer 实际LEXIANG_TOKEN"
      }
    }
  }
}
```

编码要求：UTF-8 无 BOM。

### Step 4: 验证连接

调用 MCP `whoami()` 获取用户信息。成功时展示：

```
✅ 乐享 MCP 连接成功！
👤 当前用户：{用户姓名}
🏢 绑定乐享：{企业名称}
```

> ⚠️ 不要回显完整 Token 值。

---

## Token 生命周期

### 未配置

引导用户访问 `https://lexiangla.com/mcp` 获取。

### 已过期（401）

不要反复重试。引导用户续期：

```
🔒 令牌已过期。请打开以下链接点击「续期」按钮：
https://lexiangla.com/mcp?company_from={company_from}
```

### 租户隔离

- COMPANY_FROM 和 TOKEN 必须属于同一租户
- 切换企业时必须重新获取 Token

---

## 故障排查

| 问题 | 解决 |
|------|------|
| 连接无响应 | 确认 URL 包含 `company_from` |
| 401 | Token 过期或租户不匹配 → 续期 |
| 参数报错 | `get_tool_schema(tool_name="xxx")` 获取最新定义 |
