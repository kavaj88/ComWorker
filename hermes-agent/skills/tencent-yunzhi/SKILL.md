---
name: tencent-yunzhi
version: 1.0.0
description: "云知平台（乐享）一站式操作。覆盖搜索/浏览/创建/编辑/上传/下载/会议导入/iWiki迁移全部能力。自动识别 URL 版本（v1 团队文档 vs v2 知识库），智能路由到对应操作模块。支持 MCP 协议（2.0）和 REST API（1.0）双通道。典型触发：「搜索乐享知识库」「上传文件到乐享」「创建文档」「编辑页面」「配置乐享」以及任何包含 lexiangla.com 链接的操作请求。"
description_zh: "腾讯云知平台一站式操作（检索、新建、上传、编辑、下载）"
description_en: "Tencent Yunzhi all-in-one assistant — search, create, upload, edit, and download"
---

# 腾讯乐享

> 一站式知识库操作 Skill。自动识别意图和 URL 版本，路由到正确模块。

---

## 🔧 环境与认证

### MCP 连接（2.0）

配置文件模板见 `mcp.json`，写入 `~/.workbuddy/mcp.json` 或 `~/.mcporter/mcporter.json`。

### REST API（1.0）

```
API_HOST = https://lxapi.lexiangla.com
```

### Token 通用规则

`lxmcp_` 前缀的 MCP Token **同时适用于** 2.0 MCP 协议和 1.0 REST API：
- MCP：通过 `mcp.json` 中 `headers.Authorization: Bearer lxmcp_xxx` 传递
- REST：直接放 `Authorization: Bearer lxmcp_xxx` HTTP 头

> ⚠️ 乐享使用 **Bearer Token 静态鉴权**，不涉及 OAuth。Token 缺失/过期时引导用户访问 `https://lexiangla.com/mcp` 获取或续期。

---

## 🚦 路由规则

### 第一步：URL 版本判断

当用户消息中包含 `lexiangla.com` 链接时，**优先**按 URL 路径判断版本：

| URL 特征 | 版本 | 说明 |
|----------|------|------|
| `/teams/{code}` 或 `/teams/{code}/docs` 或 `/teams/{code}/docs/{doc_id}` | **v1** | 团队文档区（1.0 REST API） |
| `/t/{team_id}/spaces` | **v2** | 团队知识库列表（2.0 MCP） |
| `/spaces/{space_id}` | **v2** | 知识库（2.0 MCP） |
| `/pages/{entry_id}` | **v2** | 知识库页面（2.0 MCP） |

**一句话规则**：`/teams/` → v1，`/t/` + `/spaces/` + `/pages/` → v2。

### 第二步：意图 × 版本 → 模块分派

#### 用户消息含 URL 时

```
URL 命中 v1（/teams/）：
  ├─ 意图=上传文件       → modules/v1-docs.md（三步上传）
  ├─ 意图=查看/编辑/删除  → modules/v1-docs.md
  └─ 意图=上传图片        → modules/v1-assets.md

URL 命中 v2：
  /t/{id}/spaces 或 /spaces/{id}：
    ├─ 意图=上传文件       → modules/files.md
    ├─ 意图=创建文档/导入   → modules/writer.md
    └─ 意图=浏览/搜索      → modules/search.md

  /pages/{id}：
    ├─ 意图=修改/编辑/更新  → modules/blocks.md
    ├─ 意图=上传文件        → modules/files.md
    ├─ 意图=追加/写入内容   → modules/writer.md（import_to_entry）
    └─ 意图=阅读/查看      → modules/search.md
```

#### 用户消息不含 URL 时

