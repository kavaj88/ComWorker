"use client";

import { useEffect, useMemo, useState, useRef } from "react";
import { Loader2, Upload, Trash2, Pencil, ToggleLeft, ToggleRight, Send, Users, Package, UserPlus } from "lucide-react";
import {
  getPlatformSkills,
  uploadPlatformSkill,
  deleteSkill,
  updateSkill,
  togglePlatformSkill,
  adminPushSkillToAllUsers,
  adminInstallSkillToUser,
  getAdminCatalogSkills,
  getDefaultInstall,
  setSkillDefaultInstall,
  getAdminUserSkills,
  adminInstallManagedSkill,
  adminUninstallUserSkill,
  getUsers,
  pushSkillToUsers,
  type PlatformSkill,
  type CatalogSkillsResponse,
  type SkillEditBody,
} from "@/lib/api";
import type { UserSummary } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import UserPickerModal from "@/components/UserPickerModal";
import PushUsersModal from "@/components/PushUsersModal";

type UnifiedSkill = {
  key: string;
  name: string;
  title: string;
  description: string;
  category: string;
  source: "platform" | "builtin";
  enabled: boolean;
  overridden: boolean;
};

const SELF_BUILT = "自建技能";

export default function SkillsPage() {
  const [platformSkills, setPlatformSkills] = useState<PlatformSkill[]>([]);
  const [catalog, setCatalog] = useState<CatalogSkillsResponse | null>(null);
  const [defaultInstall, setDefaultInstallState] = useState<{ enabled: boolean; overrides: Record<string, boolean> }>({ enabled: true, overrides: {} });
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState("");
  const [query, setQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [showUserPicker, setShowUserPicker] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [showPerUser, setShowPerUser] = useState(false);
  const [showPushUsers, setShowPushUsers] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    setLoading(true);
    try {
      const [ps, cat, di] = await Promise.all([
        getPlatformSkills(),
        getAdminCatalogSkills(),
        getDefaultInstall(),
      ]);
      setPlatformSkills(ps.skills);
      setCatalog(cat);
      setDefaultInstallState({ enabled: di.enabled, overrides: di.overrides || {} });
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const unified = useMemo<UnifiedSkill[]>(() => {
    const list: UnifiedSkill[] = [];
    for (const p of platformSkills) {
      list.push({
        key: `platform:${p.name}`,
        name: p.name,
        title: p.title || p.name,
        description: p.description || "",
        category: SELF_BUILT,
        source: "platform",
        enabled: p.enabled,
        overridden: false,
      });
    }
    if (catalog) {
      for (const [name, meta] of Object.entries(catalog.skills)) {
        const effectiveDefault = name in defaultInstall.overrides ? defaultInstall.overrides[name] : defaultInstall.enabled;
        list.push({
          key: `builtin:${name}`,
          name,
          title: meta.title || name,
          description: meta.description || "",
          category: meta.category || "未分类",
          source: "builtin",
          enabled: effectiveDefault,
          overridden: !!meta.overridden,
        });
      }
    }
    return list;
  }, [platformSkills, catalog, defaultInstall]);

  const categories = useMemo(() => {
    const set = new Set<string>([SELF_BUILT]);
    catalog?.categories.forEach((c) => set.add(c));
    return Array.from(set);
  }, [catalog]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return unified.filter((s) => {
      const matchQ = !q || s.name.toLowerCase().includes(q) || s.title.toLowerCase().includes(q) || s.description.toLowerCase().includes(q);
      const matchC = activeCategory === null || s.category === activeCategory;
      return matchQ && matchC;
    });
  }, [unified, query, activeCategory]);

  const selectedSkills = useMemo(() => unified.filter((s) => selectedKeys.has(s.key)), [unified, selectedKeys]);
  const singleSelected = selectedSkills.length === 1 ? selectedSkills[0] : null;
  const allSelected = filtered.length > 0 && filtered.every((s) => selectedKeys.has(s.key));
  const someSelected = filtered.some((s) => selectedKeys.has(s.key));
  const toggleSelect = (key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const toggleSelectAll = () => {
    setSelectedKeys((prev) => {
      if (filtered.every((s) => prev.has(s.key))) {
        const next = new Set(prev);
        for (const s of filtered) next.delete(s.key);
        return next;
      }
      const next = new Set(prev);
      for (const s of filtered) next.add(s.key);
      return next;
    });
  };
  const clearSelection = () => setSelectedKeys(new Set());

  async function handleToggleDefault() {
    setBatchBusy(true);
    try {
      for (const skill of selectedSkills) {
        if (skill.source === "platform") {
          await togglePlatformSkill(skill.name, !skill.enabled);
        } else {
          await setSkillDefaultInstall(skill.name, !skill.enabled);
        }
      }
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBatchBusy(false);
    }
  }

  async function handleDelete() {
    if (!confirm(`确定删除选中的 ${selectedSkills.length} 个技能？内置技能将标记为已删除，不再展示。`)) return;
    setBatchBusy(true);
    try {
      for (const skill of selectedSkills) {
        const res = await deleteSkill(skill.name);
        setMessage(res.kind === "builtin" ? `已将内置技能 "${res.name}" 标记为删除` : `已删除技能 "${res.name}"`);
      }
      clearSelection();
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBatchBusy(false);
    }
  }

  async function handlePushToAll() {
    if (!confirm(`确定把选中的 ${selectedSkills.length} 个技能推送给所有现存用户？`)) return;
    setBatchBusy(true);
    setMessage("");
    try {
      for (const skill of selectedSkills) {
        const res = await adminPushSkillToAllUsers(skill.name);
        let msg = `已推送 "${res.name}"：${res.pushed}/${res.total} 个用户成功`;
        if (res.failed.length) msg += `，${res.failed.length} 个失败`;
        setMessage(msg);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "推送失败");
    } finally {
      setBatchBusy(false);
    }
  }

  async function handleAssignToUser(user: UserSummary) {
    if (selectedSkills.length === 0) return;
    setShowUserPicker(false);
    setBatchBusy(true);
    try {
      for (const skill of selectedSkills) {
        await adminInstallSkillToUser(user.id, skill.name);
      }
      setMessage(`已为 ${user.username} 安装 ${selectedSkills.length} 个技能（系统级，用户不可卸载）`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "安装失败");
    } finally {
      setBatchBusy(false);
    }
  }

  async function handlePushToUsers(userIds: string[]) {
    if (selectedSkills.length === 0) return;
    setBatchBusy(true);
    try {
      for (const skill of selectedSkills) {
        const res = await pushSkillToUsers(skill.name, userIds);
        let msg = `已推送「${res.name}」：${res.pushed}/${res.total} 个用户成功`;
        if (res.failed.length) msg += `，${res.failed.length} 个失败`;
        setMessage(msg);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "推送失败");
    } finally {
      setBatchBusy(false);
    }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setMessage("");
    try {
      await uploadPlatformSkill(file);
      await load();
      setMessage(`已上传 ${file.name}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-8 text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" /> 加载中...
      </div>
    );
  }

  return (
    <div className="p-8 space-y-6">
      <div className="mb-2">
        <h1 className="text-2xl font-bold">技能管理</h1>
        <p className="text-sm text-gray-500 mt-1">
          统一管理内置技能与自建技能。选中卡片后在上方工具栏操作；内置技能可编辑/删除/推送/指派，自建技能归为「自建技能」分类。
        </p>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="inline-flex items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-gray-300 accent-blue-600"
            checked={allSelected}
            ref={(el) => { if (el) el.indeterminate = !allSelected && someSelected; }}
            onChange={() => toggleSelectAll()}
            disabled={filtered.length === 0}
          />
          全选
        </label>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索技能名称 / 标题 / 描述..."
          className="w-full max-w-xs rounded-md border px-3 py-2 text-sm"
        />
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <button
            title="默认安装（新用户）"
            disabled={selectedKeys.size === 0 || batchBusy}
            onClick={() => handleToggleDefault()}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            {singleSelected?.enabled ? <ToggleRight className="h-4 w-4 text-green-600" /> : <ToggleLeft className="h-4 w-4" />}
            {singleSelected?.enabled ? "取消默认" : "设默认"}
          </button>
          <button
            title="编辑"
            disabled={selectedKeys.size !== 1 || batchBusy}
            onClick={() => { setShowEdit(true); }}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Pencil className="h-4 w-4" /> 编辑
          </button>
          <button
            title="推送给所有用户"
            disabled={selectedKeys.size === 0 || batchBusy}
            onClick={() => handlePushToAll()}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Users className="h-4 w-4" /> 推送
          </button>
          <button
            title="推送给指定用户（多选）"
            disabled={selectedKeys.size !== 1 || batchBusy}
            onClick={() => setShowPushUsers(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" /> 推送给指定用户
          </button>
          <button
            title="指派给指定用户"
            disabled={selectedKeys.size !== 1 || batchBusy}
            onClick={() => setShowUserPicker(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" /> 推送给用户
          </button>
          <button
            title="按用户安装/卸载"
            disabled={selectedKeys.size !== 1 || batchBusy}
            onClick={() => setShowPerUser(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <UserPlus className="h-4 w-4" /> 按用户管理
          </button>
          <button
            title="删除"
            disabled={selectedKeys.size === 0 || batchBusy}
            onClick={() => handleDelete()}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-red-500 disabled:opacity-40"
          >
            <Trash2 className="h-4 w-4" /> 删除
          </button>
          <Button onClick={() => fileRef.current?.click()} disabled={uploading} className="relative">
            {uploading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Upload className="h-4 w-4 mr-2" />}
            上传技能
            <input ref={fileRef} type="file" accept=".zip" className="hidden" onChange={handleUpload} />
          </Button>
        </div>
      </div>

      {selectedKeys.size > 0 && (
        <div className="mb-3 rounded-md bg-primary/5 px-3 py-2 text-sm">
          已选中：<b>{selectedKeys.size}</b> 个技能
          <button className="ml-2 text-muted-foreground hover:text-foreground" onClick={() => clearSelection()}>
            取消选择
          </button>
        </div>
      )}

      {message && (
        <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">{message}</div>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setActiveCategory(null)}
          className={`text-xs px-3 py-1.5 rounded-full border ${activeCategory === null ? "bg-blue-500 text-white border-blue-500" : "border-gray-300 text-gray-600 hover:bg-gray-50"}`}
        >
          全部
        </button>
        {categories.map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => setActiveCategory(c)}
            className={`text-xs px-3 py-1.5 rounded-full border ${activeCategory === c ? "bg-blue-500 text-white border-blue-500" : "border-gray-300 text-gray-600 hover:bg-gray-50"}`}
          >
            {c}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
        {filtered.length === 0 && (
          <Card className="col-span-full">
            <CardContent className="p-8 text-center text-gray-500">
              <Package className="h-8 w-8 mx-auto mb-2 opacity-40" />
              <p>暂无技能</p>
              <p className="text-xs mt-1">上传 .zip 技能包（需包含 SKILL.md），或等待内置技能加载</p>
            </CardContent>
          </Card>
        )}
        {filtered.map((skill) => {
          const isSel = selectedKeys.has(skill.key);
          return (
          <Card key={skill.key} className={`cursor-pointer transition-shadow hover:shadow-md ${isSel ? "ring-2 ring-primary" : ""}`}>
            <CardContent
              data-skill-name={skill.name}
              data-skill-source={skill.source}
              className="p-4"
              onClick={() => toggleSelect(skill.key)}
            >
              <div className="flex items-start gap-2 mb-2">
                <input
                  type="checkbox"
                  checked={isSel}
                  onChange={() => toggleSelect(skill.key)}
                  onClick={(e) => e.stopPropagation()}
                  className="mt-0.5 h-4 w-4 shrink-0 rounded border-gray-300 accent-blue-600"
                />
                <div className="flex items-start justify-between gap-2 min-w-0 flex-1">
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <Package className="h-4 w-4 text-gray-400 shrink-0" />
                    <span className="font-medium text-sm truncate">{skill.title}</span>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <span className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">
                      {skill.source === "builtin" ? "内置" : "自建"}
                    </span>
                    {skill.source === "builtin" && skill.overridden && (
                      <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">已改</span>
                    )}
                    {skill.enabled ? (
                      <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded">默认安装</span>
                    ) : (
                      <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded">非默认</span>
                    )}
                  </div>
                </div>
              </div>
              {skill.description && <div className="text-xs text-gray-500 line-clamp-2">{skill.description}</div>}
              <div className="text-xs text-gray-400 mt-1.5 truncate">
                分类：{skill.category} · {skill.name}
              </div>
            </CardContent>
          </Card>
          );
        })}
      </div>

      <UserPickerModal
        open={showUserPicker}
        title="指派技能给指定用户"
        onClose={() => setShowUserPicker(false)}
        onSelect={handleAssignToUser}
      />

      <PushUsersModal
        open={showPushUsers}
        title="推送给指定用户（多选）"
        confirmLabel="推送"
        onClose={() => setShowPushUsers(false)}
        onPush={handlePushToUsers}
      />

      {showEdit && singleSelected && (
        <SkillEditModal
          skill={singleSelected}
          onClose={() => setShowEdit(false)}
          onSaved={async () => {
            setShowEdit(false);
            await load();
          }}
        />
      )}

      {showPerUser && singleSelected && (
        <PerUserModal
          skill={singleSelected}
          onClose={() => setShowPerUser(false)}
        />
      )}
    </div>
  );
}

function SkillEditModal({ skill, onClose, onSaved }: { skill: UnifiedSkill; onClose: () => void; onSaved: () => Promise<void> }) {
  const [title, setTitle] = useState(skill.title === skill.name ? "" : skill.title);
  const [description, setDescription] = useState(skill.description);
  const [category, setCategory] = useState(skill.category === "未分类" ? "" : skill.category);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function save() {
    setBusy(true);
    setErr("");
    const body: SkillEditBody = {
      title: title.trim() || null,
      description: description.trim() || null,
      category: category.trim() || null,
      content: skill.source === "builtin" ? (content || null) : null,
    };
    try {
      await updateSkill(skill.name, body);
      await onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold mb-3">编辑技能（{skill.source === "builtin" ? "内置" : "自建"}）</h2>
        <div className="space-y-3 max-h-[70vh] overflow-y-auto">
          <label className="block text-sm">
            <span className="text-gray-600">标题</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1 w-full rounded border px-2 py-1.5 text-sm" placeholder={skill.name} />
          </label>
          <label className="block text-sm">
            <span className="text-gray-600">描述</span>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="mt-1 w-full rounded border px-2 py-1.5 text-sm" />
          </label>
          <label className="block text-sm">
            <span className="text-gray-600">分类</span>
            <input value={category} onChange={(e) => setCategory(e.target.value)} className="mt-1 w-full rounded border px-2 py-1.5 text-sm" placeholder="如：效率 / 写作" />
          </label>
          {skill.source === "builtin" && (
            <label className="block text-sm">
              <span className="text-gray-600">SKILL.md 内容（可选，覆盖内置内容；仅管理端元数据，不影响运行时执行）</span>
              <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={6} className="mt-1 w-full rounded border px-2 py-1.5 text-sm font-mono" placeholder="留空则不修改内置 SKILL.md" />
            </label>
          )}
          {err && <div className="text-sm text-red-600">{err}</div>}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={save} disabled={busy}>{busy ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}保存</Button>
        </div>
      </div>
    </div>
  );
}

function PerUserModal({ skill, onClose }: { skill: UnifiedSkill; onClose: () => void }) {
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [userId, setUserId] = useState("");
  const [state, setState] = useState<{ user: string[]; managed: string[] }>({ user: [], managed: [] });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    getUsers(1, 500).then((r) => setUsers(r.items || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (userId) loadState();
    else setState({ user: [], managed: [] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function loadState() {
    try {
      const res = await getAdminUserSkills(userId);
      setState({ user: res.user, managed: res.managed });
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  const installed = state.user.includes(skill.name) || state.managed.includes(skill.name);

  async function install() {
    setBusy(true);
    setMsg("");
    try {
      await adminInstallManagedSkill(userId, skill.name);
      await loadState();
      setMsg(`已为所选用户安装「${skill.name}」（系统级）`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "安装失败");
    } finally {
      setBusy(false);
    }
  }

  async function uninstall() {
    setBusy(true);
    setMsg("");
    try {
      await adminUninstallUserSkill(userId, skill.name);
      await loadState();
      setMsg(`已卸载所选用户的「${skill.name}」`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "卸载失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold mb-3">按用户管理：{skill.title}</h2>
        <div className="space-y-3">
          <select value={userId} onChange={(e) => setUserId(e.target.value)} className="w-full border rounded px-3 py-2 text-sm bg-white">
            <option value="">选择目标用户...</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.username}（{u.email || u.role}）</option>
            ))}
          </select>
          {userId && (
            <div className="text-sm text-gray-600">
              当前状态：{installed ? (state.managed.includes(skill.name) ? "系统已安装" : "用户已安装") : "未安装"}
            </div>
          )}
          {msg && <div className="text-sm text-blue-700">{msg}</div>}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>关闭</Button>
          <Button variant="outline" disabled={!userId || busy} onClick={uninstall}>卸载</Button>
          <Button disabled={!userId || busy} onClick={install}>{busy ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}安装</Button>
        </div>
      </div>
    </div>
  );
}
