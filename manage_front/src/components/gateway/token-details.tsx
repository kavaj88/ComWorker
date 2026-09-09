"use client";
import { useState } from "react";
import { toast } from "sonner";
import {
  gateway,
  GatewayOptions,
  GatewayRoute,
  GatewayToken,
  getTokenSecret,
  protocols,
  formatTime,
} from "@/lib/gateway-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Copy, Eye, EyeOff } from "lucide-react";

export function TokenDetails({
  token,
  options,
  onClose,
}: {
  token: GatewayToken;
  options: GatewayOptions;
  onClose: () => void;
}) {
  const [base, setBase] = useState(
    () =>
      process.env.NEXT_PUBLIC_API_URL ||
      (typeof window !== "undefined" ? window.location.origin : ""),
  );
  const [secret, setSecret] = useState<string | null | undefined>(undefined);
  const [secretLoading, setSecretLoading] = useState(false);
  async function revealSecret() {
    if (secret !== undefined) {
      setSecret(undefined);
      return;
    }
    setSecretLoading(true);
    try {
      const value = await getTokenSecret(token.id);
      setSecret(value);
      if (value === null) {
        toast.error(
          "该令牌创建于「可查看明文」功能上线前，无明文记录；请吊销后重新创建。",
        );
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSecretLoading(false);
    }
  }
  async function copySecret(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("令牌已复制");
    } catch {
      toast.error("复制失败，请手动选中复制");
    }
  }
  const modelOptions = options.models.filter((m) =>
    token.allowed_models.includes(m.id),
  );
  const supported = [...new Set(modelOptions.flatMap((m) => m.protocols))];
  let valid = false;
  try {
    const url = new URL(base);
    valid =
      ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash;
  } catch {}
  const endpoint = `${base.replace(/\/+$/, "")}${options.gateway_path}`;
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-auto">
        <DialogTitle>{token.name} · 详情与接入</DialogTitle>
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-gray-500">所属用户</dt>
            <dd>{token.username}</dd>
          </div>
          <div>
            <dt className="text-gray-500">令牌标识</dt>
            <dd className="flex items-center gap-2">
              <span className="break-all font-mono text-xs">
                {secret === undefined ? token.token_hint : secret === null ? "无明文记录" : secret}
              </span>
              <button
                type="button"
                title={secret !== undefined ? "隐藏完整令牌" : "显示完整令牌"}
                className="rounded p-1 text-gray-400 hover:bg-slate-100 hover:text-slate-700"
                disabled={secretLoading}
                onClick={revealSecret}
              >
                {secret !== undefined ? (
                  <EyeOff className="h-3.5 w-3.5" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
              </button>
              {secret && (
                <button
                  type="button"
                  title="复制令牌"
                  className="rounded p-1 text-gray-400 hover:bg-slate-100 hover:text-slate-700"
                  onClick={() => copySecret(secret)}
                >
                  <Copy className="h-3.5 w-3.5" />
                </button>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">有效期</dt>
            <dd>{token.expires_at ? formatTime(token.expires_at) : "长期"}</dd>
          </div>
          <div>
            <dt className="text-gray-500">最近调用</dt>
            <dd>{formatTime(token.last_used_at)}</dd>
          </div>
          <div>
            <dt className="text-gray-500">创建时间</dt>
            <dd>{formatTime(token.created_at)}</dd>
          </div>
          <div>
            <dt className="text-gray-500">正文留存</dt>
            <dd>
              {token.retain_content ? `${token.retention_days} 天` : "关闭"}
            </dd>
          </div>
        </dl>
        <label className="space-y-2">
          平台公网 URL
          <Input
            value={base}
            onChange={(e) => setBase(e.target.value)}
            placeholder="https://你的平台域名"
          />
        </label>
        <p className="text-xs text-amber-700">
          该地址需要配置到平台网关的转发，并关闭流式响应缓冲；默认管理端地址不代表已完成公网接入配置。下方为通用协议参数，未进行
          Codex、Claude 或 OpenCode 客户端实测。
        </p>
        {!valid ? (
          <p role="alert" className="text-red-600">
            请输入有效的 HTTP(S) 平台 URL，不含凭据、查询参数或片段。
          </p>
        ) : (
          <div className="space-y-3">
            {supported.map((protocol) => (
              <div key={protocol} className="rounded-lg border p-3">
                <h3 className="font-medium">
                  {protocols[protocol] || protocol}
                </h3>
                <pre className="mt-2 overflow-auto text-xs whitespace-pre-wrap break-all">{`URL: ${endpoint}${protocol === "responses" ? "/responses" : protocol === "anthropic_messages" ? "/messages" : "/chat/completions"}\n${protocol === "anthropic_messages" ? "x-api-key: <YOUR_GATEWAY_TOKEN>\nanthropic-version: 2023-06-01" : "Authorization: Bearer <YOUR_GATEWAY_TOKEN>"}\n模型: ${modelOptions
                  .filter((m) => m.protocols.includes(protocol))
                  .map((m) => m.id)
                  .join(", ")}`}</pre>
              </div>
            ))}
            {!supported.length && (
              <p>授权模型尚未声明可用协议，请检查上游设置。</p>
            )}
          </div>
        )}
        <div>
          <h3 className="font-medium">已保存的授权模型</h3>
          <p className="text-xs break-all">{token.allowed_models.join("、")}</p>
        </div>
      </DialogContent>
    </Dialog>
  );
}
export function SecretDialog({
  secret,
  onClose,
}: {
  secret: string;
  onClose: () => void;
}) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(secret);
      toast.success("令牌已复制");
    } catch {
      toast.error("复制失败，请手动选中令牌复制");
    }
  }
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogTitle>令牌已创建</DialogTitle>
        <p>
          请立即复制并妥善保存。如需稍后查看，可在令牌列表中点击小眼睛重新显示。
        </p>
        <pre className="select-all whitespace-pre-wrap break-all rounded-lg bg-gray-100 p-4">
          {secret}
        </pre>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={copy}>
            复制令牌
          </Button>
          <Button onClick={onClose}>已保存，关闭</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
export function RouteSettings({
  routes,
  onSaved,
}: {
  routes: GatewayRoute[];
  onSaved: () => void;
}) {
  return (
    <details className="rounded-xl border bg-white p-4">
      <summary className="cursor-pointer font-medium">上游协议能力设置</summary>
      <p className="my-3 text-sm text-gray-500">
        声明供应商实际支持的原生协议，网关仅转发同协议请求，不转换协议。变更前请确认上游能力。
      </p>
      <div className="space-y-3">
        {routes.map((route) => (
          <RouteRow
            key={`${route.provider_id}:${route.protocols.join()}`}
            route={route}
            onSaved={onSaved}
          />
        ))}
        {!routes.length && <p>暂无可配置的供应商</p>}
      </div>
    </details>
  );
}
function RouteRow({
  route,
  onSaved,
}: {
  route: GatewayRoute;
  onSaved: () => void;
}) {
  const [selected, setSelected] = useState(route.protocols);
  const [busy, setBusy] = useState(false);
  async function save() {
    if (
      busy ||
      !window.confirm(
        `确认更新「${route.name}」支持的协议？这将影响网关可接收的请求。`,
      )
    )
      return;
    setBusy(true);
    try {
      await gateway(`/routes/${encodeURIComponent(route.provider_id)}`, "PUT", {
        protocols: selected,
      });
      toast.success("协议能力已保存");
      onSaved();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-4 border-t pt-3">
      <strong className="min-w-32 text-sm">{route.name}</strong>
      {Object.entries(protocols).map(([key, label]) => (
        <label key={key} className="flex gap-2 text-sm">
          <input
            type="checkbox"
            disabled={busy}
            checked={selected.includes(key)}
            onChange={(e) =>
              setSelected(
                e.target.checked
                  ? [...selected, key]
                  : selected.filter((p) => p !== key),
              )
            }
          />
          {label}
        </label>
      ))}
      <Button
        size="sm"
        variant="outline"
        disabled={
          busy ||
          [...selected].sort().join() === [...route.protocols].sort().join()
        }
        onClick={save}
      >
        {busy ? "保存中…" : "保存"}
      </Button>
    </div>
  );
}
