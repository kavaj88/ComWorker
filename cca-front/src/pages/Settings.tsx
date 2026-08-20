import { useEffect, useMemo, useState } from 'react'
import {
  Check,
  Loader2,
  Palette,
  Server,
  Container,
  Copy,
  Sun,
  AlertCircle,
  X,
  Settings as SettingsIcon,
} from 'lucide-react'
import IconButton from '../components/ui/IconButton.tsx'
import { useToast } from '../components/ui/Toast.tsx'
import {
  DEFAULT_APPEARANCE,
  readAppearanceSettings,
  saveAppearanceSettings,
  type AppearanceSettings,
  type ThemeMode,
} from '../lib/appearance.ts'
import { getStatus, getContainerInfo, type ContainerInfo } from '../lib/api.ts'

const tabs = [
  { id: 'appearance', label: '外观', icon: Palette },
  { id: 'system', label: '系统设置', icon: SettingsIcon },
] as const

const accentOptions = [
  { name: '青蓝', value: '#0891b2' },
  { name: '蓝色', value: '#339cff' },
  { name: '绿色', value: '#22c55e' },
  { name: '紫色', value: '#8b5cf6' },
]

const themeOptions: Array<{ value: ThemeMode; label: string }> = [
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
  { value: 'system', label: '系统' },
]

