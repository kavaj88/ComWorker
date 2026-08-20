import { useState, useEffect, useRef, useCallback, useMemo, useDeferredValue, Fragment, type CSSProperties } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import {
  Plus,
  Send,
  Loader2,
  MessageSquare,
  Bot,
  Search,
  RefreshCw,
  ChevronDown,
  Copy,
  Check,
  X,
  FileText,
  Menu,
  Square,
  ChevronRight,
  Wrench,
  Brain,
  CircleCheck,
  AlertCircle,
  ShieldQuestion,
  Info,
  Plug,
} from 'lucide-react'
import MarkdownContent from '../components/MarkdownContent.tsx'
import AgentCreatePanel from '../components/AgentCreatePanel.tsx'
import KnowledgeReaderPanel from '../components/KnowledgeReaderPanel.tsx'
import ClearableInput from '../components/ui/ClearableInput.tsx'
import ClearableTextarea from '../components/ui/ClearableTextarea.tsx'
import IconButton from '../components/ui/IconButton.tsx'
import Tooltip from '../components/ui/Tooltip.tsx'
import { useToast } from '../components/ui/Toast.tsx'
import { ThinkingCard } from '../components/ThinkingCard.tsx'
import { ProcessCard, type ProcessStep } from '../components/ProcessCard.tsx'
import type { LayoutOutletContext } from '../components/Layout.tsx'
import {
  getSession,
  sendChatMessage,
  waitForAgentRun,
  getRunEventsStreamUrl,
  abortAgentRun,
  abortActiveSessionRun,
  respondRunApproval,
  getAccessToken,
  uploadFileToWorkspace,
  listSlashCommands,
  listModels,
  updateModelsConfig,
  openWorkspaceFile,
  listSkills,
  listUserConnectors,
} from '../lib/api.ts'
import type { Session, SessionDetail, AgentInfo, ModelChoice, AgentRunWaitResult, UserConnector } from '../lib/api.ts'
import {
  CATEGORY_LABELS,
  CATEGORY_STYLES,
  buildSlashCommandItems,
  filterSlashCommands,
  getSlashQuery,
  type SlashCommandItem,
} from '../lib/slashCommands.ts'

const systemAgentIds = new Set(['main', 'manager', 'programmer', 'researcher', 'hr', 'doctor'])

/**
 * Extract agentId from session key.
 * Format: agent:<agentId>:session-<timestamp>
 */
function getAgentIdFromKey(key: string): string {
  const parts = key.split(':')
  if (parts.length >= 2 && parts[0] === 'agent') return parts[1]
  return 'main'
}

/**
 * Get the workspace upload dir for an agent.
 * Hermes profiles keep uploads under profiles/<agentId>/workspace/uploads.
 */
function getUploadDir(agentId: string): string {
  return 'profiles/' + agentId + '/workspace/uploads'
}

interface PendingFile {
  id: string
  file: File
  name: string
  isImage: boolean
  previewUrl?: string
}

type AgentActivityStatus = 'running' | 'completed' | 'failed' | 'thinking' | 'approval'
type RunStreamResult = 'completed' | 'failed' | 'cancelled' | 'error'

interface ReasoningSegment {
  content: string
  ts: number
  finalized: boolean
}

interface AgentActivityEvent {
  id: string
  runId: string
  type: string
  title: string
  detail?: string
  status: AgentActivityStatus
  timestamp: number
  choices?: string[]
  selectedChoice?: string
  responding?: boolean
}

interface AgentActivityArchive {
  id: string
  runId: string
  startedAt: number
  endedAt: number
  events: AgentActivityEvent[]
  expanded: boolean
  assistantIndex?: number
  thoughts?: string[]
  toolEventsExpanded?: boolean
  durationReliable?: boolean
}

interface RunActivityStream {
  ready: Promise<boolean>
  done: Promise<RunStreamResult>
}

function tryParseJSONObject(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) return null
  try {
    const parsed = JSON.parse(trimmed)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null
  } catch {
    return null
  }
}

function isToolResultMessage(content: string): boolean {
  const parsed = tryParseJSONObject(content)
  if (!parsed) return false
  const keys = Object.keys(parsed)
  return (
    ('output' in parsed && ('exit_code' in parsed || 'approval' in parsed || 'error' in parsed)) ||
    (keys.length <= 5 && 'exit_code' in parsed && ('stdout' in parsed || 'stderr' in parsed))
  )
}

function isProcessingPreludeMessage(content: string): boolean {
  const normalized = content.trim().replace(/\s+/g, ' ').toLowerCase()
  if (!normalized) return true
  return [
    'let me check',
    "i'll check",
    'i will check',
    "i'm going to check",
    'i am going to check',
    'checking ',
    'checking',
    'checking now',
    'let me check',
    'i will check',
    'check first',
    'check',
  ].some(prefix => normalized.startsWith(prefix))
}

function isVisibleChatMessage(messages: SessionDetail['messages'], index: number): boolean {
  const msg = messages[index]
  if (!msg) return false
  if (msg.role !== 'user' && msg.role !== 'assistant') return false
  if (msg.role === 'assistant' && !(msg.content || '').trim()) return false
  // An assistant message that issues tool_calls is an intermediate step in an
  // agent turn — its reasoning + tool calls are merged into the final answer's
  // ProcessCard. Hide it so the turn renders as one bubble.
  if (msg.role === 'assistant' && Array.isArray((msg as any).tool_calls) && (msg as any).tool_calls.length > 0) {
    return false
  }
  if (msg.role === 'assistant' && isProcessingPreludeMessage(msg.content || '')) {
    const followedByTool = messages.slice(index + 1).some(next => {
      if (next.role === 'user') return false
      return next.role === 'tool'
    })
    if (followedByTool) return false
  }
  return !(msg.role === 'assistant' && isToolResultMessage(msg.content || ''))
}

function filterVisibleMessages(messages: SessionDetail['messages']): SessionDetail['messages'] {
  return messages.filter((_, index) => isVisibleChatMessage(messages, index))
}

function latestVisibleAssistantTurn(messages: SessionDetail['messages']): SessionDetail['messages'] {
  let assistantIndex = -1
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === 'assistant' && isVisibleChatMessage(messages, index)) {
      assistantIndex = index
      break
    }
  }
  if (assistantIndex < 0) return []

  let userIndex = -1
  for (let index = assistantIndex - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === 'user') {
      userIndex = index
      break
    }
  }
  return messages.slice(userIndex + 1, assistantIndex + 1)
}

function visibleAssistantCountBefore(messages: SessionDetail['messages'], rawAssistantIndex: number): number {
  let count = 0
  for (let index = 0; index <= rawAssistantIndex; index += 1) {
    if (messages[index]?.role === 'assistant' && isVisibleChatMessage(messages, index)) {
      count += 1
    }
  }
  return count
}

function latestVisibleAssistantIndex(messages: SessionDetail['messages']): number | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === 'assistant' && isVisibleChatMessage(messages, index)) {
      return visibleAssistantCountBefore(messages, index) - 1
    }
  }
  return undefined
}

function hasProcessingForLatestTurn(messages: SessionDetail['messages']): boolean {
  const turn = latestVisibleAssistantTurn(messages)
  return turn.some((msg, index) => {
    if (msg.role === 'tool') return true
    if (msg.role !== 'assistant') return false
    if (msg.role === 'assistant' && isProcessingPreludeMessage(msg.content || '')) {
      return turn.slice(index + 1).some(next => {
        if (next.role === 'user') return false
        return next.role === 'tool'
      })
    }
    return false
  })
}

function buildProcessStepsForTurn(
  rawMessages: SessionDetail['messages'],
  visibleAssistantRawIndex: number,
): ProcessStep[] {
  const steps: ProcessStep[] = []
  let turnStart = visibleAssistantRawIndex
  while (turnStart > 0 && rawMessages[turnStart - 1]?.role !== 'user') {
    turnStart -= 1
  }
  for (let i = turnStart; i <= visibleAssistantRawIndex; i += 1) {
    const msg = rawMessages[i]
    if (!msg || msg.role !== 'assistant') continue
    const reasoning = (msg.reasoning_content || '').trim()
    if (reasoning) {
      steps.push({ type: 'thinking', content: reasoning })
    }
    const toolCalls = (msg as any).tool_calls
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      for (const tc of toolCalls) {
        const fn = tc?.function || {}
        const name = formatToolName(String(fn.name || 'tool'))
        const args = typeof fn.arguments === 'string' ? fn.arguments : JSON.stringify(fn.arguments || {})
        const resultMsg = rawMessages
          .slice(i + 1)
          .find(m => m?.role === 'tool' && (m as any).tool_call_id === (tc.call_id || tc.id))
        const isError = typeof resultMsg?.content === 'string' && /error|failed|not found/i.test(resultMsg.content.slice(0, 200))
        steps.push({
          type: 'tool',
          toolName: name,
          toolArgs: args,
          status: isError ? 'failed' : 'completed',
        })
      }
    }
  }
  return steps
}

function buildProcessingEvents(messages: SessionDetail['messages']): AgentActivityEvent[] {
  return messages.flatMap((msg, index) => {
    if (msg.role === 'tool') {
      const detail = (msg.content || '').trim()
      return [{
        id: 'history-tool:' + index,
        runId: '',
        type: 'tool.completed',
        title: '工具已完成',
        detail: detail.length > 420 ? detail.slice(0, 420) + '...' : detail,
        status: 'completed' as AgentActivityStatus,
        timestamp: Date.now(),
      }]
    }
    return []
  })
}

function extractProcessingThoughts(messages: SessionDetail['messages']): string[] {
  return messages.flatMap((msg, index) => {
    if (msg.role !== 'assistant' || !isProcessingPreludeMessage(msg.content || '')) return []
    const followedByTool = messages.slice(index + 1).some(next => {
      if (next.role === 'user') return false
      return next.role === 'tool'
    })
    return followedByTool ? [msg.content.trim()] : []
  })
}

function formatActivityDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.round(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes <= 0) return String(seconds) + 's'
  return String(minutes) + 'm ' + String(seconds) + 's'
}

function archiveStorageKey(sessionKey: string): string {
  return 'comworker:activity-archives:' + sessionKey
}

function loadActivityArchives(sessionKey: string): AgentActivityArchive[] {
  try {
    const raw = window.sessionStorage.getItem(archiveStorageKey(sessionKey))
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(item => item && Array.isArray(item.events)) : []
  } catch {
    return []
  }
}

function saveActivityArchives(sessionKey: string, archives: AgentActivityArchive[]) {
  try {
    window.sessionStorage.setItem(archiveStorageKey(sessionKey), JSON.stringify(archives.slice(-6)))
  } catch {
    // Ignore storage quota/privacy mode errors; live state still works.
  }
}

