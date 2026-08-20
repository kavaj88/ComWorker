"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Save, ImagePlus, Trash2, CheckCircle2, AlertTriangle, Settings } from "lucide-react";
import { getPlatformConfig, updatePlatformConfig, uploadPlatformLogo } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const DEFAULT_LOGO_SRC = "/comworker-logo.webp";

export default function PlatformConfigPage() {
  const [name, setName] = useState("");
  const [logo, setLogo] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getPlatformConfig()
      .then((cfg) => {
        setName(cfg.name);
        setLogo(cfg.logo);
      })
      .catch((e) => setMessage({ type: "error", text: (e as Error).message }))
      .finally(() => setLoading(false));
  }, []);

  async function saveName() {
    setSaving(true);
    setMessage(null);
    try {
      const res = await updatePlatformConfig({ name });
      setMessage({ type: "success", text: `平台名称已保存为「${res.name}」，刷新客户端页面后生效。` });
    } catch (e) {
      setMessage({ type: "error", text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  }

  async function onPickLogo(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setMessage(null);
    try {
      const res = await uploadPlatformLogo(file);
      setLogo(res.logo);
      setMessage({ type: "success", text: "Logo 已更新并保存，刷新客户端页面后生效。" });
    } catch (err) {
      setMessage({ type: "error", text: (err as Error).message });
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function removeLogo() {
    setSaving(true);
    setMessage(null);
    try {
      await updatePlatformConfig({ name, logo: "" });
      setLogo(null);
      setMessage({ type: "success", text: "已恢复默认 Logo。" });
    } catch (e) {
      setMessage({ type: "error", text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-gray-500">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        加载中...
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">平台配置</h1>
        <p className="mt-1 text-sm text-gray-500">
          配置客户端（CCA 前端）侧边栏展示的平台名称与 Logo。
        </p>
      </div>

      {message && (
        <div
          className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${
            message.type === "success"
              ? "border-green-200 bg-green-50 text-green-700"
              : "border-red-200 bg-red-50 text-red-700"
          }`}
        >
          {message.type === "success" ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          ) : (
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          )}
          <span className="whitespace-pre-wrap">{message.text}</span>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings className="h-4 w-4" />
            平台名称
          </CardTitle>
          <CardDescription>客户端侧边栏顶部显示的名称，默认「ComWorker」。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-end gap-3">
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="platform-name">平台名称</Label>
              <Input
                id="platform-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="ComWorker"
                maxLength={64}
              />
            </div>
            <Button onClick={() => void saveName()} disabled={saving || !name.trim()}>
              {saving ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Save className="mr-1.5 h-4 w-4" />}
              保存名称
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ImagePlus className="h-4 w-4" />
            平台 Logo
          </CardTitle>
          <CardDescription>客户端侧边栏顶部 Logo。支持 PNG / JPEG / WebP / SVG / GIF，最大 512KB。未设置时使用默认 Logo。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-6">
            <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-full border border-gray-200 bg-gray-50">
              <img
                src={logo || DEFAULT_LOGO_SRC}
                alt="平台 Logo 预览"
                className="h-full w-full object-cover"
                onError={(e) => {
                  (e.target as HTMLImageElement).src = DEFAULT_LOGO_SRC;
                }}
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/svg+xml,image/gif"
                className="hidden"
                onChange={(e) => void onPickLogo(e)}
              />
              <Button variant="outline" onClick={() => fileRef.current?.click()} disabled={uploading}>
                {uploading ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <ImagePlus className="mr-1.5 h-4 w-4" />}
                上传新 Logo
              </Button>
              <Button
                variant="outline"
                className="text-red-600 hover:bg-red-50"
                onClick={() => void removeLogo()}
                disabled={saving || !logo}
              >
                <Trash2 className="mr-1.5 h-4 w-4" />
                恢复默认
              </Button>
            </div>
          </div>
          <p className="mt-4 text-xs text-gray-400">
            上传后立即保存。修改名称或恢复默认后，客户端页面需要刷新（Ctrl+Shift+R）才能看到新效果。
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