function SettingRow({
  label,
  description,
  children,
}: {
  label: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex min-h-14 items-center justify-between gap-5 border-t border-light-border px-4 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium text-light-text">{label}</div>
        {description && <div className="mt-0.5 text-xs text-light-text-secondary">{description}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={checked}
      onClick={() => onChange(!checked)}
      className={`flex h-7 w-12 cursor-pointer items-center rounded-full p-0.5 transition-colors ${
        checked ? 'bg-accent-blue' : 'bg-slate-200'
      }`}
    >
      <span
        className={`h-6 w-6 rounded-full bg-white shadow-sm transition-transform ${
          checked ? 'translate-x-5' : 'translate-x-0'
        }`}
      />
    </button>
  )
}

function AppearancePreview({ settings }: { settings: AppearanceSettings }) {
  return (
    <div className="overflow-hidden rounded-xl border border-light-border bg-light-card">
      <div className="flex items-center justify-between border-b border-light-border px-4 py-3">
        <div>
          <div className="text-sm font-semibold text-light-text">主题预览</div>
          <div className="mt-1 text-xs text-light-text-secondary">查看侧栏、卡片和强调色的整体效果</div>
        </div>
        <div className="flex items-center gap-1 rounded-full bg-light-card-hover p-1">
          {themeOptions.map(option => (
            <span
              key={option.value}
              className={`rounded-full px-3 py-1 text-xs ${
                settings.theme === option.value
                  ? 'bg-light-card text-light-text shadow-sm'
                  : 'text-light-text-secondary'
              }`}
            >
              {option.label}
            </span>
          ))}
        </div>
      </div>

      <div className="grid min-h-36 grid-cols-[0.72fr_1fr] text-xs">
        <div className="border-r border-light-border bg-light-sidebar p-3">
          <div className="mb-3 flex items-center gap-2 text-light-text">
            <Palette size={15} className="text-accent-blue" />
            <span className="font-medium">ComWorker Lite</span>
          </div>
          <div className="space-y-1">
            <div className="rounded-lg bg-light-card px-3 py-2 text-light-text shadow-sm">工作台</div>
            <div className="rounded-lg px-3 py-2 text-light-text-secondary">设置</div>
            <div className="rounded-lg px-3 py-2 text-light-text-secondary">Agent 对话</div>
          </div>
        </div>
        <div className="bg-light-bg p-3">
          <div className="mb-3 flex items-center justify-between">
            <span className="font-medium text-light-text">外观</span>
            <span className="rounded-full bg-accent-blue px-2.5 py-1 text-white">{settings.accent}</span>
          </div>
          <div className="space-y-2">
            <div className="h-9 rounded-lg border border-light-border bg-light-card" />
            <div className="h-9 rounded-lg border border-light-border bg-light-card-hover" />
            <div className="h-2 w-2/3 rounded-full bg-accent-blue" />
          </div>
        </div>
      </div>
    </div>
  )
}

// ── 系统设置（从 3080 frontend SystemSettings 迁移，使用客户端浅色主题）────────
// 注意：hermes 架构下「网关配置 / 重启网关 / 一键修复」三项后端已硬编码拒绝或
// 返回占位桩，且用户容器无 comworker 网关进程，属于无效功能，已从本页删除。
// 仅保留真实可用的「网关连接状态 / 当前模型」与「容器信息」。
function SystemSettings() {
  const [status, setStatus] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [containerInfo, setContainerInfo] = useState<ContainerInfo | null>(null)

  const toast = useToast()

  const loadData = async () => {
    setLoading(true)
    setError('')
    try {
      const [statusData, containerData] = await Promise.all([
        getStatus().catch(() => null),
        getContainerInfo().catch(() => null),
      ])
      setStatus(statusData)
      setContainerInfo(containerData)
    } catch (err: any) {
      setError(err?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void loadData() }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 size={28} className="animate-spin text-accent-blue" />
      </div>
    )
  }

  const gatewayConnected = status?.gateway_connected === true

  return (
    <div className="mt-8 space-y-6 max-w-2xl">
      {error && (
        <div className="rounded-lg bg-accent-red/10 p-3 text-sm text-accent-red flex items-center gap-2">
          <AlertCircle size={16} />
          {error}
          <button onClick={() => setError('')} className="ml-auto"><X size={14} /></button>
        </div>
      )}

      {/* 网关连接状态（真实：容器 /health + 当前模型） */}
      <section className="rounded-xl border border-light-border bg-light-card overflow-hidden">
        <div className="px-5 py-3 border-b border-light-border flex items-center gap-2">
          <Server size={16} className="text-light-text-secondary" />
          <h2 className="text-sm font-semibold text-light-text">网关状态</h2>
        </div>
        <div className="px-5 py-4">
          <div className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-2.5 text-sm">
            <span className="text-light-text-secondary">连接状态</span>
            <span className="flex items-center gap-1.5">
              <span className={`inline-block w-2 h-2 rounded-full ${gatewayConnected ? 'bg-accent-green' : 'bg-accent-red'}`} />
              <span className={gatewayConnected ? 'text-accent-green' : 'text-accent-red'}>
                {gatewayConnected ? '已连接' : '未连接'}
              </span>
            </span>
            <span className="text-light-text-secondary">当前模型</span>
            <span className="text-light-text font-mono text-xs">{String(status?.model || '-')}</span>
          </div>
        </div>
      </section>

      {/* 容器信息（真实：DB + Docker 读取，用于排查） */}
      <section className="rounded-xl border border-light-border bg-light-card overflow-hidden">
        <div className="px-5 py-3 border-b border-light-border flex items-center gap-2">
          <Container size={16} className="text-light-text-secondary" />
          <h2 className="text-sm font-semibold text-light-text">容器信息</h2>
        </div>
        <div className="px-5 py-4">
          {containerInfo?.container_name ? (
            <>
              <div className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-2.5 text-sm">
                <span className="text-light-text-secondary">容器名称</span>
                <span className="flex items-center gap-2">
                  <span className="text-light-text font-mono text-xs">{containerInfo.container_name}</span>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(containerInfo.container_name || '')
                      toast.show({ message: '已复制容器名称', variant: 'success' })
                    }}
                    className="text-light-text-secondary hover:text-light-text transition-colors"
                    title="复制容器名称"
                  >
                    <Copy size={12} />
                  </button>
                </span>
                <span className="text-light-text-secondary">容器状态</span>
                <span className="flex items-center gap-1.5">
                  <span className={`inline-block w-2 h-2 rounded-full ${
                    containerInfo.status === 'running' ? 'bg-accent-green' :
                    containerInfo.status === 'restarting' ? 'bg-accent-red animate-pulse' :
                    containerInfo.status === 'creating' ? 'bg-accent-yellow' : 'bg-accent-red'
                  }`} />
                  <span className={
                    containerInfo.status === 'running' ? 'text-accent-green' :
                    containerInfo.status === 'restarting' ? 'text-accent-red' :
                    containerInfo.status === 'creating' ? 'text-accent-yellow' : 'text-light-text-secondary'
                  }>
                    {containerInfo.status === 'running' ? '运行中' :
                     containerInfo.status === 'restarting' ? '异常重启中' :
                     containerInfo.status === 'creating' ? '创建中' :
                     containerInfo.status === 'paused' ? '已暂停' :
                     containerInfo.status === 'exited' ? '已停止' :
                     containerInfo.status === 'archived' ? '已归档' : containerInfo.status}
                  </span>
                </span>
                <span className="text-light-text-secondary">创建时间</span>
                <span className="text-light-text text-xs">
                  {containerInfo.created_at ? new Date(containerInfo.created_at).toLocaleString('zh-CN') : '-'}
                </span>
              </div>

              {containerInfo.ports && containerInfo.ports.filter(p => p.host_port).length > 0 && (
                <div className="mt-3 rounded-lg bg-light-bg p-3 border border-light-border">
                  <span className="text-xs font-medium text-light-text-secondary">端口映射</span>
                  <div className="mt-2 space-y-1">
                    {containerInfo.ports.filter(p => p.host_port).map(p => (
                      <div key={p.container_port} className="flex items-center gap-2 text-xs font-mono">
                        <span className="text-light-text-secondary">{p.container_port}</span>
                        <span className="text-light-text-secondary">{'→'}</span>
                        <span className="text-light-text">{p.host_port}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="text-sm text-light-text-secondary">暂无容器</p>
          )}

          <p className="mt-3 text-[11px] text-light-text-secondary">
            网关由平台统一托管，配置与重启需运维在服务器侧进行。遇到问题时可将上方容器名称告知管理员协助排查。
          </p>
        </div>
      </section>
    </div>
  )
}
// ──────────────────────────────────────────────────────────────────────────────

export default function Settings() {
  const [activeTab, setActiveTab] = useState<(typeof tabs)[number]['id']>('appearance')
  const [settings, setSettings] = useState<AppearanceSettings>(() => readAppearanceSettings())

  const updateSettings = (patch: Partial<AppearanceSettings>) => {
    setSettings(current => {
      const next = { ...current, ...patch }
      saveAppearanceSettings(next)
      return next
    })
  }

  const activeLabel = useMemo(
    () => tabs.find(tab => tab.id === activeTab)?.label || '设置',
    [activeTab],
  )

  return (
    <div className="h-full overflow-hidden bg-light-bg">
      <div className="flex h-full">
        <aside className="hidden w-64 shrink-0 border-r border-light-border bg-light-sidebar px-3 py-4 md:block">
          <div className="mb-3 px-2 text-xs font-medium text-light-text-secondary">设置</div>
          <nav className="space-y-1">
            {tabs.map(tab => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`flex w-full cursor-pointer items-center gap-3 rounded-xl px-3 py-2 text-left text-sm transition-colors ${
                  activeTab === tab.id
                    ? 'bg-light-card text-light-text shadow-sm'
                    : 'text-light-text-secondary hover:bg-light-card/70 hover:text-light-text'
                }`}
              >
                <tab.icon size={17} />
                <span>{tab.label}</span>
              </button>
            ))}
          </nav>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-4xl flex-col px-5 py-8 sm:px-8 lg:py-12">
            <div className="mb-6 flex flex-wrap gap-2 md:hidden">
              {tabs.map(tab => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex cursor-pointer items-center gap-2 rounded-xl px-3 py-2 text-sm transition-colors ${
                    activeTab === tab.id
                      ? 'bg-light-card-hover text-light-text'
                      : 'text-light-text-secondary hover:bg-light-card-hover hover:text-light-text'
                  }`}
                >
                  <tab.icon size={16} />
                  {tab.label}
                </button>
              ))}
            </div>

            <h1 className="text-2xl font-semibold tracking-normal text-light-text">{activeLabel}</h1>

            {activeTab === 'appearance' ? (
              <div className="mt-8 space-y-4">
                <AppearancePreview settings={settings} />

                <section className="overflow-hidden rounded-xl border border-light-border bg-light-card">
                  <SettingRow label="主题" description="使用浅色、深色，或跟随系统设置">
                    <div className="flex rounded-full bg-light-card-hover p-1">
                      {themeOptions.map(option => (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => updateSettings({ theme: option.value })}
                          className={`flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-sm transition-colors ${
                            settings.theme === option.value
                              ? 'bg-light-card text-light-text shadow-sm'
                              : 'text-light-text-secondary hover:text-light-text'
                          }`}
                        >
                          <Sun size={14} />
                          {option.label}
                        </button>
                      ))}
                    </div>
                  </SettingRow>

                  <SettingRow label="强调色" description="用于按钮、焦点和关键状态">
                    <div className="flex items-center gap-2">
                      {accentOptions.map(option => (
                        <button
                          key={option.value}
                          type="button"
                          aria-label={`选择${option.name}`}
                          onClick={() => updateSettings({ accent: option.value })}
                          className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-full border border-light-border"
                          style={{ backgroundColor: option.value }}
                        >
                          {settings.accent === option.value && <Check size={15} className="text-white" />}
                        </button>
                      ))}
                    </div>
                  </SettingRow>

                  <SettingRow label="半透明侧边栏" description="让侧边栏更轻，保留当前浅青色气质">
                    <Toggle
                      checked={settings.translucentSidebar}
                      label="切换半透明侧边栏"
                      onChange={checked => updateSettings({ translucentSidebar: checked })}
                    />
                  </SettingRow>

                  <SettingRow label="界面密度" description="紧凑模式会收紧卡片和列表间距">
                    <div className="flex rounded-full bg-light-card-hover p-1">
                      {[
                        { value: 'comfortable', label: '舒适' },
                        { value: 'compact', label: '紧凑' },
                      ].map(option => (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => updateSettings({ density: option.value as AppearanceSettings['density'] })}
                          className={`cursor-pointer rounded-full px-3 py-1.5 text-sm transition-colors ${
                            settings.density === option.value
                              ? 'bg-light-card text-light-text shadow-sm'
                              : 'text-light-text-secondary hover:text-light-text'
                          }`}
                        >
                          {option.label}
                        </button>
                      ))}
                    </div>
                  </SettingRow>

                  <SettingRow label="对比度" description="调节文字和边框的清晰度">
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min="35"
                        max="75"
                        value={settings.contrast}
                        onChange={event => updateSettings({ contrast: Number(event.target.value) })}
                        className="w-36 accent-accent-blue"
                        aria-label="调整对比度"
                      />
                      <span className="w-8 text-right text-sm text-light-text-secondary">{settings.contrast}</span>
                    </div>
                  </SettingRow>
                </section>

                <button
                  type="button"
                  onClick={() => updateSettings(DEFAULT_APPEARANCE)}
                  className="self-start rounded-xl border border-light-border px-4 py-2 text-sm text-light-text-secondary transition-colors hover:bg-light-card-hover hover:text-light-text"
                >
                  恢复默认外观
                </button>
              </div>
            ) : (
              <SystemSettings />
            )}
          </div>
        </main>
      </div>
    </div>
  )
}

