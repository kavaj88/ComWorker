"use client";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  gateway,
  GatewayOptions,
  GatewayToken,
  protocols,
} from "@/lib/gateway-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

export const selectClass = "h-9 rounded-md border bg-white px-3 text-sm";
export function TokenEditor({
  token,
  options,
  onClose,
  onSaved,
}: {
  token: GatewayToken | null;
  options: GatewayOptions;
  onClose: () => void;
  onSaved: (secret?: string) => void;
}) {
  const [name, setName] = useState(token?.name || "");
  const [user, setUser] = useState(token?.user_id || "");
  const [models, setModels] = useState(token?.allowed_models || []);
  const [agents, setAgents] = useState<string[]>(token?.allowed_agents || []);
  const [agentProtocols, setAgentProtocols] = useState<string[]>(token?.allowed_protocols || []);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [permissionUser, setPermissionUser] = useState("");
  const [permissionError, setPermissionError] = useState("");
  const [expiry, setExpiry] = useState(() => {
    if (!token?.expires_at) return "";
    const date = new Date(token.expires_at);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
  });
  const [retain, setRetain] = useState(token?.retain_content || false);
  const [days, setDays] = useState(token?.retention_days || 7);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!user) return;
    let active = true;
    gateway<GatewayOptions>(`/options?user_id=${encodeURIComponent(user)}`)
      .then((data) => {
        if (!active) return;
        setPermissions(data.models.map((m) => m.id));
        setPermissionUser(user);
        setPermissionError("");
      })
      .catch((e) => {
        if (active) {
          setPermissionError(e.message);
          setPermissionUser(user);
        }
      });
    return () => {
      active = false;
    };
  }, [user]);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const body = {
        name: name.trim(),
        allowed_models: models,
        allowed_agents: agents,
        allowed_protocols: agentProtocols,
        expires_at: expiry ? new Date(expiry).toISOString() : null,
        retain_content: retain,
        retention_days: days,
        ...(!token ? { user_id: user } : {}),
      };
      const result = await gateway<{ secret?: string }>(
        token ? `/tokens/${encodeURIComponent(token.id)}` : "/tokens",
        token ? "PATCH" : "POST",
        body,
      );
      onSaved(result.secret);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent className="sm:max-w-xl max-h-[90vh] overflow-auto">
        <DialogTitle>{token ? "编辑令牌" : "创建独立网关令牌"}</DialogTitle>
        <form onSubmit={save} className="space-y-4">
          <label className="block space-y-2">
            名称
            <Input
              required
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：研发工作站"
            />
          </label>
          <label className="block space-y-2">
            所属用户
            <select
              required
              disabled={!!token}
              className={`${selectClass} w-full`}
              value={user}
              onChange={(e) => {
                setUser(e.target.value);
                setModels([]);
              }}
            >
              <option value="">选择用户</option>
              {options.users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="rounded-lg border p-3 space-y-2">
            <legend className="px-1">授权模型白名单</legend>
            <p className="text-xs text-gray-500">
              仅显示该用户当前可用模型；保存及每次调用由服务端校验权限。
            </p>
            {!user ? (
              <p>请先选择用户</p>
            ) : permissionUser !== user ? (
              <p role="status">正在加载用户权限…</p>
            ) : permissionError ? (
              <p role="alert" className="text-red-600">
                权限读取失败：{permissionError}
              </p>
            ) : (
              <div className="max-h-48 overflow-auto space-y-2">
                {options.models
                  .filter((m) => permissions.includes(m.id))
                  .map((m) => (
                    <label key={m.id} className="flex gap-2 items-start">
                      <input
                        type="checkbox"
                        checked={models.includes(m.id)}
                        onChange={(e) =>
                          setModels((previous) =>
                            e.target.checked
                              ? [...previous, m.id]
                              : previous.filter((id) => id !== m.id),
                          )
                        }
                      />
                      <span>
                        {m.name}
                        <span className="block text-xs text-gray-500">
                          {m.id} ·{" "}
                          {m.protocols
                            .map((p) => protocols[p] || p)
                            .join(" / ") || "尚未声明协议"}
                        </span>
                      </span>
                    </label>
                  ))}
                {!options.models.some((m) => permissions.includes(m.id)) && (
                  <p>该用户暂无可用模型</p>
                )}
              </div>
            )}
            {models
              .filter((id) => !permissions.includes(id))
              .map((id) => (
                <label key={id} className="flex gap-2 text-amber-700">
                  <input
                    type="checkbox"
                    checked
                    onChange={() => setModels(models.filter((m) => m !== id))}
                  />
                  {id}（权限已变化，请取消）
                </label>
              ))}
          </fieldset>
          <fieldset className="rounded-lg border p-3 space-y-2">
            <legend className="px-1">授权协议</legend>
            <p className="text-xs text-gray-500">
              决定该令牌可用于哪种客户端协议：OpenAI 对应 Chat Completions，Anthropic
              对应 Messages。留空则仅能调用模型网关，无法使用 Agent 协议接入。
            </p>
            <div className="flex gap-4">
              {(["openai", "anthropic"] as const).map((p) => (
                <label key={p} className="flex gap-2 items-center">
                  <input
                    type="checkbox"
                    checked={agentProtocols.includes(p)}
                    onChange={(e) =>
                      setAgentProtocols((previous) =>
                        e.target.checked
                          ? [...previous, p]
                          : previous.filter((x) => x !== p),
                      )
                    }
                  />
                  {p === "openai" ? "OpenAI 兼容" : "Anthropic 兼容"}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset className="rounded-lg border p-3 space-y-2">
            <legend className="px-1">授权 Agent 白名单（Agent 协议网关）</legend>
            <p className="text-xs text-gray-500">
              每行一个 Agent ID（如 main、insurance）；留空表示允许该用户全部 Agent。仅在勾选上述协议后生效。
            </p>
            <textarea
              className="w-full rounded-md border bg-white p-2 text-sm font-mono"
              rows={3}
              placeholder={"main\ninsurance"}
              value={agents.join("\n")}
              onChange={(e) =>
                setAgents(
                  e.target.value
                    .split(/[\s,]+/)
                    .map((s) => s.trim())
                    .filter(Boolean),
                )
              }
            />
            {agents.length > 0 && (
              <p className="text-xs text-gray-500">{agents.length} 个 Agent 已授权</p>
            )}
          </fieldset>
          <label className="block space-y-2">
            有效期（留空为长期）
            <Input
              type="datetime-local"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
            />
          </label>
          <label className="flex gap-2">
            <input
              type="checkbox"
              checked={retain}
              onChange={(e) => setRetain(e.target.checked)}
            />
            保存请求及响应正文
          </label>
          <p className="text-xs text-gray-500">
            默认只记录元数据。开启后正文可能包含敏感内容，每侧最多 256
            KiB，超出将标记截断。
          </p>
          {retain && (
            <label className="block space-y-2">
              正文保留天数
              <Input
                type="number"
                min={1}
                max={90}
                required
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
              />
            </label>
          )}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onClose}
            >
              取消
            </Button>
            <Button
              type="submit"
              disabled={
                busy ||
                !name.trim() ||
                !user ||
                (!models.length && !agents.length) ||
                permissionUser !== user ||
                !!permissionError
              }
            >
              {busy ? "保存中…" : "保存令牌"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
