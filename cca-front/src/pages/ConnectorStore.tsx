import { useEffect, useState, type ReactNode } from "react";
import { Search } from "lucide-react";
import { fetchJSON } from "../lib/api";

interface PlatformConnector {
  id: string
  name: string
  display_name: string
  description: string
  examples?: string | null
  icon?: string | null
  transport: string
  credential_strategy: string
  needs_auth: boolean
  required_keys?: string[]
  enabled: boolean
  locked: boolean
}

interface CustomServer {
  name: string
  enabled?: boolean
  url?: string
  command?: string
  args?: string[]
  env?: Record<string, string>
  headers?: Record<string, string>
  auth?: string
  tools?: number
}

const STRATEGY_LABEL: Record<string, string> = {
  none: "开放",
  shared: "共享密钥",
  api_key: "API Key",
  oauth: "OAuth 授权",
  cli: "CLI 登录",
}

function matchesConnector(c: PlatformConnector, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [c.display_name, c.name, c.description].filter(Boolean).join(" ").toLowerCase().includes(q)
}

function matchesCustom(s: CustomServer, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [s.name, s.url, s.command].filter(Boolean).join(" ").toLowerCase().includes(q)
}

export default function ConnectorStore() {
  const [tab, setTab] = useState<"catalog" | "mine">("catalog")
  const [catalog, setCatalog] = useState<PlatformConnector[]>([])
  const [custom, setCustom] = useState<CustomServer[]>([])
  // The custom-server management endpoint (/api/comworker/mcp/servers) is only
  // implemented in the dev FastAPI server, not in the production aiohttp
  // gateway — so it 404s on every load in production. Track that so we don't
  // spam a scary red "404" box, and can hide the (non-functional) add button.
  const [customUnavailable, setCustomUnavailable] = useState(false)
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState("")
  const [msgType, setMsgType] = useState<"info" | "error" | "success">("info")
  const [busy, setBusy] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [search, setSearch] = useState("")

  // per-strategy flows (api_key still needs a key-entry modal; install/auth do not)
  const [apiKey, setApiKey] = useState<PlatformConnector | null>(null)

  function showMessage(text: string, type: "info" | "error" | "success" = "info") {
    setMessage(text)
    setMsgType(type)
  }

  async function loadCatalog() {
    try {
      const res = await fetchJSON<{ connectors: PlatformConnector[] }>("/api/connectors")
      setCatalog(res.connectors)
    } catch (e) {
      showMessage(e instanceof Error ? e.message : "加载平台连接器失败", "error")
    }
  }
  async function loadCustom() {
    try {
      const res = await fetchJSON<CustomServer[]>("/api/comworker/mcp/servers")
      setCustom(Array.isArray(res) ? res : [])
      setCustomUnavailable(false)
    } catch (e) {
      const msg = e instanceof Error ? e.message : ""
      // 404 = endpoint not deployed in this environment; treat as "no custom
      // servers" instead of showing a red error box on every page refresh.
      if (msg.includes("404")) {
        setCustom([])
        setCustomUnavailable(true)
        return
      }
      showMessage(msg || "加载自定义连接器失败", "error")
    }
  }
  useEffect(() => {
    setLoading(true)
    Promise.all([loadCatalog(), loadCustom()]).finally(() => setLoading(false))
  }, [])

  async function setEnabled(c: PlatformConnector, enabled: boolean) {
    setBusy(c.id)
    try {
      if (enabled) await fetchJSON(`/api/connectors/${c.id}/enable`, { method: "PUT" })
      else await fetchJSON(`/api/connectors/${c.id}/disable`, { method: "PUT" })
      await loadCatalog()
    } catch (e) {
      showMessage(e instanceof Error ? e.message : "操作失败", "error")
    } finally {
      setBusy(null)
    }
  }

  async function startOAuth(c: PlatformConnector) {
    setBusy(c.id)
    // Open the target window synchronously inside the click handler (user
    // gesture) so popup blockers don't silently kill it: for a first-time
    // enable the container restart below can take ~2 minutes, and any
    // window.open after that await would be outside the user-activation
    // window and blocked without any error shown.
    const win = window.open("", "mcp_oauth", "width=480,height=720")
    try {
      // Only enable when not already enabled. Re-authorizing an enabled
      // connector must NOT restart the hermes runtime (~2 min warmup) —
      // otherwise oauth/start lands in the startup window and the popup
      // above is silently blocked before we ever navigate it.
      if (!c.enabled) {
        await fetchJSON(`/api/connectors/${c.id}/enable`, { method: "PUT" })
      }
      const redirect_uri = `${window.location.origin}/api/comworker/mcp/${encodeURIComponent(c.name)}/oauth/callback`
      const res = await fetchJSON<{ auth_url: string; state: string }>(
        `/api/comworker/mcp/${encodeURIComponent(c.name)}/oauth/start`,
        { method: "POST", body: JSON.stringify({ redirect_uri }) },
      )
      // Navigate the pre-opened window to the provider's authorization page
      // (external, not our own modal). The user completes login there; we
      // poll status below.
      if (win) {
        win.location.href = res.auth_url
      } else {
        // Popup was blocked — fall back to surfacing the link inline.
        showMessage(
          `「${c.display_name}」已发起授权，请在浏览器打开以下链接完成登录授权：\n${res.auth_url}\n\n授权完成后状态会自动生效。`,
          "info",
        )
      }
      const timer = setInterval(async () => {
        try {
          const st = await fetchJSON<{ status: string; error?: string }>(
            `/api/comworker/mcp/${encodeURIComponent(c.name)}/oauth/poll?state=${res.state}`,
          )
          if (st.status === "done" || st.status === "error") {
            clearInterval(timer)
            if (st.status === "error") showMessage(`授权失败：${st.error || "未知错误"}`, "error")
            else showMessage(`「${c.display_name}」授权成功，连接器已就绪`, "success")
            await loadCatalog()
          }
        } catch {
          /* keep polling */
        }
      }, 1500)
    } catch (e) {
      win?.close()
      showMessage(e instanceof Error ? e.message : "发起授权失败", "error")
    } finally {
      setBusy(null)
    }
  }

  async function submitApiKey(c: PlatformConnector, values: Record<string, string>) {
    setBusy(c.id)
    try {
      await fetchJSON(`/api/connectors/${c.id}/enable`, {
        method: "PUT",
        body: JSON.stringify({ credentials: values }),
      })
      setApiKey(null)
      showMessage(`「${c.display_name}」已启用`, "success")
      await loadCatalog()
    } catch (e) {
      showMessage(e instanceof Error ? e.message : "启用失败", "error")
    } finally {
      setBusy(null)
    }
  }

  async function runCli(c: PlatformConnector) {
    // No modal: show a spinner on the card (busy state) and surface the
    // result/error in the box above the list.
    setBusy(c.id)
    try {
      // Only enable when the connector is not already enabled. Re-authorizing
      // (e.g. refreshing a CLI OAuth token) should not trigger a container
      // restart, because the hermes runtime can take ~2 minutes to become ready
      // after a restart, and the following cli/install + cli/auth calls would
      // hit that startup window and fail with "starting up".
      if (!c.enabled) {
        await fetchJSON(`/api/connectors/${c.id}/enable`, { method: "PUT" })
      }
      await fetchJSON(`/api/comworker/mcp/${encodeURIComponent(c.name)}/cli/install`, { method: "POST" })
      const res = await fetchJSON<{
        outputs?: string[]
        authorize_url?: string | null
        status?: string
      }>(`/api/comworker/mcp/${encodeURIComponent(c.name)}/cli/auth`, { method: "POST" })
      const authOut = (res.outputs || []).join("\n").trim()
      // Device-code CLIs (tmeet, lark, ...) return an authorize URL and keep
      // waiting for the user to finish the browser step. Surface the URL and
      // verify via cli/status instead of falsely claiming success on HTTP 200.
      if (res.authorize_url) {
        showMessage(
          `「${c.display_name}」已发起授权，请在浏览器打开以下链接完成登录授权：\n${res.authorize_url}\n\n授权完成后状态会自动生效。`,
          "info",
        )
        const ok = await pollCliLogin(c)
        if (ok) {
          showMessage(`「${c.display_name}」授权成功，已可使用。`, "success")
        } else {
          showMessage(
            `「${c.display_name}」尚未在浏览器完成授权。请打开上面的链接登录并确认授权后，再次点击「重新授权」。`,
            "error",
          )
        }
      } else {
        showMessage(
          authOut ? `「${c.display_name}」安装并授权完成：\n${authOut}` : `「${c.display_name}」安装并授权完成。`,
          "success",
        )
      }
      await loadCatalog()
    } catch (e) {
      const detail = e instanceof Error ? e.message : "执行失败"
      showMessage(`「${c.display_name}」安装/授权失败：\n${detail}`, "error")
    } finally {
      setBusy(null)
    }
  }

  // Poll cli/status until the CLI reports "Logged in" (device-code flow
  // completed in the browser) or we time out. Returns true on success.
  async function pollCliLogin(c: PlatformConnector, maxRounds = 40): Promise<boolean> {
    for (let i = 0; i < maxRounds; i++) {
      await new Promise((r) => setTimeout(r, 3000))
      try {
        const st = await fetchJSON<{ output?: string }>(
          `/api/comworker/mcp/${encodeURIComponent(c.name)}/cli/status`,
          { method: "POST" },
        )
        if (/logged in/i.test(st.output || "")) return true
      } catch {
        // ignore transient polling errors
      }
    }
    return false
  }

  async function toggleCustom(s: CustomServer) {
    setBusy(s.name)
    try {
      await fetchJSON(`/api/comworker/mcp/servers/${encodeURIComponent(s.name)}/enabled`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !s.enabled }),
      })
      await loadCustom()
    } catch (e) {
      showMessage(e instanceof Error ? e.message : "操作失败", "error")
    } finally {
      setBusy(null)
    }
  }
  async function removeCustom(s: CustomServer) {
    if (!confirm(`删除自定义连接器「${s.name}」？`)) return
    setBusy(s.name)
    try {
      await fetchJSON(`/api/comworker/mcp/servers/${encodeURIComponent(s.name)}`, { method: "DELETE" })
      await loadCustom()
    } catch (e) {
      showMessage(e instanceof Error ? e.message : "删除失败", "error")
    } finally {
      setBusy(null)
    }
  }
  async function testCustom(s: CustomServer) {
    setBusy(s.name)
    try {
      const res = await fetchJSON<{ ok?: boolean; tools?: unknown[]; error?: string }>(
        `/api/comworker/mcp/servers/${encodeURIComponent(s.name)}/test`,
        { method: "POST" },
      )
      if (res.error) showMessage(`测试失败：${res.error}`, "error")
      else showMessage(`测试成功，发现 ${res.tools?.length ?? 0} 个工具`, "success")
    } catch (e) {
      showMessage(e instanceof Error ? e.message : "测试失败", "error")
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden px-4 py-4 sm:px-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold leading-tight tracking-normal text-light-text sm:text-[28px]">
            连接器
          </h1>
          <p className="mt-1 text-sm text-light-text-secondary">
            启用官方 MCP 连接器，或添加自己的 MCP Server（stdio / HTTP / SSE）。
          </p>
        </div>
        {tab === "mine" && !customUnavailable && (
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className="inline-flex h-9 items-center rounded-lg border border-light-border bg-light-card px-3 text-sm font-medium text-light-text shadow-sm transition-colors hover:bg-accent-blue/10 hover:text-accent-blue"
          >
            + 添加连接器
          </button>
        )}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex gap-2">
          <TabButton active={tab === "catalog"} onClick={() => setTab("catalog")}>
            平台连接器
          </TabButton>
          <TabButton active={tab === "mine"} onClick={() => setTab("mine")}>
            我的连接器
          </TabButton>
        </div>
        <div className="relative flex-1 min-w-[180px] sm:max-w-xs">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-light-text-secondary"
          />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索连接器名称或描述"
            className="h-9 w-full rounded-lg border border-light-border bg-light-card pl-8 pr-3 text-sm text-light-text placeholder:text-light-text-secondary focus:border-accent-blue focus:outline-none"
          />
        </div>
      </div>

      {message && (
        <div
          className={`mb-3 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md border px-3 py-2 text-sm ${
            msgType === "error"
              ? "border-accent-red/40 bg-accent-red/10 text-accent-red"
              : msgType === "success"
                ? "border-green-400/50 bg-green-50 text-green-700"
                : "border-light-border bg-light-card text-light-text-secondary"
          }`}
        >
          {message}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-light-text-secondary">加载中...</p>
      ) : tab === "catalog" ? (
        (() => {
          const list = catalog.filter((c) => matchesConnector(c, search))
          return list.length === 0 ? (
            <EmptyHint text={search ? "没有匹配的连接器。" : "暂无平台连接器。管理员可在管理端「连接器管理」中添加。"} />
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto pr-1 grid gap-3 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
              {list.map((c) => (
                <CatalogCard
                  key={c.id}
                  c={c}
                  busy={busy === c.id}
                  onEnable={() => setEnabled(c, true)}
                  onDisable={() => setEnabled(c, false)}
                  onOAuth={() => startOAuth(c)}
                  onApiKey={() => setApiKey(c)}
                  onCli={() => runCli(c)}
                />
              ))}
            </div>
          )
        })()
      ) : (() => {
        // 我的连接器：已开通（enabled）的平台连接器 + 自定义 MCP Server（若可用）
        const enabledList = catalog.filter((c) => c.enabled && matchesConnector(c, search))
        const customList = custom.filter((s) => matchesCustom(s, search))
        const showCustom = !customUnavailable
        const isEmpty = enabledList.length === 0 && (!showCustom || customList.length === 0)
        if (isEmpty) {
          if (search) return <EmptyHint text="没有匹配的连接器。" />
          if (!showCustom) return <EmptyHint text="你还没有开通任何连接器，去「平台连接器」启用即可。" />
          return <EmptyHint text="你还没有开通任何连接器，也没有自定义 MCP Server。去「平台连接器」启用，或点击右上角「添加连接器」。" />
        }
        return (
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto pr-1">
            {enabledList.length > 0 && (
              <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
                {enabledList.map((c) => (
                  <CatalogCard
                    key={c.id}
                    c={c}
                    busy={busy === c.id}
                    onEnable={() => setEnabled(c, true)}
                    onDisable={() => setEnabled(c, false)}
                    onOAuth={() => startOAuth(c)}
                    onApiKey={() => setApiKey(c)}
                    onCli={() => runCli(c)}
                  />
                ))}
              </div>
            )}
            {showCustom && customList.length > 0 && (
              <div>
                <h2 className="mb-2 text-sm font-semibold text-light-text-secondary">自定义 MCP Server</h2>
                <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
                  {customList.map((s) => (
                    <div
                      key={s.name}
                      className="flex flex-col rounded-lg border border-light-border bg-light-card p-4 shadow-sm"
                    >
                      <div className="mb-1 flex items-start justify-between gap-2">
                        <h3 className="truncate text-sm font-semibold text-light-text">{s.name}</h3>
                        <span
                          className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${
                            s.enabled ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"
                          }`}
                        >
                          {s.enabled ? "启用" : "停用"}
                        </span>
                      </div>
                      <p className="mb-3 truncate text-xs text-light-text-secondary">
                        {s.url || (s.command ? `命令: ${s.command}` : "stdio")}
                      </p>
                      <div className="mt-auto flex flex-wrap gap-2">
                        <button
                          type="button"
                          disabled={busy === s.name}
                          onClick={() => toggleCustom(s)}
                          className="inline-flex h-8 items-center rounded-lg bg-accent-blue/10 px-3 text-xs font-medium text-accent-blue shadow-sm transition-colors hover:bg-accent-blue/20 disabled:opacity-40"
                        >
                          {s.enabled ? "停用" : "启用"}
                        </button>
                        <button
                          type="button"
                          disabled={busy === s.name}
                          onClick={() => testCustom(s)}
                          className="inline-flex h-8 items-center rounded-lg border border-light-border px-3 text-xs font-medium text-light-text shadow-sm transition-colors hover:bg-light-hover disabled:opacity-40"
                        >
                          测试
                        </button>
                        <button
                          type="button"
                          disabled={busy === s.name}
                          onClick={() => removeCustom(s)}
                          className="inline-flex h-8 items-center rounded-lg bg-accent-red/10 px-3 text-xs font-medium text-accent-red shadow-sm transition-colors hover:bg-accent-red/20 disabled:opacity-40"
                        >
                          删除
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )
      })()}

      {showAdd && (
        <AddCustomModal
          onClose={() => setShowAdd(false)}
          onAdded={() => {
            setShowAdd(false)
            void loadCustom()
          }}
        />
      )}

      {apiKey && (
        <ApiKeyModal
          connector={apiKey}
          onClose={() => setApiKey(null)}
          onSubmit={(values) => submitApiKey(apiKey, values)}
        />
      )}
    </div>
  )
}

function CatalogCard({
  c,
  busy,
  onEnable,
  onDisable,
  onOAuth,
  onApiKey,
  onCli,
}: {
  c: PlatformConnector
  busy: boolean
  onEnable: () => void
  onDisable: () => void
  onOAuth: () => void
  onApiKey: () => void
  onCli: () => void
}) {
  const examples = c.examples ? c.examples.split("\n").filter(Boolean) : []
  return (
    <div className="flex flex-col rounded-lg border border-light-border bg-light-card p-4 shadow-sm">
      <div className="mb-1 flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-light-text">{c.display_name}</h3>
        <span className="shrink-0 rounded bg-light-hover px-1.5 py-0.5 text-[10px] text-light-text-secondary">
          {STRATEGY_LABEL[c.credential_strategy] || c.transport}
        </span>
      </div>
      <p className="mb-2 text-sm leading-relaxed text-light-text-secondary line-clamp-4">
        {c.description || "暂无描述"}
      </p>
      {examples.length > 0 && (
        <details className="mb-2 rounded border border-light-border px-2 py-1 text-xs text-light-text-secondary">
          <summary className="cursor-pointer select-none">示例话术</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {examples.slice(0, 4).map((ex, i) => (
              <li key={i}>{ex}</li>
            ))}
          </ul>
        </details>
      )}
      <div className="mt-auto flex items-center justify-between">
        {c.locked ? (
          <span className="text-xs text-amber-600">已强制启用</span>
        ) : (
          <span className="text-xs text-light-text-secondary">
            {c.enabled ? "已启用" : "未启用"}
          </span>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {busy ? (
            <span className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-light-hover px-3 text-xs font-medium text-accent-blue">
              <Spinner />
              处理中…
            </span>
          ) : (
            <>
              {c.credential_strategy === "oauth" && (
                <button
                  type="button"
                  disabled={c.locked}
                  onClick={onOAuth}
                  className="inline-flex h-8 items-center rounded-lg bg-accent-blue/10 px-3 text-xs font-medium text-accent-blue shadow-sm transition-colors hover:bg-accent-blue/20 disabled:opacity-40"
                >
                  {c.enabled ? "重新授权" : "授权"}
                </button>
              )}
              {c.credential_strategy === "api_key" && (
                <button
                  type="button"
                  disabled={c.locked}
                  onClick={onApiKey}
                  className="inline-flex h-8 items-center rounded-lg bg-accent-blue/10 px-3 text-xs font-medium text-accent-blue shadow-sm transition-colors hover:bg-accent-blue/20 disabled:opacity-40"
                >
                  {c.enabled ? "编辑密钥" : "启用"}
                </button>
              )}
              {c.credential_strategy === "cli" && (
                <button
                  type="button"
                  disabled={c.locked}
                  onClick={onCli}
                  className="inline-flex h-8 items-center rounded-lg bg-accent-blue/10 px-3 text-xs font-medium text-accent-blue shadow-sm transition-colors hover:bg-accent-blue/20 disabled:opacity-40"
                >
                  {c.enabled ? "重新授权" : "安装并授权"}
                </button>
              )}
              {(c.credential_strategy === "none" || c.credential_strategy === "shared") &&
                !c.locked && (
                  <button
                    type="button"
                    onClick={() => (c.enabled ? onDisable() : onEnable())}
                    className={`inline-flex h-8 items-center rounded-lg px-3 text-xs font-medium shadow-sm transition-colors ${
                      c.enabled
                        ? "bg-accent-red/10 text-accent-red hover:bg-accent-red/20"
                        : "bg-accent-blue/10 text-accent-blue hover:bg-accent-blue/20"
                    }`}
                  >
                    {c.enabled ? "停用" : "启用"}
                  </button>
                )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
        active ? "bg-accent-blue/10 text-accent-blue" : "text-light-text-secondary hover:bg-light-hover"
      }`}
    >
      {children}
    </button>
  )
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 text-center text-sm text-light-text-secondary">
      {text}
    </div>
  )
}

function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
      role="status"
      aria-label="处理中"
    />
  )
}

function ApiKeyModal({
  connector,
  onClose,
  onSubmit,
}: {
  connector: PlatformConnector
  onClose: () => void
  onSubmit: (values: Record<string, string>) => void
}) {
  const keys = connector.required_keys || []
  const [values, setValues] = useState<Record<string, string>>({})
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-4" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-lg border border-light-border bg-light-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-1 text-base font-semibold text-light-text">填写「{connector.display_name}」密钥</h2>
        <p className="mb-3 text-xs text-light-text-secondary">
          密钥仅保存在你的容器，不会上传到平台。
        </p>
        <div className="space-y-3">
          {keys.length === 0 && (
            <p className="text-sm text-light-text-secondary">该连接器无需额外密钥，可直接启用。</p>
          )}
          {keys.map((k) => (
            <label key={k} className="block">
              <span className="mb-1 block text-xs font-medium text-light-text-secondary">{k}</span>
              <input
                type="password"
                value={values[k] || ""}
                onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))}
                className="w-full rounded border border-light-border px-2 py-1.5 text-sm font-mono"
                placeholder={k}
              />
            </label>
          ))}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded border border-light-border px-3 py-1.5 text-sm text-light-text">
            取消
          </button>
          <button
            onClick={() => onSubmit(values)}
            className="rounded bg-accent-blue px-3 py-1.5 text-sm font-medium text-white"
          >
            启用
          </button>
        </div>
      </div>
    </div>
  )
}

function AddCustomModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [name, setName] = useState("")
  const [transport, setTransport] = useState<"streamable_http" | "sse" | "stdio">("streamable_http")
  const [url, setUrl] = useState("")
  const [command, setCommand] = useState("")
  const [argsText, setArgsText] = useState("")
  const [headersText, setHeadersText] = useState("")
  const [err, setErr] = useState("")
  const [busy, setBusy] = useState(false)

  async function submit() {
    setBusy(true)
    setErr("")
    if (!name) {
      setErr("请填写名称")
      setBusy(false)
      return
    }
    const body: Record<string, unknown> = { name }
    if (transport === "stdio") {
      if (!command) {
        setErr("stdio 类型需填写命令")
        setBusy(false)
        return
      }
      body.command = command
      if (argsText.trim()) body.args = argsText.split(/\s+/).filter(Boolean)
    } else {
      if (!url) {
        setErr("HTTP/SSE 类型需填写 URL")
        setBusy(false)
        return
      }
      body.url = url
    }
    if (headersText.trim()) {
      try {
        body.headers = JSON.parse(headersText)
      } catch {
        setErr("headers 不是合法 JSON")
        setBusy(false)
        return
      }
    }
    try {
      await fetchJSON("/api/comworker/mcp/servers", { method: "POST", body: JSON.stringify(body) })
      onAdded()
    } catch (e) {
      setErr(e instanceof Error ? e.message : "添加失败")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-lg border border-light-border bg-light-card p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-3 text-base font-semibold text-light-text">添加自定义连接器</h2>
        <div className="space-y-3">
          <Labeled label="名称 (server key)">
            <input value={name} onChange={(e) => setName(e.target.value)} className="w-full rounded border border-light-border px-2 py-1.5 text-sm" />
          </Labeled>
          <Labeled label="传输类型">
            <select
              value={transport}
              onChange={(e) => setTransport(e.target.value as "streamable_http" | "sse" | "stdio")}
              className="w-full rounded border border-light-border px-2 py-1.5 text-sm"
            >
              <option value="streamable_http">streamable_http</option>
              <option value="sse">sse</option>
              <option value="stdio">stdio</option>
            </select>
          </Labeled>
          {transport === "stdio" ? (
            <>
              <Labeled label="命令">
                <input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="npx" className="w-full rounded border border-light-border px-2 py-1.5 text-sm" />
              </Labeled>
              <Labeled label="参数 (空格分隔)">
                <input value={argsText} onChange={(e) => setArgsText(e.target.value)} placeholder="-y @some/mcp-server" className="w-full rounded border border-light-border px-2 py-1.5 text-sm" />
              </Labeled>
            </>
          ) : (
            <Labeled label="URL">
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/mcp" className="w-full rounded border border-light-border px-2 py-1.5 text-sm" />
            </Labeled>
          )}
          <Labeled label="Headers (JSON，可选)">
            <textarea value={headersText} onChange={(e) => setHeadersText(e.target.value)} rows={3} placeholder='{"Authorization":"Bearer xxx"}' className="w-full rounded border border-light-border px-2 py-1.5 text-sm font-mono" />
          </Labeled>
        </div>
        {err && <p className="mt-2 text-sm text-accent-red">{err}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded border border-light-border px-3 py-1.5 text-sm text-light-text">
            取消
          </button>
          <button
            onClick={submit}
            disabled={busy}
            className="rounded bg-accent-blue px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? "添加中..." : "添加"}
          </button>
        </div>
      </div>
    </div>
  )
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-light-text-secondary">{label}</span>
      {children}
    </label>
  )
}
