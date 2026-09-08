"use client";

import { useEffect, useMemo, useState, useRef } from "react";
import { Brain, Check, Key, Loader2, Plug, Plus, Save, Trash2, Users, UserPlus, Send } from "lucide-react";
import {
  getModelsConfig,
  updateModelsConfig,
  testModelConnection,
  updateProviderScope,
  setProviderAutoInstall,
  getUserModels,
  updateUserModels,
  getUsers,
  type AdminModelItem,
  type AdminProviderConfig,
  type AdminModelsConfig,
  type ModelConnectionTestResult,
} from "@/lib/api";
import type { UserSummary } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import PushUsersModal from "@/components/PushUsersModal";

const providerPresets = [
  { id: "deepseek", name: "DeepSeek", type: "deepseek", baseUrl: "https://api.deepseek.com/v1", models: ["deepseek-chat", "deepseek-reasoner"] },
  { id: "minimax", name: "MiniMax 中国区", type: "minimax-cn", baseUrl: "https://api.minimaxi.com/anthropic", models: ["MiniMax-M2.7"] },
  { id: "openai", name: "OpenAI", type: "openai", baseUrl: "", models: ["gpt-5.4", "gpt-5.4-mini"] },
  { id: "anthropic", name: "Claude", type: "anthropic", baseUrl: "", models: ["claude-sonnet-4-5", "claude-opus-4-6"] },
  { id: "openrouter", name: "OpenRouter", type: "openrouter", baseUrl: "", models: ["anthropic/claude-sonnet-4.5", "openai/gpt-5.4"] },
  { id: "zhipu", name: "智谱 GLM", type: "zhipu", baseUrl: "https://open.bigmodel.cn/api/paas/v4", models: ["glm-4-plus", "glm-4.5"] },
  { id: "kimi", name: "Kimi", type: "kimi", baseUrl: "https://api.moonshot.cn/v1", models: ["kimi-k2.5", "moonshot-v1-128k"] },
  { id: "doubao", name: "豆包", type: "doubao", baseUrl: "https://ark.cn-beijing.volces.com/api/v3", models: ["doubao-seed-1-6"] },
  { id: "custom", name: "自定义 OpenAI 兼容", type: "custom", baseUrl: "", models: [""] },
];

const API_MODES = [
  { value: "openai-completions", label: "OpenAI 兼容" },
  { value: "anthropic-messages", label: "Anthropic 兼容" },
  { value: "google-generative-ai", label: "Google Generative AI" },
];

// Resolve a concrete protocol value from a provider's stored `api` (which may be
// a real api_mode, or — for legacy rows — an echoed provider_type) and type.
function effectiveApi(p: AdminProviderConfig): string {
  const a = (p.api || "").trim().toLowerCase();
  if (a === "anthropic-messages" || a === "anthropic_messages") return "anthropic-messages";
  if (a === "openai-completions" || a === "chat_completions") return "openai-completions";
  if (a === "google-generative-ai" || a === "google_ai") return "google-generative-ai";
  if (a.includes("anthropic") || (p.providerType || "").trim().toLowerCase() === "minimax-cn") return "anthropic-messages";
  return "openai-completions";
}

function emptyProvider(id: string): AdminProviderConfig {
  const preset = providerPresets.find((item) => item.id === id) ?? providerPresets[providerPresets.length - 1];
  const api = preset.type === "anthropic" || preset.type === "minimax-cn" ? "anthropic-messages" : "openai-completions";
  return {
    id: preset.id,
    name: preset.name,
    providerType: preset.type,
    api,
    baseUrl: preset.baseUrl,
    apiKey: "",
    enabled: true,
    models: preset.models.map((model) => ({ id: model, name: model, enabled: true })),
  };
}

function scopeBadge(p: AdminProviderConfig): { text: string; cls: string } {
  if (p.enabled === false) return { text: "已停用", cls: "bg-gray-100 text-gray-500" };
  const mode = p.scopeMode || "all";
  if (mode === "all") return { text: "推送给全部", cls: "bg-green-100 text-green-700" };
  if (mode === "allow") return { text: `指定用户 (${p.scopeUserIds?.length || 0})`, cls: "bg-blue-100 text-blue-700" };
  return { text: `排除 (${p.scopeUserIds?.length || 0})`, cls: "bg-amber-100 text-amber-700" };
}

