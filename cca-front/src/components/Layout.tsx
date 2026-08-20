import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Check,
  Copy,
  BookOpen,
  Brain,
  Bot,
  ChevronRight,
  Clock,
  Loader2,
  LogOut,
  Menu,
  Maximize2,
  MoreHorizontal,
  Minimize2,
  Pencil,
  Plus,
  Puzzle,
  Plug,
  Radio,
  Sparkles,
  Settings as SettingsIcon,
  Trash2,
  X,
} from 'lucide-react'
import UserAvatar from './UserAvatar.tsx'
import ClearableInput from './ui/ClearableInput.tsx'
import IconButton from './ui/IconButton.tsx'
import Popconfirm from './ui/Popconfirm.tsx'
import {
  deleteSession,
  getAccessToken,
  getMe,
  getPlatformConfig,
  listAgents,
  listSessions,
  logout,
  updateSessionTitle,
} from '../lib/api.ts'
import type { AgentInfo, AuthUser, PlatformConfig, Session } from '../lib/api.ts'

export type LayoutOutletContext = {
  user: AuthUser | null
  agents: AgentInfo[]
  agentsLoading: boolean
  currentSessionTitle: string | null
  refreshAgents: (options?: { force?: boolean }) => Promise<void>
  refreshSessions: (options?: { silent?: boolean; force?: boolean }) => Promise<void>
  addOptimisticSession: (session: Session) => void
  markSessionRead: (key: string) => void
  setSessionThinking: (key: string, thinking: boolean) => void
  openMobileSidebar: () => void
}

type ContextMenuState = {
  x: number
  y: number
  session: Session
} | null

type AgentMenuState = {
  x: number
  y: number
  agentId: string
} | null

const primaryNav = [
  { to: '/chat', label: '新对话', icon: Pencil },
  { to: '/knowledge', label: '知识库', icon: BookOpen },
  { to: '/skills', label: '技能', icon: Sparkles },
  { to: '/connectors', label: '连接器', icon: Plug },
  { to: '/experts', label: '专家', icon: Bot },
  { to: '/channels', label: '渠道管理', icon: Radio },
  { to: '/plugins', label: '插件管理', icon: Puzzle },
]

const userNav = [
  { to: '/models', label: 'AI 模型', icon: Brain },
  { to: '/cron', label: '定时任务', icon: Clock },
  { to: '/settings', label: '设置', icon: SettingsIcon },
]

const unreadSessionsStorageKey = 'comworker_unread_sessions'

/** Returns true when *value* looks like an auto-generated session ID
 *  (e.g. ``agent:main:session-1786194...`` or ``session-1786194...``)
 *  rather than a human-readable title. */
function _isRawSessionId(value: string): boolean {
  const v = value.trim()
  if (v.startsWith('agent:') && v.includes(':session-')) return true
  return /^session-\d{10,}$/.test(v)
}

function getSessionTitle(session: Session): string {
  const t = session.title?.trim()
  if (t && !_isRawSessionId(t)) return t
  // Fallback: format from timestamp in the session key or created_at
  const ts = session.created_at
    ? new Date(session.created_at)
    : (session.key.match(/session-(\d{10,})/)?.[1]
      ? new Date(Number(session.key.match(/session-(\d{10,})/)?.[1]) * 1000)
      : null)
  if (ts && !isNaN(ts.getTime())) {
    const m = (ts.getMonth() + 1).toString().padStart(2, '0')
    const d = ts.getDate().toString().padStart(2, '0')
    return `对话 ${m}-${d}`
  }
  return '对话'
}

function getConfirmSessionTitle(session: Session): string {
  const title = getSessionTitle(session)
  const chars = Array.from(title)
  if (chars.length <= 36) return title
  return `${chars.slice(0, 36).join('')}...`
}

function isSystemSession(session: Session): boolean {
  const value = `${session.key} ${session.title || ''}`.toLowerCase()
  return value.includes('heartbeat') || value.includes('心跳')
}

function isUserChatSession(session: Session): boolean {
  return session.key.startsWith('agent:')
}

