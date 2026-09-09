"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy, Eye, EyeOff, KeyRound, Plus, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  gateway,
  GatewayOptions,
  GatewayRoute,
  GatewayToken,
  formatTime,
  getTokenSecret,
} from "@/lib/gateway-api";
import { TokenEditor, selectClass } from "@/components/gateway/token-editor";
import {
  TokenDetails,
  SecretDialog,
  RouteSettings,
} from "@/components/gateway/token-details";
import { RequestPanel, UsagePanel } from "@/components/gateway/activity-panels";

function tokenStatus(token: GatewayToken) {
  if (token.revoked_at) return "已吊销";
  if (!token.enabled) return "已停用";
  if (token.expires_at && Date.parse(token.expires_at) <= Date.now())
    return "已过期";
  return "启用";
}

export default function GatewayPage() {
  const [tab, setTab] = useState("tokens");
  const [tokens, setTokens] = useState<GatewayToken[]>([]);
  const [options, setOptions] = useState<GatewayOptions | null>(null);
  const [routes, setRoutes] = useState<GatewayRoute[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [editor, setEditor] = useState<GatewayToken | "new" | null>(null);
  const [details, setDetails] = useState<GatewayToken | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [revealLoading, setRevealLoading] = useState<string | null>(null);
  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [t, o, r] = await Promise.all([
        gateway<{ items: GatewayToken[] }>("/tokens"),
        gateway<GatewayOptions>("/options"),
        gateway<{ items: GatewayRoute[] }>("/routes"),
      ]);
      setTokens(t.items);
      setOptions(o);
      setRoutes(r.items);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void reload();
  }, [reload]);

  async function change(token: GatewayToken, revoke = false) {
    if (busy) return;
    if (
      revoke &&
      !window.confirm(
        `永久吊销「${token.name}」？使用此令牌的客户端将无法继续调用，且不可恢复。`,
      )
    )
      return;
    setBusy(token.id);
    try {
      await gateway(
        `/tokens/${encodeURIComponent(token.id)}${revoke ? "/revoke" : ""}`,
        revoke ? "POST" : "PATCH",
        revoke ? undefined : { enabled: !token.enabled },
      );
      toast.success(
        revoke ? "令牌已吊销" : token.enabled ? "令牌已停用" : "令牌已启用",
      );
      await reload();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const visible = tokens.filter(
    (t) =>
      (!search ||
        `${t.name} ${t.token_hint}`
          .toLowerCase()
          .includes(search.toLowerCase())) &&
      (!userFilter || t.user_id === userFilter) &&
      (!statusFilter || tokenStatus(t) === statusFilter),
  );

  async function toggleReveal(token: GatewayToken) {
    if (revealed[token.id] !== undefined) {
      setRevealed((m) => {
        const n = { ...m };
        delete n[token.id];
        return n;
      });
      return;
    }
    setRevealLoading(token.id);
    try {
      const value = await getTokenSecret(token.id);
      if (value) {
        setRevealed((m) => ({ ...m, [token.id]: value }));
      } else {
        toast.error(
          "该令牌创建于「可查看明文」功能上线前，无明文记录；请吊销后重新创建。",
        );
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRevealLoading(null);
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <KeyRound className="h-6 w-6 text-slate-600" />
            <h1 className="text-2xl font-semibold tracking-tight">API 网关</h1>
          </div>
          <p className="mt-2 text-sm text-gray-500">
            为本地开发工具分配模型权限，查看每个令牌的调用与用量。
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" disabled={loading} onClick={reload}>
            <RefreshCw className={loading ? "animate-spin" : ""} />
            刷新
          </Button>
          <Button
            disabled={!options || loading}
            onClick={() => setEditor("new")}
          >
            <Plus />
            创建令牌
          </Button>
        </div>
      </div>
      <div
        className="flex gap-6 border-b"
        role="tablist"
        aria-label="API 网关页面"
      >
        {[
          ["tokens", "访问令牌"],
          ["requests", "请求记录"],
          ["usage", "用量概览"],
        ].map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`border-b-2 px-1 pb-3 text-sm font-medium ${tab === id ? "border-slate-900 text-slate-900" : "border-transparent text-gray-500 hover:text-gray-900"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {error ? (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-700"
        >
          加载失败：{error}
          <Button className="ml-3" variant="outline" onClick={reload}>
            重试
          </Button>
        </div>
      ) : !options ? (
        <p role="status" className="py-16 text-center text-gray-500">
          正在加载网关配置…
        </p>
      ) : (
        <>
          {tab === "tokens" && (
            <div className="space-y-5">
              <div className="flex flex-wrap items-center gap-3">
                <Input
                  className="max-w-xs"
                  placeholder="搜索令牌名称或标识"
                  aria-label="搜索令牌"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                <select
                  aria-label="所属用户"
                  className={selectClass}
                  value={userFilter}
                  onChange={(e) => setUserFilter(e.target.value)}
                >
                  <option value="">全部用户</option>
                  {options.users.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.username}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="令牌状态"
                  className={selectClass}
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  <option value="">全部状态</option>
                  {["启用", "已停用", "已过期", "已吊销"].map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
                <span className="ml-auto text-sm text-gray-500">
                  {tokens.filter((t) => tokenStatus(t) === "启用").length}{" "}
                  个有效令牌
                </span>
              </div>
              <div className="overflow-auto rounded-xl border bg-white">
                <table className="w-full text-left text-sm">
                  <thead className="bg-gray-50 text-gray-500">
                    <tr>
                      {[
                        "名称 / 标识",
                        "所属用户",
                        "授权模型",
                        "最近使用",
                        "有效期",
                        "状态",
                        "操作",
                      ].map((label) => (
                        <th className="whitespace-nowrap p-3" key={label}>
                          {label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((token) => (
                      <tr
                        key={token.id}
                        className="border-t hover:bg-slate-50/50"
                      >
                        <td className="p-3">
                          <button
                            className="font-medium hover:underline"
                            onClick={() => setDetails(token)}
                          >
                            {token.name}
                          </button>
                          <p className="mt-1 flex items-center gap-1 font-mono text-xs text-gray-400">
                            <span className="break-all">
                              {revealed[token.id] ?? token.token_hint}
                            </span>
                            <button
                              type="button"
                              title={revealed[token.id] ? "隐藏完整令牌" : "显示完整令牌"}
                              className="rounded p-1 text-gray-400 hover:bg-slate-100 hover:text-slate-700"
                              disabled={revealLoading === token.id}
                              onClick={() => toggleReveal(token)}
                            >
                              {revealed[token.id] ? (
                                <EyeOff className="h-3.5 w-3.5" />
                              ) : (
                                <Eye className="h-3.5 w-3.5" />
                              )}
                            </button>
                            {revealed[token.id] && (
                              <button
                                type="button"
                                title="复制令牌"
                                className="rounded p-1 text-gray-400 hover:bg-slate-100 hover:text-slate-700"
                                onClick={() => copySecret(revealed[token.id])}
                              >
                                <Copy className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </p>
                        </td>
                        <td className="p-3">
                          {token.username || "用户已移除"}
                        </td>
                        <td
                          className="p-3"
                          title={token.allowed_models.join("\n")}
                        >
                          {token.allowed_models.length} 个模型
                        </td>
                        <td className="whitespace-nowrap p-3 text-gray-500">
                          {formatTime(token.last_used_at)}
                        </td>
                        <td className="whitespace-nowrap p-3">
                          {token.expires_at
                            ? formatTime(token.expires_at)
                            : "长期"}
                        </td>
                        <td className="p-3">
                          <span
                            className={`whitespace-nowrap rounded-full px-2 py-1 text-xs ${tokenStatus(token) === "启用" ? "bg-emerald-50 text-emerald-700" : "bg-gray-100 text-gray-500"}`}
                          >
                            {tokenStatus(token)}
                          </span>
                        </td>
                        <td className="p-3">
                          <div className="flex gap-1">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setDetails(token)}
                            >
                              接入
                            </Button>
                            {!token.revoked_at && (
                              <>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  disabled={!!busy}
                                  onClick={() => setEditor(token)}
                                >
                                  编辑
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  disabled={!!busy}
                                  onClick={() => change(token)}
                                >
                                  {token.enabled ? "停用" : "启用"}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="destructive"
                                  disabled={!!busy}
                                  onClick={() => change(token, true)}
                                >
                                  吊销
                                </Button>
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!visible.length && (
                  <div className="py-14 text-center">
                    <KeyRound className="mx-auto mb-3 h-8 w-8 text-slate-300" />
                    <p className="font-medium">
                      {tokens.length ? "没有匹配的令牌" : "创建第一个访问令牌"}
                    </p>
                    <p className="mt-2 text-sm text-gray-500">
                      {tokens.length
                        ? "调整筛选条件后重试。"
                        : "选择用户和模型白名单，即可生成独立的客户端凭据。"}
                    </p>
                  </div>
                )}
              </div>
              <RouteSettings routes={routes} onSaved={reload} />
            </div>
          )}
          {tab === "requests" && (
            <RequestPanel options={options} tokens={tokens} />
          )}
          {tab === "usage" && <UsagePanel tokens={tokens} />}
          {editor && (
            <TokenEditor
              key={editor === "new" ? "new" : editor.id}
              token={editor === "new" ? null : editor}
              options={options}
              onClose={() => setEditor(null)}
              onSaved={(value) => {
                setEditor(null);
                if (value) setSecret(value);
                void reload();
              }}
            />
          )}
          {details && (
            <TokenDetails
              token={details}
              options={options}
              onClose={() => setDetails(null)}
            />
          )}
        </>
      )}
      {secret && (
        <SecretDialog secret={secret} onClose={() => setSecret(null)} />
      )}
    </div>
  );
}