| 意图关键词 | 模块 |
|-----------|------|
| 配置、连接、setup、token、401、过期、切换企业 | `modules/setup.md` |
| 搜索、查找、找、看看、阅读、浏览、打开、有没有 | `modules/search.md` |
| 创建、新建、写、写入、导入、保存到、发到乐享 | `modules/writer.md` |
| 修改、编辑、更新、改、调整排版、追加、插入、删掉段落 | `modules/blocks.md` |
| 上传文件、传 PDF/Word/Excel/PPT/图片、下载文件 | `modules/files.md` |
| 会议录制、会议纪要、导入会议、iWiki、迁移文档 | `modules/connectors.md` |
| 明确说"1.0 接口"/"v1" + 文档操作 | `modules/v1-docs.md` |
| 明确说"1.0 接口"/"v1" + 图片 | `modules/v1-assets.md` |

#### 无法判断时 → 追问用户目标

---

## ⚠️ 通用规则

### 团队 ID 转换

1.0 REST API 的 `team_id` 参数需要 **UUID 格式**，不能直接用 URL 中的 code（如 `k100684`）。
转换方法：调用 MCP `team_describe_team(team_id="k100684")` → 返回 `team.id`（UUID）。

### URL 中 node 参数

`/teams/{code}/docs?node={node_id}` 中的 `node` 是目标文件夹 ID，上传时作为 `directory.data.id` 传入。

### 结果链接生成

- v2 页面：`https://{domain}/pages/{entry_id}`
- v1 文档：`https://{domain}/teams/{team_code}/docs/{doc_id}`
- `{domain}` 从 `whoami` 返回的 `company.company_domain` 获取，或默认 `csig.lexiangla.com`

### 写入安全约束

- 写入操作**必须**基于用户明确提供的目标信息
- 不要自行猜测目标知识库/目录
- 删除操作需二次确认

### 批量内容获取：合并执行策略

当需要批量获取多篇文档内容时（如遍历目录、生成报告/大纲等），**必须**将多步操作合并到尽可能少的命令中执行，避免用户因逐条审批而任务停滞：

1. **一次性脚本优先**：将「获取列表 → 判断类型 → 下载文件 → 解析内容」串联到一个 shell/python 脚本中一次执行，而非拆成多条独立命令
2. **循环合并**：用 `for` 循环批量处理同类操作（如批量获取文档详情、批量下载文件），一条命令完成
3. **减少交互次数**：目标是整个批量获取流程 **不超过 2~3 次命令执行**（1 次获取列表 + 1~2 次批量获取/解析内容）
4. **`requires_approval: false`**：对读取类操作（curl GET、markitdown 解析等），设置为不需要用户审批，仅写入/删除操作需审批

**示例**（批量获取目录下所有文档内容）：
```bash
# 一条命令完成：获取列表 → 逐个获取内容 → 区分类型 → 下载文件 → 解析 → 输出汇总
python3 -c "
import json, subprocess, os
TOKEN = 'lxmcp_xxx'
API = 'https://lxapi.lexiangla.com'
# Step 1: 获取文档列表
# Step 2: 遍历每篇文档，判断类型
# Step 3: 富文本直接提取 md_content，文件类型下载+markitdown
# Step 4: 输出所有文档摘要到文件
"
```

### 跨版本内容获取降级策略

当需要获取文档/条目的**正文内容**时，按以下顺序尝试：

```
1. 优先使用 v2 MCP：entry_describe_ai_parse_content(entry_id)
   ├─ 成功 → 使用返回的 HTML/Markdown/OCR 内容
   └─ 失败（v1 文档 ID 不兼容 v2 MCP）→ 降级到 v1

2. 降级到 v1 REST：GET /cgi-bin/v1/docs/{doc_id}
   ├─ included 含 type="document" → 富文本，直接读取 content/md_content
   └─ included 含 type="file" → 文件类型
       ├─ 通过 links.download 下载文件（有效期 3 分钟）
       ├─ 使用 markitdown 解析 pptx/docx 提取文字
       ├─ 使用 markitdown 解析 pdf 提取文字（纯图片 PDF 无法提取）
       └─ TODO: 待后续开发提供文件内容解析接口后，可直接通过 API 获取解析内容
```

