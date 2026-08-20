"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Card, CardHeader, CardTitle, CardAction, CardContent } from "@/components/ui/card";
import { Send, Pencil, Trash2, Plus } from "lucide-react";
import {
  listAgents,
  createAgent,
  updateAgent,
  deleteAgent,
  adminPushAgentToAllUsers,
  adminAssignAgentToUser,
  pushAgentToUsers,
  type Agent,
} from "@/lib/api";
import UserPickerModal from "@/components/UserPickerModal";
import PushUsersModal from "@/components/PushUsersModal";
import type { UserSummary } from "@/types";

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [editing, setEditing] = useState<Agent | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showUserPicker, setShowUserPicker] = useState(false);
  const [showPushUsers, setShowPushUsers] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const res = await listAgents();
      setAgents(res.agents);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return agents.filter(
      (a) =>
        !q ||
        a.name.toLowerCase().includes(q) ||
        a.agent_id.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q),
    );
  }, [agents, query]);

  const selected = useMemo(
    () => agents.find((a) => a.agent_id === selectedId) || null,
    [agents, selectedId],
  );

  async function remove(a: Agent) {
    if (!confirm(`确定删除专家「${a.name}」？`)) return;
    await deleteAgent(a.agent_id);
    setAgents((xs) => xs.filter((x) => x.agent_id !== a.agent_id));
  }
  async function push(a: Agent) {
    setBusyId(a.agent_id);
    try {
      const res = await adminPushAgentToAllUsers(a.agent_id);
      setMessage(
        `已推送「${a.name}」：${res.pushed}/${res.total} 成功` +
          (res.failed.length ? `，${res.failed.length} 失败` : ""),
      );
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "推送失败");
    } finally {
      setBusyId(null);
    }
  }
  async function assignToUser(user: UserSummary) {
    if (!selected) return;
    setShowUserPicker(false);
    setBusyId(selected.agent_id);
    try {
      const res = await adminAssignAgentToUser(user.id, selected.agent_id);
      setMessage(`已将「${selected.name}」指派给 ${user.username}（${res.status}）`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "指派失败");
    } finally {
      setBusyId(null);
    }
  }

  async function handlePushToUsers(userIds: string[]) {
    if (!selected) return;
    const res = await pushAgentToUsers(selected.agent_id, userIds);
    setMessage(
      `已推送「${selected.name}」：${res.pushed}/${res.total} 成功` +
        (res.failed.length ? `，${res.failed.length} 失败` : ""),
    );
  }

  return (
    <div className="p-8">
      <div className="mb-4">
        <h1 className="text-2xl font-bold">专家管理</h1>
        <p className="text-sm text-muted-foreground mt-1">
          管理平台专家（agent）模板。选中卡片后在上方工具栏操作；可推送给所有用户，或指派给指定用户（在其容器内创建专家档案）。
        </p>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索专家名称 / 描述..."
          className="w-full max-w-xs rounded-md border px-3 py-2 text-sm"
        />
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <button
            title="推送给所有用户"
            disabled={!selected || busyId === selected.agent_id}
            onClick={() => selected && push(selected)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" /> 推送
          </button>
          <button
            title="指派给指定用户"
            disabled={!selected || busyId === selected.agent_id}
            onClick={() => setShowUserPicker(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" /> 推送给用户
          </button>
          <button
            title="推送给指定用户（多选）"
            disabled={!selected || busyId === selected.agent_id}
            onClick={() => setShowPushUsers(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" /> 推送给指定用户
          </button>
          <button
            title="编辑"
            disabled={!selected || selected.readonly}
            onClick={() => {
              if (selected) {
                setEditing(selected);
                setShowForm(true);
              }
            }}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Pencil className="h-4 w-4" /> 编辑
          </button>
          <button
            title="删除"
            disabled={!selected || selected.readonly}
            onClick={() => selected && remove(selected)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-red-500 disabled:opacity-40"
          >
            <Trash2 className="h-4 w-4" /> 删除
          </button>
          <button
            onClick={() => {
              setEditing(null);
              setShowForm(true);
            }}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-2 text-sm font-medium"
          >
            <Plus className="h-4 w-4" /> 新建专家
          </button>
        </div>
      </div>

      {selected && (
        <div className="mb-3 rounded-md bg-primary/5 px-3 py-2 text-sm">
          已选中：<b>{selected.name}</b>（{selected.agent_id}）
          {selected.builtin && <span className="ml-2 text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">内置</span>}
          <button className="ml-2 text-muted-foreground hover:text-foreground" onClick={() => setSelectedId(null)}>
            取消选择
          </button>
        </div>
      )}

      {message && <div className="mb-3 rounded-md bg-muted px-3 py-2 text-sm">{message}</div>}

      {loading ? (
        <p className="text-sm text-muted-foreground">加载中...</p>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground">没有匹配的专家。</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-3">
          {filtered.map((a) => (
            <div
              key={a.agent_id}
              data-agent-id={a.agent_id}
              onClick={() => setSelectedId(a.agent_id)}
              className={`cursor-pointer rounded-lg transition-colors ${
                selectedId === a.agent_id ? "ring-2 ring-primary" : ""
              }`}
            >
              <Card size="sm">
                <CardHeader>
                  <CardTitle className="truncate text-sm">{a.name}</CardTitle>
                  <CardAction className="flex flex-wrap justify-end gap-1.5">
                    {a.builtin && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">内置</span>
                    )}
                    {a.is_default && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">默认</span>
                    )}
                    {!a.is_enabled && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">停用</span>
                    )}
                  </CardAction>
                </CardHeader>
                <CardContent className="pt-1">
                  <p className="text-xs text-muted-foreground line-clamp-4">{a.description || "暂无描述"}</p>
                  <p className="mt-2 text-[10px] text-muted-foreground/70">ID：{a.agent_id}</p>
                </CardContent>
              </Card>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <AgentForm
          agent={editing}
          onClose={() => setShowForm(false)}
          onSaved={() => {
            setShowForm(false);
            void load();
          }}
        />
      )}

      <UserPickerModal
        open={showUserPicker}
        title="指派专家给指定用户"
        onClose={() => setShowUserPicker(false)}
        onSelect={assignToUser}
      />

      <PushUsersModal
        open={showPushUsers}
        title="推送专家给指定用户（多选）"
        confirmLabel="推送"
        onClose={() => setShowPushUsers(false)}
        onPush={handlePushToUsers}
      />
    </div>
  );
}

function AgentForm({
  agent,
  onClose,
  onSaved,
}: {
  agent: Agent | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    agent_id: agent?.agent_id || "",
    name: agent?.name || "",
    description: agent?.description || "",
    avatar: agent?.avatar || "",
    system_prompt: agent?.system_prompt || "",
    is_default: agent?.is_default || false,
    is_enabled: agent?.is_enabled ?? true,
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const isEdit = !!agent;

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function save() {
    setBusy(true);
    setErr("");
    const payload: Record<string, unknown> = {
      name: form.name,
      description: form.description,
      avatar: form.avatar || null,
      system_prompt: form.system_prompt,
      is_default: form.is_default,
      is_enabled: form.is_enabled,
    };
    if (!isEdit) payload.agent_id = form.agent_id;
    try {
      if (isEdit) await updateAgent(agent!.agent_id, payload);
      else await createAgent(payload as Parameters<typeof createAgent>[0]);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold mb-3">{isEdit ? "编辑专家" : "新建专家"}</h2>
        <div className="space-y-3 max-h-[70vh] overflow-y-auto">
          <Field label="技术标识（agent id，小写字母/数字/-/_，不可与内置 agent 重名）">
            <input
              disabled={isEdit}
              value={form.agent_id}
              onChange={(e) => set("agent_id", e.target.value)}
              placeholder="例如：test_expert"
              className="w-full rounded border px-2 py-1.5 text-sm font-mono"
            />
          </Field>
          <Field label="显示名称（对外称呼，如「小助手」）">
            <input
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="对外展示的称呼"
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="描述（可选）">
            <input
              value={form.description}
              onChange={(e) => set("description", e.target.value)}
              placeholder="一句话介绍这个专家的用途"
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="头像（可选，图片 URL）">
            <input
              value={form.avatar}
              onChange={(e) => set("avatar", e.target.value)}
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="人设 / 系统提示词（SOUL）">
            <textarea
              value={form.system_prompt}
              onChange={(e) => set("system_prompt", e.target.value)}
              rows={6}
              placeholder={"例如：\n你是一名资深产品经理，擅长把模糊需求拆解成清晰的 PRD，语气专业、简洁。"}
              className="w-full rounded border px-2 py-1.5 text-sm font-mono"
            />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.is_default} onChange={(e) => set("is_default", e.target.checked)} />
            设为默认专家
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.is_enabled} onChange={(e) => set("is_enabled", e.target.checked)} />
            启用
          </label>
        </div>
        {err && <p className="mt-2 text-sm text-red-600">{err}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded border px-3 py-1.5 text-sm">
            取消
          </button>
          <button
            onClick={save}
            disabled={busy}
            className="rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
          >
            {busy ? "保存中..." : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}