export default function ModelsPage() {
  const [config, setConfig] = useState<AdminModelsConfig | null>(null);
  const [providers, setProviders] = useState<Record<string, AdminProviderConfig>>({});
  const [defaultModel, setDefaultModel] = useState("");
  const [selectedPreset, setSelectedPreset] = useState("deepseek");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [query, setQuery] = useState("");
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [showPushUsers, setShowPushUsers] = useState(false);
  const [showPerUser, setShowPerUser] = useState(false);
  const [testState, setTestState] = useState<Record<string, { loading: boolean; result?: ModelConnectionTestResult }>>({});
  // Tracks which providers had their API key field edited by the user, so we
  // only send `apiKey` on save when actually modified (and never echo the
  // masked value back as a new key).
  const [keyDirty, setKeyDirty] = useState<Set<string>>(new Set());

  useEffect(() => {
    getModelsConfig()
      .then((data) => {
        setConfig(data);
        setProviders(data.configuredProviders || {});
        setDefaultModel(data.configuredModel || "");
        setKeyDirty(new Set());
      })
      .finally(() => setLoading(false));
  }, []);

  const providerList = useMemo(() => Object.entries(providers), [providers]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return providerList.filter(([id, p]) => !q || id.includes(q) || (p.name || "").toLowerCase().includes(q));
  }, [providerList, query]);
  const selectedProviders = useMemo(
    () => providerList.filter(([id]) => selectedKeys.has(id)).map(([, p]) => p),
    [providerList, selectedKeys],
  );
  const allSelected = filtered.length > 0 && filtered.every(([id]) => selectedKeys.has(id));
  const someSelected = filtered.some(([id]) => selectedKeys.has(id));

  const toggleSelect = (id: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    setSelectedKeys((prev) => {
      if (filtered.every(([id]) => prev.has(id))) {
        const next = new Set(prev);
        for (const [id] of filtered) next.delete(id);
        return next;
      }
      const next = new Set(prev);
      for (const [id] of filtered) next.add(id);
      return next;
    });
  };
  const clearSelection = () => setSelectedKeys(new Set());

  const availableModels = useMemo(() => {
    return Object.entries(providers).flatMap(([providerId, provider]) => {
      if (!provider.enabled) return [];
      return (provider.models || [])
        .filter((model) => model.id && model.enabled !== false)
        .map((model) => ({
          id: `${providerId}/${model.id}`,
          label: `${provider.name || providerId} / ${model.name || model.id}`,
        }));
    });
  }, [providers]);

  const updateProvider = (id: string, patch: Partial<AdminProviderConfig>) => {
    setProviders((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  };

  const updateModel = (providerId: string, index: number, patch: Partial<AdminModelItem>) => {
    setProviders((prev) => {
      const provider = prev[providerId];
      const models = [...(provider.models || [])];
      models[index] = { ...models[index], ...patch };
      return { ...prev, [providerId]: { ...provider, models } };
    });
  };

  const addProvider = () => {
    const base = emptyProvider(selectedPreset);
    let id = base.id || selectedPreset;
    let suffix = 2;
    while (providers[id]) {
      id = `${base.id}-${suffix}`;
      suffix += 1;
    }
    setProviders((prev) => ({ ...prev, [id]: { ...base, id } }));
  };

  const removeProviderLocal = (id: string) => {
    setProviders((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    setMessage("");
    try {
      const payloadProviders = Object.fromEntries(
        Object.entries(providers).map(([id, provider]) => [
          id,
          {
            ...provider,
            api: effectiveApi(provider),
            apiKey: keyDirty.has(id) ? (provider.apiKey || "") : undefined,
            models: (provider.models || []).filter((model) => model.id.trim()),
          },
        ])
      );
      const saved = await updateModelsConfig({ providers: payloadProviders, defaultModel: defaultModel || undefined });
      setConfig(saved);
      setProviders(saved.configuredProviders || {});
      setDefaultModel(saved.configuredModel || "");
      setMessage("模型配置已保存");
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || "";
      setMessage(detail ? `保存失败：${detail}` : "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  };

  async function testProvider(id: string) {
    const p = providers[id];
    if (!p) return;
    if (!p.baseUrl?.trim()) {
      setMessage("请先填写 Base URL 再测试连接");
      return;
    }
    const models = (p.models || []).filter((m) => m.id?.trim());
    if (models.length === 0) {
      setMessage("请至少添加一个模型再测试连接");
      return;
    }
    setTestState((prev) => ({ ...prev, [id]: { loading: true } }));
    try {
      const res = await testModelConnection({
        baseUrl: p.baseUrl!.trim(),
        apiKey: p.apiKey || "",
        api: effectiveApi(p),
        model: models[0].id.trim(),
      });
      setTestState((prev) => ({ ...prev, [id]: { loading: false, result: res } }));
    } catch (e: any) {
      setTestState((prev) => ({
        ...prev,
        [id]: { loading: false, result: { ok: false, status: 0, message: e?.message || "测试失败" } },
      }));
    }
  }

  // ─── Push actions (operate on the selected providers) ─────────────────────
  async function handlePushAll() {
    if (selectedProviders.length === 0) return;
    setBatchBusy(true);
    setMessage("");
    try {
      for (const p of selectedProviders) {
        const res = await updateProviderScope(p.id!, "all");
        updateProvider(p.id!, { scopeMode: res.scopeMode, scopeUserIds: res.scopeUserIds });
      }
      setMessage(`已推送给全部用户：${selectedProviders.length} 个模型`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "推送失败");
    } finally {
      setBatchBusy(false);
    }
  }

  async function handlePushUsers(userIds: string[]) {
    if (selectedProviders.length === 0) return;
    setBatchBusy(true);
    setMessage("");
    try {
      for (const p of selectedProviders) {
        const res = await updateProviderScope(p.id!, "allow", userIds);
        updateProvider(p.id!, { scopeMode: res.scopeMode, scopeUserIds: res.scopeUserIds });
      }
      setMessage(`已推送给 ${userIds.length} 个指定用户：${selectedProviders.length} 个模型`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "推送失败");
    } finally {
      setBatchBusy(false);
    }
  }

  async function handleToggleAutoInstall() {
    if (selectedProviders.length === 0) return;
    setBatchBusy(true);
    setMessage("");
    try {
      for (const p of selectedProviders) {
        const next = !p.autoInstall;
        const res = await setProviderAutoInstall(p.id!, next);
        updateProvider(p.id!, { autoInstall: res.autoInstall });
      }
      const anyOn = selectedProviders.some((p) => !p.autoInstall);
      setMessage(anyOn ? "已开启新用户默认安装" : "已关闭新用户默认安装");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBatchBusy(false);
    }
  }

  async function handleDelete() {
    if (selectedProviders.length === 0) return;
    if (!confirm(`确定删除选中的 ${selectedProviders.length} 个模型供应商？`)) return;
    setBatchBusy(true);
    setMessage("");
    try {
      const nextProviders = { ...providers };
      for (const p of selectedProviders) delete nextProviders[p.id!];
      const saved = await updateModelsConfig({ providers: nextProviders, defaultModel: defaultModel || undefined });
      setConfig(saved);
      setProviders(saved.configuredProviders || {});
      setDefaultModel(saved.configuredModel || "");
      clearSelection();
      setMessage(`已删除 ${selectedProviders.length} 个模型供应商`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBatchBusy(false);
    }
  }

  if (loading) {
    return <div className="flex h-64 items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" /></div>;
  }

  const selCount = selectedKeys.size;
  const autoInstallOn = selectedProviders.length > 0 && selectedProviders.every((p) => p.autoInstall);

  return (
    <div className="p-8 space-y-6">
      <div className="mb-2">
        <h1 className="text-2xl font-bold">模型管理</h1>
        <p className="text-sm text-gray-500 mt-1">
          配置模型供应商后，选中卡片在上方工具栏操作：可推送给全部/指定用户、设置新用户默认安装、在用户管理页禁用具体推送。客户端只显示对其可见的模型。
        </p>
      </div>

      {message && <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">{message}</div>}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">默认模型（平台级）</CardTitle>
        </CardHeader>
        <CardContent>
          <Select value={defaultModel} onValueChange={(value) => setDefaultModel(value || "")}>
            <SelectTrigger className="max-w-xl">
              <SelectValue placeholder="选择默认模型" />
            </SelectTrigger>
            <SelectContent>
              {availableModels.map((model) => (
                <SelectItem key={model.id} value={model.id}>{model.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

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
          placeholder="搜索供应商 ID / 名称..."
          className="w-full max-w-xs rounded-md border px-3 py-2 text-sm"
        />
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <button
            title="推送给全部用户"
            disabled={selCount === 0 || batchBusy}
            onClick={() => handlePushAll()}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Users className="h-4 w-4" /> 推送全部
          </button>
          <button
            title="推送给指定用户（多选）"
            disabled={selCount !== 1 || batchBusy}
            onClick={() => setShowPushUsers(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <Send className="h-4 w-4" /> 推送给指定用户
          </button>
          <button
            title="新用户默认安装"
            disabled={selCount === 0 || batchBusy}
            onClick={() => handleToggleAutoInstall()}
            className={`inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm disabled:opacity-40 ${autoInstallOn ? "text-green-600" : "text-muted-foreground hover:text-foreground"}`}
          >
            {autoInstallOn ? <Check className="h-4 w-4" /> : <UserPlus className="h-4 w-4" />}
            新用户默认安装
          </button>
          <button
            title="按用户管理（禁用/启用具体推送）"
            disabled={selCount !== 1 || batchBusy}
            onClick={() => setShowPerUser(true)}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            <UserPlus className="h-4 w-4" /> 按用户管理
          </button>
          <button
            title="删除"
            disabled={selCount === 0 || batchBusy}
            onClick={() => handleDelete()}
            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-2 text-sm text-muted-foreground hover:text-red-500 disabled:opacity-40"
          >
            <Trash2 className="h-4 w-4" /> 删除
          </button>
          <Button onClick={save} disabled={saving}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
            保存
          </Button>
        </div>
      </div>

      {selCount > 0 && (
        <div className="mb-3 rounded-md bg-primary/5 px-3 py-2 text-sm">
          已选中：<b>{selCount}</b> 个模型供应商
          <button className="ml-2 text-muted-foreground hover:text-foreground" onClick={() => clearSelection()}>
            取消选择
          </button>
        </div>
      )}

      <div className="flex items-center gap-2">
        <Select value={selectedPreset} onValueChange={(value) => value && setSelectedPreset(value)}>
          <SelectTrigger className="w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {providerPresets.map((preset) => (
              <SelectItem key={preset.id} value={preset.id}>{preset.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="outline" onClick={addProvider}><Plus className="mr-2 h-4 w-4" />添加供应商</Button>
      </div>

      <div className="grid gap-4">
        {filtered.length === 0 && (
          <Card>
            <CardContent className="p-8 text-center text-gray-500">暂无模型供应商</CardContent>
          </Card>
        )}
        {filtered.map(([id, provider]) => {
          const isSel = selectedKeys.has(id);
          const badge = scopeBadge(provider);
          return (
          <Card key={id} className={isSel ? "ring-2 ring-primary" : ""}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0">
              <CardTitle className="flex items-center gap-2 text-base">
                <input
                  type="checkbox"
                  checked={isSel}
                  onChange={() => toggleSelect(id)}
                  className="h-4 w-4 rounded border-gray-300 accent-blue-600"
                />
                <Brain className="h-4 w-4" />
                {provider.name || id}
                {provider.configured && <Badge variant="secondary"><Check className="mr-1 h-3 w-3" />已配置 Key</Badge>}
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${badge.cls}`}>{badge.text}</span>
                {provider.autoInstall && (
                  <span className="text-[10px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded">新用户默认</span>
                )}
              </CardTitle>
              <Button variant="ghost" size="icon" onClick={() => removeProviderLocal(id)}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 md:grid-cols-4">
                <div>
                  <Label>供应商 ID</Label>
                  <Input value={id} disabled />
                </div>
                <div>
                  <Label>显示名称</Label>
                  <Input value={provider.name || ""} onChange={(e) => updateProvider(id, { name: e.target.value })} />
                </div>
                <div>
                  <Label>类型</Label>
                  <Input value={provider.providerType || ""} onChange={(e) => updateProvider(id, { providerType: e.target.value })} />
                </div>
                <div>
                  <Label>状态</Label>
                  <Select value={provider.enabled ? "enabled" : "disabled"} onValueChange={(value) => updateProvider(id, { enabled: value === "enabled" })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="enabled">启用</SelectItem>
                      <SelectItem value="disabled">停用</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="grid gap-4 md:grid-cols-3">
                <div>
                  <Label>Base URL</Label>
                  <Input value={provider.baseUrl || ""} onChange={(e) => updateProvider(id, { baseUrl: e.target.value })} placeholder="https://api.example.com/v1" />
                </div>
                <div>
                  <Label>API Key</Label>
                  <div className="flex gap-2 items-center">
                    <Input
                      type={keyDirty.has(id) ? "password" : "text"}
                      value={keyDirty.has(id) ? (provider.apiKey || "") : (provider.apiKey || provider.apiKeyMasked || "")}
                      placeholder={provider.configured ? "已配置（点击输入框可替换）" : "sk-..."}
                      onChange={(e) => {
                        if (!keyDirty.has(id)) setKeyDirty((prev) => new Set(prev).add(id));
                        updateProvider(id, { apiKey: e.target.value });
                      }}
                    />
                    <Key className="h-5 w-5 text-gray-400" />
                  </div>
                  {provider.configured && !keyDirty.has(id) && (
                    <p className="mt-1 text-[11px] text-gray-500">
                      当前密钥：<span className="font-mono">{provider.apiKeyMasked || "已配置"}</span>　不修改直接保存则保留原值
                    </p>
                  )}
                </div>
                <div>
                  <Label>API 协议</Label>
                  <Select
                    value={effectiveApi(provider)}
                    onValueChange={(value) => updateProvider(id, { api: value ?? undefined })}
                  >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {API_MODES.map((m) => (
                        <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => testProvider(id)}
                  disabled={testState[id]?.loading}
                >
                  {testState[id]?.loading ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Plug className="mr-1 h-3 w-3" />}
                  {testState[id]?.loading ? "测试中…" : "测试连接"}
                </Button>
                {testState[id]?.result && (
                  <div className={`mt-2 rounded-md border p-2 text-xs ${
                    testState[id]!.result!.ok
                      ? "border-green-200 bg-green-50 text-green-700"
                      : "border-red-200 bg-red-50 text-red-700"
                  }`}>
                    <div className="flex items-start gap-1.5">
                      <span>{testState[id]!.result!.ok ? "✓" : "✗"}</span>
                      <div>
                        <div>{testState[id]!.result!.message}</div>
                        {testState[id]!.result!.suggestion && (
                          <div className="mt-1 opacity-80 leading-relaxed">{testState[id]!.result!.suggestion}</div>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>模型</Label>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => updateProvider(id, { models: [...(provider.models || []), { id: "", name: "", enabled: true }] })}
                  >
                    <Plus className="mr-1 h-3 w-3" />添加模型
                  </Button>
                </div>
                {(provider.models || []).map((model, index) => (
                  <div key={index} className="grid gap-2 md:grid-cols-[1fr_1fr_120px_40px]">
                    <Input value={model.id} onChange={(e) => updateModel(id, index, { id: e.target.value })} placeholder="模型 ID" />
                    <Input value={model.name || ""} onChange={(e) => updateModel(id, index, { name: e.target.value })} placeholder="显示名称" />
                    <Select value={model.enabled === false ? "disabled" : "enabled"} onValueChange={(value) => updateModel(id, index, { enabled: value === "enabled" })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="enabled">启用</SelectItem>
                        <SelectItem value="disabled">停用</SelectItem>
                      </SelectContent>
                    </Select>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => updateProvider(id, { models: provider.models.filter((_, i) => i !== index) })}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
          );
        })}
      </div>

      <PushUsersModal
        open={showPushUsers}
        title="推送给指定用户（多选）"
        confirmLabel="推送"
        onClose={() => setShowPushUsers(false)}
        onPush={handlePushUsers}
      />

      {showPerUser && selectedProviders.length === 1 && (
        <PerProviderUserModal
          provider={selectedProviders[0]}
          onClose={() => setShowPerUser(false)}
        />
      )}
    </div>
  );
}

function PerProviderUserModal({ provider, onClose }: { provider: AdminProviderConfig; onClose: () => void }) {
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [userId, setUserId] = useState("");
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    getUsers(1, 500).then((r) => setUsers(r.items || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (userId) loadState();
    else setAllowed(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function loadState() {
    try {
      const res = await getUserModels(userId);
      const item = res.providers.find((p) => p.id === provider.id);
      setAllowed(item ? item.allowed : null);
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  async function apply(next: boolean) {
    if (!userId) return;
    setBusy(true);
    setMsg("");
    try {
      await updateUserModels(userId, { [provider.id!]: next });
      setAllowed(next);
      setMsg(next ? `已为该用户启用「${provider.name}」` : `已禁用该用户的「${provider.name}」`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold mb-3">按用户管理：{provider.name}</h2>
        <div className="space-y-3">
          <select value={userId} onChange={(e) => setUserId(e.target.value)} className="w-full border rounded px-3 py-2 text-sm bg-white">
            <option value="">选择目标用户...</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.username}（{u.email || u.role}）</option>
            ))}
          </select>
          {userId && (
            <div className="text-sm text-gray-600">
              当前状态：{allowed === null ? "未知" : allowed ? "已推送（可见）" : "已禁用（不可见）"}
            </div>
          )}
          {msg && <div className="text-sm text-blue-700">{msg}</div>}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>关闭</Button>
          <Button variant="outline" disabled={!userId || busy} onClick={() => apply(false)}>禁用</Button>
          <Button disabled={!userId || busy} onClick={() => apply(true)}>{busy ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}启用</Button>
        </div>
      </div>
    </div>
  );
}
