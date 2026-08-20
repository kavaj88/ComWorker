"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Card, CardHeader, CardTitle, CardAction, CardContent } from "@/components/ui/card";
import { ToggleLeft, ToggleRight, Pencil, Trash2, Send, Plus } from "lucide-react";
import {
  getAdminConnectors,
  createConnector,
  updateConnector,
  deleteConnector,
  setConnectorDefault,
  setConnectorMandatory,
  pushConnectorToAllUsers,
  adminInstallConnectorForUser,
  pushConnectorToUsers,
  type Connector,
} from "@/lib/api";
import UserPickerModal from "@/components/UserPickerModal";
import PushUsersModal from "@/components/PushUsersModal";
import type { UserSummary } from "@/types";

export default function ConnectorsCatalogPage() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [editing, setEditing] = useState<Connector | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "active" | "inactive">("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showUserPicker, setShowUserPicker] = useState(false);
  const [showPushUsers, setShowPushUsers] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const res = await getAdminConnectors();
      setConnectors(res.connectors);
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
    return connectors.filter((c) => {
      const matchQ =
        !q ||
        c.name.toLowerCase().includes(q) ||
        c.display_name.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q);
      const matchF =
        filter === "all" ||
        (filter === "active" ? c.status === "active" : c.status !== "active");
      return matchQ && matchF;
    });
  }, [connectors, query, filter]);

  const selected = useMemo(
    () => connectors.find((c) => c.id === selectedId) || null,
    [connectors, selectedId],
  );

  async function toggleDefault(c: Connector) {
    setBusyId(c.id);
    try {
      await setConnectorDefault(c.id, !c.is_default);
      setConnectors((cs) => cs.map((x) => (x.id === c.id ? { ...x, is_default: !x.is_default } : x)));
    } finally {
      setBusyId(null);
    }
  }
  async function toggleMandatory(c: Connector) {
    setBusyId(c.id);
    try {
      await setConnectorMandatory(c.id, !c.is_mandatory);
      setConnectors((cs) => cs.map((x) => (x.id === c.id ? { ...x, is_mandatory: !x.is_mandatory } : x)));
    } finally {
      setBusyId(null);
    }
  }
  async function remove(c: Connector) {
    if (!confirm(`确定删除连接器「${c.display_name}」？`)) return;
    await deleteConnector(c.id);
    setConnectors((cs) => cs.filter((x) => x.id !== c.id));
  }
  async function push(c: Connector) {
    setBusyId(c.id);
    try {
      const res = await pushConnectorToAllUsers(c.id);
      setMessage(
        `已推送「${c.display_name}」：${res.pushed}/${res.total} 成功` +
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
    setBusyId(selected.id);
    try {
      const res = await adminInstallConnectorForUser(user.id, selected.id);
      setMessage(`已将连接器「${selected.display_name}」代装给 ${user.username}`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "代装失败");
    } finally {
      setBusyId(null);
    }
  }

  async function handlePushToUsers(userIds: string[]) {
    if (!selected) return;
    const res = await pushConnectorToUsers(selected.id, userIds);
    setMessage(
      `已推送「${selected.display_name}」：${res.pushed}/${res.total} 成功` +
        (res.failed.length ? `，${res.failed.length} 失败` : ""),
    );
  }

  return (
    <div className="p-8">
      <div className="mb-4">
        <h1 className="text-2xl font-bold">连接器管理</h1>
        <p className="text-sm text-muted-foreground mt-1">
          管理官方 MCP 连接器目录。选中卡片后在上方工具栏操作；开启「默认」后新用户自动获得；开启「强制」则对所有用户生效（用户不可关闭）。
        </p>
      </div>

      {/* 工具栏：搜索框 + 状态筛选 + 操作按钮，全部与搜索框齐平 */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索连接器名称 / 标题 / 描述..."
          className="w-full max-w-xs rounded-md border px-3 py-2 text-sm"
        />
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value as "all" | "active" | "inactive")}
          className="rounded-md border px-3 py-2 text-sm"
        >
          <option value="all">全部状态</option>
          <option value="active">已启用</option>
          <option value="inactive">已停用</option>
        </select>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <button
            title="默认安装（新用户）"
            disabled={!selected || busyId === selected.id}
            onClick={() => selected && toggleDefault(selected)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            {selected?.is_default ? <ToggleRight className="h-4 w-4 text-primary" /> : <ToggleLeft className="h-4 w-4" />}
            默认
          </button>
          <button
            title="强制所有用户"
            disabled={!selected || busyId === selected.id}
            onClick={() => selected && toggleMandatory(selected)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            {selected?.is_mandatory ? <ToggleRight className="h-4 w-4 text-amber-500" /> : <ToggleLeft className="h-4 w-4" />}
            强制
          </button>
          <button
            title="推送给所有用户"
            disabled={!selected || busyId === selected.id}
            onClick={() => selected && push(selected)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" /> 推送
          </button>
          <button
            title="代装给指定用户"
            disabled={!selected || busyId === selected.id}
            onClick={() => setShowUserPicker(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" /> 推送给用户
          </button>
          <button
            title="推送给指定用户（多选）"
            disabled={!selected || busyId === selected.id}
            onClick={() => setShowPushUsers(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" /> 推送给指定用户
          </button>
          <button
            title="编辑"
            disabled={!selected}
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
            disabled={!selected}
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
            <Plus className="h-4 w-4" /> 新建连接器
          </button>
        </div>
      </div>

      {selected && (
        <div className="mb-3 rounded-md bg-primary/5 px-3 py-2 text-sm">
          已选中：<b>{selected.display_name}</b>（{selected.name}）· {selected.status === "active" ? "启用" : "停用"}
          <button
            className="ml-2 text-muted-foreground hover:text-foreground"
            onClick={() => setSelectedId(null)}
          >
            取消选择
          </button>
        </div>
      )}

      {message && <div className="mb-3 rounded-md bg-muted px-3 py-2 text-sm">{message}</div>}

      {loading ? (
        <p className="text-sm text-muted-foreground">加载中...</p>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground">没有匹配的连接器。</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-3">
          {filtered.map((c) => (
            <div
              key={c.id}
              data-connector-id={c.id}
              onClick={() => setSelectedId(c.id)}
              className={`cursor-pointer rounded-lg transition-colors ${
                selectedId === c.id ? "ring-2 ring-primary" : ""
              }`}
            >
              <Card size="sm">
                <CardHeader>
                  <CardTitle className="truncate text-sm">{c.display_name}</CardTitle>
                  <CardAction className="flex flex-wrap justify-end gap-1.5">
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded ${
                        c.status === "active" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"
                      }`}
                    >
                      {c.status === "active" ? "启用" : "停用"}
                    </span>
                    {c.is_default && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">默认</span>
                    )}
                    {c.is_mandatory && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">强制</span>
                    )}
                    {c.has_shared_credential && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">已配密钥</span>
                    )}
                  </CardAction>
                </CardHeader>
                <CardContent className="pt-1">
                  <p className="text-xs text-muted-foreground line-clamp-4">{c.description || "暂无描述"}</p>
                  <p className="mt-2 text-[10px] text-muted-foreground/70">
                    类型：{c.transport} · 凭证：{c.credential_strategy}
                  </p>
                </CardContent>
              </Card>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <ConnectorForm
          connector={editing}
          onClose={() => setShowForm(false)}
          onSaved={() => {
            setShowForm(false);
            void load();
          }}
        />
      )}

      <UserPickerModal
        open={showUserPicker}
        title="代装连接器给指定用户"
        onClose={() => setShowUserPicker(false)}
        onSelect={assignToUser}
      />

      <PushUsersModal
        open={showPushUsers}
        title="推送连接器给指定用户（多选）"
        confirmLabel="推送"
        onClose={() => setShowPushUsers(false)}
        onPush={handlePushToUsers}
      />
    </div>
  );
}

function ConnectorForm({
  connector,
  onClose,
  onSaved,
}: {
  connector: Connector | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    name: connector?.name || "",
    display_name: connector?.display_name || "",
    description: connector?.description || "",
    transport: connector?.transport || "streamable_http",
    credential_strategy: connector?.credential_strategy || "none",
    config_json: connector ? JSON.stringify(connector.config_json, null, 2) : "{}",
    cli_config_json: connector?.cli_config_json ? JSON.stringify(connector.cli_config_json, null, 2) : "{}",
    examples: connector?.examples || "",
    shared_credential: "",
    is_default: connector?.is_default || false,
    is_mandatory: connector?.is_mandatory || false,
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const isEdit = !!connector;

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function save() {
    setBusy(true);
    setErr("");
    let cfg: Record<string, unknown>;
    try {
      cfg = JSON.parse(form.config_json || "{}");
    } catch {
      setErr("config_json 不是合法 JSON");
      setBusy(false);
      return;
    }
    let cliCfg: Record<string, unknown> = {};
    if (form.credential_strategy === "cli") {
      try {
        cliCfg = JSON.parse(form.cli_config_json || "{}");
      } catch {
        setErr("cli_config_json 不是合法 JSON");
        setBusy(false);
        return;
      }
    }
    const payload: Record<string, unknown> = {
      display_name: form.display_name,
      description: form.description,
      transport: form.transport,
      credential_strategy: form.credential_strategy,
      config_json: cfg,
      is_default: form.is_default,
      is_mandatory: form.is_mandatory,
    };
    if (form.credential_strategy === "cli") payload.cli_config_json = cliCfg;
    if (form.examples) payload.examples = form.examples;
    if (!isEdit) payload.name = form.name;
    if (form.shared_credential) payload.shared_credential = form.shared_credential;
    try {
      if (isEdit) await updateConnector(connector!.id, payload as Partial<Connector>);
      else await createConnector(payload as Partial<Connector> & { name: string; display_name: string });
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
        <h2 className="text-lg font-semibold mb-3">{isEdit ? "编辑连接器" : "新建连接器"}</h2>
        <div className="space-y-3 max-h-[70vh] overflow-y-auto">
          <Field label="名称 (server key，小写字母/数字/-)">
            <input
              disabled={isEdit}
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="显示名称">
            <input
              value={form.display_name}
              onChange={(e) => set("display_name", e.target.value)}
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="描述（尽量详细，会展示给用户）">
            <textarea
              value={form.description}
              onChange={(e) => set("description", e.target.value)}
              rows={3}
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="示例话术（可选，客户端卡片展开展示，每行一句用户能说的话）">
            <textarea
              value={form.examples}
              onChange={(e) => set("examples", e.target.value)}
              rows={3}
              placeholder={"例如：\n查询我通达信自选股的实时行情\n把自选股导出成 csv"}
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="传输类型">
              <select
                value={form.transport}
                onChange={(e) => set("transport", e.target.value)}
                className="w-full rounded border px-2 py-1.5 text-sm"
              >
                <option value="streamable_http">streamable_http</option>
                <option value="sse">sse</option>
                <option value="stdio">stdio</option>
              </select>
            </Field>
            <Field label="凭证策略">
              <select
                value={form.credential_strategy}
                onChange={(e) => set("credential_strategy", e.target.value)}
                className="w-full rounded border px-2 py-1.5 text-sm"
              >
                <option value="none">none（无需凭证，纯 URL）</option>
                <option value="shared">shared（平台统一共享密钥）</option>
                <option value="api_key">api_key（用户自填密钥，支持 ${"{VAR}"} 占位）</option>
                <option value="oauth">oauth（OAuth2 授权登录）</option>
                <option value="cli">cli（原生 CLI，扫码/授权）</option>
              </select>
            </Field>
          </div>
          <Field label="连接配置 (JSON：url / command / args / env / headers)">
            <textarea
              value={form.config_json}
              onChange={(e) => set("config_json", e.target.value)}
              rows={5}
              className="w-full rounded border px-2 py-1.5 text-sm font-mono"
            />
          </Field>
          {form.credential_strategy === "cli" && (
            <Field label="CLI 配置 (JSON：init / auth / unAuth / status 命令；authQrModal 是否弹二维码)">
              <textarea
                value={form.cli_config_json}
                onChange={(e) => set("cli_config_json", e.target.value)}
                rows={5}
                className="w-full rounded border px-2 py-1.5 text-sm font-mono"
              />
            </Field>
          )}
          {form.credential_strategy === "oauth" && (
            <p className="rounded bg-blue-50 px-2 py-1.5 text-xs text-blue-700">
              OAuth 连接器无需在此填写密钥：用户在客户端「连接器商店」点击「授权」后，由容器内 OAuth 中继完成登录并保存 token。
            </p>
          )}
          <Field label="共享凭证 (shared 策略填写，不回显明文)">
            <input
              type="password"
              value={form.shared_credential}
              onChange={(e) => set("shared_credential", e.target.value)}
              placeholder={connector?.has_shared_credential ? "（已配置，留空表示不修改）" : ""}
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.is_default}
              onChange={(e) => set("is_default", e.target.checked)}
            />
            默认对新用户启用
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.is_mandatory}
              onChange={(e) => set("is_mandatory", e.target.checked)}
            />
            强制对所有用户启用（不可关闭）
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
