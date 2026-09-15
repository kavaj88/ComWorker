"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Loader2,
  Pencil,
  ToggleLeft,
  ToggleRight,
  Send,
  UserPlus,
  Trash2,
  Plus,
  Package,
  AlertTriangle,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import {
  listCapabilities,
  getCapabilityDefaults,
  putCapabilityDefaults,
  createCapability,
  updateCapability,
  deleteCapability,
  pushCapabilityToUsers,
  getCapabilityUsers,
  putUserCapabilities,
  applyUserCapabilities,
  type CapabilityCreateBody,
  type CapabilityUpdateBody,
} from "@/lib/api";
import type { Capability, CapabilityUserState } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import PushUsersModal from "@/components/PushUsersModal";

const _CAP_KEY_RE = /^[A-Za-z][A-Za-z0-9_]*$/;

function KindBadge({ kind }: { kind?: string }) {
  if (kind === "model")
    return <span className="text-[10px] bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded">模型能力</span>;
  return <span className="text-[10px] bg-sky-100 text-sky-700 px-1.5 py-0.5 rounded">工具集</span>;
}

export default function CapabilitiesPage() {
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [defaultsMap, setDefaultsMap] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [batchBusy, setBatchBusy] = useState(false);
  const [message, setMessage] = useState("");

  const [query, setQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());

  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Capability | null>(null);
  const [showPushUsers, setShowPushUsers] = useState(false);
  const [perUserKey, setPerUserKey] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setMessage("");
    try {
      const [list, defs] = await Promise.all([listCapabilities(), getCapabilityDefaults()]);
      setCapabilities(list.capabilities);
      const map: Record<string, boolean> = {};
      for (const d of defs.defaults) map[d.capability] = d.default_inject;
      for (const c of list.capabilities) if (!(c.capability in map)) map[c.capability] = false;
      setDefaultsMap(map);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const categories = useMemo(() => {
    const set = new Set<string>(["全部"]);
    capabilities.forEach((c) => set.add(c.category || "未分类"));
    return Array.from(set);
  }, [capabilities]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return capabilities.filter((c) => {
      const matchQ =
        !q ||
        c.label.toLowerCase().includes(q) ||
        c.capability.toLowerCase().includes(q) ||
        (c.description || "").toLowerCase().includes(q);
      const matchC = activeCategory === null || activeCategory === "全部" || (c.category || "未分类") === activeCategory;
      return matchQ && matchC;
    });
  }, [capabilities, query, activeCategory]);

  const selectedCaps = useMemo(
    () => capabilities.filter((c) => selectedKeys.has(c.capability)),
    [capabilities, selectedKeys],
  );
  const singleSelected = selectedCaps.length === 1 ? selectedCaps[0] : null;
  const allSelected = filtered.length > 0 && filtered.every((c) => selectedKeys.has(c.capability));
  const someSelected = filtered.some((c) => selectedKeys.has(c.capability));
  const selectedAllDefault = selectedCaps.length > 0 && selectedCaps.every((c) => defaultsMap[c.capability]);

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
      if (filtered.every((c) => prev.has(c.capability))) {
        const next = new Set(prev);
        for (const c of filtered) next.delete(c.capability);
        return next;
      }
      return new Set(filtered.map((c) => c.capability));
    });
  };
  const clearSelection = () => setSelectedKeys(new Set());

  async function handleToggleDefault() {
    setBatchBusy(true);
    setMessage("");
    try {
      const nextMap = { ...defaultsMap };
      const target = !selectedAllDefault;
      for (const c of selectedCaps) nextMap[c.capability] = target;
      setDefaultsMap(nextMap);
      await putCapabilityDefaults(
        capabilities.map((c) => ({ capability: c.capability, default_inject: !!nextMap[c.capability] })),
      );
      await load();
      setMessage(
        target
          ? `已将选中的 ${selectedCaps.length} 个能力设为默认注入。新容器会自动带上；已有容器需在容器页重建或热补丁生效。`
          : `已取消选中能力的默认注入。`,
      );
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBatchBusy(false);
    }
  }

  async function handleDelete() {
    if (!confirm(`确定删除选中的 ${selectedCaps.length} 个能力？内置能力将停用（不再列出），自定义能力会彻底删除。`))
      return;
    setBatchBusy(true);
    setMessage("");
    try {
      for (const c of selectedCaps) {
        await deleteCapability(c.capability);
      }
      clearSelection();
      await load();
      setMessage(`已删除 ${selectedCaps.length} 个能力。`);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBatchBusy(false);
    }
  }

  async function handlePushToUsers(userIds: string[]) {
    if (selectedCaps.length === 0) return;
    setBatchBusy(true);
    setMessage("");
    try {
      const lines: string[] = [];
      for (const c of selectedCaps) {
        const res = await pushCapabilityToUsers(c.capability, userIds);
        let line = `「${c.label}」：配置已写入 ${res.pushed}/${res.total} 个用户`;
        if (res.applied) line += `，已热应用到 ${res.applied} 个运行中的容器`;
        if (res.skipped?.length)
          line += `，${res.skipped.length} 个当前无运行容器（下次启动生效）`;
        if (res.failed?.length) line += `，${res.failed.length} 个失败`;
        lines.push(line);
      }
      await load();
      setMessage(lines.join("；"));
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBatchBusy(false);
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
      <div>
        <h1 className="text-2xl font-bold">能力配置</h1>
        <p className="text-sm text-gray-500 mt-1">
          统一管理可注入用户容器的能力（如联网搜索、浏览器、图像生成）。能力相当于平台级「技能」：可设默认注入、编辑密钥与接口参数、推送给指定用户、按用户管理、或删除；也支持新增多个同类条目（如多个联网搜索）。
        </p>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="inline-flex items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-gray-300 accent-blue-600"
            checked={allSelected}
            ref={(el) => {
              if (el) el.indeterminate = !allSelected && someSelected;
            }}
            onChange={() => toggleSelectAll()}
            disabled={filtered.length === 0}
          />
          全选
        </label>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索名称 / 标识 / 描述..."
          className="w-full max-w-xs rounded-md border px-3 py-2 text-sm"
        />
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <button
            title="设为/取消默认注入（新用户）"
            disabled={selectedKeys.size === 0 || batchBusy}
            onClick={() => handleToggleDefault()}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            {selectedAllDefault ? <ToggleRight className="h-4 w-4 text-green-600" /> : <ToggleLeft className="h-4 w-4" />}
            {selectedAllDefault ? "取消默认" : "设默认"}
          </button>
          <button
            title="编辑"
            disabled={selectedKeys.size !== 1 || batchBusy}
            onClick={() => setEditing(singleSelected)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Pencil className="h-4 w-4" /> 编辑
          </button>
          <button
            title="推送给指定用户（可多选能力、多选用户）"
            disabled={selectedKeys.size === 0 || batchBusy}
            onClick={() => setShowPushUsers(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" /> 推送给指定用户
          </button>
          <button
            title="按用户管理（开启/关闭 + 应用到容器）"
            disabled={selectedKeys.size !== 1 || batchBusy}
            onClick={() => setPerUserKey(singleSelected?.capability || null)}
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
          <Button onClick={() => setShowCreate(true)} className="relative">
            <Plus className="h-4 w-4 mr-2" /> 新增能力
          </Button>
        </div>
      </div>

      {selectedKeys.size > 0 && (
        <div className="mb-3 rounded-md bg-primary/5 px-3 py-2 text-sm">
          已选中：<b>{selectedKeys.size}</b> 个能力
          <button className="ml-2 text-muted-foreground hover:text-foreground" onClick={() => clearSelection()}>
            取消选择
          </button>
        </div>
      )}

      {message && (
        <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">{message}</div>
      )}

      <div className="flex flex-wrap gap-2">
        {categories.map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => setActiveCategory(c === "全部" ? null : c)}
            className={`text-xs px-3 py-1.5 rounded-full border ${
              (c === "全部" ? activeCategory === null : activeCategory === c)
                ? "bg-blue-500 text-white border-blue-500"
                : "border-gray-300 text-gray-600 hover:bg-gray-50"
            }`}
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
              <p>暂无能力</p>
              <p className="text-xs mt-1">点击右上角「新增能力」添加一个可注入的能力条目。</p>
            </CardContent>
          </Card>
        )}
        {filtered.map((cap) => {
          const isSel = selectedKeys.has(cap.capability);
          const on = !!defaultsMap[cap.capability];
          const noKey = on && cap.requires_key && !cap.platform_key_configured;
          return (
            <Card
              key={cap.capability}
              className={`cursor-pointer transition-shadow hover:shadow-md ${isSel ? "ring-2 ring-primary" : ""}`}
            >
              <CardContent className="p-4" onClick={() => toggleSelect(cap.capability)}>
                <div className="flex items-start gap-2 mb-2">
                  <input
                    type="checkbox"
                    checked={isSel}
                    onChange={() => toggleSelect(cap.capability)}
                    onClick={(e) => e.stopPropagation()}
                    className="mt-0.5 h-4 w-4 shrink-0 rounded border-gray-300 accent-blue-600"
                  />
                  <div className="flex items-start justify-between gap-2 min-w-0 flex-1">
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <span className="font-medium text-sm truncate">{cap.label}</span>
                    </div>
                    <div className="flex items-center gap-1 shrink-0 flex-wrap justify-end">
                      <KindBadge kind={cap.kind} />
                      {cap.builtin === false && (
                        <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">自定义</span>
                      )}
                      {on ? (
                        <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded">默认注入</span>
                      ) : (
                        <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded">非默认</span>
                      )}
                      {cap.placeholder && (
                        <span className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">未实现</span>
                      )}
                    </div>
                  </div>
                </div>
                {cap.description && <div className="text-xs text-gray-500 line-clamp-2">{cap.description}</div>}
                <div className="text-xs text-gray-400 mt-1.5 truncate">分类：{cap.category || "未分类"} · {cap.capability}</div>
                {!cap.placeholder && (
                  <div className="text-xs text-gray-500 mt-1">
                    工具集：{cap.toolsets.length ? cap.toolsets.join(", ") : "—"} · 密钥变量：{cap.env_key || "—"}
                  </div>
                )}
                {!cap.placeholder && (
                  <div className="flex items-center gap-1 text-xs mt-1">
                    {cap.platform_key_configured ? (
                      <span className="inline-flex items-center gap-1 text-green-600">
                        <CheckCircle2 className="h-3 w-3" /> 平台密钥已配置
                      </span>
                    ) : cap.requires_key ? (
                      <span className="inline-flex items-center gap-1 text-gray-500">
                        <XCircle className="h-3 w-3" /> 平台密钥未配置
                      </span>
                    ) : (
                      <span className="text-gray-400">无需外部密钥</span>
                    )}
                  </div>
                )}
                {noKey && (
                  <div className="inline-flex items-center gap-1 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mt-1">
                    <AlertTriangle className="h-3 w-3" />
                    已默认注入但平台密钥未配置：实际不会挂载工具集。
                  </div>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {showCreate && <CapabilityFormModal onClose={() => setShowCreate(false)} onSaved={async () => { setShowCreate(false); await load(); }} />}
      {editing && (
        <CapabilityFormModal
          editing={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => { setEditing(null); await load(); }}
        />
      )}

      <PushUsersModal
        open={showPushUsers}
        title={`推送 ${selectedCaps.length} 个能力给指定用户`}
        confirmLabel="推送"
        onClose={() => setShowPushUsers(false)}
        onPush={handlePushToUsers}
      />

      {perUserKey && <PerUserCapabilityModal key={perUserKey} capabilityKey={perUserKey} onClose={() => setPerUserKey(null)} onChanged={load} />}
    </div>
  );
}

function CapabilityFormModal({
  editing,
  onClose,
  onSaved,
}: {
  editing?: Capability | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [key, setKey] = useState(editing?.capability || "");
  const [label, setLabel] = useState(editing?.label || "");
  const [kind, setKind] = useState<"toolset" | "model">(editing?.kind || "toolset");
  const [category, setCategory] = useState(editing?.category || "未分类");
  const [description, setDescription] = useState(editing?.description || "");
  const [toolsets, setToolsets] = useState((editing?.toolsets || []).join(", "));
  const [envKey, setEnvKey] = useState(editing?.env_key || "");
  const [platformKey, setPlatformKey] = useState(""); // not echoed back; empty = keep
  const [configText, setConfigText] = useState(
    JSON.stringify(editing?.config && Object.keys(editing.config).length ? editing.config : {}, null, 2),
  );
  const [requiresKey, setRequiresKey] = useState(editing ? !!editing.requires_key : true);
  const [placeholder, setPlaceholder] = useState(editing ? !!editing.placeholder : false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // Only `toolset` capabilities with `requires_key` need a secret + env var.
  const needsKeyConfig = kind === "toolset" && requiresKey && !placeholder;

  async function save() {
    setBusy(true);
    setErr("");
    if (!editing && !_CAP_KEY_RE.test(key.trim())) {
      setErr("标识（key）需以字母开头，仅含字母/数字/下划线");
      setBusy(false);
      return;
    }
    let config: Record<string, unknown> = {};
    const trimmed = configText.trim();
    if (trimmed) {
      try {
        config = JSON.parse(trimmed);
        if (typeof config !== "object" || config === null || Array.isArray(config)) throw new Error("config 必须是 JSON 对象");
      } catch (e) {
        setErr("配置 (JSON) 解析失败：" + (e as Error).message);
        setBusy(false);
        return;
      }
    }
    const ts = toolsets
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      if (editing) {
        const body: CapabilityUpdateBody = {
          label: label.trim() || null,
          kind,
          category: category.trim() || "未分类",
          description: description.trim() || null,
          toolsets: ts,
          env_key: envKey.trim(),
          // null => keep existing key; "" => clear; non-empty => set
          platform_key_value: platformKey.length ? platformKey : null,
          config,
          requires_key: requiresKey,
          placeholder,
        };
        await updateCapability(editing.capability, body);
      } else {
        const body: CapabilityCreateBody = {
          key: key.trim(),
          label: label.trim() || key.trim(),
          kind,
          category: category.trim() || "未分类",
          description: description.trim(),
          toolsets: ts,
          env_key: envKey.trim(),
          platform_key_value: platformKey.length ? platformKey : null,
          config,
          requires_key: requiresKey,
          placeholder,
        };
        await createCapability(body);
      }
      await onSaved();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold mb-3">{editing ? `编辑能力（${editing.label}）` : "新增能力"}</h2>
        <div className="space-y-3 max-h-[70vh] overflow-y-auto">
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-sm">
              <span className="text-gray-600">标识 key {editing ? "（不可改）" : ""}</span>
              <input
                value={key}
                disabled={!!editing}
                onChange={(e) => setKey(e.target.value)}
                className="mt-1 w-full rounded border px-2 py-1.5 text-sm disabled:bg-gray-100"
                placeholder="如 web_search_2"
              />
            </label>
            <label className="block text-sm">
              <span className="text-gray-600">显示名称</span>
              <input value={label} onChange={(e) => setLabel(e.target.value)} className="mt-1 w-full rounded border px-2 py-1.5 text-sm" placeholder="如 联网搜索(备用)" />
            </label>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-sm">
              <span className="text-gray-600">类型</span>
              <select value={kind} onChange={(e) => setKind(e.target.value as "toolset" | "model")} className="mt-1 w-full rounded border px-2 py-1.5 text-sm bg-white">
                <option value="toolset">工具集（需密钥/URL）</option>
                <option value="model">模型能力（无需外部密钥）</option>
              </select>
            </label>
            <label className="block text-sm">
              <span className="text-gray-600">分类</span>
              <input value={category} onChange={(e) => setCategory(e.target.value)} className="mt-1 w-full rounded border px-2 py-1.5 text-sm" placeholder="如 工具 / 模型" />
            </label>
          </div>
          <label className="block text-sm">
            <span className="text-gray-600">描述</span>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="mt-1 w-full rounded border px-2 py-1.5 text-sm" />
          </label>
          <label className="block text-sm">
            <span className="text-gray-600">工具集（逗号分隔，注入容器 platform_toolsets）</span>
            <input value={toolsets} disabled={!needsKeyConfig} onChange={(e) => setToolsets(e.target.value)} className="mt-1 w-full rounded border px-2 py-1.5 text-sm disabled:bg-gray-100 disabled:text-gray-400" placeholder="如 web" />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-sm">
              <span className="text-gray-600">密钥变量名 (env_key)</span>
              <input value={envKey} disabled={!needsKeyConfig} onChange={(e) => setEnvKey(e.target.value)} className="mt-1 w-full rounded border px-2 py-1.5 text-sm disabled:bg-gray-100 disabled:text-gray-400" placeholder="如 TAVILY_API_KEY" />
            </label>
            <label className="block text-sm">
              <span className="text-gray-600">平台密钥（API Key）</span>
              <input
                type="password"
                value={platformKey}
                disabled={!needsKeyConfig}
                onChange={(e) => setPlatformKey(e.target.value)}
                className="mt-1 w-full rounded border px-2 py-1.5 text-sm font-mono disabled:bg-gray-100 disabled:text-gray-400"
                placeholder={editing?.has_platform_key ? "已配置（留空则保持不变）" : "如 tvly-xxxxxxxx"}
              />
            </label>
          </div>
          <label className="block text-sm">
            <span className="text-gray-600">配置 (JSON，可含 url / backend 等参数)</span>
            <textarea
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
              rows={5}
              className="mt-1 w-full rounded border px-2 py-1.5 text-sm font-mono"
              placeholder={'{\n  "web": {\n    "backend": "tavily",\n    "url": "https://api.tavily.com"\n  }\n}'}
            />
          </label>
          <div className="flex items-center gap-5 text-sm">
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={requiresKey} onChange={(e) => setRequiresKey(e.target.checked)} className="h-4 w-4" />
              需要外部密钥
            </label>
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={placeholder} onChange={(e) => setPlaceholder(e.target.checked)} className="h-4 w-4" />
              占位/未实现（开启也不挂载工具集）
            </label>
          </div>
          {!needsKeyConfig && (
            <div className="rounded bg-gray-50 p-2 text-xs text-gray-500">
              {kind === "model"
                ? "模型能力（如图像生成）由底层模型原生提供，无需外部密钥 / URL，此处仅作开关管理。"
                : placeholder
                  ? "占位能力尚未接入工具集，开启也不会挂载工具，仅作 UI 占位。"
                  : "当前类型无需外部密钥，相关密钥字段已禁用。"}
            </div>
          )}
          {needsKeyConfig && (
            <div className="rounded bg-blue-50 p-2 text-xs text-blue-700">
              保存后，平台密钥会自动注入已开启该能力的用户容器（推送即生效，无需重启）。密钥不会明文回传显示。
            </div>
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

function PerUserCapabilityModal({
  capabilityKey,
  onClose,
  onChanged,
}: {
  capabilityKey: string;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
}) {
  const [users, setUsers] = useState<CapabilityUserState[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [msg, setMsg] = useState("");

  async function reload() {
    setLoading(true);
    try {
      const res = await getCapabilityUsers(capabilityKey);
      setUsers(res.users);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capabilityKey]);

  async function toggle(u: CapabilityUserState, enabled: boolean) {
    setBusyId(u.user_id);
    setMsg("");
    try {
      if (enabled) {
        await putUserCapabilities(u.user_id, { capabilities: [{ capability: capabilityKey, enabled: true }], remove: [] });
      } else {
        await putUserCapabilities(u.user_id, { capabilities: [], remove: [capabilityKey] });
      }
      await reload();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function applyTo(u: CapabilityUserState) {
    setBusyId(u.user_id);
    setMsg("");
    try {
      await applyUserCapabilities(u.user_id);
      setMsg(`已对 ${u.username} 的容器应用能力配置。`);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold mb-1">按用户管理：{capabilityKey}</h2>
        <p className="text-xs text-gray-500 mb-3">开启/关闭该能力对指定用户的注入；可一键应用到其运行中的容器。</p>
        {loading ? (
          <div className="flex items-center gap-2 py-8 text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中...
          </div>
        ) : (
          <div className="max-h-80 overflow-y-auto divide-y">
            {users.map((u) => (
              <div key={u.user_id} className="flex items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">
                    {u.username} {u.source === "user" && <span className="text-[10px] text-amber-600">（用户覆盖）</span>}
                  </div>
                  <div className="text-xs text-gray-400 truncate">{u.email || u.role}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <label className="inline-flex items-center gap-1 text-sm">
                    <input
                      type="checkbox"
                      checked={u.enabled}
                      disabled={busyId === u.user_id}
                      onChange={(e) => toggle(u, e.target.checked)}
                      className="h-4 w-4 accent-blue-600"
                    />
                    {u.enabled ? "已开启" : "已关闭"}
                  </label>
                  <button
                    disabled={busyId === u.user_id || !u.enabled}
                    onClick={() => applyTo(u)}
                    className="rounded border px-2 py-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40"
                  >
                    应用
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        {msg && <div className="mt-2 text-sm text-blue-700">{msg}</div>}
        <div className="mt-4 flex justify-end">
          <Button variant="outline" onClick={onClose}>关闭</Button>
        </div>
      </div>
    </div>
  );
}
