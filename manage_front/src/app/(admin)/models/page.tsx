"use client";

import { useEffect, useMemo, useState } from "react";
import { Brain, Check, Key, Loader2, Plug, Plus, Save, Trash2 } from "lucide-react";
import {
  getModelsConfig,
  updateModelsConfig,
  testModelConnection,
  type AdminModelItem,
  type AdminModelsConfig,
  type AdminProviderConfig,
  type ModelConnectionTestResult,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

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

export default function ModelsPage() {
  const [config, setConfig] = useState<AdminModelsConfig | null>(null);
  const [providers, setProviders] = useState<Record<string, AdminProviderConfig>>({});
  const [defaultModel, setDefaultModel] = useState("");
  const [selectedPreset, setSelectedPreset] = useState("deepseek");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [testState, setTestState] = useState<Record<string, { loading: boolean; result?: ModelConnectionTestResult }>>({});

  useEffect(() => {
    getModelsConfig()
      .then((data) => {
        setConfig(data);
        setProviders(data.configuredProviders || {});
        setDefaultModel(data.configuredModel || "");
      })
      .finally(() => setLoading(false));
  }, []);

  const availableModels = useMemo(() => {
    return Object.entries(providers).flatMap(([providerId, provider]) => {
      if (!provider.enabled || !provider.configured && !provider.apiKey) return [];
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

  const removeProvider = (id: string) => {
    setProviders((prev) => {
      const next = { ...prev };
      delete next[id];
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
            apiKey: provider.apiKey || undefined,
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

  if (loading) {
    return <div className="flex h-64 items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">模型配置</h1>
          <p className="mt-1 text-sm text-gray-500">只有这里启用并配置 Key 的模型会出现在用户端。</p>
        </div>
        <Button onClick={save} disabled={saving}>
          {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
          保存
        </Button>
      </div>

      {message && <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">{message}</div>}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">默认模型</CardTitle>
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
        {Object.entries(providers).map(([id, provider]) => (
          <Card key={id}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0">
              <CardTitle className="flex items-center gap-2 text-base">
                <Brain className="h-4 w-4" />
                {provider.name || id}
                {provider.configured && <Badge variant="secondary"><Check className="mr-1 h-3 w-3" />已配置 Key</Badge>}
              </CardTitle>
              <Button variant="ghost" size="icon" onClick={() => removeProvider(id)}>
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
                  <div className="flex gap-2">
                    <Input type="password" value={provider.apiKey || ""} onChange={(e) => updateProvider(id, { apiKey: e.target.value })} placeholder={provider.apiKeyMasked || "sk-..."} />
                    <Key className="mt-2 h-5 w-5 text-gray-400" />
                  </div>
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
        ))}
      </div>
    </div>
  );
}
