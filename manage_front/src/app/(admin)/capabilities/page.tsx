"use client";

import { useEffect, useState } from "react";
import { Loader2, Save, AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import {
  getCapabilityRegistry,
  getCapabilityDefaults,
  putCapabilityDefaults,
} from "@/lib/api";
import type { Capability } from "@/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function CapabilitiesPage() {
  const [registry, setRegistry] = useState<Capability[]>([]);
  const [inject, setInject] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    Promise.all([getCapabilityRegistry(), getCapabilityDefaults()])
      .then(([reg, defs]) => {
        setRegistry(reg.capabilities);
        const map: Record<string, boolean> = {};
        for (const d of defs.defaults) map[d.capability] = d.default_inject;
        for (const c of reg.capabilities) {
          if (!(c.capability in map)) map[c.capability] = false;
        }
        setInject(map);
      })
      .catch((e) => setMessage(e.message))
      .finally(() => setLoading(false));
  }, []);

  function toggle(cap: string) {
    setInject((prev) => ({ ...prev, [cap]: !prev[cap] }));
  }

  async function save() {
    setSaving(true);
    setMessage("");
    try {
      const defaults = registry
        .filter((c) => !c.placeholder)
        .map((c) => ({
          capability: c.capability,
          default_inject: !!inject[c.capability],
        }));
      await putCapabilityDefaults(defaults);
      setMessage("已保存。新构建的容器将按此默认档案注入；已有容器需在容器管理页重建或热补丁生效。");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setSaving(false);
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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">能力配置</h1>
          <p className="text-sm text-gray-500 mt-1">
            勾选「默认构建时注入」后，新建用户容器自动带上该能力。平台密钥需在网关环境变量配置。
          </p>
        </div>
        <Button onClick={save} disabled={saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Save className="h-4 w-4 mr-2" />}
          保存
        </Button>
      </div>

      {message && (
        <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">
          {message}
        </div>
      )}

      <div className="space-y-3">
        {registry.map((cap) => {
          const on = !!inject[cap.capability];
          const noKey = on && !cap.platform_key_configured;
          return (
            <Card key={cap.capability}>
              <CardContent className="flex items-center justify-between p-4">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{cap.label}</span>
                    <Badge variant="outline">{cap.capability}</Badge>
                    {cap.placeholder && (
                      <Badge variant="secondary">尚未实现</Badge>
                    )}
                  </div>
                  {cap.placeholder ? (
                    <div className="text-xs text-gray-400">路线图占位项，暂未实现密钥与工具集。</div>
                  ) : (
                    <>
                      <div className="text-xs text-gray-500">
                        工具集：{cap.toolsets.join(", ")} · 密钥变量：{cap.env_key}
                      </div>
                      <div className="flex items-center gap-1 text-xs">
                        {cap.platform_key_configured ? (
                          <span className="inline-flex items-center gap-1 text-green-600">
                            <CheckCircle2 className="h-3 w-3" /> 平台密钥已配置
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-gray-500">
                            <XCircle className="h-3 w-3" /> 平台密钥未配置
                          </span>
                        )}
                      </div>
                      {noKey && (
                        <div className="inline-flex items-center gap-1 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
                          <AlertTriangle className="h-3 w-3" />
                          已勾选默认注入但平台密钥未配置：注入后该能力无法实际工作，工具集不会挂载。
                        </div>
                      )}
                    </>
                  )}
                </div>
                <label className={`flex items-center gap-2 text-sm ${cap.placeholder ? "cursor-not-allowed opacity-50" : "cursor-pointer"}`}>
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => toggle(cap.capability)}
                    disabled={!!cap.placeholder}
                    className="h-4 w-4"
                  />
                  默认构建时注入
                </label>
              </CardContent>
            </Card>
          );
        })}
        {registry.length === 0 && (
          <Card>
            <CardContent className="p-4 text-sm text-gray-500">暂无已注册能力。</CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
