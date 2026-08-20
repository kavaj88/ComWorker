# 文档写入

> 创建新文档/页面/文件夹、Markdown/HTML 导入、外部链接导入。

---

## 工具概览

- `entry_create_entry` — 创建文档/文件夹
- `entry_import_content` — 导入 Markdown/HTML 创建新文档（⚠️ 仅新建）
- `entry_import_content_to_entry` — 导入内容到已有页面（覆盖/追加）
- `entry_rename_entry` — 重命名条目
- `file_create_hyperlink` — 导入公众号文章等外部链接

---

## 常见操作流程

### 从知识库链接写入文档

```
Step 1: 从 URL 提取 space_id
Step 2: space_describe_space(space_id) → 获取 root_entry_id
Step 3: entry_import_content(space_id, parent_id=root_entry_id, name, content, content_type="markdown")
Step 4: 返回访问链接 {domain}/pages/{entry_id}
```

> `space_id` 和 `parent_id` 要同时传；`parent_id` 用 `root_entry_id` 表示写入根目录。

### 创建文档

```
entry_create_entry(name="技术文档", parent_entry_id="abc123", entry_type="page")
```

### 导入 Markdown

```
entry_import_content(parent_id="folder123", name="技术文档", content="...", content_type="markdown")
```

### 微信公众号导入

用户提供 `mp.weixin.qq.com` 链接且意图是"导入/收藏/保存到乐享"时：
```
file_create_hyperlink(url="...", space_id="...", parent_entry_id="...")
```

> 如果用户只想阅读/总结内容，不要默认导入。

---

## 注意事项

1. `entry_import_content` 的 `parent_id` 通常用 `root_entry_id`
2. 支持的 `content_type`：`markdown`、`html`
3. 上传 PDF/Word/图片等二进制文件 → 走 `modules/files.md`
