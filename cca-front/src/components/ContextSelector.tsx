import { useState, useEffect, useMemo, useCallback } from 'react'
import { Search, X, Bot, Wrench, Plug, Check } from 'lucide-react'
import {
  listSkills,
  listUserConnectors,
  type UserConnector,
  type AgentInfo,
} from '../lib/api.ts'

export type ContextCategory = 'expert' | 'skill' | 'connector'

export interface BoundContext {
  category: ContextCategory
  id: string
  name: string
  needsAuth?: boolean
  enabled?: boolean
}

interface ContextSelectorProps {
  agents: AgentInfo[]
  value: BoundContext | null
  onSelect: (item: BoundContext) => void
  onClear: () => void
}

const CATEGORIES: { key: ContextCategory; label: string; icon: typeof Bot }[] = [
  { key: 'expert', label: '专家', icon: Bot },
  { key: 'skill', label: '技能', icon: Wrench },
  { key: 'connector', label: '连接器', icon: Plug },
]

function agentDisplayName(a: AgentInfo): string {
  return a.identity?.name || a.displayName || a.name || a.id
}

export default function ContextSelector({ agents, value, onSelect, onClear }: ContextSelectorProps) {
  const [open, setOpen] = useState(false)
  const [activeCat, setActiveCat] = useState<ContextCategory>('expert')
  const [query, setQuery] = useState('')

  const [skills, setSkills] = useState<{ name: string; description: string; title?: string }[]>([])
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [connectors, setConnectors] = useState<UserConnector[]>([])
  const [connectorsLoading, setConnectorsLoading] = useState(false)

  const expertList = useMemo(
    () => agents.filter((a) => a.id !== 'main' && a.id !== 'manager'),
    [agents],
  )

  // Lazily load skills / connectors when their category is first opened.
  useEffect(() => {
    if (activeCat === 'skill' && skills.length === 0 && !skillsLoading) {
      setSkillsLoading(true)
      listSkills()
        .then((list) => setSkills(list.map((s) => ({ name: s.name, description: s.description, title: s.title }))))
        .catch(() => setSkills([]))
        .finally(() => setSkillsLoading(false))
    }
    if (activeCat === 'connector' && connectors.length === 0 && !connectorsLoading) {
      setConnectorsLoading(true)
      listUserConnectors()
        .then((list) => setConnectors(list))
        .catch(() => setConnectors([]))
        .finally(() => setConnectorsLoading(false))
    }
  }, [activeCat, skills.length, skillsLoading, connectors.length, connectorsLoading])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (activeCat === 'expert') {
      return expertList
        .filter((a) => !q || agentDisplayName(a).toLowerCase().includes(q) || a.id.toLowerCase().includes(q))
        .map((a) => ({ id: a.id, name: agentDisplayName(a) }))
    }
    if (activeCat === 'skill') {
      return skills
        .filter((s) => !q || (s.title || s.name).toLowerCase().includes(q) || s.name.toLowerCase().includes(q))
        .map((s) => ({ id: s.name, name: s.title || s.name, desc: s.description }))
    }
    return connectors
      .filter((c) => !q || c.display_name.toLowerCase().includes(q) || c.name.toLowerCase().includes(q))
      .map((c) => ({
        id: c.id,
        name: c.display_name,
        desc: c.description,
        needsAuth: c.needs_auth,
        enabled: c.enabled,
      }))
  }, [activeCat, query, expertList, skills, connectors])

  const handlePick = useCallback(
    (item: { id: string; name: string; desc?: string; needsAuth?: boolean; enabled?: boolean }) => {
      // 连接器以管理端为准：未启用的不可在选择器里打开，也不允许前端自动 enable。
      if (activeCat === 'connector' && item.enabled === false) {
        return
      }
      onSelect({
        category: activeCat,
        id: item.id,
        name: item.name,
        needsAuth: item.needsAuth,
        enabled: item.enabled,
      })
      setOpen(false)
      setQuery('')
    },
    [activeCat, onSelect],
  )

  const activeMeta = CATEGORIES.find((c) => c.key === activeCat)!

  return (
    <div className="relative">
      {value ? (
        <div className="mb-2 flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-accent-blue/40 bg-accent-blue/10 px-2.5 py-1 text-xs text-accent-blue">
            {value.category === 'expert' && <Bot size={12} />}
            {value.category === 'skill' && <Wrench size={12} />}
            {value.category === 'connector' && <Plug size={12} />}
            <span className="font-medium">{value.name}</span>
          </span>
          <button
            type="button"
            onClick={onClear}
            className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-1 text-xs text-light-text-secondary hover:text-red-500"
            title="清除绑定"
          >
            <X size={12} />
            清除
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="mb-2 inline-flex items-center gap-1 rounded-full border border-dashed border-light-text-secondary/40 px-2.5 py-1 text-xs text-light-text-secondary hover:border-accent-blue hover:text-accent-blue"
        >
          <Plug size={12} />
          添加上下文（专家 / 技能 / 连接器）
        </button>
      )}

      {open && (
        <div className="absolute bottom-full z-30 mb-2 flex h-80 w-[420px] max-w-[92vw] overflow-hidden rounded-xl border border-light-bg bg-white shadow-2xl dark:border-dark-bg dark:bg-dark-surface">
          {/* 左：分类 */}
          <div className="w-28 shrink-0 border-r border-light-bg bg-light-bg/50 p-2 dark:border-dark-bg dark:bg-dark-bg/50">
            {CATEGORIES.map((cat) => {
              const Icon = cat.icon
              const active = cat.key === activeCat
              return (
                <button
                  key={cat.key}
                  type="button"
                  onClick={() => {
                    setActiveCat(cat.key)
                    setQuery('')
                  }}
                  className={`mb-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm ${
                    active
                      ? 'bg-accent-blue text-white'
                      : 'text-light-text-secondary hover:bg-light-bg dark:hover:bg-dark-bg'
                  }`}
                >
                  <Icon size={15} />
                  {cat.label}
                </button>
              )
            })}
          </div>

          {/* 右：列表 */}
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="border-b border-light-bg p-2 dark:border-dark-bg">
              <div className="flex items-center gap-2 rounded-lg bg-light-bg px-2.5 py-1.5 dark:bg-dark-bg">
                <Search size={14} className="text-light-text-secondary" />
                <input
                  autoFocus
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={`搜索${activeMeta.label}`}
                  className="w-full bg-transparent text-sm outline-none"
                />
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {activeCat === 'expert' && filtered.length === 0 && (
                <p className="px-2 py-6 text-center text-sm text-light-text-secondary">没有可选的专家</p>
              )}
              {activeCat === 'skill' && skillsLoading && (
                <p className="px-2 py-6 text-center text-sm text-light-text-secondary">加载技能中…</p>
              )}
              {activeCat === 'connector' && connectorsLoading && (
                <p className="px-2 py-6 text-center text-sm text-light-text-secondary">加载连接器中…</p>
              )}
              {!skillsLoading && !connectorsLoading && filtered.length === 0 && (
                <p className="px-2 py-6 text-center text-sm text-light-text-secondary">无匹配项</p>
              )}

              {filtered.map((item) => {
                const selected = value?.category === activeCat && value?.id === item.id
                const disabled = activeCat === 'connector' && item.enabled === false
                return (
                  <button
                    key={item.id}
                    type="button"
                    disabled={disabled}
                    onClick={() => handlePick(item)}
                    title={disabled ? '该连接器未启用，请到连接器页面手动启用' : undefined}
                    className={`mb-1 flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left ${
                      selected
                        ? 'bg-accent-blue/10 ring-1 ring-accent-blue/40'
                        : disabled
                          ? 'cursor-not-allowed opacity-50'
                          : 'hover:bg-light-bg dark:hover:bg-dark-bg'
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 text-sm font-medium">
                        {item.name}
                        {activeCat === 'connector' && disabled && (
                          <span className="rounded bg-slate-500/15 px-1.5 py-0.5 text-[10px] text-slate-500">
                            未启用
                          </span>
                        )}
                        {activeCat === 'connector' && item.needsAuth && !disabled && (
                          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-600">
                            需授权
                          </span>
                        )}
                      </div>
                      {item.desc && (
                        <div className="mt-0.5 line-clamp-1 text-xs text-light-text-secondary">{item.desc}</div>
                      )}
                    </div>
                    {selected && <Check size={15} className="mt-0.5 shrink-0 text-accent-blue" />}
                  </button>
                )
              })}
            </div>

            <div className="border-t border-light-bg px-3 py-1.5 text-[11px] text-light-text-secondary dark:border-dark-bg">
              每次仅绑定 1 个上下文；选“专家”将切换到该专家对话，选“技能/连接器”将在本会话中生效。
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
