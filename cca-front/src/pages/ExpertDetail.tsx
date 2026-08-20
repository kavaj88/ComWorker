import { useEffect, useState } from 'react'
import { useNavigate, useOutletContext, useParams } from 'react-router-dom'
import { ArrowLeft, Bot, FileText, Loader2, MessageSquare } from 'lucide-react'
import type { LayoutOutletContext } from '../components/Layout.tsx'
import { getAgentFile } from '../lib/api.ts'
import type { AgentFileResult } from '../lib/api.ts'

const configFiles = ['IDENTITY.md', 'AGENTS.md', 'SOUL.md', 'USER.md']

function expertName(agent: LayoutOutletContext['agents'][number] | undefined, id: string): string {
  return agent?.identity?.name || agent?.name || id
}

export default function ExpertDetail() {
  const { id = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { agents, agentsLoading, openMobileSidebar } = useOutletContext<LayoutOutletContext>()
  const agent = agents.find(item => item.id === id)
  const [files, setFiles] = useState<Record<string, AgentFileResult['file']>>({})
  const [loadingFiles, setLoadingFiles] = useState(true)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  useEffect(() => {
    if (!id) return
    let cancelled = false
    setLoadingFiles(true)
    Promise.all(configFiles.map(async name => {
      try {
        const result = await getAgentFile(id, name)
        return [name, result.file] as const
      } catch {
        return [name, { name, path: name, missing: true }] as const
      }
    })).then(entries => {
      if (!cancelled) setFiles(Object.fromEntries(entries))
    }).finally(() => {
      if (!cancelled) setLoadingFiles(false)
    })
    return () => { cancelled = true }
  }, [id])

  if (agentsLoading) {
    return <div className="flex h-full items-center justify-center text-light-text-secondary"><Loader2 className="mr-2 animate-spin" /> 正在加载专家</div>
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-light-bg">
      <div className="flex min-h-0 flex-1 flex-col px-4 py-5 sm:px-5 lg:px-6">
        <header className="mb-4 flex shrink-0 flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <button onClick={openMobileSidebar} className="mb-3 inline-flex items-center gap-2 rounded-xl border border-light-border bg-light-card px-3 py-2 text-sm text-light-text-secondary shadow-sm lg:hidden">菜单</button>
            <button onClick={() => navigate('/experts')} className="mb-3 inline-flex items-center gap-2 text-sm text-light-text-secondary hover:text-light-text"><ArrowLeft size={16} /> 返回专家列表</button>
            <div className="flex items-center gap-3">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-2xl bg-light-card-hover text-xl">
                {agent?.identity?.avatar || agent?.identity?.avatarUrl ? <img src={agent.identity.avatar || agent.identity.avatarUrl} alt="" className="h-full w-full object-cover" /> : agent?.identity?.emoji || <Bot className="text-accent-blue" />}
              </span>
              <div>
                <h1 className="text-xl font-semibold text-light-text">{expertName(agent, id)}</h1>
                <p className="text-sm text-light-text-secondary">{id}</p>
              </div>
            </div>
          </div>
          <button onClick={() => navigate(`/chat?new=1&agent=${encodeURIComponent(id)}`)} className="inline-flex items-center gap-2 rounded-xl bg-accent-blue px-4 py-2 text-sm font-medium text-white shadow-sm"><MessageSquare size={16} /> 开始对话</button>
        </header>

        <section className="min-h-0 flex-1 overflow-hidden rounded-2xl border border-light-border bg-light-card shadow-sm">
          <div className="border-b border-light-border px-4 py-3 text-sm font-medium text-light-text"><FileText className="mr-2 inline" size={16} /> 配置文件</div>
          <div className="h-full min-h-0 overflow-y-auto p-4">
            {loadingFiles ? (
              <div className="flex items-center justify-center py-16 text-light-text-secondary"><Loader2 className="mr-2 animate-spin" /> 正在读取配置</div>
            ) : (
              <div className="space-y-3">
                {configFiles.map(name => {
                  const file = files[name]
                  const isOpen = expanded[name]
                  return (
                    <div key={name} className="rounded-2xl border border-light-border bg-light-card-hover/60">
                      <button onClick={() => setExpanded(prev => ({ ...prev, [name]: !prev[name] }))} className="flex w-full items-center justify-between px-4 py-3 text-left">
                        <span className="text-sm font-medium text-light-text">{name}</span>
                        <span className="text-xs text-light-text-secondary">{file?.missing ? '缺失' : '查看'}</span>
                      </button>
                      {isOpen && (
                        <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap border-t border-light-border px-4 py-3 text-sm leading-6 text-light-text-secondary">{file?.content || '(空)'}</pre>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}