> ⚠️ v1 的 doc_id 与 v2 的 entry_id **不通用**。通过 `/teams/` URL 获取的文档 ID 只能用 v1 接口查询。详见 `modules/v1-docs.md`。

### 常见错误速查

| 错误 | 处理 |
|------|------|
| 401 | Token 过期 → 引导用户访问 `https://lexiangla.com/mcp` 点击续期 |
| 403 | COS 签名过期（上传场景）→ 重新执行 Step 1 获取新签名 |
| 404 | 文档/条目不存在或无权限 → 确认 ID 正确 |
| `_mcp_fields` | 所有 MCP 工具均支持此参数，按需选择返回字段以减少 token |
| 参数不确定 | 执行 `get_tool_schema(tool_name="xxx")` 获取最新定义 |

---

## 📦 模块索引

路由判定完成后，读取对应模块文件获取详细操作指南：

| 模块 | 文件 | 功能 | 通道 |
|------|------|------|------|
| 配置向导 | `modules/setup.md` | MCP 配置/连接验证/故障排查/Token 管理 | — |
| 搜索阅读 | `modules/search.md` | 关键词/语义搜索、知识库浏览、目录导航、文档读取 | v2 MCP |
| 文档写入 | `modules/writer.md` | 创建文档/页面/文件夹、Markdown/HTML 导入 | v2 MCP |
| 页面编辑 | `modules/blocks.md` | Block 级增删改移、批量编辑、Markdown 转 Block | v2 MCP |
| 文件管理 | `modules/files.md` | 文件三步上传/下载/更新/同步 | v2 MCP |
| 外部导入 | `modules/connectors.md` | 腾讯会议录制导入、iWiki 文档迁移 | v2 MCP |
| 1.0 文档 | `modules/v1-docs.md` | 文档 CRUD（创建/查询/编辑/上传/删除） | v1 REST |
| 1.0 图片 | `modules/v1-assets.md` | 图片上传/下载 | v1 REST |

### 模块间协作

| 场景 | 主模块 | 辅助 |
|------|--------|------|
| 上传文件到知识库（需先找 entry_id） | files | search |
| 编辑已有页面（需先读 block 结构） | blocks | search |
| 上传到 1.0 团队目录（需 UUID 转换） | v1-docs | search（team_describe_team） |
| 创建含图片的富文本文档 | v1-docs | v1-assets（先上传图片） |
| 获取文档内容（v2 失败时降级 v1） | search → v1-docs | markitdown（解析下载的文件） |

---

## 参考文档

| 文档 | 说明 |
|------|------|
| `references/common-errors.md` | 常见错误排查 |
| `references/block-schema.md` | Block 类型完整说明 |
| `references/mcp-examples.md` | 复杂 Block 结构示例 |
| `references/markdown-to-block.md` | Markdown 转 Block 指南 |
| `references/block-update.md` | 批量更新 Block 方法 |
| `references/content-reorganize.md` | 文档结构重组 |
| `references/theme-config.md` | 主题配置 |
| `references/doc-templates.md` | 文档模板 |
| `references/markdown-import.md` | Markdown 导入详解 |
| `references/folder-sync.md` | 文件夹同步方案 |
| `references/api-*.md` | 1.0 REST API 各接口详细文档 |
| `references/examples.md` | 1.0 端到端完整示例 |

## 辅助资源

| 资源 | 说明 |
|------|------|
| `assets/lexiang-block-schema.json` | Block Schema JSON 定义 |
| `assets/examples/` | Block 结构示例文件 |
| `assets/themes/` | 主题配置文件 |
| `scripts/docs-v1.py` | 1.0 文档 CLI 工具 |
| `scripts/assets-v1.py` | 1.0 图片 CLI 工具 |
| `scripts/upload-files.py` | 批量文件上传脚本 |
| `scripts/sync-folder.ts` | 文件夹增量同步 |
