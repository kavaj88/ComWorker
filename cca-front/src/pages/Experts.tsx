import { useMemo, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { Bot, MessageSquare, Search, Settings } from 'lucide-react'
import ClearableInput from '../components/ui/ClearableInput.tsx'
import AgentCreatePanel from '../components/AgentCreatePanel.tsx'
import type { LayoutOutletContext } from '../components/Layout.tsx'

function expertName(agent: LayoutOutletContext['agents'][number]): string {
  if (agent.id === 'main') return '快速问答'
  return agent.identity?.name || agent.name || agent.id
}

export default function Experts() {
  const navigate = useNavigate()
  const { agents, agentsLoading, openMobileSidebar, refreshAgents } = useOutletContext<LayoutOutletContext>()
  const [query, setQuery] = useState('')
  const [agentPanelOpen, setAgentPanelOpen] = useState(false)
  const experts = useMemo(() => agents.filter(agent => agent.id !== 'main'), [agents])
  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase()
    if (!term) return experts
    return experts.filter(agent => `${agent.id} ${expertName(agent)}`.toLowerCase().includes(term))
  }, [experts, query])

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-light-bg">
      <div className="flex min-h-0 flex-1 flex-col px-4 py-5 sm:px-5 lg:px-6">
        <header className="mb-4 flex shrink-0 flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <button onClick={openMobileSidebar} className="mb-3 inline-flex items-center gap-2 rounded-xl border border-light-border bg-light-card px-3 py-2 text-sm text-light-text-secondary shadow-sm lg:hidden">菜单</button>
            <div className="flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-accent-blue/10 text-accent-blue"><Bot size={22} /></span>
              <div>
                <h1 className="text-xl font-semibold text-light-text">专家</h1>
                <p className="text-sm text-light-text-secondary">查看专家配置、发起对话。</p>
              </div>
            </div>
          </div>
          <ClearableInput value={query} onValueChange={setQuery} placeholder="搜索专家名称或 ID" className="h-10 w-full rounded-xl border border-light-border bg-light-card px-3 text-sm outline-none xl:w-80" clearLabel="清空搜索" />
          <button
            type="button"
            onClick={() => setAgentPanelOpen(true)}
            className="flex shrink-0 cursor-pointer items-center gap-2 rounded-xl border border-light-border bg-light-card px-4 py-2.5 text-sm font-medium text-light-text transition-colors hover:bg-light-card-hover"
          >
            <Bot size={17} className="text-accent-blue" />
            创建专属 Agent
          </button>
        </header>

        {agentsLoading ? (
          <div className="flex items-center justify-center py-20 text-light-text-secondary">正在加载专家</div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-light-border bg-light-card py-20 text-sm text-light-text-secondary">
            <Search className="mb-3" /> 暂无匹配专家
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {filtered.map(agent => (
              <article key={agent.id} className="rounded-2xl border border-light-border bg-light-card p-4 shadow-sm transition hover:border-accent-blue/30 hover:shadow-md">
                <div className="flex items-start gap-3">
                  <span className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-2xl bg-light-card-hover text-xl">
                    {agent.identity?.avatar || agent.identity?.avatarUrl ? <img src={agent.identity.avatar || agent.identity.avatarUrl} alt="" className="h-full w-full object-cover" /> : agent.identity?.emoji || <Bot className="text-accent-blue" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate text-base font-semibold text-light-text">{expertName(agent)}</h2>
                    <p className="truncate text-xs text-light-text-secondary">{agent.id}</p>
                  </div>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button onClick={() => navigate(`/chat?new=1&agent=${encodeURIComponent(agent.id)}`)} className="inline-flex items-center gap-1.5 rounded-xl bg-accent-blue px-3 py-2 text-sm font-medium text-white"><MessageSquare size={15} /> 对话</button>
                  <button onClick={() => navigate(`/experts/${encodeURIComponent(agent.id)}`)} className="inline-flex items-center gap-1.5 rounded-xl border border-light-border px-3 py-2 text-sm text-light-text-secondary hover:bg-light-card-hover"><Settings size={15} /> 详情</button>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
      <AgentCreatePanel
        open={agentPanelOpen}
        onClose={() => setAgentPanelOpen(false)}
        onCreated={async (agentId, displayName) => {
          await refreshAgents({ force: true })
          navigate(
            `/chat?new=1&agent=${encodeURIComponent(agentId)}&createdAgent=${encodeURIComponent(displayName)}`,
          )
        }}
      />
    </div>
  )
}