function formatRelativeTime(iso: string | null): string {
  if (!iso) return ''
  const diffMs = Date.now() - new Date(iso).getTime()
  const minute = 60 * 1000
  const hour = 60 * minute
  const day = 24 * hour
  if (diffMs < hour) return `${Math.max(1, Math.round(diffMs / minute))} 分钟`
  if (diffMs < day) return `${Math.round(diffMs / hour)} 小时`
  return `${Math.round(diffMs / day)} 天`
}

function getAgentIdFromKey(key: string): string {
  const parts = key.split(':')
  if (parts.length >= 2 && parts[0] === 'agent') return parts[1]
  return 'main'
}

function getAgentDisplayName(agent: AgentInfo | undefined, agentId: string): string {
  if (agentId === 'main') return '快速问答'
  return agent?.identity?.name || agent?.name || agentId
}

function normalizeAgentList(items: AgentInfo[]): AgentInfo[] {
  return [...items]
    .map(agent => agent.id === 'main'
      ? { ...agent, name: '快速问答', identity: { ...agent.identity, name: '快速问答' } }
      : agent)
    .sort((a, b) => {
      if (a.id === 'main') return -1
      if (b.id === 'main') return 1
      return getAgentDisplayName(a, a.id).localeCompare(getAgentDisplayName(b, b.id), 'zh-Hans')
    })
}

function normalizeSessionKey(key: string): string {
  return key.replace(/:/g, '')
}

function loadUnreadSessionKeys(): Set<string> {
  try {
    const raw = localStorage.getItem(unreadSessionsStorageKey)
    const parsed = raw ? JSON.parse(raw) : []
    if (!Array.isArray(parsed)) return new Set()
    return new Set(parsed.filter(item => typeof item === 'string'))
  } catch {
    return new Set()
  }
}

function persistUnreadSessionKeys(keys: Set<string>): void {
  localStorage.setItem(unreadSessionsStorageKey, JSON.stringify(Array.from(keys)))
}

async function copyText(value: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(value)
    return
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = value
    textarea.style.position = 'fixed'
    textarea.style.left = '-9999px'
    textarea.style.top = '0'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
}

function SidebarSessionSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-1 px-2 py-1.5" aria-hidden="true">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="flex items-center gap-2 rounded-xl px-1 py-1.5">
          <span className="skeleton-shimmer h-4 w-4 shrink-0 rounded-md" />
          <span className="skeleton-shimmer h-3.5 flex-1 rounded-full" />
          <span className="skeleton-shimmer h-3 w-8 shrink-0 rounded-full" />
        </div>
      ))}
    </div>
  )
}

function SessionStatusIndicator({ loading, unread }: { loading: boolean; unread: boolean }) {
  if (loading) {
    return (
      <Loader2
        size={12}
        aria-label="AI 正在回复"
        className="shrink-0 animate-spin text-[var(--color-accent-blue)]"
      />
    )
  }

  if (!unread) return null

  return (
    <span
      aria-label="AI 有未读回复"
      title="AI 有未读回复"
      className="h-2 w-2 shrink-0 rounded-full bg-[var(--color-accent-blue)] shadow-[0_0_0_2px_var(--color-light-card),0_0_8px_color-mix(in_srgb,var(--color-accent-blue)_45%,transparent)]"
    />
  )
}