function isImageFile(file: File): boolean {
  return file.type.startsWith('image/')
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return String(bytes) + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function normalizeSessionKey(key: string): string {
  return key.replace(/:/g, '')
}

function buildFallbackTitleFromText(fileCount = 0): string {
  if (fileCount > 0) return fileCount === 1 ? '处理附件' : '处理 ' + fileCount + ' 个附件'
  return '新对话'
}

function buildTitleFromMessages(messages: SessionDetail['messages']): string {
  const firstUserMessage = messages.find(msg => msg.role === 'user' && msg.content.trim())
  if (!firstUserMessage) return ''
  const text = firstUserMessage.content.trim()
  if (!text) return ''
  // Strip leading/trailing whitespace collapse, truncate to ~40 chars
  const cleaned = text.replace(/\s+/g, ' ').slice(0, 40)
  return cleaned.length < text.replace(/\s+/g, ' ').length ? cleaned + '…' : cleaned
}

function hasAssistantAfterLastUser(messages: SessionDetail['messages']): boolean {
  const visibleMessages = filterVisibleMessages(messages)
  const lastUserIndex = visibleMessages.map(msg => msg.role).lastIndexOf('user')
  if (lastUserIndex < 0) return visibleMessages.some(msg => msg.role === 'assistant' && msg.content.trim())
  return visibleMessages
    .slice(lastUserIndex + 1)
    .some(msg => msg.role === 'assistant' && msg.content.trim())
}

function isRunFinished(status: string | undefined): boolean {
  return ['ok', 'completed', 'error', 'failed', 'aborted', 'cancelled'].includes(status || '')
}

function isRunFailed(status: string | undefined): boolean {
  return ['error', 'failed'].includes(status || '')
}

// Check if reasoning text is semantically similar to streamed text content.
// Used to hide reasoning step when the model restates the same plan as both
// reasoning.available and message.delta — showing both looks duplicated.
// Heuristic: strip punctuation/whitespace, then compare character overlap.
// Returns true when >60% of characters in the shorter string appear in the
// longer one.
function isSimilarText(a: string, b: string): boolean {
  const normalize = (s: string) => s.replace(/[\s，。、,.!?！？;；:：、()（）"'`'']/g, '').toLowerCase()
  const an = normalize(a)
  const bn = normalize(b)
  if (an.length < 5 || bn.length < 5) return false
  if (an === bn) return true
  if (an.includes(bn) || bn.includes(an)) return true
  const shorter = an.length < bn.length ? an : bn
  const longer = an.length < bn.length ? bn : an
  const longSet = new Set(longer)
  let common = 0
  for (const c of shorter) if (longSet.has(c)) common++
  return common / shorter.length > 0.6
}

function formatToolName(name: string): string {
  return name
    .replace(/^mcp__/, '')
    .replace(/__/g, ' / ')
    .replace(/[_-]+/g, ' ')
    .trim() || '未知工具'
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

function buildActivityTitle(eventType: string, payload: any): string {
  const upstreamTitle = firstString(payload.title, payload.label, payload.name)
  if (upstreamTitle) return upstreamTitle
  if (eventType === 'tool.started') {
    return '正在运行 ' + formatToolName(String(payload.tool || 'tool'))
  }
  if (eventType === 'tool.completed') {
    return (payload.error ? '工具执行失败' : '工具已完成') + ': ' + formatToolName(String(payload.tool || 'tool'))
  }
  if (eventType === 'reasoning.available') return '正在思考下一步'
  if (eventType === 'approval.request') return '等待授权'
  if (eventType === 'run.failed') return 'Agent 执行失败'
  return 'Agent 状态'
}

function buildActivityDetail(eventType: string, payload: any): string | undefined {
  const preview = firstString(payload.preview, payload.description, payload.command, payload.input)
  const text = firstString(payload.text, payload.summary)
  const error = firstString(payload.error, payload.message)
  if (eventType === 'tool.started') return preview || undefined
  if (eventType === 'tool.completed') {
    const duration = typeof payload.duration === 'number' ? payload.duration.toFixed(1) + 's' : ''
    return error || duration || undefined
  }
  if (eventType === 'reasoning.available') return text || preview || '正在分析任务并选择工具'
  if (eventType === 'approval.request') return preview || text || '需要授权后才能继续'
  if (eventType === 'run.failed') return error || undefined
  return preview || text || error || undefined
}

function normalizeApprovalChoices(payload: any): string[] {
  return Array.isArray(payload.choices)
    ? payload.choices.filter((choice: unknown): choice is string => typeof choice === 'string' && Boolean(choice.trim()))
    : []
}

function approvalChoiceLabel(choice: string): string {
  const labels: Record<string, string> = {
    once: '本次允许',
    session: '本会话允许',
    always: '始终允许',
    deny: '拒绝',
  }
  return labels[choice] || choice
}

const agentDescriptions: Record<string, string> = {
  main: '处理通用任务的默认助手',
  manager: '拆解任务并协调多个 Agent',
  programmer: '代码、工程、调试和技术方案',
  researcher: '检索公开信息并整理结论',
  hr: '招聘与人力流程助手',
  doctor: '医疗咨询场景的专业助手',
}

function ChatHistorySkeleton() {
  return (
    <div className="mx-auto max-w-5xl space-y-5 py-2" aria-label="正在加载对话历史">
      <div className="flex justify-end gap-3">
        <div className="flex w-full max-w-[64%] flex-col items-end gap-2">
          <div className="skeleton-shimmer h-11 w-full rounded-xl" />
          <div className="skeleton-shimmer h-2.5 w-16 rounded-full" />
        </div>
      </div>

      <div className="flex">
        <div className="w-full max-w-[78%] px-1 py-2">
          <div className="skeleton-shimmer h-3.5 w-11/12 rounded-full" />
          <div className="skeleton-shimmer mt-2.5 h-3.5 w-full rounded-full" />
          <div className="skeleton-shimmer mt-2.5 h-3.5 w-8/12 rounded-full" />
          <div className="skeleton-shimmer mt-3 h-2.5 w-14 rounded-full" />
        </div>
      </div>

      <div className="flex justify-end gap-3">
        <div className="flex w-full max-w-[52%] flex-col items-end gap-2">
          <div className="skeleton-shimmer h-10 w-full rounded-xl" />
          <div className="skeleton-shimmer h-2.5 w-14 rounded-full" />
        </div>
      </div>

      <div className="flex">
        <div className="w-full max-w-[72%] px-1 py-2">
          <div className="skeleton-shimmer h-3.5 w-full rounded-full" />
          <div className="skeleton-shimmer mt-2.5 h-3.5 w-9/12 rounded-full" />
          <div className="skeleton-shimmer mt-3 h-2.5 w-14 rounded-full" />
        </div>
      </div>
    </div>
  )
}

// ── 进行中 run 的跨页面/跨刷新恢复 ──────────────────────────────────────────
// 切走对话页（组件卸载）会关闭 Run 事件流；刷新页面则 React 状态全失。
// 把"进行中 run"同时存到模块单例（同 SPA 内切页可恢复）与 sessionStorage
// （整页刷新可恢复），挂载时再自动 re-attach 流式，避免"页面空白 / 结果突然出现"。
interface InFlightRun {
  sessionKey: string
  runId: string
  model: string
}
let INFLIGHT_RUN: InFlightRun | null = null
const INFLIGHT_RUN_STORAGE_KEY = 'comworker:inflight-run'

function persistInFlightRun(run: InFlightRun | null) {
  INFLIGHT_RUN = run
  try {
    if (run) {
      window.sessionStorage.setItem(INFLIGHT_RUN_STORAGE_KEY, JSON.stringify(run))
    } else {
      window.sessionStorage.removeItem(INFLIGHT_RUN_STORAGE_KEY)
    }
  } catch {
    // ignore storage errors (private mode / quota)
  }
}

function loadInFlightRun(): InFlightRun | null {
  if (INFLIGHT_RUN) return INFLIGHT_RUN
  try {
    const raw = window.sessionStorage.getItem(INFLIGHT_RUN_STORAGE_KEY)
    if (raw) {
      INFLIGHT_RUN = JSON.parse(raw) as InFlightRun
      return INFLIGHT_RUN
    }
  } catch {
    // ignore
  }
  return null
}

function clearInFlightRunIfMatches(runId: string | null | undefined) {
  if (!runId) return
  if (INFLIGHT_RUN && INFLIGHT_RUN.runId === runId) {
    persistInFlightRun(null)
  }
}

export default function Chat() {
  const [searchParams, setSearchParams] = useSearchParams()
  const {
    agents,
    currentSessionTitle,
    refreshAgents,
    refreshSessions,
    addOptimisticSession,
    setSessionThinking,
    openMobileSidebar,
  } = useOutletContext<LayoutOutletContext>()

  // Sessions
  const [activeSessionKey, setActiveSessionKey] = useState<string | null>(null)

  // Chat
  const [messages, setMessages] = useState<SessionDetail['messages']>([])
  const [chatLoading, setChatLoading] = useState(false)
  const [input, setInput] = useState('')
  const [slashCommands, setSlashCommands] = useState<SlashCommandItem[]>([])
  const [slashCommandsLoading, setSlashCommandsLoading] = useState(false)
  const [slashCommandsError, setSlashCommandsError] = useState('')
  const [slashActiveIndex, setSlashActiveIndex] = useState(0)
  const [slashMenuDismissed, setSlashMenuDismissed] = useState(false)
  const [sendingBySession, setSendingBySession] = useState<Record<string, boolean>>({})
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null)
  const [readerPanel, setReaderPanel] = useState<{ agentId: string; path: string } | null>(null)
  const [displayedTextBySession, setDisplayedTextBySession] = useState<Record<string, string>>({})
  // Reasoning is tracked as a list of segments — one per LLM call. Each
  // `reasoning.available` event finalizes the current segment (replaces its
  // content with the complete snippet) so the next `message.delta.reasoning_content`
  // starts a new segment. This matches `buildProcessStepsForTurn`, which emits
  // one thinking step per assistant message (one per LLM call).
  const [displayedReasoningBySession, setDisplayedReasoningBySession] = useState<Record<string, ReasoningSegment[]>>({})
  const [activityBySession, setActivityBySession] = useState<Record<string, AgentActivityEvent[]>>({})
  const [activityArchivesBySession, setActivityArchivesBySession] = useState<Record<string, AgentActivityArchive[]>>({})
  const activityBySessionRef = useRef<Record<string, AgentActivityEvent[]>>({})
  const targetTextBySessionRef = useRef<Record<string, string>>({})
  const reasoningTextBySessionRef = useRef<Record<string, ReasoningSegment[]>>({})
  const textFirstTsBySessionRef = useRef<Record<string, number>>({})
  const typewriterTimersRef = useRef<Record<string, ReturnType<typeof setInterval>>>({})
  const sendingBySessionRef = useRef<Record<string, boolean>>({})
  const runIdBySessionRef = useRef<Record<string, string>>({})
  const abortedSessionRef = useRef<Record<string, boolean>>({})
  const sseCompletedRef = useRef<Record<string, boolean>>({})
  const sseReceivedRef = useRef<Record<string, boolean>>({})
  // Once a tool call happens in a run, the AI's text deltas are draft/working
  // notes (incoherent planning text the model emits as `content` instead of
  // `reasoning_content`). Hide them — ProcessCard shows thinking + tools for
  // progress, and the final answer loads from the API after run completes.
  const textHiddenBySessionRef = useRef<Record<string, boolean>>({})
  const sseFinalTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({})
  const runEventSourcesRef = useRef<Record<string, EventSource>>({})
  const runStreamDoneRef = useRef<Record<string, RunStreamResult>>({})
  // 解析器映射：组件卸载关闭 Run SSE 时，用"已 detached"结果唤醒挂起的 await stream.done，
  // 让上一次实例的 waitForResponse 正常收尾（不泄漏、不卡死侧栏转圈）。
  const runStreamDoneResolverRef = useRef<Record<string, (result: RunStreamResult) => void>>({})
  const runStreamSettledRef = useRef<Record<string, boolean>>({})
  const runActivityStartedAtRef = useRef<Record<string, number>>({})
  const sessionMessagesCacheRef = useRef<Record<string, SessionDetail['messages']>>({})

  const setSendingForSession = useCallback((key: string, value: boolean) => {
    setSessionThinking(key, value)
    setSendingBySession(prev => {
      const next = { ...prev }
      if (value) {
        next[key] = true
      } else {
        delete next[key]
      }
      sendingBySessionRef.current = next
      return next
    })
  }, [setSessionThinking])

  const clearStreamingText = useCallback((key: string) => {
    targetTextBySessionRef.current[key] = ''
    reasoningTextBySessionRef.current[key] = []
    delete textFirstTsBySessionRef.current[key]
    delete sseReceivedRef.current[key]
    delete textHiddenBySessionRef.current[key]
    setDisplayedTextBySession(prev => {
      if (!prev[key]) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
    setDisplayedReasoningBySession(prev => {
      if (!prev[key]) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
    if (typewriterTimersRef.current[key]) {
      clearInterval(typewriterTimersRef.current[key])
      delete typewriterTimersRef.current[key]
    }
  }, [])

  const setRunIdForSession = useCallback((key: string, runId: string | null) => {
    const next = { ...runIdBySessionRef.current }
    if (runId) {
      next[key] = runId
    } else {
      delete next[key]
    }
    runIdBySessionRef.current = next
  }, [])

  const clearActivityForSession = useCallback((key: string) => {
    setActivityBySession(prev => {
      if (!prev[key]) return prev
      const next = { ...prev }
      delete next[key]
      activityBySessionRef.current = next
      return next
    })
  }, [])

  const archiveActivityForSession = useCallback((key: string, runId?: string | null, visibleMessages?: SessionDetail['messages'], rawMessages?: SessionDetail['messages']) => {
    const liveEvents = activityBySessionRef.current[key] || []
    const latestTurn = rawMessages ? latestVisibleAssistantTurn(rawMessages) : []
    const rawProcessingEvents = latestTurn.length > 0 ? buildProcessingEvents(latestTurn) : []
    const events = liveEvents.length > 0 ? liveEvents : rawProcessingEvents
    const thoughts = latestTurn.length > 0 ? extractProcessingThoughts(latestTurn) : []
    if (events.length === 0 && thoughts.length === 0) return
    const resolvedRunId = runId || events[0]?.runId || runIdBySessionRef.current[key] || ''
    const endedAt = Date.now()
    const startedAt = runActivityStartedAtRef.current[key] || events[0]?.timestamp || endedAt
    // assistantIndex must match the index used in rendering (messages.map((msg, i) => ...)),
    // which iterates over visibleMessages. So prefer the visible-array index.
    const assistantIndex = visibleMessages
      ? visibleMessages.map(msg => msg.role).lastIndexOf('assistant')
      : rawMessages
        ? latestVisibleAssistantIndex(rawMessages)
      : undefined
    const archive: AgentActivityArchive = {
      id: String(resolvedRunId || key) + ':' + String(startedAt),
      runId: resolvedRunId,
      startedAt,
      endedAt,
      events,
      expanded: false,
      thoughts,
      toolEventsExpanded: false,
      durationReliable: liveEvents.length > 0,
      assistantIndex: assistantIndex !== undefined && assistantIndex >= 0 ? assistantIndex : undefined,
    }
    setActivityArchivesBySession(prev => {
      const current = prev[key] || []
      const deduped = current.filter(item => item.id !== archive.id)
      const archives = [...deduped, archive].slice(-6)
      saveActivityArchives(key, archives)
      return { ...prev, [key]: archives }
    })
  }, [])

  const closeRunEventStream = useCallback((key: string) => {
    const stream = runEventSourcesRef.current[key]
    if (stream) {
      stream.close()
      delete runEventSourcesRef.current[key]
    }
    // 若因组件卸载而关闭（尚无真实结束事件），用中性 'completed' 唤醒挂起的
    // await stream.done，使上一次实例的 waitForResponse 收尾、释放资源。
    const resolver = runStreamDoneResolverRef.current[key]
    if (resolver && !runStreamSettledRef.current[key]) {
      runStreamSettledRef.current[key] = true
      delete runStreamDoneResolverRef.current[key]
      resolver('completed')
    }
  }, [])

  const addActivityEvent = useCallback((key: string, event: AgentActivityEvent) => {
    setActivityBySession(prev => {
      const current = prev[key] || []
      // No slice limit — final state (buildProcessStepsForTurn) shows all
      // tool_calls without truncation, so streaming must too. Activity events
      // are cleared per-turn (clearActivityForSession), so memory is bounded
      // by the current turn's tool count.
      const nextEvents = [...current.filter(item => item.id !== event.id), event]
        .sort((a, b) => a.timestamp - b.timestamp)
      const next = { ...prev, [key]: nextEvents }
      activityBySessionRef.current = next
      return next
    })
  }, [])

  const setStreamingText = useCallback((key: string, text: string) => {
    if (!text) {
      clearStreamingText(key)
      return
    }
    targetTextBySessionRef.current[key] = text
    // Render target text directly. The SSE stream already provides a natural
    // streaming effect; layering a typewriter on top causes markdown re-parsing
    // lag and React key collisions on long responses.
    setDisplayedTextBySession(prev => (prev[key] === text ? prev : { ...prev, [key]: text }))
  }, [clearStreamingText])

  const applyLoadedMessages = useCallback((key: string, nextMessages: SessionDetail['messages']) => {
    const visibleMessages = filterVisibleMessages(nextMessages)
    setActivityArchivesBySession(prev => {
      if (prev[key]?.length) return prev
      const stored = loadActivityArchives(key)
      return stored.length ? { ...prev, [key]: stored } : prev
    })
    // Build a map from visible assistant message to its raw index, so we can
    // extract process steps (reasoning + tool_calls) from the raw turn.
    const visibleToRawIndices: number[] = []
    let rawCursor = 0
    for (const visMsg of visibleMessages) {
      while (rawCursor < nextMessages.length && nextMessages[rawCursor] !== visMsg) {
        rawCursor += 1
      }
      visibleToRawIndices.push(rawCursor)
      rawCursor += 1
    }
    let visibleAssistantCounter = 0
    let visibleCounter = 0
    const mapped = visibleMessages.map((m, i) => {
      const rawIdx = visibleToRawIndices[i]
      const steps = m.role === 'assistant' ? buildProcessStepsForTurn(nextMessages, rawIdx) : []
      const hasSteps = steps.length > 0
      let persistedModel: string | undefined
      if (m.role === 'assistant') {
        persistedModel = getMessageModel(key, visibleAssistantCounter)
        console.log('[DEBUG] applyLoadedMessages assistant msg', { key, i, role: m.role, visibleAssistantCounter, persistedModel, contentPreview: (m.content || '').slice(0, 50) })
        visibleAssistantCounter += 1
      }
      // 恢复真实时间：优先用持久化的（用户消息发送时记录 / 助手消息完成时记录），否则用后端自带的（通常为空）
      const persistedTime = getMessageTime(key, visibleCounter)
      visibleCounter += 1
      return {
        ...m,
        model: persistedModel,
        timestamp: persistedTime || m.timestamp || undefined,
        hasReasoning: m.role === 'assistant' && !!(m.reasoning_content && m.reasoning_content.trim()),
        processSteps: hasSteps ? steps : undefined,
      }
    })
    // Cache the enriched messages (with processSteps) so switching back to this
    // session doesn't lose the ProcessCard. Previously we cached visibleMessages
    // (pre-enrichment), which made the card disappear on revisit.
    if (mapped.length > 0) {
      sessionMessagesCacheRef.current[key] = mapped
    }
    if (hasAssistantAfterLastUser(visibleMessages)) {
      clearStreamingText(key)
      if (activityBySessionRef.current[key]?.length || hasProcessingForLatestTurn(nextMessages)) {
        archiveActivityForSession(key, null, visibleMessages, nextMessages)
      }
      clearActivityForSession(key)
      closeRunEventStream(key)
      setRunIdForSession(key, null)
      sseCompletedRef.current[key] = true
      if (sendingBySessionRef.current[key]) {
        setSendingForSession(key, false)
      }
    }
    if (activeSessionKeyRef.current !== key) return
    setMessages(prev => {
      if (mapped.length === 0 && prev.length > 0) {
        return prev
      }
      // Preserve the optimistic trailing user message when the backend hasn't
      // persisted it yet. This happens when a run is aborted before hermes
      // saves the inbound user turn (race window between create_run and
      // _persist_session). Without this, aborting mid-thought makes the
      // user's question disappear on the next getSession reload.
      const lastPrev = prev[prev.length - 1]
      const lastPrevContent =
        lastPrev?.role === 'user' && typeof lastPrev.content === 'string' ? lastPrev.content.trim() : ''
      if (
        lastPrevContent &&
        !mapped.some(m => m.role === 'user' && typeof m.content === 'string' && m.content.trim() === lastPrevContent)
      ) {
        return [...mapped, lastPrev!]
      }
      return mapped
    })
  }, [archiveActivityForSession, clearActivityForSession, clearStreamingText, closeRunEventStream, setRunIdForSession, setSendingForSession])

  const [draftAgentId, setDraftAgentId] = useState('')
  const [isDraftSession, setIsDraftSession] = useState(false)
  // 会话级上下文绑定（专家 / 技能 / 连接器），单选。专家会切换 agent；
  // 技能/连接器在每次发送时通过后端 context 注入系统提示。
  const [boundContext, setBoundContext] = useState<BoundContext | null>(null)
  const [agentPickerOpen, setAgentPickerOpen] = useState(false)
  const [agentCreateOpen, setAgentCreateOpen] = useState(false)
  const [agentSearch, setAgentSearch] = useState('')
  const [agentPickerStyle, setAgentPickerStyle] = useState<CSSProperties>({})
  const [agentPickerListMaxHeight, setAgentPickerListMaxHeight] = useState(288)
  const [activeContextCategory, setActiveContextCategory] = useState<'expert' | 'skill' | 'connector'>('expert')
  const [skills, setSkills] = useState<{ name: string; description?: string; title?: string }[]>([])
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [connectors, setConnectors] = useState<UserConnector[]>([])
  const [connectorsLoading, setConnectorsLoading] = useState(false)
  const [modelChoices, setModelChoices] = useState<ModelChoice[]>([])
  const [selectedModel, setSelectedModel] = useState('')
  const [modelChangeHint, setModelChangeHint] = useState('')
  const hintAtCountRef = useRef(0)
  /** 记录每条助手消息生成时使用的模型：按「可见助手消息序号」存储（不依赖原始数组索引，避免工具调用插入隐藏消息导致错位） */
  const messageModelRef = useRef<Map<number, string>>(new Map())
  /** 模型持久化：localStorage 按 session 存 index→model 数组，刷新后可恢复 */
  const modelStoreKey = (sessionKey: string) => `comworker_msg_models_${sessionKey}`
  const loadModelStore = (sessionKey: string): (string | null)[] => {
    try {
      const raw = localStorage.getItem(modelStoreKey(sessionKey))
      return raw ? JSON.parse(raw) : []
    } catch { return [] }
  }
  const saveModelStore = (sessionKey: string, arr: (string | null)[]) => {
    try { localStorage.setItem(modelStoreKey(sessionKey), JSON.stringify(arr)) } catch { /* ignore */ }
  }
  /** 记录下一条（即将生成的）助手消息的模型：追加到数组末尾，内存+持久化 */
  const recordMessageModel = (sessionKey: string, model: string) => {
    const arr = loadModelStore(sessionKey)
    arr.push(model)
    saveModelStore(sessionKey, arr)
    messageModelRef.current.set(arr.length - 1, model)
    console.log('[DEBUG] recordMessageModel', { sessionKey, model, newArrLength: arr.length, newIndex: arr.length - 1 })
  }
  /** 按「可见助手消息序号」恢复模型：内存优先，否则从持久化数组取 */
  const getMessageModel = (sessionKey: string, assistantIdx: number): string | undefined => {
    if (messageModelRef.current.has(assistantIdx)) {
      console.log('[DEBUG] getMessageModel hit Map', { sessionKey, assistantIdx, model: messageModelRef.current.get(assistantIdx) })
      return messageModelRef.current.get(assistantIdx)
    }
    const arr = loadModelStore(sessionKey)
    const val = arr[assistantIdx] || undefined
    console.log('[DEBUG] getMessageModel from localStorage', { sessionKey, assistantIdx, val, arrLen: arr.length, arr: JSON.stringify(arr) })
    return val
  }
  /** 时间持久化：localStorage 按 session 存「可见消息序号 → ISO 时间」，刷新后可恢复。不估算，仅存真实时间。 */
  const timeStoreKey = (sessionKey: string) => `comworker_msg_times_${sessionKey}`
  const loadTimeStore = (sessionKey: string): string[] => {
    try { return JSON.parse(localStorage.getItem(timeStoreKey(sessionKey)) || '[]') } catch { return [] }
  }
  const saveTimeStore = (sessionKey: string, arr: string[]) => {
    try { localStorage.setItem(timeStoreKey(sessionKey), JSON.stringify(arr)) } catch { /* ignore */ }
  }
  const recordMessageTime = (sessionKey: string, time: string) => {
    const arr = loadTimeStore(sessionKey)
    arr.push(time)
    saveTimeStore(sessionKey, arr)
  }
  const getMessageTime = (sessionKey: string, visibleIdx: number): string | undefined => {
    const arr = loadTimeStore(sessionKey)
    return arr[visibleIdx] || undefined
  }
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaRef>(null)
  const slashMenuRef = useRef<HTMLDivElement>(null)
  const activeSessionKeyRef = useRef<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const agentPickerRef = useRef<HTMLDivElement>(null)
  const agentPickerButtonRef = useRef<HTMLButtonElement>(null)
  const sessionLoadSeqRef = useRef(0)
  /** 当前正在执行的 run 所用的模型：在 handleSend 时写入，finishStream completed 时读取并持久化。
   *  这样保证只有真正完成产出的助手消息才会记录模型，避免失败/中止导致索引偏移。 */
  const pendingModelRef = useRef('')
  const toast = useToast()

  useEffect(() => {
    listModels()
      .then(result => {
        const models = result.models || []
        setModelChoices(models)
        const stored = window.localStorage.getItem('comworker:selected-model') || ''
        const enabled = models.filter(m => !m.disabled)
        const configured = result.configuredModel || ''
        let next = ''
        if (stored && models.some(m => m.id === stored && !m.disabled)) next = stored
        else if (enabled.some(m => m.id === configured)) next = configured
        else next = enabled[0]?.id || ''
        setSelectedModel(next)
      })
      .catch(() => {
        setModelChoices([])
      })
  }, [])

  useEffect(() => {
    if (selectedModel) {
      window.localStorage.setItem('comworker:selected-model', selectedModel)
    }
  }, [selectedModel])

  const resolveKnownSessionKey = useCallback((rawKey: string): string => {
    const normalized = normalizeSessionKey(rawKey)
    const candidates = [
      activeSessionKeyRef.current,
      ...Object.keys(sendingBySessionRef.current),
      ...Object.keys(targetTextBySessionRef.current),
    ].filter(Boolean) as string[]
    return candidates.find(key => normalizeSessionKey(key) === normalized) || rawKey
  }, [])

  // Files
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([])

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, activeSessionKey, displayedTextBySession, activityBySession, scrollToBottom])

  useEffect(() => {
    const createdAgent = searchParams.get('createdAgent')
    if (!createdAgent) return
    toast.success('已创建 ' + createdAgent + '，可以开始对话了', 6000)
  }, [searchParams, toast])

  const updateAgentPickerPosition = useCallback(() => {
    const button = agentPickerButtonRef.current
    if (!button) return

    const rect = button.getBoundingClientRect()
    const viewportWidth = window.innerWidth
    const viewportHeight = window.innerHeight
    const margin = 12
    const gap = 8
    const panelWidth = Math.min(420, viewportWidth - margin * 2)
    const left = Math.min(
      Math.max(rect.left, margin),
      viewportWidth - panelWidth - margin,
    )

    const spaceAbove = rect.top - margin
    const spaceBelow = viewportHeight - rect.bottom - margin
    const openBelow = spaceBelow >= 280 || spaceBelow > spaceAbove
    const availableSpace = Math.max(
      180,
      (openBelow ? spaceBelow : spaceAbove) - gap,
    )
    const panelMaxHeight = Math.min(420, availableSpace)
    const top = openBelow
      ? Math.min(rect.bottom + gap, viewportHeight - panelMaxHeight - margin)
      : Math.max(margin, rect.top - panelMaxHeight - gap)
    const reservedHeight = 124

    setAgentPickerStyle({
      position: 'fixed',
      top,
      left,
      width: panelWidth,
      maxHeight: panelMaxHeight,
    })
    setAgentPickerListMaxHeight(Math.max(108, panelMaxHeight - reservedHeight))
  }, [agentSearch, draftAgentId])

  useEffect(() => {
    if (!agentPickerOpen) return
    if (activeContextCategory === 'skill' && skills.length === 0 && !skillsLoading) {
      setSkillsLoading(true)
      listSkills()
        .then((list) => setSkills(list.map((s) => ({ name: s.name, description: s.description, title: s.title }))))
        .catch(() => setSkills([]))
        .finally(() => setSkillsLoading(false))
    }
    if (activeContextCategory === 'connector' && connectors.length === 0 && !connectorsLoading) {
      setConnectorsLoading(true)
      listUserConnectors()
        .then((list) => setConnectors(list))
        .catch(() => setConnectors([]))
        .finally(() => setConnectorsLoading(false))
    }
  }, [agentPickerOpen, activeContextCategory, skills.length, skillsLoading, connectors.length, connectorsLoading])

  useEffect(() => {
    if (!agentPickerOpen) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node | null
      if (target && agentPickerRef.current?.contains(target)) return
      setAgentPickerOpen(false)
    }
    const updatePosition = () => updateAgentPickerPosition()
    requestAnimationFrame(updatePosition)
    document.addEventListener('mousedown', closeOnOutsideClick)
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [agentPickerOpen, updateAgentPickerPosition])

  // Restore session from URL param
  useEffect(() => {
    const sessionKey = searchParams.get('session')
    if (sessionKey && sessionKey !== activeSessionKey) {
      loadSession(sessionKey)
      return
    }
    if (!sessionKey && searchParams.get('new') !== '1') {
      sessionLoadSeqRef.current += 1
      setActiveSessionKey(null)
      activeSessionKeyRef.current = null
      setMessages([])
      setPendingFiles([])
      setChatLoading(false)
      setIsDraftSession(false)
    }
  }, [searchParams])

  useEffect(() => {
    if (searchParams.get('new') !== '1') return
    const agentId = searchParams.get('agent') || ''
    sessionLoadSeqRef.current += 1
    setActiveSessionKey(null)
    activeSessionKeyRef.current = null
    setMessages([])
    setPendingFiles([])
    setChatLoading(false)
    setIsDraftSession(true)
    setDraftAgentId(agentId)
    setBoundContext(
      agentId && agentId !== 'main'
        ? {
            category: 'expert',
            id: agentId,
            name: agents.find((a) => a.id === agentId)?.identity?.name || agents.find((a) => a.id === agentId)?.name || agentId,
          }
        : null,
    )
    setAgentPickerOpen(false)
    setTimeout(() => inputRef.current?.focus(), 100)
  }, [searchParams, agents])

  useEffect(() => {
    let cancelled = false
    const agentId = activeSessionKey
      ? getAgentIdFromKey(activeSessionKey)
      : draftAgentId || searchParams.get('agent') || 'main'

    setSlashCommandsLoading(true)
    setSlashCommandsError('')

    listSlashCommands(agentId)
      .then(result => {
        if (!cancelled) {
          setSlashCommands(buildSlashCommandItems(result.commands || []))
          setSlashCommandsError('')
        }
      })
      .catch((err: any) => {
        if (!cancelled) {
          setSlashCommands([])
          setSlashCommandsError(err?.message || '加载命令失败')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setSlashCommandsLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [activeSessionKey, draftAgentId, searchParams])

  const loadSession = async (key: string, options: { force?: boolean } = {}) => {
    const loadSeq = sessionLoadSeqRef.current + 1
    sessionLoadSeqRef.current = loadSeq
    setActiveSessionKey(key)
    activeSessionKeyRef.current = key
    setIsDraftSession(false)
    setDraftAgentId(getAgentIdFromKey(key))
    setBoundContext(null)
    setAgentPickerOpen(false)
    setChatLoading(true)
    setSearchParams({ session: key })
    const cachedMessages = sessionMessagesCacheRef.current[key]
    if (!options.force && cachedMessages) {
      setMessages(cachedMessages)
      setChatLoading(false)
      return
    }
    try {
      let detail: SessionDetail | null = null
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          detail = await getSession(key)
          break
        } catch (err: any) {
          if (sessionLoadSeqRef.current !== loadSeq || activeSessionKeyRef.current !== key) return
          if (attempt === 2) throw err
          await new Promise(resolve => setTimeout(resolve, 500))
        }
      }
      if (sessionLoadSeqRef.current !== loadSeq || activeSessionKeyRef.current !== key) return
      applyLoadedMessages(key, (detail?.messages || []) as SessionDetail['messages'])
      // 挂载时恢复进行中 run（切页/刷新后流式续接）
      void resumeInFlightRun(key)
    } catch (err: any) {
      if (sessionLoadSeqRef.current !== loadSeq || activeSessionKeyRef.current !== key) return
      toast.error(err?.message || '加载对话失败')
      setMessages([])
    } finally {
      if (sessionLoadSeqRef.current === loadSeq && activeSessionKeyRef.current === key) {
        setChatLoading(false)
      }
    }
  }

  const createDraftSession = (agentId = '') => {
    sessionLoadSeqRef.current += 1
    setActiveSessionKey(null)
    activeSessionKeyRef.current = null
    setMessages([])
    setPendingFiles([])
    setIsDraftSession(true)
    setDraftAgentId(agentId)
    setSearchParams(agentId ? { new: '1', agent: agentId } : { new: '1' })
    setTimeout(() => inputRef.current?.focus(), 100)
  }

  // File handling
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files) return
    addFiles(Array.from(files))
    e.target.value = ''
  }

  const addFiles = (files: File[]) => {
    const newPending: PendingFile[] = files.map(file => {
      const isImg = isImageFile(file)
      const pf: PendingFile = {
        id: String(Date.now()) + '-' + Math.random().toString(36).slice(2),
        file,
        name: file.name,
        isImage: isImg,
      }
      if (isImg) {
        pf.previewUrl = URL.createObjectURL(file)
      }
      return pf
    })
    setPendingFiles(prev => [...prev, ...newPending])
  }

  const removePendingFile = (id: string) => {
    setPendingFiles(prev => {
      const removed = prev.find(f => f.id === id)
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl)
      return prev.filter(f => f.id !== id)
    })
  }

  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items
    if (!items) return
    const imageFiles: File[] = []
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/')) {
        const file = items[i].getAsFile()
        if (file) imageFiles.push(file)
      }
    }
    if (imageFiles.length > 0) {
      addFiles(imageFiles)
    }
  }

  // SSE connection for real-time chat events (replaces WebSocket)
  const sseRef = useRef<EventSource | null>(null)

  const handleChatEvent = useCallback((payload: any) => {
    const { state, sessionKey: rawSessionKey } = payload
    if (!rawSessionKey) {
      return
    }

    const eventSessionKey = resolveKnownSessionKey(String(rawSessionKey))

    // Streaming delta: chat SSE only handles session lifecycle (started/final/error).
    // Text and reasoning_content deltas come through Run SSE (streamRunActivity),
    // which receives the raw message.delta events with the `delta` field.
    // The backend's _map_event_to_compat_block does translate message.delta into
    // a chat delta event, but in practice the Run SSE is the authoritative source
    // for text accumulation — handling text here too would double-append.
    if (state === 'delta' && payload.message) {
      return
    }

    // Started: clear streaming text for new turn
    if (state === 'started') {
      setStreamingText(eventSessionKey, '')
      reasoningTextBySessionRef.current[eventSessionKey] = []
      delete textHiddenBySessionRef.current[eventSessionKey]
      return
    }

    // Final / error / aborted: load final messages, then clear streaming
    if (state === 'final' || state === 'error' || state === 'aborted') {
      // Keep streaming text visible until messages load.

      if (sseFinalTimersRef.current[eventSessionKey]) {
        clearTimeout(sseFinalTimersRef.current[eventSessionKey])
      }
      sseFinalTimersRef.current[eventSessionKey] = setTimeout(async () => {
        // No new final events for 3s means the agent is done.
        for (let attempt = 0; attempt < 8; attempt += 1) {
          try {
            const detail = await getSession(eventSessionKey)
            const loadedMessages = detail.messages || []
            applyLoadedMessages(eventSessionKey, loadedMessages)
            if (hasAssistantAfterLastUser(loadedMessages) || state === 'error' || state === 'aborted') {
              clearStreamingText(eventSessionKey)
              setSendingForSession(eventSessionKey, false)
              sseCompletedRef.current[eventSessionKey] = true
              // Clear activity events after messages are loaded
              clearActivityForSession(eventSessionKey)
              refreshSessions({ silent: true, force: true })
              return
            }
          } catch {
            // keep retrying briefly; history may lag behind the lifecycle event
          }
          await new Promise(resolve => setTimeout(resolve, 1000))
        }

        clearStreamingText(eventSessionKey)
        setSendingForSession(eventSessionKey, false)
        sseCompletedRef.current[eventSessionKey] = true
        toast.error('回复还没有写入完成，请稍后刷新')
      }, 3000)
    }
  }, [applyLoadedMessages, clearStreamingText, refreshSessions, resolveKnownSessionKey, setSendingForSession, setStreamingText, toast])

  // Connect SSE on mount
  useEffect(() => {
    const token = getAccessToken()
    if (!token) {
      return
    }
    // Always use relative URL so SSE goes through Vite proxy, avoiding CORS issues
    const url = '/api/comworker/events/stream?token=' + encodeURIComponent(token)
    const sse = new EventSource(url)
    sseRef.current = sse

    sse.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg.event === 'chat' && msg.payload) {
          handleChatEvent(msg.payload)
        }
      } catch {
        // ignore
      }
    }

    return () => {
      Object.values(sseFinalTimersRef.current).forEach(timer => clearTimeout(timer))
      sseFinalTimersRef.current = {}
      Object.values(typewriterTimersRef.current).forEach(timer => clearInterval(timer))
      typewriterTimersRef.current = {}
      Object.keys(runEventSourcesRef.current).forEach(key => closeRunEventStream(key))
      runEventSourcesRef.current = {}
      runStreamDoneRef.current = {}
      sse.close()
      sseRef.current = null
    }
  }, [handleChatEvent])

  const streamRunActivity = useCallback((key: string, runId: string): RunActivityStream => {
    closeRunEventStream(key)
    delete runStreamDoneRef.current[key]
    delete sseReceivedRef.current[key]
    delete textHiddenBySessionRef.current[key]
    runActivityStartedAtRef.current[key] = Date.now()

    const sse = new EventSource(getRunEventsStreamUrl(runId))
    runEventSourcesRef.current[key] = sse
    let sawEvent = false
    let settled = false
    let lastEventType = ''
    let resolveDone: (result: RunStreamResult) => void = () => {}
    const done = new Promise<RunStreamResult>(resolve => {
      resolveDone = resolve
    })
    // 记录解析器并在（重新）开始时清除"已结算"标记，供卸载时安全唤醒挂起 Promise。
    runStreamSettledRef.current[key] = false
    runStreamDoneResolverRef.current[key] = resolveDone
    const ready = new Promise<boolean>(resolve => {
      const readyTimer = window.setTimeout(() => resolve(sawEvent), 3500)
      sse.onopen = () => {
        window.clearTimeout(readyTimer)
        resolve(true)
      }
    })

    const finishStream = (result: RunStreamResult) => {
      if (settled) return
      settled = true
      runStreamSettledRef.current[key] = true
      delete runStreamDoneResolverRef.current[key]
      runStreamDoneRef.current[key] = result
      sseCompletedRef.current[key] = true
      // 真实结束事件到达：清除"进行中 run"持久化，避免刷新后误 re-attach 已结束的 run。
      clearInFlightRunIfMatches(runId)

      // 助手消息成功完成：记录真实完成时间 + 产生此消息时所用的模型（从 pendingModelRef 取）
      if (result === 'completed') {
        recordMessageTime(key, new Date().toISOString())
        if (pendingModelRef.current) {
          console.log('[DEBUG] finishStream completed, recording model', { key, model: pendingModelRef.current })
          recordMessageModel(key, pendingModelRef.current)
          pendingModelRef.current = '' // 用完清空，防止重复记录
        }
      }
      closeRunEventStream(key)
      resolveDone(result)
    }

    sse.onmessage = (evt) => {
      if (abortedSessionRef.current[key]) return
      try {
        const payload = JSON.parse(evt.data)
        const eventType = String(payload.type || payload.event || '')
        const eventRunId = typeof payload.run_id === 'string' ? payload.run_id : runId
        if (eventRunId && eventRunId !== runId) return
        sawEvent = true
        // Mark SSE as active so waitForResponse fallback loop knows to exit
        // — SSE is the authoritative source, fallback /wait polling would
        // call applyLoadedMessages and clear streaming text mid-flight.
        sseReceivedRef.current[key] = true

        if (eventType === 'message.delta') {
          // When text arrives after tools, it belongs to a new assistant message.
          // Clear accumulated text so streaming state matches final state (which
          // only shows the last assistant message's content).
          if (lastEventType === 'tool.completed' || lastEventType === 'tool.started') {
            // Clear only text, NOT reasoning — setStreamingText('') would call
            // clearStreamingText and wipe reasoning too, causing the "思考" step
            // to disappear mid-stream when text arrives after tool calls.
            targetTextBySessionRef.current[key] = ''
            delete textFirstTsBySessionRef.current[key]
            setDisplayedTextBySession(prev => {
              if (!prev[key]) return prev
              const next = { ...prev }
              delete next[key]
              return next
            })
          }
          if (payload.reasoning_content) {
            const thinkingDelta = typeof payload.reasoning_content === 'string'
              ? payload.reasoning_content
              : ''
            if (thinkingDelta) {
              const segments = reasoningTextBySessionRef.current[key] || []
              const last = segments[segments.length - 1]
              let nextSegments: ReasoningSegment[]
              if (last && !last.finalized) {
                nextSegments = segments.slice(0, -1).concat({ ...last, content: last.content + thinkingDelta })
              } else {
                nextSegments = segments.concat({ content: thinkingDelta, ts: Date.now(), finalized: false })
              }
              reasoningTextBySessionRef.current[key] = nextSegments
              setDisplayedReasoningBySession(prev => ({ ...prev, [key]: nextSegments }))
            }
          }

          const delta = typeof payload.delta === 'string' ? payload.delta : ''
          if (delta) {
            // Once a tool has run in this turn, the model's content deltas are
            // draft/working notes (the model emits them as `content` instead of
            // `reasoning_content`). They're incoherent planning text, not the
            // final answer — hide them. The final answer loads from the API
            // after run.completed. Reasoning is NOT affected (still shows in
            // the ProcessCard thinking block).
            if (textHiddenBySessionRef.current[key]) {
              lastEventType = 'message.delta'
              return
            }
            if (!textFirstTsBySessionRef.current[key]) {
              textFirstTsBySessionRef.current[key] = Date.now()
            }
            const current = targetTextBySessionRef.current[key] || ''
            setStreamingText(key, current + delta)
          }
          lastEventType = 'message.delta'
          return
        }

        if (eventType === 'approval.responded') {
          const choice = typeof payload.choice === 'string' ? payload.choice : ''
          setActivityBySession(prev => {
            const current = prev[key] || []
            const next = {
              ...prev,
              [key]: current.map(activity => activity.type === 'approval.request'
                ? {
                    ...activity,
                    title: choice ? '已授权：' + approvalChoiceLabel(choice) : '授权已处理',
                    status: 'completed' as AgentActivityStatus,
                    selectedChoice: choice,
                    responding: false,
                  }
                : activity),
            }
            activityBySessionRef.current = next
            return next
          })
          return
        }

        if (eventType === 'reasoning.available') {
          // reasoning.available carries a complete reasoning snippet in `text`
          // for one LLM call. Finalize the current segment (replace partial
          // delta-accumulated content with the complete text) so the next
          // `message.delta.reasoning_content` starts a new segment. This
          // matches `buildProcessStepsForTurn`, which emits one thinking step
          // per assistant message (one per LLM call).
          const text = typeof payload.text === 'string' ? payload.text : ''
          if (text) {
            const segments = reasoningTextBySessionRef.current[key] || []
            const last = segments[segments.length - 1]
            let nextSegments: ReasoningSegment[]
            if (last && !last.finalized) {
              nextSegments = segments.slice(0, -1).concat({ content: text, ts: last.ts, finalized: true })
            } else {
              nextSegments = segments.concat({ content: text, ts: Date.now(), finalized: true })
            }
            reasoningTextBySessionRef.current[key] = nextSegments
            setDisplayedReasoningBySession(prev => ({ ...prev, [key]: nextSegments }))
          }
          return
        }

        if (eventType === 'tool.started' || eventType === 'tool.completed' || eventType === 'approval.request' || eventType === 'run.failed') {
          // First tool event in this run: hide streaming text from here on.
          // The model's content deltas before the final answer are incoherent
          // draft/working notes — clear what's shown and stop accumulating.
          // Reasoning is NOT cleared (still shows in ProcessCard thinking block).
          if (eventType === 'tool.started' || eventType === 'tool.completed') {
            if (!textHiddenBySessionRef.current[key]) {
              textHiddenBySessionRef.current[key] = true
              targetTextBySessionRef.current[key] = ''
              delete textFirstTsBySessionRef.current[key]
              setDisplayedTextBySession(prev => {
                if (!prev[key]) return prev
                const next = { ...prev }
                delete next[key]
                return next
              })
            }
            // Finalize the current reasoning segment so the next
            // `message.delta.reasoning_content` (from the next LLM call) starts
            // a new segment. Without this, reasoning from call 2 would append
            // to call 1's segment, making streaming show fewer thinking steps
            // than the final state.
            const segments = reasoningTextBySessionRef.current[key] || []
            const last = segments[segments.length - 1]
            if (last && !last.finalized) {
              const nextSegments = segments.slice(0, -1).concat({ ...last, finalized: true })
              reasoningTextBySessionRef.current[key] = nextSegments
              setDisplayedReasoningBySession(prev => ({ ...prev, [key]: nextSegments }))
            }
          }
          const status: AgentActivityStatus =
            eventType === 'tool.completed'
              ? payload.error ? 'failed' : 'completed'
              : eventType === 'approval.request'
                ? 'approval'
                : eventType === 'run.failed'
                  ? 'failed'
                  : 'running'
          // For tool.started, dedupe by tool+preview so multiple calls of the
          // same tool (e.g. several terminal commands) each get their own entry.
          // For tool.completed (which carries no preview), find the oldest
          // running entry of the same tool and mark it complete (FIFO match).
          if (eventType === 'tool.completed') {
            const toolName = String(payload.tool || '')
            // Use client time consistently (see reasoning.available comment above).
            const ts = Date.now()
            setActivityBySession(prev => {
              const current = prev[key] || []
              // Find oldest running entry of same tool
              let targetIdx = -1
              // Compare stripped titles: both sides strip the "正在运行 " prefix
              // (or any upstream title passes through unchanged). Comparing
              // buildActivityTitle(...) (unstripped) to current[i].title stripped
              // was always false — '正在运行 X' !== 'X' — so every completed
              // fell through to the standalone path, doubling tool entries and
              // losing the command preview.
              const expectedTitle = buildActivityTitle('tool.started', { tool: toolName }).replace(/^正在运行\s*/, '')
              for (let i = 0; i < current.length; i++) {
                if (current[i].type === 'tool.started' && current[i].status === 'running'
                    && expectedTitle === current[i].title.replace(/^正在运行\s*/, '')) {
                  targetIdx = i
                  break
                }
              }
              if (targetIdx === -1) {
                // No running match — fall through to create a standalone completed entry
                const standalone = {
                  id: 'tool.completed:' + toolName + ':' + ts,
                  runId,
                  type: eventType,
                  title: buildActivityTitle(eventType, payload),
                  detail: buildActivityDetail(eventType, payload),
                  status,
                  timestamp: ts,
                }
                const nextEvents = [...current, standalone].sort((a, b) => a.timestamp - b.timestamp)
                activityBySessionRef.current = { ...prev, [key]: nextEvents }
                return { ...prev, [key]: nextEvents }
              }
              const nextEvents = current.map((item, i) => i === targetIdx
                ? { ...item, type: 'tool.completed' as never, status: status as never, title: buildActivityTitle('tool.completed', payload), detail: buildActivityDetail('tool.completed', payload), timestamp: ts }
                : item
              )
              activityBySessionRef.current = { ...prev, [key]: nextEvents }
              return { ...prev, [key]: nextEvents }
            })
            if (eventType === 'run.failed') {
              finishStream('failed')
            }
            lastEventType = eventType
            return
          }
          const eventKey = payload.tool_call_id
            || (payload.tool && payload.preview ? payload.tool + ':' + payload.preview : payload.tool || payload.preview)
            || eventType
          addActivityEvent(key, {
            id: String(eventType) + ':' + String(eventKey),
            runId,
            type: eventType,
            title: buildActivityTitle(eventType, payload),
            detail: buildActivityDetail(eventType, payload),
            status,
            timestamp: Date.now(),
            choices: eventType === 'approval.request' ? normalizeApprovalChoices(payload) : undefined,
          })
          if (eventType === 'run.failed') {
            finishStream('failed')
          }
          lastEventType = eventType
          return
        }

        if (eventType === 'run.completed' || eventType === 'run.cancelled') {
          finishStream(eventType === 'run.completed' ? 'completed' : 'cancelled')
          lastEventType = eventType
        }
      } catch {
        // ignore malformed event chunks
      }
    }

    sse.onerror = () => {
      finishStream(sawEvent ? 'error' : 'error')
    }

    return { ready, done }
  }, [addActivityEvent, closeRunEventStream, setStreamingText])

  const loadFinalMessages = useCallback(async (key: string, failed = false) => {
    const maxLoadAttempts = failed ? 3 : 8
    for (let attempt = 0; attempt < maxLoadAttempts; attempt += 1) {
      const detail = await getSession(key)
      const loadedMessages = detail.messages || []
      applyLoadedMessages(key, loadedMessages)
      if (hasAssistantAfterLastUser(loadedMessages) || failed || attempt === maxLoadAttempts - 1) {
        clearStreamingText(key)
        sseCompletedRef.current[key] = true
        return
      }
      await new Promise(r => setTimeout(r, 1000))
    }
  }, [applyLoadedMessages, clearStreamingText])

  // Pull a human-readable error out of a finished run result. The backend
  // now puts the real failure reason (e.g. "No provider configured for
  // model 'X'" or an upstream auth error) into `message` on a failed run.
  const extractRunError = (result: AgentRunWaitResult | null | undefined): string => {
    const m = result?.message
    let text = ''
    if (typeof m === 'string') text = m
    else if (m && typeof m === 'object' && typeof (m as { content?: unknown }).content === 'string') {
      text = (m as { content: string }).content
    }
    const t = (text || '').toString().trim()
    return t || 'Agent 执行出错，请稍后重试'
  }

  // The SSE `run.failed` event already delivers the underlying error as an
  // activity detail. Prefer it: the /wait fallback can fail to retrieve events
  // (e.g. hermes returns 401 on its events endpoint), so we must not depend on
  // it to surface the real reason a run failed.
  const findRunFailedError = (key: string): string => {
    const events = activityBySessionRef.current[key] || []
    const failed = events.find(e => e.type === 'run.failed')
    const detail = failed?.detail
    return typeof detail === 'string' && detail.trim() ? detail.trim() : ''
  }

  // Surface the real failure reason instead of a generic toast.
  const showRunFailure = async (key: string, runId: string | null) => {
    let message = findRunFailedError(key)
    if (!message && runId) {
      try {
        const r = await waitForAgentRun(runId, 3000)
        message = extractRunError(r)
      } catch {
        message = ''
      }
    }
    toast.error(message || 'Agent 执行出错，请稍后重试')
  }

  const waitForResponse = async (key: string, runId: string | null, stream?: RunActivityStream | null) => {
    // SSE handles incremental display. Completion should come from runId-based
    // waiting so we don't mistake a partial assistant message for a finished turn.
    sseCompletedRef.current[key] = false
    if (runId && stream) {
      const streamReady = await stream.ready
      if (streamReady) {
        const result = await stream.done
        if (abortedSessionRef.current[key]) return
        await loadFinalMessages(key, result === 'failed' || result === 'error')
        if (result === 'failed' || result === 'error') {
          void showRunFailure(key, runId)
        }
        return
      }
    }
    const maxWaitMs = 900000 // Allow longer tool-heavy agent runs.
    const perRequestTimeoutMs = 25000
    const startTime = Date.now()

    while (Date.now() - startTime < maxWaitMs) {
      if (abortedSessionRef.current[key]) return
      if (sseCompletedRef.current[key]) return
      // If SSE started receiving events, it's the authoritative source —
      // avoid applyLoadedMessages clearing streaming text mid-flight (causes
      // "messy text" / text disappearing/reappearing). But we still need to
      // wait for SSE to finish and load final messages.
      if (sseReceivedRef.current[key]) {
        if (stream) {
          const result = await stream.done
          await loadFinalMessages(key, result === 'failed' || result === 'error')
          if (result === 'failed' || result === 'error') {
            void showRunFailure(key, runId)
          }
        }
        return
      }
      if (runId) {
        try {
          const remainingMs = maxWaitMs - (Date.now() - startTime)
          const waitResult = await waitForAgentRun(runId, Math.min(perRequestTimeoutMs, remainingMs))

          if (sseCompletedRef.current[key]) return

          if (waitResult.status === 'timeout') {
            continue
          }

          const finished = isRunFinished(waitResult.status)
          if (finished) {
            await loadFinalMessages(key, isRunFailed(waitResult.status))
            if (isRunFailed(waitResult.status)) {
              toast.error(extractRunError(waitResult))
            }
            return
          }
          const maxLoadAttempts = 1
          for (let attempt = 0; attempt < maxLoadAttempts; attempt += 1) {
            const detail = await getSession(key)
            const loadedMessages = detail.messages || []
            applyLoadedMessages(key, loadedMessages)
            if (hasAssistantAfterLastUser(loadedMessages) || attempt === maxLoadAttempts - 1) {
              if (hasAssistantAfterLastUser(loadedMessages) || finished) {
                clearStreamingText(key)
                sseCompletedRef.current[key] = true
                if (isRunFailed(waitResult.status)) {
                  toast.error(extractRunError(waitResult))
                }
                return
              }
            }
            await new Promise(r => setTimeout(r, 1000))
          }
          await new Promise(r => setTimeout(r, 1200))
          continue
        } catch {
          await new Promise(r => setTimeout(r, 1500))
          continue
        }
      }

      // Legacy fallback if backend doesn't return a runId.
      await new Promise(r => setTimeout(r, 3000))
      try {
        const detail = await getSession(key)
        const msgs = detail.messages || []
        const lastMsg = msgs[msgs.length - 1]
        if (lastMsg?.role === 'assistant' && hasAssistantAfterLastUser(msgs) && !targetTextBySessionRef.current[key]) {
          applyLoadedMessages(key, msgs)
          clearStreamingText(key)
          sseCompletedRef.current[key] = true
          return
        }
      } catch {
        // ignore and keep waiting
      }
    }

    // Timeout: load final state
    try {
      await loadFinalMessages(key)
    } catch {}
    clearStreamingText(key)
    sseCompletedRef.current[key] = true
  }

  // 切页/刷新后恢复"进行中 run"：直接轮询 run 状态并即时加载消息，
  // 不依赖 run-events SSE 的事件回放（后端对历史事件不回放，且已完成 run 重连会 404）。
  // 这样页面不会空白、结果会在 run 结束后秒级出现、发送/思考态也会正确清除。
  const resumeInFlightRun = useCallback(async (key: string) => {
    if (sendingBySessionRef.current[key] || runIdBySessionRef.current[key]) return
    const run = loadInFlightRun()
    if (!run || run.sessionKey !== key) return

    // 1) 立即渲染服务端已有消息，避免页面空白
    try {
      const detail = await getSession(key)
      if (detail?.messages?.length) applyLoadedMessages(key, detail.messages)
    } catch {
      // ignore
    }

    // 2) 轮询 run 状态直到结束（2s 粒度，远快于旧方案 25s 慢轮询）
    setSendingForSession(key, true)
    setRunIdForSession(key, run.runId)
    let finished = false
    let failed = false
    const pollStart = Date.now()
    while (Date.now() - pollStart < 900000) {
      if (abortedSessionRef.current[key]) {
        clearInFlightRunIfMatches(run.runId)
        setSendingForSession(key, false)
        return
      }
      let st = ''
      try {
        const r = await waitForAgentRun(run.runId, 3000)
        st = (r && r.status) || ''
      } catch {
        st = ''
      }
      if (isRunFinished(st)) {
        finished = true
        failed = isRunFailed(st)
        break
      }
      // 进行中：周期性刷新消息，展示部分进度
      try {
        const detail = await getSession(key)
        if (detail?.messages?.length) applyLoadedMessages(key, detail.messages)
      } catch {
        // ignore
      }
      await new Promise((resolve) => setTimeout(resolve, 2000))
    }

    clearInFlightRunIfMatches(run.runId)
    try {
      await loadFinalMessages(key, failed)
    } catch {
      // ignore
    }
    setSendingForSession(key, false)
    if (failed) void showRunFailure(key, run.runId)
  }, [applyLoadedMessages, getSession, isRunFinished, isRunFailed, loadFinalMessages, showRunFailure, waitForAgentRun, setSendingForSession, setRunIdForSession])

  const handleSelectContext = useCallback(
    (item: BoundContext) => {
      if (item.category === 'expert') {
        // 专家：切换到该 agent 的对话（进入草稿态），并标记绑定。
        setActiveSessionKey(null)
        setIsDraftSession(true)
        setDraftAgentId(item.id)
        setBoundContext(item)
        return
      }
      // 技能 / 连接器：在当前会话中绑定。连接器以管理端/连接器页开关为准，
      // 前端选择器不再自动 enable；未启用的条目在选择器中置灰不可选。
      setBoundContext(item)
    },
    [toast],
  )

  const handleClearContext = useCallback(() => {
    if (boundContext?.category === 'expert') {
      // 清除专家绑定：切回 main agent 草稿态。
      setActiveSessionKey(null)
      setIsDraftSession(true)
      setDraftAgentId('')
    }
    setBoundContext(null)
  }, [boundContext])

  const handleSend = async () => {
    const text = input.trim()
    if ((!text && pendingFiles.length === 0) || (!activeSessionKeyRef.current && !isDraftSession) || chatLoading) return
    if (!selectedModel) {
      toast.error('暂无可用模型，请联系管理员配置模型 Key')
      return
    }

    const requestedAgentId = draftAgentId || searchParams.get('agent') || 'main'
    const sendingSessionKey = activeSessionKeyRef.current || 'agent:' + (requestedAgentId || 'main') + ':session-' + Date.now()
    if (sendingBySession[sendingSessionKey]) return
    abortedSessionRef.current[sendingSessionKey] = false
    const isFirstTurn = !activeSessionKeyRef.current
    let firstTurnTitle = ''
    if (isFirstTurn) {
      const now = new Date().toISOString()
      // 标题用"第一个问题"（取首条用户消息、清理空白、截断）；过长由显示层再截断。
      firstTurnTitle = text
        ? buildTitleFromMessages([{ role: 'user', content: text }])
        : (pendingFiles.length > 0 ? buildFallbackTitleFromText(pendingFiles.length) : '新对话')
      const optimisticSession: Session = {
        key: sendingSessionKey,
        title: firstTurnTitle,
        created_at: now,
        updated_at: now,
      }
      addOptimisticSession(optimisticSession)
      setActiveSessionKey(sendingSessionKey)
      activeSessionKeyRef.current = sendingSessionKey
      setIsDraftSession(false)
      setAgentPickerOpen(false)
      setSearchParams({ session: sendingSessionKey })
    }
    setSendingForSession(sendingSessionKey, true)
    clearActivityForSession(sendingSessionKey)

    try {
      const agentId = getAgentIdFromKey(sendingSessionKey)
      const uploadDir = getUploadDir(agentId)

      // Upload all files to agent workspace
      const uploadedPaths: string[] = []
      for (const pf of pendingFiles) {
        const result = await uploadFileToWorkspace(pf.file, uploadDir)
        const uploadedPath = result.path || result.name || pf.name
        uploadedPaths.push(uploadedPath)
      }

      // Build final message with file references
      let finalMessage = text
      if (uploadedPaths.length > 0) {
        const fileRefs = uploadedPaths
          .map(p => '[Attachment: ~/.comworker/' + p + ']')
          .join('\n')
        finalMessage = finalMessage
          ? finalMessage + '\n\n' + fileRefs
          : fileRefs
      }

      // Optimistic UI
      const displayParts: string[] = []
      if (text) displayParts.push(text)
      if (uploadedPaths.length > 0) {
        uploadedPaths.forEach(p => {
          const name = p.split('/').pop() || p
          displayParts.push('File: ' + name)
        })
      }

      const userMsg = {
        role: 'user',
        content: displayParts.join('\n'),
        timestamp: new Date().toISOString(),
      }
      setMessages(prev => {
        const next = [...prev, userMsg]
        sessionMessagesCacheRef.current[sendingSessionKey] = next
        // 记录用户消息真实时间（持久化，刷新后可恢复）
        recordMessageTime(sendingSessionKey, userMsg.timestamp)
        // 保存当前模型到 ref，等 finishStream completed 时再持久化（避免失败 run 导致索引偏移）
        pendingModelRef.current = selectedModel
        console.log('[DEBUG] handleSend set pendingModelRef', { sendingSessionKey, selectedModel })
        return next
      })
      setInput('')
      pendingFiles.forEach(pf => {
        if (pf.previewUrl) URL.revokeObjectURL(pf.previewUrl)
      })
      setPendingFiles([])

      clearStreamingText(sendingSessionKey)
      // 标题用"第一个问题"（首轮已乐观设置并截断）。直接把标题传给后端，由 send_message
      // 在创建首条消息时持久化，避免额外一次 updateSessionTitle 竞态。
      // 会话级上下文绑定：技能/连接器作为 context 注入后端，专家由 agent 切换承载。
      const sendContext =
        boundContext && (boundContext.category === 'skill' || boundContext.category === 'connector')
          ? { type: boundContext.category, id: boundContext.id, name: boundContext.name }
          : undefined
      const sendResult = await sendChatMessage(
        sendingSessionKey,
        finalMessage,
        selectedModel,
        isFirstTurn ? firstTurnTitle : undefined,
        sendContext,
      )
      if (sendResult.title) {
        const now = new Date().toISOString()
        addOptimisticSession({
          key: sendingSessionKey,
          title: sendResult.title,
          created_at: now,
          updated_at: now,
        })
      }
      setRunIdForSession(sendingSessionKey, sendResult.runId)
      // 持久化进行中 run（模块单例 + sessionStorage），供切页/刷新后自动 re-attach 续流。
      if (sendResult.runId) {
        persistInFlightRun({ sessionKey: sendingSessionKey, runId: sendResult.runId, model: selectedModel })
      }
      let activityStream: RunActivityStream | null = null
      if (sendResult.runId) {
        activityStream = streamRunActivity(sendingSessionKey, sendResult.runId)
      }
      if (abortedSessionRef.current[sendingSessionKey]) {
        if (sendResult.runId) {
          await abortAgentRun(sendResult.runId, sendingSessionKey)
        } else {
          await abortActiveSessionRun(sendingSessionKey)
        }
        return
      }
      await waitForResponse(sendingSessionKey, sendResult.runId, activityStream)
      void refreshSessions({ silent: true, force: true })
    } catch (err: any) {
      if (!abortedSessionRef.current[sendingSessionKey]) {
        toast.error(err?.message || '发送失败')
      }
    } finally {
      setSendingForSession(sendingSessionKey, false)
      setRunIdForSession(sendingSessionKey, null)
      closeRunEventStream(sendingSessionKey)
    }
  }

  const handleAbortCurrentRun = async () => {
    const key = activeSessionKeyRef.current
    if (!key || !sendingBySessionRef.current[key]) return

    abortedSessionRef.current[key] = true
    sseCompletedRef.current[key] = true
    if (sseFinalTimersRef.current[key]) {
      clearTimeout(sseFinalTimersRef.current[key])
      delete sseFinalTimersRef.current[key]
    }
    clearStreamingText(key)
    clearActivityForSession(key)
    closeRunEventStream(key)
    setSendingForSession(key, false)
    setRunIdForSession(key, null)
    clearInFlightRunIfMatches(runIdBySessionRef.current[key])

    try {
      const runId = runIdBySessionRef.current[key]
      if (runId) {
        await abortAgentRun(runId, key)
      } else {
        await abortActiveSessionRun(key)
      }
      const detail = await getSession(key).catch(() => null)
      if (detail) {
        applyLoadedMessages(key, detail.messages || [])
      }
      void refreshSessions({ silent: true, force: true })
    } catch (err: any) {
      toast.error(err?.message || '终止失败')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (showSlashMenu && filteredSlashCommands.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSlashActiveIndex(prev => (prev + 1) % filteredSlashCommands.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSlashActiveIndex(prev => (prev - 1 + filteredSlashCommands.length) % filteredSlashCommands.length)
        return
      }
      if ((e.key === 'Enter' || e.key === 'Tab') && !e.nativeEvent.isComposing) {
        e.preventDefault()
        applySlashCommand(filteredSlashCommands[slashActiveIndex] || filteredSlashCommands[0])
        return
      }
    }
    if (showSlashMenu && e.key === 'Escape') {
      e.preventDefault()
      setSlashMenuDismissed(true)
      return
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleRefresh = () => {
    if (activeSessionKey) {
      loadSession(activeSessionKey, { force: true })
    }
  }

  const handleSelectDraftAgent = (agentId: string) => {
    setDraftAgentId(agentId)
    setAgentPickerOpen(false)
    setAgentSearch('')
    setSearchParams({ new: '1', agent: agentId })
  }

  const handleClearDraftAgent = () => {
    setDraftAgentId('')
    setAgentPickerOpen(false)
    setAgentSearch('')
    setSearchParams({ new: '1' })
  }

  const handleAgentCreated = async (agentId: string, displayName: string) => {
    setAgentCreateOpen(false)
    toast.success('已创建 ' + displayName + '，可以开始对话了', 6000)
    await refreshAgents({ force: true })
    handleSelectDraftAgent(agentId)
  }

  const applySlashCommand = (command: SlashCommandItem) => {
    setInput('/' + command.name + ' ')
    setSlashActiveIndex(0)
    setSlashMenuDismissed(false)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const formatTime = (iso: string | null) => {
    if (!iso) return ''
    const d = new Date(iso)
    const now = new Date()
    const isToday = d.toDateString() === now.toDateString()
    const time = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0')
    if (isToday) return time
    // 昨天
    const yesterday = new Date(now)
    yesterday.setDate(yesterday.getDate() - 1)
    if (d.toDateString() === yesterday.toDateString()) return '昨天 ' + time
    // 更早
    return String(d.getMonth() + 1) + '/' + String(d.getDate()) + ' ' + time
  }

  const hasContent = input.trim() || pendingFiles.length > 0
  const isCurrentSending = Boolean(activeSessionKey && sendingBySession[activeSessionKey])
  const slashQuery = getSlashQuery(input)
  const filteredSlashCommands = filterSlashCommands(slashCommands, slashQuery || '')
  const showSlashMenu = slashQuery !== null && !slashMenuDismissed && !chatLoading && !isCurrentSending
  const groupedSlashCommands = filteredSlashCommands.reduce<Record<string, SlashCommandItem[]>>((acc, command) => {
    const key = command.category
    if (!acc[key]) acc[key] = []
    acc[key].push(command)
    return acc
  }, {})

  useEffect(() => {
    setSlashActiveIndex(0)
    setSlashMenuDismissed(false)
  }, [slashQuery])

  useEffect(() => {
    if (!showSlashMenu || filteredSlashCommands.length === 0) return
    if (slashActiveIndex >= filteredSlashCommands.length) {
      setSlashActiveIndex(0)
    }
  }, [showSlashMenu, filteredSlashCommands, slashActiveIndex])

  useEffect(() => {
    if (!showSlashMenu) return
    const activeButton = slashMenuRef.current?.querySelector<HTMLButtonElement>('[data-active="true"]')
    activeButton?.scrollIntoView({ block: 'nearest' })
  }, [showSlashMenu, slashActiveIndex])

  const displayedText = activeSessionKey ? displayedTextBySession[activeSessionKey] || '' : ''
  const displayedReasoning = activeSessionKey ? displayedReasoningBySession[activeSessionKey] || [] : []
  // Defer streaming text so rapid SSE deltas (one per token) don't block the
  // main thread on every keystroke-sized update. React batches deferred updates
  // and renders them at lower priority, keeping input responsive.
  const deferredDisplayedText = useDeferredValue(displayedText)
  const deferredDisplayedReasoning = useDeferredValue(displayedReasoning)
  // Ref mirror so streamingProcessSteps can read the latest text for reasoning
  // dedup without re-running on every token. Previously deferredDisplayedText
  // was a useMemo dep, causing ProcessCard to re-render on every token.
  const deferredDisplayedTextRef = useRef(deferredDisplayedText)
  deferredDisplayedTextRef.current = deferredDisplayedText
  const currentActivity = activeSessionKey ? activityBySession[activeSessionKey] || [] : []
  const streamingProcessSteps: ProcessStep[] = useMemo(() => {
    // Build a unified timeline of (timestamp, step) pairs and sort by time so
    // text/reasoning/tool calls appear in the order they actually occurred.
    // Without this, text "好的" that arrived before tool calls would render
    // after them (or vice versa) depending on the static render order.
    const events: { ts: number; step: ProcessStep }[] = []

    const textContent = deferredDisplayedTextRef.current.trim()
    // Render one thinking step per reasoning segment (one per LLM call) to
    // match `buildProcessStepsForTurn`, which emits one thinking step per
    // assistant message. Hide a segment if it closely matches the streamed
    // text — the model often restates its plan as both reasoning and output,
    // and showing both looks like a duplicate.
    for (const seg of deferredDisplayedReasoning) {
      const reasoningText = seg.content.trim()
      const showReasoning = reasoningText && !(
        textContent && isSimilarText(reasoningText, textContent)
      )
      if (showReasoning) {
        events.push({
          ts: seg.ts,
          step: { type: 'thinking', content: seg.content },
        })
      }
    }

    for (const ev of currentActivity) {
      if (ev.type === 'tool.started' || ev.type === 'tool.completed') {
        const isRunning = ev.type === 'tool.started' && ev.status === 'running'
        const isFailed = ev.status === 'failed'
        events.push({
          ts: ev.timestamp,
          step: {
            type: 'tool',
            toolName: ev.title.replace(/^正在运行\s*/, '').replace(/^(工具已完成|工具执行失败):\s*/, ''),
            toolArgs: ev.detail,
            status: isFailed ? 'failed' : isRunning ? 'running' : 'completed',
          },
        })
      }
    }

    events.sort((a, b) => a.ts - b.ts)
    return events.map(e => e.step)
  }, [deferredDisplayedReasoning, currentActivity, activeSessionKey])
  const currentActivityArchives = activeSessionKey ? activityArchivesBySession[activeSessionKey] || [] : []
  const archiveByAssistantIndex = useMemo(() => {
    const map = new Map<number, AgentActivityArchive>()
    currentActivityArchives.forEach(archive => {
      if (typeof archive.assistantIndex === 'number') {
        map.set(archive.assistantIndex, archive)
      }
    })
    return map
  }, [currentActivityArchives])
  const isDraftStart = isDraftSession && messages.length === 0 && !activeSessionKey
  const agentOptions = useMemo(() => {
    const hasMain = agents.some(agent => agent.id === 'main')
    const mainAgent: AgentInfo = {
      id: 'main',
      name: '快速问答',
      identity: { name: '快速问答' },
    }
    const visibleAgents = hasMain ? agents : [mainAgent, ...agents]
    return [...visibleAgents].sort((a, b) => {
      if (a.id === 'main') return -1
      if (b.id === 'main') return 1
      const aName = a.identity?.name || a.name || a.id
      const bName = b.identity?.name || b.name || b.id
      return aName.localeCompare(bName, 'zh-Hans')
    })
  }, [agents])
  const currentAgentId = activeSessionKey ? getAgentIdFromKey(activeSessionKey) : draftAgentId
  const handleOpenKnowledgeFile = useCallback((kbPath: string) => {
    if (!currentAgentId) return
    setReaderPanel({ agentId: currentAgentId, path: kbPath })
  }, [currentAgentId])
  const handleOpenWorkspaceFile = useCallback(async (path: string) => {
    try {
      await openWorkspaceFile(path)
    } catch (err: any) {
      toast.error(err?.message || '打开文件失败')
    }
  }, [])
  const selectedAgent = currentAgentId ? agentOptions.find(agent => agent.id === currentAgentId) : null
  const selectedAgentLabel =
    !currentAgentId || currentAgentId === 'main'
      ? '快速问答'
      : selectedAgent?.identity?.name || selectedAgent?.name || currentAgentId || '未知专家'
  const conversationTitle = isDraftStart
    ? '新对话'
    : currentSessionTitle?.trim() ||
      buildTitleFromMessages(messages) ||
      selectedAgentLabel + ' 对话'
  const canChangeAgent = isDraftSession && messages.length === 0 && !isCurrentSending

  const pendingFilesPreview = pendingFiles.length > 0 && (
    <div className="flex flex-wrap gap-2">
      {pendingFiles.map(pf => (
        <div
          key={pf.id}
          className="relative group rounded-lg border border-light-border bg-light-card overflow-hidden"
        >
          {pf.isImage && pf.previewUrl ? (
            <div className="relative">
              <img
                src={pf.previewUrl}
                alt={pf.name}
                className="h-16 w-16 object-cover"
              />
              <div className="absolute bottom-0 left-0 right-0 bg-black/50 px-1 py-0.5">
                <div className="text-[9px] text-white truncate">{pf.name}</div>
              </div>
            </div>
          ) : (
            <div className="h-16 w-auto flex items-center gap-2 px-3">
              <FileText size={16} className="text-accent-blue shrink-0" />
              <div className="min-w-0">
                <div className="text-xs text-light-text truncate max-w-[120px]">{pf.name}</div>
                <div className="text-[10px] text-light-text-secondary">{formatFileSize(pf.file.size)}</div>
              </div>
            </div>
          )}
          <span className="absolute top-0.5 right-0.5 opacity-0 transition-opacity group-hover:opacity-100">
            <Tooltip content={'移除附件 ' + pf.name}>
              <button
                onClick={() => removePendingFile(pf.id)}
                className="flex h-4 w-4 items-center justify-center rounded-full bg-black/60 text-white transition-colors hover:bg-black/75"
              >
                <X size={10} />
              </button>
            </Tooltip>
          </span>
        </div>
      ))}
    </div>
  )

  const CATEGORY_TABS: { key: typeof activeContextCategory; label: string; icon: typeof Bot }[] = [
    { key: 'expert', label: '专家', icon: Bot },
    { key: 'skill', label: '技能', icon: Wrench },
    { key: 'connector', label: '连接器', icon: Plug },
  ]

  const contextPickerQuery = agentSearch.trim().toLowerCase()

  const contextPickerItems = useMemo(() => {
    if (activeContextCategory === 'expert') {
      return agentOptions
        .filter((a) => a.id !== 'main')
        .filter((a) => {
          if (!contextPickerQuery) return true
          const values = [a.id, a.name, a.identity?.name].filter(Boolean).join(' ').toLowerCase()
          return values.includes(contextPickerQuery)
        })
        .map((a) => ({
          id: a.id,
          name: a.identity?.name || a.name || a.id,
          desc: agentDescriptions[a.id] || '专用任务助手',
        }))
    }
    if (activeContextCategory === 'skill') {
      return skills
        .filter((s) => {
          if (!contextPickerQuery) return true
          const name = (s.title || s.name).toLowerCase()
          return name.includes(contextPickerQuery) || s.name.toLowerCase().includes(contextPickerQuery)
        })
        .map((s) => ({ id: s.name, name: s.title || s.name, desc: s.description }))
    }
    return connectors
      .filter((c) => {
        if (!contextPickerQuery) return true
        return c.display_name.toLowerCase().includes(contextPickerQuery) || c.name.toLowerCase().includes(contextPickerQuery)
      })
      .map((c) => ({
        id: c.id,
        name: c.display_name,
        desc: c.description,
        enabled: c.enabled,
        needsAuth: c.needs_auth,
      }))
  }, [activeContextCategory, agentOptions, contextPickerQuery, skills, connectors])

  const isContextSelected = (cat: typeof activeContextCategory, id: string) =>
    boundContext?.category === cat && boundContext?.id === id

  const handlePickContextItem = (cat: typeof activeContextCategory, item: { id: string; name: string; enabled?: boolean; needsAuth?: boolean }) => {
    if (cat === 'connector' && item.enabled === false) return
    if (cat === 'expert') {
      handleSelectDraftAgent(item.id)
      setBoundContext({ category: 'expert', id: item.id, name: item.name })
      return
    }
    setBoundContext({
      category: cat,
      id: item.id,
      name: item.name,
      needsAuth: item.needsAuth,
      enabled: item.enabled,
    })
    setAgentPickerOpen(false)
    setAgentSearch('')
  }

  const handleClearContextBinding = () => {
    handleClearDraftAgent()
    setBoundContext(null)
  }

  const contextPicker = agentPickerOpen && canChangeAgent && (
    <div
      className="z-40 flex overflow-hidden rounded-2xl border border-light-border bg-white p-2 shadow-xl shadow-slate-200/80"
      style={agentPickerStyle}
    >
      {/* 左侧分类 */}
      <div className="w-24 shrink-0 border-r border-light-border pr-2">
        {CATEGORY_TABS.map((cat) => {
          const Icon = cat.icon
          const active = cat.key === activeContextCategory
          return (
            <button
              key={cat.key}
              type="button"
              onClick={() => {
                setActiveContextCategory(cat.key)
                setAgentSearch('')
              }}
              className={`mb-1 flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-sm ${
                active
                  ? 'bg-accent-blue text-white'
                  : 'text-light-text-secondary hover:bg-light-card-hover'
              }`}
            >
              <Icon size={15} />
              {cat.label}
            </button>
          )
        })}
      </div>

      {/* 右侧列表 */}
      <div className="flex min-w-0 flex-1 flex-col pl-2">
        <div className="mb-2 flex items-center gap-2 rounded-xl border border-light-border px-3 py-2 text-sm text-light-text-secondary">
          <Search size={15} />
          <ClearableInput
            value={agentSearch}
            onValueChange={setAgentSearch}
            className="min-w-0 flex-1 bg-transparent text-sm text-light-text outline-none placeholder:text-light-text-secondary"
            placeholder={`搜索${CATEGORY_TABS.find((c) => c.key === activeContextCategory)?.label}`}
            autoFocus
            clearLabel="清空搜索"
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto pr-1" style={{ maxHeight: agentPickerListMaxHeight }}>
          {activeContextCategory === 'skill' && skillsLoading && (
            <div className="px-2 py-6 text-center text-xs text-light-text-secondary">加载技能中…</div>
          )}
          {activeContextCategory === 'connector' && connectorsLoading && (
            <div className="px-2 py-6 text-center text-xs text-light-text-secondary">加载连接器中…</div>
          )}
          {!skillsLoading && !connectorsLoading && contextPickerItems.length === 0 && (
            <div className="px-2 py-6 text-center text-xs text-light-text-secondary">没有匹配的条目</div>
          )}

          {contextPickerItems.map((item) => {
            const disabled = activeContextCategory === 'connector' && item.enabled === false
            const selected = isContextSelected(activeContextCategory, item.id)
            return (
              <button
                key={item.id}
                type="button"
                disabled={disabled}
                title={disabled ? '该连接器未启用，请到连接器页面手动启用' : undefined}
                onClick={() => handlePickContextItem(activeContextCategory, item)}
                className={`mb-1 flex w-full items-start gap-3 rounded-xl px-3 py-2 text-left text-sm transition-colors ${
                  selected
                    ? 'bg-accent-blue/10 ring-1 ring-accent-blue/40'
                    : disabled
                      ? 'cursor-not-allowed opacity-50'
                      : 'hover:bg-light-card-hover'
                }`}
              >
                {activeContextCategory === 'expert' && <Bot size={16} className="mt-0.5 text-accent-blue" />}
                {activeContextCategory === 'skill' && <Wrench size={16} className="mt-0.5 text-accent-blue" />}
                {activeContextCategory === 'connector' && <Plug size={16} className="mt-0.5 text-accent-blue" />}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 truncate text-light-text">
                    {item.name}
                    {activeContextCategory === 'connector' && disabled && (
                      <span className="rounded bg-slate-500/15 px-1.5 py-0.5 text-[10px] text-slate-500">未启用</span>
                    )}
                    {activeContextCategory === 'connector' && item.needsAuth && !disabled && (
                      <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-600">需授权</span>
                    )}
                  </div>
                  {item.desc && <div className="truncate text-xs text-light-text-secondary">{item.desc}</div>}
                </div>
                {selected && <Check size={15} className="mt-0.5 text-accent-blue" />}
              </button>
            )
          })}
        </div>

        <div className="mt-2 space-y-1 border-t border-light-border pt-2">
          {(boundContext || draftAgentId) && !contextPickerQuery && (
            <button
              onClick={handleClearContextBinding}
              className="flex w-full cursor-pointer items-center gap-3 rounded-xl px-3 py-2 text-left text-sm text-light-text-secondary transition-colors hover:bg-light-card-hover hover:text-light-text"
            >
              <X size={16} />
              <span>使用快速问答</span>
            </button>
          )}
          <button
            onClick={() => {
              setAgentPickerOpen(false)
              setAgentCreateOpen(true)
            }}
            className="flex w-full cursor-pointer items-center gap-3 rounded-xl px-3 py-2 text-left text-sm text-light-text-secondary transition-colors hover:bg-light-card-hover hover:text-light-text"
          >
            <Plus size={16} />
            <span>创建专属专家</span>
          </button>
        </div>
      </div>
    </div>
  )

  const renderAgentSelector = (compact = false) => {
    const SelectedIcon =
      boundContext?.category === 'skill'
        ? Wrench
        : boundContext?.category === 'connector'
          ? Plug
          : Bot
    const selectedLabel = boundContext
      ? boundContext.name
      : !currentAgentId || currentAgentId === 'main'
        ? '快速问答'
        : selectedAgentLabel
    return (
      <div ref={agentPickerRef} className="relative">
        <button
          ref={agentPickerButtonRef}
          onClick={() => {
            if (!canChangeAgent) return
            setAgentPickerOpen((value) => !value)
          }}
          disabled={!canChangeAgent}
          className={
            'flex items-center gap-2 rounded-xl border border-light-border bg-light-card px-3 py-1.5 text-xs transition-colors ' +
            (canChangeAgent
              ? 'cursor-pointer text-light-text-secondary hover:border-accent-blue/30 hover:text-light-text'
              : 'cursor-not-allowed text-light-text-secondary/60') +
            ' ' +
            (compact && !boundContext && !draftAgentId ? 'text-accent-blue' : '')
          }
          title={canChangeAgent ? '选择上下文（专家 / 技能 / 连接器）' : '当前对话已锁定，无法切换'}
        >
          <SelectedIcon size={14} />
          <span className="max-w-[180px] truncate">{selectedLabel}</span>
          <ChevronDown size={13} />
        </button>
        {contextPicker}
      </div>
    )
  }

  // Persist the chosen model as the active model for this user's agent.
  // The hermes runtime always uses its config `model.default`, so wiring the
  // dropdown here (instead of a separate "set default" button in settings)
  // makes "select a model in chat → that model is used" actually work.
  const prevModelRef = useRef(selectedModel)

  const handleModelChange = (value: string) => {
    const prev = prevModelRef.current || selectedModel || ''
    const prevName = modelChoices.find(m => m.id === prev)?.name || prev
    const newName = modelChoices.find(m => m.id === value)?.name || value

    setSelectedModel(value)
    prevModelRef.current = value

    if (!value) return
    updateModelsConfig({ defaultModel: value }).catch(() => {
      // Non-fatal: the send path still works off whatever default is set.
    })

    if (prev && value && prev !== value) {
      hintAtCountRef.current = messages.length
      const hint = `模型已从 ${prevName} 更改为 ${newName}`
      toast.success(hint)
      setModelChangeHint(hint)
    }
  }

  const renderModelSelector = () => (
    <label className="flex items-center gap-1.5 rounded-xl border border-light-border bg-light-card px-2 py-1 text-xs text-light-text-secondary">
      <Brain size={14} />
      <select
        value={selectedModel}
        onChange={event => handleModelChange(event.target.value)}
        disabled={modelChoices.length === 0 || isCurrentSending}
        className="max-w-[220px] bg-transparent text-light-text outline-none disabled:text-light-text-secondary"
        title="选择模型"
      >
        {modelChoices.length === 0 ? (
          <option value="">暂无可用模型</option>
        ) : modelChoices.map(model => (
          <option key={model.id} value={model.id} disabled={!!model.disabled}>
            {model.id}{model.disabled ? '（已禁用）' : ''}
          </option>
        ))}
      </select>
    </label>
  )

  const renderActivityIcon = (activity: AgentActivityEvent) => {
    const iconClass = activity.status === 'failed'
      ? 'text-accent-red'
      : activity.status === 'completed'
        ? 'text-accent-green'
        : activity.status === 'approval'
          ? 'text-accent-yellow'
          : 'text-accent-blue'

    if (activity.status === 'completed') return <CircleCheck size={14} className={iconClass} />
    if (activity.status === 'failed') return <AlertCircle size={14} className={iconClass} />
    if (activity.status === 'thinking') return <Brain size={14} className={iconClass} />
    if (activity.status === 'approval') return <ShieldQuestion size={14} className={iconClass} />
    return <Wrench size={14} className={iconClass + ' ' + (activity.status === 'running' ? 'animate-pulse' : '')} />
  }

  const handleApprovalChoice = useCallback(async (activity: AgentActivityEvent, choice: string) => {
    const key = activeSessionKeyRef.current
    if (!key) return
    setActivityBySession(prev => ({
      ...prev,
      [key]: (prev[key] || []).map(item => item.id === activity.id ? { ...item, responding: true } : item),
    }))
    activityBySessionRef.current = {
      ...activityBySessionRef.current,
      [key]: (activityBySessionRef.current[key] || []).map(item => item.id === activity.id ? { ...item, responding: true } : item),
    }
    try {
      await respondRunApproval(activity.runId, choice)
      setActivityBySession(prev => {
        const next = {
          ...prev,
          [key]: (prev[key] || []).map(item => item.id === activity.id
          ? {
              ...item,
                    title: '已授权：' + approvalChoiceLabel(choice),
              selectedChoice: choice,
              responding: false,
              status: 'completed' as AgentActivityStatus,
            }
          : item),
        }
        activityBySessionRef.current = next
        return next
      })
      toast.info('授权已提交')
    } catch (err: any) {
      setActivityBySession(prev => {
        const next = {
          ...prev,
          [key]: (prev[key] || []).map(item => item.id === activity.id ? { ...item, responding: false } : item),
        }
        activityBySessionRef.current = next
        return next
      })
      toast.error(err?.message || '授权提交失败')
    }
  }, [toast])

  const renderApprovalActions = (activity: AgentActivityEvent) => {
    if (activity.type !== 'approval.request' || activity.status !== 'approval') return null
    const choices = activity.choices && activity.choices.length > 0
      ? activity.choices
      : ['once', 'deny']
    return (
      <div className="mt-2 flex flex-wrap gap-1.5">
        {choices.map(choice => {
          const isDeny = choice === 'deny'
          return (
            <button
              key={choice}
              type="button"
              disabled={activity.responding}
              onClick={() => handleApprovalChoice(activity, choice)}
              className={'rounded-md border px-2 py-1 text-[11px] font-medium transition-colors disabled:cursor-wait disabled:opacity-60 ' + (
                isDeny
                  ? 'border-accent-red/25 bg-white text-accent-red hover:bg-accent-red/5'
                  : 'border-accent-blue/25 bg-white text-accent-blue hover:bg-accent-blue/5'
              )}
            >
              {activity.responding ? '提交中...' : approvalChoiceLabel(choice)}
            </button>
          )
        })}
      </div>
    )
  }

  const renderActivityRows = (events: AgentActivityEvent[]) => (
    <>
      {events.map(activity => (
        <div key={activity.id} className="flex min-w-0 items-start gap-2 text-xs">
          <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">
            {renderActivityIcon(activity)}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium text-light-text">{activity.title}</span>
            {activity.detail && (
              <span className="mt-0.5 block line-clamp-2 break-words text-light-text-secondary">
                {activity.detail}
              </span>
            )}
            {renderApprovalActions(activity)}
          </span>
        </div>
      ))}
    </>
  )

  const renderAgentActivity = (events = currentActivity, compact = false) => {
    if (events.length === 0) return null
    return (
      <div className={(compact ? 'mt-2' : 'mb-3') + ' space-y-1.5 rounded-lg border border-light-border bg-light-card-hover/55 px-3 py-2'}>
        <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-normal text-light-text-secondary">
          <Bot size={12} />
          Agent 正在执行
        </div>
        {renderActivityRows(events)}
      </div>
    )
  }

  const toggleActivityArchive = useCallback((archiveId: string) => {
    const key = activeSessionKeyRef.current
    if (!key) return
    setActivityArchivesBySession(prev => {
      const archives = (prev[key] || []).map(item => item.id === archiveId ? { ...item, expanded: !item.expanded } : item)
      saveActivityArchives(key, archives)
      return { ...prev, [key]: archives }
    })
  }, [])

  const toggleArchiveTools = useCallback((archiveId: string) => {
    const key = activeSessionKeyRef.current
    if (!key) return
    setActivityArchivesBySession(prev => {
      const archives = (prev[key] || []).map(item => item.id === archiveId ? { ...item, toolEventsExpanded: !item.toolEventsExpanded } : item)
      saveActivityArchives(key, archives)
      return { ...prev, [key]: archives }
    })
  }, [])

  const renderActivityArchive = (archive?: AgentActivityArchive) => {
    if (!archive || (archive.events.length === 0 && !(archive.thoughts?.length))) return null
    return (
      <div className="mb-3 border-b border-light-border pb-3">
        <button
          type="button"
          onClick={() => toggleActivityArchive(archive.id)}
          className="flex items-center gap-1.5 text-xs text-light-text-secondary transition-colors hover:text-light-text"
        >
          <span>
            {archive.durationReliable === false
              ? '已处理'
              : '已处理 ' + formatActivityDuration(archive.endedAt - archive.startedAt)}
          </span>
          <ChevronRight
            size={13}
            className={'transition-transform ' + (archive.expanded ? 'rotate-90' : '')}
          />
        </button>
        {archive.expanded && (
          <div className="mt-3 space-y-3">
            {archive.thoughts?.map((thought, index) => (
              <p key={archive.id + ':thought:' + index} className="text-sm leading-relaxed text-light-text">
                {thought}
              </p>
            ))}
            {archive.events.length > 0 && (
              <div>
                <button
                  type="button"
                  onClick={() => toggleArchiveTools(archive.id)}
                  className="flex items-center gap-1.5 text-xs text-light-text-secondary transition-colors hover:text-light-text"
                >
                  <Wrench size={12} />
                  <span>运行了 {archive.events.length} 个命令</span>
                  <ChevronRight
                    size={13}
                    className={'transition-transform ' + (archive.toolEventsExpanded ? 'rotate-90' : '')}
                  />
                </button>
                {archive.toolEventsExpanded && renderAgentActivity(archive.events, true)}
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  const renderComposer = (hero = false) => (
    <div className={hero ? 'relative rounded-[26px] border border-light-border bg-white p-3 shadow-lg shadow-slate-200/80' : 'relative mx-auto max-w-5xl rounded-[26px] border border-light-border bg-white p-3 shadow-lg shadow-slate-200/70'}>
      {showSlashMenu && (
        <div
          ref={slashMenuRef}
          className={
            'absolute inset-x-3 z-30 overflow-y-auto rounded-2xl border border-light-border bg-white shadow-xl ' +
            (hero
              ? 'top-[calc(100%+0.5rem)] max-h-[42vh]'
              : 'bottom-[calc(100%+0.5rem)] max-h-72')
          }
        >
          {filteredSlashCommands.length === 0 ? (
            <div className="px-4 py-3 text-sm text-light-text-secondary">
              {slashCommandsLoading
                ? '正在加载 Hermes 命令...'
                : slashCommandsError
                  ? '命令加载失败：' + slashCommandsError
                  : slashCommands.length === 0
                    ? '暂无可用 Hermes 命令'
                    : '没有匹配的命令'}
            </div>
          ) : (
            Object.entries(groupedSlashCommands).map(([category, commands]) => {
              const categoryKey = category as keyof typeof CATEGORY_LABELS
              const styles = CATEGORY_STYLES[categoryKey]
              return (
              <div key={category} className="border-b border-light-border last:border-b-0">
                <div className={'px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-normal ' + styles.header}>
                  <span className={'rounded-full border px-2 py-0.5 ' + styles.badge}>
                    {CATEGORY_LABELS[categoryKey]}
                  </span>
                </div>
                <div className="pb-2">
                  {commands.map(command => {
                    const index = filteredSlashCommands.findIndex(item => item.name === command.name)
                    const isActive = index === slashActiveIndex
                    return (
                      <button
                        key={command.source + '-' + command.name}
                        type="button"
                        data-active={isActive ? 'true' : 'false'}
                        onMouseDown={event => event.preventDefault()}
                        onMouseEnter={() => setSlashActiveIndex(index)}
                        onClick={() => applySlashCommand(command)}
                        className={'flex w-full items-start gap-3 px-4 py-2.5 text-left transition-colors ' + (
                          isActive ? styles.active : 'hover:bg-light-card-hover'
                        )}
                      >
                        <span className="min-w-0 flex-1">
                          <span className="flex min-w-0 items-center gap-2">
                            <span className={'truncate text-sm font-medium ' + (isActive ? styles.command : 'text-light-text')}>
                              /{command.name}
                            </span>
                            {command.argsHint && (
                              <span className="truncate text-xs text-light-text-secondary">
                                {command.argsHint}
                              </span>
                            )}
                          </span>
                          <span className="mt-0.5 block line-clamp-2 text-xs text-light-text-secondary">
                            {command.description}
                          </span>
                        </span>
                      </button>
                    )
                  })}
                </div>
              </div>
              )
            })
          )}
        </div>
      )}
      {pendingFilesPreview && (
        <div className="px-2 pb-2">{pendingFilesPreview}</div>
      )}
      <div className="flex flex-col">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          onChange={handleFileSelect}
          className="hidden"
        />
        <ClearableTextarea
          ref={inputRef}
          value={input}
          onValueChange={(value) => {
            setInput(value)
            if (!value.startsWith('/')) setSlashMenuDismissed(false)
          }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder={hero ? '给 Hermes 发送消息，输入 / 查看命令' : '继续提问，输入 / 查看命令'}
          rows={hero ? 3 : 2}
          className="min-h-[72px] w-full resize-none bg-transparent px-2 py-2 text-[15px] text-light-text outline-none placeholder:text-slate-400"
          disabled={isCurrentSending}
          clearLabel="清空消息"
        />
        <div className="mt-2 flex items-center justify-between gap-3 px-1">
          <div className="flex min-w-0 items-center gap-2">
            <IconButton
              label="上传附件"
              onClick={() => fileInputRef.current?.click()}
              disabled={isCurrentSending}
              size="md"
              tone="primary"
              className="h-9 w-9 rounded-xl"
            >
              <Plus size={18} />
            </IconButton>
            {renderAgentSelector(true)}
            {renderModelSelector()}
          </div>
          {isCurrentSending ? (
            <IconButton
              label="缁堟鍥炲"
              onClick={handleAbortCurrentRun}
              surface="plain"
              className="h-9 w-9 rounded-full !bg-[var(--color-accent-blue)] !text-white transition-colors duration-150 hover:!bg-[color-mix(in_srgb,var(--color-accent-blue)_82%,white)] hover:!text-white"
            >
              <Square size={14} />
            </IconButton>
          ) : (
            <IconButton
              label="发送"
              onClick={handleSend}
              disabled={!hasContent}
              surface="plain"
              className="h-9 w-9 rounded-full !bg-[var(--color-accent-blue)] !text-white transition-colors duration-150 hover:!bg-[color-mix(in_srgb,var(--color-accent-blue)_82%,white)] hover:!text-white disabled:!bg-slate-300"
            >
              <Send size={16} />
            </IconButton>
          )}
        </div>
      </div>
    </div>
  )

  return (
    <div className="flex h-full">
      {/* Chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {activeSessionKey || isDraftSession ? (
          <>
            {/* Chat header */}
            <div className="px-5 py-3 border-b border-light-border flex items-center justify-between shrink-0">
              <div className="flex items-center gap-2 min-w-0">
                <IconButton
                  label="灞曞紑鑿滃崟"
                  onClick={openMobileSidebar}
                  size="md"
                  surface="plain"
                  className="-ml-2 lg:hidden"
                >
                  <Menu size={20} />
                </IconButton>
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-accent-blue/10 text-accent-blue">
                  <Bot size={16} />
                </div>
                <span className="truncate text-sm font-medium text-light-text" title={conversationTitle}>
                  {conversationTitle}
                </span>
                {!isDraftStart && selectedAgentLabel && (
                  <span className="hidden shrink-0 rounded-full border border-light-border px-2 py-0.5 text-xs text-light-text-secondary sm:inline">
                    {selectedAgentLabel}
                  </span>
                )}
              </div>
              {activeSessionKey && (
                <IconButton
                  label="鍒锋柊"
                  onClick={handleRefresh}
                  size="sm"
                >
                  <RefreshCw size={14} />
                </IconButton>
              )}
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-5 py-4">
              {chatLoading ? (
                <ChatHistorySkeleton />
              ) : messages.length === 0 && !isDraftSession ? (
                <div className="flex flex-col items-center justify-center py-20 text-light-text-secondary">
                  <MessageSquare size={40} className="mb-3 opacity-30" />
                  <p className="text-sm">发送一条消息开始对话</p>
                </div>
              ) : isDraftStart ? (
                <div className="flex h-full items-start justify-center px-6 pt-[13vh]">
                  <div className="w-full max-w-5xl">
                    <h1 className="mb-12 text-center text-3xl font-medium tracking-normal text-light-text">
                      接下来想做什么？
                    </h1>
                    {renderComposer(true)}
                  </div>
                </div>
              ) : (
                <div className="space-y-4 max-w-5xl mx-auto">
                  {messages.map((msg, i) => {
                    if (msg.role !== 'user' && msg.role !== 'assistant') return null
                    if (msg.role === 'assistant' && !msg.content?.trim() && !msg.reasoning_content?.trim()) return null
                    return (
                      <Fragment key={i}>
                      <div
                        className={msg.role === 'user' ? 'flex justify-end' : 'flex'}
                      >
                        <div className={'flex flex-col ' + (msg.role === 'user' ? 'max-w-[78%] items-end' : 'w-full items-start')}>
                          <div
                            className={(
                              msg.role === 'user'
                                ? 'w-full rounded-xl bg-accent-blue px-4 py-2.5 text-white'
                                : 'w-full px-1 py-1 text-light-text'
                            )}
                          >
                            {msg.role === 'user' ? (
                              <div className="text-sm whitespace-pre-wrap break-words">{msg.content}</div>
                            ) : (
                              <>
                                {(msg as any).processSteps?.length > 0 && (
                                  <ProcessCard
                                    steps={(msg as any).processSteps}
                                    streaming={!msg.content?.trim()}
                                    onOpenKnowledgeFile={handleOpenKnowledgeFile}
                                  />
                                )}
                                <MarkdownContent
                                  content={msg.content}
                                  onOpenKnowledgeFile={handleOpenKnowledgeFile}
                                  onOpenWorkspaceFile={handleOpenWorkspaceFile}
                                />
                              </>
                            )}
                          </div>
                          {msg.role !== 'user' && (
                            <div className="flex items-center gap-2 mt-1">
                              <button
                                onClick={() => {
                                  navigator.clipboard.writeText(msg.content)
                                  setCopiedIdx(i)
                                  setTimeout(() => setCopiedIdx(null), 2000)
                                }}
                                className="flex items-center gap-1 px-2 py-0.5 text-[11px] text-light-text-secondary hover:text-light-text rounded transition-colors"
                              >
                                {copiedIdx === i ? <><Check size={12} /> 已复制</> : <><Copy size={12} /> 复制</>}
                              </button>
                              {msg.model && (
                                <span className="flex items-center gap-1 text-[11px] text-light-text-muted">
                                  <span className="inline-block w-2 h-2 rounded-full bg-accent-blue/60" />
                                  {modelChoices.find(m => m.id === msg.model)?.name || msg.model}
                                </span>
                              )}
                              {msg.timestamp && (
                                <span className="text-[11px] text-light-text-muted">{formatTime(msg.timestamp)}</span>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                      {modelChangeHint && i === hintAtCountRef.current - 1 && (
                        <div className="flex items-center justify-center py-3">
                          <span className="text-[11px] text-light-text-muted animate-in fade-in duration-300">
                            {modelChangeHint}
                            <Info size={10} className="inline ml-0.5 opacity-50" />
                          </span>
                        </div>
                      )}
                      </Fragment>
                    )
                  })}
                  {isCurrentSending && (
                    <div className="flex">
                      <div className="w-full min-w-[260px] px-1 py-1">
                        {streamingProcessSteps.length > 0 ? (
                          <>
                            <ProcessCard steps={streamingProcessSteps} streaming onOpenKnowledgeFile={handleOpenKnowledgeFile} />
                            {displayedText && (
                              <div className="text-light-text mt-1.5">
                                <div className="text-sm whitespace-pre-wrap break-words">{deferredDisplayedText}</div>
                                <span className="inline-block w-1.5 h-4 ml-0.5 bg-accent-blue rounded-sm animate-pulse align-text-bottom" />
                              </div>
                            )}
                          </>
                        ) : displayedText ? (
                          <div className="text-light-text">
                            <div className="text-sm whitespace-pre-wrap break-words">{deferredDisplayedText}</div>
                            <span className="inline-block w-1.5 h-4 ml-0.5 bg-accent-blue rounded-sm animate-pulse align-text-bottom" />
                          </div>
                        ) : (
                          <div className="flex items-center gap-2 text-sm text-light-text-secondary">
                            <Loader2 size={14} className="animate-spin" />
                            正在思考...
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                  <div ref={messagesEndRef} />
                </div>
              )}
            </div>
            {/* Input */}
            {!isDraftStart && (
              <div className="px-5 py-3 shrink-0">
                {renderComposer()}
              </div>
            )}
          </>
        ) : (
          <div className="relative flex-1 flex flex-col items-center justify-center text-light-text-secondary">
            <IconButton
              label="打开菜单"
              onClick={openMobileSidebar}
              size="md"
              surface="plain"
              className="absolute left-3 top-3 lg:hidden"
            >
              <Menu size={20} />
            </IconButton>
            <MessageSquare size={48} className="mb-4 opacity-20" />
            <p className="text-sm mb-4">选择一个对话，或新建对话</p>
            <button
              onClick={() => createDraftSession()}
              className="flex items-center gap-2 rounded-lg bg-accent-blue px-4 py-2 text-sm font-medium text-white hover:bg-accent-blue/90 transition-colors"
            >
              <Plus size={16} />
              新对话
            </button>
          </div>
        )}
      </div>

      <AgentCreatePanel
        open={agentCreateOpen}
        onClose={() => setAgentCreateOpen(false)}
        onCreated={handleAgentCreated}
      />

      {readerPanel && (
        <KnowledgeReaderPanel
          agentId={readerPanel.agentId}
          path={readerPanel.path}
          onClose={() => setReaderPanel(null)}
        />
      )}

    </div>
  )
}
