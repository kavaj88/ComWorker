# 搜索与阅读

> 知识库搜索、浏览、目录导航、文档内容读取（只读操作）。

---

## 工具概览

### 🔍 搜索与发现
- `search_kb_search` — 关键词搜索
- `search_kb_embedding_search` — 语义向量搜索
- `team_list_teams` / `team_describe_team` / `team_list_frequent_teams` — 团队查询
- `space_list_spaces` / `space_describe_space` / `space_list_recently_spaces` — 知识库查询

### 📖 条目与结构
- `entry_list_children` — 浏览目录结构
- `entry_describe_entry` — 获取条目元信息（不含正文）
- `entry_describe_ai_parse_content` — 获取 AI 解析内容（**含正文**）
- `entry_list_parents` — 获取父级路径（面包屑）
- `entry_list_latest_entries` — 获取最近更新条目

---

## 搜索策略

| 工具 | 适用场景 |
|------|----------|
| `search_kb_search` | 精确关键词匹配 |
| `search_kb_embedding_search` | 模糊查询、"记得大意但忘了标题" |

**建议**：语义搜索召回后，再用 `entry_describe_ai_parse_content` 精确读取。

### 搜索结果链接

| target_type | URL 格式 |
|-------------|----------|
| `kb_page` | `{domain}/pages/{target_id}` |
| `kb_file` / `kb_video` | `{domain}/teams/{team_id}/docs/{target_id}` |

---

## 内容读取

| 工具 | 返回 | 用途 |
|------|------|------|
| `entry_describe_entry` | 元信息（ID/名称/类型） | 基本信息 |
| `entry_describe_ai_parse_content` | **正文内容** | 读取分析 |