export default function Layout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [user, setUser] = useState<AuthUser | null>(null)
  const [platform, setPlatform] = useState<PlatformConfig>({ name: 'ComWorker', logo: null })
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [agentsLoading, setAgentsLoading] = useState(true)
  const [sessions, setSessions] = useState<Session[]>([])
  const [optimisticSessions, setOptimisticSessions] = useState<Session[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [contextMenu, setContextMenu] = useState<ContextMenuState>(null)
  const [agentMenu, setAgentMenu] = useState<AgentMenuState>(null)
  const [renamingKey, setRenamingKey] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [agentsFolderOpen, setAgentsFolderOpen] = useState(true)
  const [ordinaryFolderOpen, setOrdinaryFolderOpen] = useState(true)
  const [collapsedAgents, setCollapsedAgents] = useState<Record<string, boolean>>({})
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [unreadSessionKeys, setUnreadSessionKeys] = useState<Set<string>>(() => loadUnreadSessionKeys())
  const [loadingSessionKeys, setLoadingSessionKeys] = useState<Set<string>>(() => new Set())
  const activeSessionKeyRef = useRef<string | null>(null)
  const knownSessionKeysRef = useRef<string[]>([])

  const activeSessionKey = useMemo(() => {
    const params = new URLSearchParams(location.search)
    return params.get('session')
  }, [location.search])

  useEffect(() => {
    activeSessionKeyRef.current = activeSessionKey
  }, [activeSessionKey])

  const markSessionUnread = useCallback((key: string) => {
    setUnreadSessionKeys(prev => {
      if (prev.has(key)) return prev
      const next = new Set(prev)
      next.add(key)
      persistUnreadSessionKeys(next)
      return next
    })
  }, [])

  const markSessionRead = useCallback((key: string) => {
    setUnreadSessionKeys(prev => {
      if (!prev.has(key)) return prev
      const next = new Set(prev)
      next.delete(key)
      persistUnreadSessionKeys(next)
      return next
    })
  }, [])

  const setSessionThinking = useCallback((key: string, loading: boolean) => {
    setLoadingSessionKeys(prev => {
      if (loading && prev.has(key)) return prev
      if (!loading && !prev.has(key)) return prev

      const next = new Set(prev)
      if (loading) {
        next.add(key)
      } else {
        next.delete(key)
      }
      return next
    })
  }, [])

  const refreshSessions = useCallback(async (options: { silent?: boolean; force?: boolean } = {}) => {
    if (!options.silent) {
      setSessionsLoading(true)
    }
    try {
      const result = await listSessions({ force: options.force })
      setSessions(result)
      const actualKeys = new Set(result.map(session => session.key))
      setOptimisticSessions(prev => prev.filter(session => !actualKeys.has(session.key)))
    } catch {
      setSessions([])
    } finally {
      if (!options.silent) {
        setSessionsLoading(false)
      }
    }
  }, [])

  const addOptimisticSession = useCallback((session: Session) => {
    setOptimisticSessions(prev => {
      if (sessions.some(item => item.key === session.key)) {
        return prev
      }
      if (prev.some(item => item.key === session.key)) {
        return prev.map(item => (item.key === session.key ? { ...item, ...session } : item))
      }
      return [session, ...prev]
    })
  }, [sessions])

  const refreshAgents = useCallback(async (options: { force?: boolean } = {}) => {
    setAgentsLoading(true)
    try {
      const result = await listAgents(options)
      setAgents(normalizeAgentList(result.agents || []))
    } catch {
      setAgents([])
    } finally {
      setAgentsLoading(false)
    }
  }, [])

  useEffect(() => {
    getMe().then(setUser).catch(() => {})
    // Platform branding (name + logo) configured from the admin console; fall
    // back to defaults when the endpoint is unavailable.
    getPlatformConfig().then(setPlatform).catch(() => {})
    void refreshAgents()
    void refreshSessions()
  }, [refreshAgents, refreshSessions])

  useEffect(() => {
    const closeMenu = () => {
      setContextMenu(null)
      setAgentMenu(null)
      setUserMenuOpen(false)
    }
    window.addEventListener('click', closeMenu)
    window.addEventListener('keydown', closeMenu)
    return () => {
      window.removeEventListener('click', closeMenu)
      window.removeEventListener('keydown', closeMenu)
    }
  }, [])

  useEffect(() => {
    setMobileSidebarOpen(false)
  }, [location.pathname, location.search])

  const visibleSessions = useMemo(() => {
    const actualKeys = new Set(sessions.map(session => session.key))
    return [
      ...optimisticSessions.filter(session => !actualKeys.has(session.key)),
      ...sessions,
    ].filter(session => isUserChatSession(session) && !isSystemSession(session))
  }, [optimisticSessions, sessions])

  useEffect(() => {
    knownSessionKeysRef.current = [
      ...new Set([
        ...visibleSessions.map(session => session.key),
        ...(activeSessionKey ? [activeSessionKey] : []),
      ]),
    ]
  }, [activeSessionKey, visibleSessions])

  const resolveKnownSessionKey = useCallback((rawKey: string): string => {
    const normalized = normalizeSessionKey(rawKey)
    return knownSessionKeysRef.current.find(key => normalizeSessionKey(key) === normalized) || rawKey
  }, [])

  useEffect(() => {
    if (activeSessionKey) {
      markSessionRead(activeSessionKey)
    }
  }, [activeSessionKey, markSessionRead])

  useEffect(() => {
    const token = getAccessToken()
    if (!token) return

    const sse = new EventSource(`/api/comworker/events/stream?token=${encodeURIComponent(token)}`)
    sse.onmessage = event => {
      try {
        const message = JSON.parse(event.data)
        if (message.event !== 'chat' || !message.payload) return

        const state = message.payload.state
        const rawSessionKey = String(message.payload.sessionKey || '')
        if (!rawSessionKey) return

        const sessionKey = resolveKnownSessionKey(rawSessionKey)
        if (state === 'started' || state === 'delta') {
          setSessionThinking(sessionKey, true)
          if (state === 'started') {
            void refreshSessions({ silent: true, force: true })
          }
          return
        }

        if (state === 'final' || state === 'error' || state === 'aborted') {
          setSessionThinking(sessionKey, false)
          if (state === 'final') {
            if (sessionKey === activeSessionKeyRef.current) {
              markSessionRead(sessionKey)
            } else {
              markSessionUnread(sessionKey)
              void refreshSessions({ silent: true, force: true })
            }
          }
        }
      } catch {
        // Ignore malformed stream events; chat page still handles its own stream state.
      }
    }

    return () => sse.close()
  }, [markSessionRead, markSessionUnread, refreshSessions, resolveKnownSessionKey, setSessionThinking])

  const currentSessionTitle = useMemo(() => {
    if (!activeSessionKey) return null
    const session = visibleSessions.find(item => item.key === activeSessionKey)
    if (!session) return null
    return getSessionTitle(session)
  }, [activeSessionKey, visibleSessions])

  const agentGroups = useMemo(() => {
    const sorted = [...visibleSessions].sort(
      (a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime(),
    )
    const sessionsByAgent = new Map<string, Session[]>()
    sorted.forEach(session => {
      const agentId = getAgentIdFromKey(session.key)
      sessionsByAgent.set(agentId, [...(sessionsByAgent.get(agentId) || []), session])
    })

    const agentIds = new Set<string>()
    sessionsByAgent.forEach((_value, agentId) => {
      agentIds.add(agentId)
    })
    agentIds.delete('main')

    return Array.from(agentIds)
      .map(agentId => {
        const agent = agents.find(item => item.id === agentId)
        return {
          id: agentId,
          label: getAgentDisplayName(agent, agentId),
          sessions: sessionsByAgent.get(agentId) || [],
        }
      })
  }, [agents, visibleSessions])

  const ordinarySessions = useMemo(
    () =>
      [...visibleSessions]
        .filter(session => getAgentIdFromKey(session.key) === 'main')
        .sort((a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime()),
    [visibleSessions],
  )

  const allAgentsCollapsed = agentGroups.length > 0 && agentGroups.every(group => collapsedAgents[group.id])

  const toggleAllAgents = () => {
    if (allAgentsCollapsed) {
      setAgentsFolderOpen(true)
      setCollapsedAgents({})
      return
    }
    setAgentsFolderOpen(true)
    setCollapsedAgents(Object.fromEntries(agentGroups.map(group => [group.id, true])))
  }

  const startAgentSession = (agentId: string) => {
    setAgentMenu(null)
    setMobileSidebarOpen(false)
    navigate(`/chat?new=1&agent=${encodeURIComponent(agentId)}`)
  }

  const startRename = (session: Session) => {
    setRenamingKey(session.key)
    setRenameValue(getSessionTitle(session))
    setContextMenu(null)
  }

  const saveRename = async (key: string) => {
    await updateSessionTitle(key, renameValue)
    setRenamingKey(null)
    setRenameValue('')
    await refreshSessions({ force: true })
  }

  const copySessionId = async (key: string) => {
    await copyText(key)
    setCopiedKey(key)
    setContextMenu(null)
    window.setTimeout(() => setCopiedKey(null), 1600)
  }

  const removeSession = async (session: Session) => {
    setContextMenu(null)
    await deleteSession(session.key)
    await refreshSessions({ force: true })
    if (activeSessionKey === session.key) {
      navigate('/chat')
    }
  }

  const handleLogout = () => {
    logout()
  }

  return (
    <div className="flex h-screen overflow-hidden bg-light-bg text-light-text">
      {location.pathname !== '/chat' && (
        <IconButton
          label="展开菜单"
          onClick={() => setMobileSidebarOpen(true)}
          size="md"
          surface="plain"
          className="fixed left-3 top-3 z-30 bg-light-card/80 shadow-sm backdrop-blur lg:hidden"
        >
          <Menu size={20} />
        </IconButton>
      )}

      <button
        type="button"
        aria-label="关闭菜单"
        onClick={() => setMobileSidebarOpen(false)}
        className={`mobile-sidebar-backdrop fixed inset-0 z-40 bg-slate-950/40 backdrop-blur-[1px] lg:hidden ${
          mobileSidebarOpen ? 'is-open' : ''
        }`}
      />

      <aside
        className={`app-sidebar fixed inset-y-0 left-0 z-50 flex w-[82vw] max-w-[380px] shrink-0 flex-col border-r border-light-border bg-light-sidebar px-3 py-3 shadow-2xl shadow-slate-950/20 lg:relative lg:z-auto lg:w-[284px] lg:translate-x-0 lg:shadow-none ${
          mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="mb-4 flex items-center gap-3 rounded-2xl bg-light-card/80 px-2.5 py-2.5 shadow-sm ring-1 ring-light-border/70">
          <img src={platform.logo || '/comworker-logo.webp'} alt={platform.name} className="h-10 w-10 rounded-full object-cover ring-2 ring-white shadow-sm" />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-light-text">{platform.name}</div>
            <div className="truncate text-xs text-light-text-secondary">专家协作与快速问答</div>
          </div>
        </div>

        <nav className="space-y-1">
          {primaryNav.map(item => (
            <NavLink
              key={`${item.to}-${item.label}`}
              to={item.label === '新对话' ? '/chat?new=1' : item.to}
              className={({ isActive }) =>
                `flex cursor-pointer items-center gap-3 rounded-xl px-2.5 py-2 text-sm transition-colors ${
                  isActive && item.label !== '新对话'
                    ? 'bg-light-card text-light-text shadow-sm'
                    : 'text-light-text-secondary hover:bg-light-card/70 hover:text-light-text'
                }`
              }
              onClick={() => setMobileSidebarOpen(false)}
            >
              <item.icon size={17} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="mt-7 min-h-0 flex-1 overflow-y-auto pr-1">
          <div className="mb-2 flex items-center justify-between px-2 text-xs text-slate-500">
            <button
              onClick={() => setAgentsFolderOpen(value => !value)}
              className="flex cursor-pointer items-center gap-1 rounded-md text-xs transition-colors hover:text-light-text"
            >
              <span>专家对话</span>
              <ChevronRight
                size={13}
                className={`sidebar-chevron ${agentsFolderOpen ? 'is-open' : ''}`}
              />
            </button>
            <IconButton
              label={allAgentsCollapsed ? '全部展开' : '全部收起'}
              onClick={toggleAllAgents}
              size="md"
              surface="plain"
              className="h-7 w-7"
            >
              {allAgentsCollapsed ? <Maximize2 size={14} /> : <Minimize2 size={14} />}
            </IconButton>
          </div>

          <div className={`sidebar-collapse ${agentsFolderOpen ? 'is-open' : ''}`}>
                <div>
                  {sessionsLoading ? (
                    <SidebarSessionSkeleton count={3} />
                  ) : agentGroups.length === 0 ? (
                      <div className="px-2 py-3 text-xs text-slate-500">暂无专家对话</div>
                  ) : (
                    agentGroups.map(group => (
                <section key={group.id}>
                  <div className="group/agent flex items-center gap-1 rounded-xl px-2 py-1.5 text-sm text-light-text-secondary hover:bg-light-card/60">
                    <button
                      onClick={() =>
                        setCollapsedAgents(prev => ({ ...prev, [group.id]: !prev[group.id] }))
                      }
                      className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-left"
                    >
                      <ChevronRight
                        size={14}
                        className={`sidebar-chevron ${collapsedAgents[group.id] ? '' : 'is-open'}`}
                      />
                      <Bot size={16} />
                      <span className="truncate">{group.label}</span>
                    </button>
                    <IconButton
                      label={`新建 ${group.label} 对话`}
                      onClick={() => startAgentSession(group.id)}
                      size="sm"
                      surface="plain"
                      className="opacity-0 group-hover/agent:opacity-100"
                    >
                      <Plus size={14} />
                    </IconButton>
                    <IconButton
                      label={`${group.label} 更多操作`}
                      onClick={event => {
                        event.stopPropagation()
                        setContextMenu(null)
                        setAgentMenu({ x: event.clientX, y: event.clientY, agentId: group.id })
                      }}
                      size="sm"
                      surface="plain"
                      className="opacity-0 group-hover/agent:opacity-100"
                    >
                      <MoreHorizontal size={15} />
                    </IconButton>
                  </div>
                  <div className={`sidebar-collapse ${collapsedAgents[group.id] ? '' : 'is-open'}`}>
                    <div className="space-y-0.5 pl-6">
                      {group.sessions.slice(0, 6).map(session => {
                        const isActive = activeSessionKey === session.key
                        const isRenaming = renamingKey === session.key
                        const hasUnread = unreadSessionKeys.has(session.key)
                        const isLoading = loadingSessionKeys.has(session.key)
                        return (
                          <div
                            key={session.key}
                            onContextMenu={event => {
                              event.preventDefault()
                              setContextMenu({ x: event.clientX, y: event.clientY, session })
                            }}
                            className={`group flex items-center gap-2 rounded-xl px-2 py-1.5 transition-colors ${
                              isActive ? 'bg-light-card text-light-text shadow-sm' : 'text-light-text-secondary hover:bg-light-card/70 hover:text-light-text'
                            }`}
                          >
                            {isRenaming ? (
                              <div className="flex min-w-0 flex-1 items-center gap-1">
                                <ClearableInput
                                  value={renameValue}
                                  onValueChange={setRenameValue}
                                  onKeyDown={event => {
                                    if (event.key === 'Enter') void saveRename(session.key)
                                    if (event.key === 'Escape') setRenamingKey(null)
                                  }}
                                  className="min-w-0 flex-1 rounded-lg border border-accent-blue/30 bg-white px-2 py-1 text-xs outline-none"
                                  autoFocus
                                  clearLabel="清空标题"
                                />
                                <IconButton
                                  label="保存标题"
                                  onClick={() => void saveRename(session.key)}
                                  size="sm"
                                  tone="primary"
                                >
                                  <Check size={13} />
                                </IconButton>
                                <IconButton
                                  label="取消重命名"
                                  onClick={() => setRenamingKey(null)}
                                  size="sm"
                                >
                                  <X size={13} />
                                </IconButton>
                              </div>
                            ) : (
                              <button
                                onClick={() => {
                                  markSessionRead(session.key)
                                  setMobileSidebarOpen(false)
                                  navigate(`/chat?session=${encodeURIComponent(session.key)}`)
                                }}
                                className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-left"
                              >
                                <span className="flex min-w-0 items-center gap-1.5">
                                  <span className="truncate text-sm">{getSessionTitle(session)}</span>
                                  <SessionStatusIndicator loading={isLoading} unread={hasUnread} />
                                </span>
                                <span className="ml-auto shrink-0 text-xs text-slate-400">
                                  {copiedKey === session.key ? '已复制' : formatRelativeTime(session.updated_at)}
                                </span>
                              </button>
                            )}
                          </div>
                        )
                      })}
                      {group.sessions.length > 6 && (
                        <button className="cursor-pointer px-2 py-1 text-sm text-slate-500 hover:text-light-text">
                          展开显示
                        </button>
                      )}
                    </div>
                  </div>
                </section>
                    ))
                  )}
                </div>
              </div>
              <div className={`sidebar-collapse ${agentsFolderOpen ? '' : 'is-open'}`}>
                <div className="px-2 py-3 text-xs text-slate-500">
                  {sessionsLoading ? '正在加载专家对话' : `已收起 ${agentGroups.length} 个专家`}
                </div>
              </div>

            <section className="mt-5 border-t border-slate-200/70 pt-4">
              <div className="mb-2 flex items-center justify-between px-2 text-xs text-slate-500">
                <button
                  onClick={() => setOrdinaryFolderOpen(value => !value)}
                  className="flex cursor-pointer items-center gap-1 rounded-md text-xs transition-colors hover:text-light-text"
                >
                  <span>对话历史</span>
                  <ChevronRight
                    size={13}
                    className={`sidebar-chevron ${ordinaryFolderOpen ? 'is-open' : ''}`}
                  />
                </button>
                <IconButton
                  label="新建快速问答"
                  onClick={() => startAgentSession('main')}
                  size="sm"
                  surface="plain"
                >
                  <Pencil size={14} />
                </IconButton>
              </div>

              <div className={`sidebar-collapse ${ordinaryFolderOpen ? 'is-open' : ''}`}>
                <div className="space-y-0.5">
                  {sessionsLoading ? (
                    <SidebarSessionSkeleton count={6} />
                  ) : ordinarySessions.length === 0 ? (
                    <div className="px-2 py-1 text-xs text-slate-400">暂无对话历史</div>
                  ) : ordinarySessions.slice(0, 8).map(session => {
                    const isActive = activeSessionKey === session.key
                    const isRenaming = renamingKey === session.key
                    const hasUnread = unreadSessionKeys.has(session.key)
                    const isLoading = loadingSessionKeys.has(session.key)
                    return (
                      <div
                        key={session.key}
                        onContextMenu={event => {
                          event.preventDefault()
                          setContextMenu({ x: event.clientX, y: event.clientY, session })
                        }}
                        className={`group flex items-center gap-2 rounded-xl px-2 py-1.5 transition-colors ${
                            isActive ? 'bg-light-card text-light-text shadow-sm' : 'text-light-text-secondary hover:bg-light-card/70 hover:text-light-text'
                        }`}
                      >
                        {isRenaming ? (
                          <div className="flex min-w-0 flex-1 items-center gap-1">
                            <ClearableInput
                              value={renameValue}
                              onValueChange={setRenameValue}
                              onKeyDown={event => {
                                if (event.key === 'Enter') void saveRename(session.key)
                                if (event.key === 'Escape') setRenamingKey(null)
                              }}
                              className="min-w-0 flex-1 rounded-lg border border-accent-blue/30 bg-white px-2 py-1 text-xs outline-none"
                              autoFocus
                              clearLabel="清空标题"
                            />
                            <IconButton
                              label="保存标题"
                              onClick={() => void saveRename(session.key)}
                              size="sm"
                              tone="primary"
                            >
                              <Check size={13} />
                            </IconButton>
                            <IconButton
                              label="取消重命名"
                              onClick={() => setRenamingKey(null)}
                              size="sm"
                            >
                              <X size={13} />
                            </IconButton>
                          </div>
                        ) : (
                          <button
                            onClick={() => {
                              markSessionRead(session.key)
                              setMobileSidebarOpen(false)
                              navigate(`/chat?session=${encodeURIComponent(session.key)}`)
                            }}
                            className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-left"
                          >
                            <span className="flex min-w-0 items-center gap-1.5">
                              <span className="truncate text-sm">{getSessionTitle(session)}</span>
                              <SessionStatusIndicator loading={isLoading} unread={hasUnread} />
                            </span>
                            <span className="ml-auto shrink-0 text-xs text-slate-400">
                              {copiedKey === session.key ? '已复制' : formatRelativeTime(session.updated_at)}
                            </span>
                          </button>
                        )}
                      </div>
                    )
                  })}
                  {ordinarySessions.length > 8 && (
                    <button className="cursor-pointer px-2 py-1 text-sm text-slate-500 hover:text-light-text">
                      展开显示
                    </button>
                  )}
                </div>
              </div>
            </section>
        </div>

        <div className="relative mt-3">
          {userMenuOpen && (
            <div
              className="absolute bottom-full left-0 right-0 z-50 mb-1 rounded-xl border border-light-border bg-white p-1 shadow-xl shadow-slate-200/80"
              onClick={event => event.stopPropagation()}
            >
              {userNav.map(item => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                      isActive
                        ? 'bg-light-card text-light-text shadow-sm'
                        : 'text-light-text hover:bg-light-card-hover'
                    }`
                  }
                  onClick={() => setUserMenuOpen(false)}
                >
                  <item.icon size={15} />
                  <span>{item.label}</span>
                </NavLink>
              ))}
              <div className="my-1 border-t border-light-border/60" />
              <button
                type="button"
                onClick={() => {
                  setUserMenuOpen(false)
                  handleLogout()
                }}
                className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-accent-red transition-colors hover:bg-accent-red/10"
              >
                <LogOut size={15} />
                <span>退出登录</span>
              </button>
            </div>
          )}
          <button
            type="button"
            onClick={event => {
              event.stopPropagation()
              setUserMenuOpen(v => !v)
            }}
            className="flex w-full items-center justify-between rounded-xl px-2 py-2 text-left text-slate-700 hover:bg-light-card-hover/50 transition-colors"
          >
            <div className="min-w-0 text-xs text-slate-500">
              <div className="truncate text-sm text-slate-700">{user?.username || '当前用户'}</div>
              <div className="truncate">{user?.email || platform.name}</div>
            </div>
            <div className="flex items-center gap-2">
              <UserAvatar user={user} />
              <ChevronRight
                size={14}
                className={`text-slate-400 transition-transform ${userMenuOpen ? '-rotate-90' : 'rotate-90'}`}
              />
            </div>
          </button>
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-hidden bg-light-bg">
        <Outlet
          context={{
            user,
            agents,
            agentsLoading,
            currentSessionTitle,
            refreshAgents,
            refreshSessions,
            addOptimisticSession,
            markSessionRead,
            setSessionThinking,
            openMobileSidebar: () => setMobileSidebarOpen(true),
          } satisfies LayoutOutletContext}
        />
      </main>

      {contextMenu && (
        <div
          className="fixed z-50 w-48 rounded-xl border border-light-border bg-white p-1 shadow-xl shadow-slate-200/80"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={event => event.stopPropagation()}
        >
          <button
            onClick={() => startRename(contextMenu.session)}
            className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-light-text transition-colors hover:bg-light-card-hover"
          >
            <Pencil size={15} />
            重命名
          </button>
          <button
            onClick={() => void copySessionId(contextMenu.session.key)}
            className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-light-text transition-colors hover:bg-light-card-hover"
          >
            <Copy size={15} />
            复制会话 ID
          </button>
          <Popconfirm
            title="删除这个会话？"
            description={`会话“${getConfirmSessionTitle(contextMenu.session)}”将被删除，此操作不可恢复。`}
            confirmText="删除"
            danger
            onConfirm={() => void removeSession(contextMenu.session)}
          >
            <button
              className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-accent-red transition-colors hover:bg-accent-red/10"
            >
              <Trash2 size={15} />
              删除
            </button>
          </Popconfirm>
        </div>
      )}

      {agentMenu && (
        <div
          className="fixed z-50 w-48 rounded-xl border border-light-border bg-white p-1 shadow-xl shadow-slate-200/80"
          style={{ left: agentMenu.x, top: agentMenu.y }}
          onClick={event => event.stopPropagation()}
        >
          <button
            onClick={() => startAgentSession(agentMenu.agentId)}
            className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-light-text transition-colors hover:bg-light-card-hover"
          >
            <Plus size={15} />
            新建对话
          </button>
          <button
            onClick={() => {
              void copyText(agentMenu.agentId)
              setAgentMenu(null)
            }}
            className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-light-text transition-colors hover:bg-light-card-hover"
          >
            <Copy size={15} />
            复制 Agent ID
          </button>
        </div>
      )}
    </div>
  )
}
