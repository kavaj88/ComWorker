// API client for ComWorker Platform Gateway (multi-tenant mode)

// Always use relative URL to go through Vite proxy, avoiding CORS preflight
const API_URL = ''

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

// ---------------------------------------------------------------------------
// Setup-wizard state (empty-product onboarding)
// ---------------------------------------------------------------------------

export interface SetupStatus {
  required_steps: string[]
  recommended_steps: string[]
  completed_steps: string[]
  missing_steps: string[]
  missing_recommended_steps: string[]
  is_complete: boolean
  is_dismissed: boolean
}

export async function getSetupStatus(): Promise<SetupStatus> {
  // Public — but we still go through fetchJSON for consistency. Errors
  // propagate so the caller can render a "retry" UI instead of hanging.
  const data = await fetchJSON<SetupStatus>('/api/setup/status')
  return data
}

export async function completeSetup(): Promise<SetupStatus> {
  return fetchJSON<SetupStatus>('/api/setup/complete', {
    method: 'POST',
    body: JSON.stringify({ confirm: true }),
  })
}

export async function dismissSetup(): Promise<SetupStatus> {
  return fetchJSON<SetupStatus>('/api/setup/dismiss', { method: 'POST' })
}

export interface AuthUser {
  id: string
  username: string
  email: string
  role: 'admin' | 'user'
  must_change_password: boolean
  created_at: string
}

export interface AgentInfo {
  id: string
  name?: string | null
  workspace?: string
  model?: {
    primary?: string
    fallbacks?: string[]
  }
  identity?: {
    name?: string
    emoji?: string
    avatar?: string
    theme?: string
    avatarUrl?: string
  }
}

export interface AgentListResult {
  defaultId: string
  mainKey: string
  scope: string
  agents: AgentInfo[]
}

let agentsCache: AgentListResult | null = null
let agentsRequest: Promise<AgentListResult> | null = null
let sessionsCache: Session[] | null = null
let sessionsRequest: Promise<Session[]> | null = null

export interface CreateAgentInput {
  agentId?: string
  displayName: string
  description?: string
  avatar?: string
}

export interface CreateAgentResult {
  ok: boolean
  agentId: string
  name?: string
  workspace?: string
  model?: string
}

export interface AgentFileResult {
  agentId: string
  workspace: string
  file: {
    name: string
    path: string
    missing?: boolean
    size?: number
    updatedAtMs?: number
    content?: string
  }
}

export interface AgentIconResult {
  svg: string
  dataUrl: string
  id?: string
  url?: string
  sourceUrl?: string
  expiresInMs?: number
}

function hashText(input: string): number {
  let hash = 2166136261
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

function escapeSvgText(input: string): string {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function resolveRole(text: string): 'teacher' | 'developer' | 'doctor' | 'researcher' | 'writer' | 'manager' | 'assistant' {
  const lower = text.toLowerCase()
  if (/english|英语|grammar|语法|pronunciation|发音|language|语言|teacher|老师/.test(lower)) return 'teacher'
  if (/code|程序|开发|编程|program|developer|工程/.test(lower)) return 'developer'
  if (/doctor|医疗|医生|health|诊断|病|clinic/.test(lower)) return 'doctor'
  if (/research|研究|论文|资料|搜索|学术/.test(lower)) return 'researcher'
  if (/write|写作|文案|润色|编辑|内容/.test(lower)) return 'writer'
  if (/manager|管理|计划|项目|协调|运营/.test(lower)) return 'manager'
  return 'assistant'
}

function buildLocalAgentIcon(name: string, description: string, seed = ''): AgentIconResult {
  const palette = [
    ['#0891b2', '#22c55e'],
    ['#2563eb', '#06b6d4'],
    ['#7c3aed', '#ec4899'],
    ['#16a34a', '#84cc16'],
    ['#dc2626', '#f97316'],
    ['#0f766e', '#14b8a6'],
  ]
  const source = `${name}\n${description}\n${seed}`
  const hash = hashText(source)
  const [primary, secondary] = palette[hash % palette.length]
  const title = escapeSvgText(name || 'Agent')
  const role = resolveRole(`${name} ${description}`)
  const skin = ['#f7c59f', '#e8b48a', '#d99a72', '#f1d0b5'][hash % 4]
  const hair = ['#293241', '#3d2c2e', '#4a3428', '#1f2937'][(hash >>> 3) % 4]
  const shirt = ['#ffffff', '#ecfeff', '#f8fafc', '#eef2ff'][(hash >>> 5) % 4]
  const accessory =
    role === 'teacher'
      ? `<path d="M34 80h17v9H34z" fill="#fff" opacity=".9"/><path d="M37 83h11" stroke="${primary}" stroke-width="2" stroke-linecap="round"/>`
      : role === 'developer'
        ? `<rect x="72" y="70" width="20" height="14" rx="3" fill="#0f172a" opacity=".84"/><path d="M78 75l-3 2 3 2M86 75l3 2-3 2" stroke="#fff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>`
        : role === 'doctor'
          ? `<path d="M79 69v16a9 9 0 0 1-18 0v-3" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round"/><circle cx="61" cy="82" r="4" fill="#fff"/>`
          : role === 'researcher'
            ? `<circle cx="83" cy="77" r="8" fill="none" stroke="#fff" stroke-width="4"/><path d="M89 83l7 7" stroke="#fff" stroke-width="4" stroke-linecap="round"/>`
            : role === 'writer'
              ? `<path d="M78 67l13 13-14 6-5-5z" fill="#fff" opacity=".92"/><path d="M76 83l-3 7 7-3" fill="${secondary}"/>`
              : role === 'manager'
                ? `<path d="M78 68h16v22H78z" fill="#fff" opacity=".9"/><path d="M82 75h8M82 82h8" stroke="${primary}" stroke-width="2" stroke-linecap="round"/>`
                : `<circle cx="84" cy="78" r="9" fill="#fff" opacity=".9"/><path d="M80 78h8M84 74v8" stroke="${primary}" stroke-width="2.4" stroke-linecap="round"/>`
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" role="img" aria-label="${title} icon">
  <defs>
    <linearGradient id="g" x1="18" y1="12" x2="102" y2="108" gradientUnits="userSpaceOnUse">
      <stop stop-color="${primary}"/>
      <stop offset="1" stop-color="${secondary}"/>
    </linearGradient>
    <clipPath id="r"><rect x="14" y="14" width="92" height="92" rx="28"/></clipPath>
  </defs>
  <rect x="14" y="14" width="92" height="92" rx="28" fill="url(#g)"/>
  <g clip-path="url(#r)">
    <circle cx="30" cy="30" r="18" fill="rgba(255,255,255,.16)"/>
    <circle cx="96" cy="96" r="30" fill="rgba(255,255,255,.13)"/>
    <path d="M33 112c4-24 18-37 36-37s32 13 36 37" fill="${shirt}" opacity=".96"/>
    <path d="M43 112c5-18 14-27 26-27s21 9 26 27" fill="${secondary}" opacity=".2"/>
    <circle cx="60" cy="48" r="24" fill="${skin}"/>
    <path d="M36 47c2-19 13-31 31-31 14 0 24 8 28 21-10-3-19-9-26-17-8 12-18 20-33 27z" fill="${hair}"/>
    <circle cx="51" cy="52" r="2.3" fill="#1f2937"/>
    <circle cx="69" cy="52" r="2.3" fill="#1f2937"/>
    <path d="M53 63c4 4 10 4 14 0" stroke="#7f1d1d" stroke-width="2.4" stroke-linecap="round" fill="none"/>
    <path d="M44 45c-4 1-7 5-6 10 1 4 4 7 8 7" fill="${skin}" opacity=".96"/>
    <path d="M76 45c4 1 7 5 6 10-1 4-4 7-8 7" fill="${skin}" opacity=".96"/>
    ${accessory}
  </g>
</svg>`
  return {
    svg,
    dataUrl: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
  }
}

export interface Session {
  key: string
  title?: string
  created_at: string | null
  updated_at: string | null
}

export interface SessionDetail {
  key: string
  messages: Array<{
    role: string
    content: string
    timestamp: string | null
    model?: string
  }>
  created_at: string | null
  updated_at: string | null
}

export interface AgentRunWaitResult {
  runId: string
  status: 'ok' | 'completed' | 'error' | 'failed' | 'aborted' | 'cancelled' | 'timeout'
  startedAt: number | null
  endedAt: number | null
  error: unknown
  message?: unknown
}

export interface SlashCommandInfo {
  name: string
  description: string
  argument_hint: string | null
  aliases: string[]
  category: string
  scope: 'text' | 'native' | 'both'
  source: 'builtin' | 'skill'
  skill_name: string | null
}

export interface SlashCommandsResult {
  agentId: string
  commands: SlashCommandInfo[]
}

export interface FileEntry {
  name: string
  path: string
  type: 'file' | 'directory'
  size: number | null
  content_type?: string | null
  modified: string
}

export interface BrowseDirectoryResult {
  type: 'directory'
  path: string
  root: string
  items: FileEntry[]
}

export interface BrowseFileResult {
  type: 'file'
  path: string
  name: string
  size: number
  content_type: string
  modified: string
  content?: string
}

export type BrowseResult = BrowseDirectoryResult | BrowseFileResult

export interface KnowledgePageMeta {
  path: string
  name: string
  title: string
  type?: string | null
  domain?: string | null
  status?: string | null
  tags: string[]
  summary?: string | null
  created?: string | null
  updated?: string | null
  size: number
  modified: string
  wikilinks: string[]
}

export interface KnowledgeListResult {
  agentId: string
  knowledgeRoot: string
  exists: boolean
  pages: KnowledgePageMeta[]
  directories?: Array<{
    path: string
    name: string
    modified: string
  }>
  attachments?: Array<{
    path: string
    name: string
    size: number
    modified: string
  }>
}

export interface KnowledgeReadResult {
  page: KnowledgePageMeta
  content: string
  backlinks: string[]
}

export interface KnowledgeSearchResult {
  path: string
  title: string
  line: number
  text: string
}

export interface KnowledgeGraphResult {
  nodes: Array<{ id: string; title: string; type?: string | null; tags: string[] }>
  edges: Array<{ source: string; target: string }>
}

export interface ModelChoice {
  id: string
  name: string
  provider: string
  providerName?: string
  disabled?: boolean
}

export interface ModelsResult {
  models: ModelChoice[]
  configuredModel: string
  configuredProviders: Record<string, unknown>
  runtime?: string
}

// ---------------------------------------------------------------------------
// Token management
// ---------------------------------------------------------------------------

const ACCESS_TOKEN_KEY = 'comworker_access_token'
const REFRESH_TOKEN_KEY = 'comworker_refresh_token'

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

function setTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, access)
  localStorage.setItem(REFRESH_TOKEN_KEY, refresh)
}

function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
}

export function isLoggedIn(): boolean {
  return getAccessToken() !== null
}

// ---------------------------------------------------------------------------
// Core HTTP helper
// ---------------------------------------------------------------------------

let refreshPromise: Promise<boolean> | null = null

async function parseErrorMessage(res: Response): Promise<string> {
  const fallback = `请求失败 (${res.status})`

  try {
    const body = await res.text()
    if (!body) return fallback

    try {
      const data = JSON.parse(body) as { detail?: string; message?: string }
      return data.detail || data.message || body || fallback
    } catch {
      return body || fallback
    }
  } catch {
    return fallback
  }
}

async function tryRefreshToken(): Promise<boolean> {
  const refresh = getRefreshToken()
  if (!refresh) return false

  // Deduplicate concurrent refresh attempts
  if (refreshPromise) return refreshPromise

  refreshPromise = (async () => {
    try {
      const res = await fetch(`${API_URL}/api/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (!res.ok) return false
      const data: TokenResponse = await res.json()
      setTokens(data.access_token, data.refresh_token)
      return true
    } catch {
      return false
    } finally {
      refreshPromise = null
    }
  })()

  return refreshPromise
}

export async function fetchJSON<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getAccessToken()

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  let res = await fetch(`${API_URL}${path}`, { ...options, headers })

  // On 401 attempt a silent token refresh and retry once
  if (res.status === 401 && token) {
    const refreshed = await tryRefreshToken()
    if (refreshed) {
      headers['Authorization'] = `Bearer ${getAccessToken()}`
      res = await fetch(`${API_URL}${path}`, { ...options, headers })
    } else {
      clearTokens()
      window.location.href = '/login'
      throw new Error('Session expired')
    }
  }

  if (!res.ok) {
    throw new Error(await parseErrorMessage(res))
  }

  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Auth functions
// ---------------------------------------------------------------------------

export async function login(
  username: string,
  password: string,
): Promise<TokenResponse> {
  const data = await fetchJSON<TokenResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
  setTokens(data.access_token, data.refresh_token)
  return data
}


export function logout(): void {
  clearTokens()
  window.location.href = '/login'
}

export async function getMe(): Promise<AuthUser> {
  return fetchJSON<AuthUser>('/api/auth/me')
}

// ---------------------------------------------------------------------------
// Platform branding (name + logo, configured from the admin console)
// ---------------------------------------------------------------------------

export interface PlatformConfig {
  name: string
  logo: string | null
}

export async function getPlatformConfig(): Promise<PlatformConfig> {
  return fetchJSON<PlatformConfig>('/api/comworker/platform-config')
}

// ---------------------------------------------------------------------------
// Cron Jobs
// ---------------------------------------------------------------------------

export interface CronJob {
  id: string
  name: string
  enabled: boolean
  schedule_kind: string
  schedule_display: string
  schedule_expr: string | null
  schedule_every_ms: number | null
  message: string
  deliver: boolean
  channel: string | null
  to: string | null
  session_key?: string | null
  last_output?: string | null
  last_output_at_ms?: number | null
  last_delivery_error?: string | null
  next_run_at_ms: number | null
  last_run_at_ms: number | null
  last_status: string | null
  last_error: string | null
  created_at_ms: number
}

export async function listCronJobs(includeDisabled = true): Promise<CronJob[]> {
  const params = includeDisabled ? '?include_disabled=true' : ''
  return fetchJSON<CronJob[]>(`/api/comworker/cron/jobs${params}`)
}

export async function createCronJob(params: {
  name: string
  message: string
  agentId?: string
  sessionKey?: string
  sessionTitle?: string
  every_seconds?: number
  cron_expr?: string
  at_iso?: string
}): Promise<CronJob> {
  return fetchJSON<CronJob>('/api/comworker/cron/jobs', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export async function deleteCronJob(jobId: string): Promise<void> {
  await fetchJSON<unknown>(`/api/comworker/cron/jobs/${encodeURIComponent(jobId)}`, {
    method: 'DELETE',
  })
}

export async function toggleCronJob(jobId: string, enabled: boolean): Promise<CronJob> {
  return fetchJSON<CronJob>(
    `/api/comworker/cron/jobs/${encodeURIComponent(jobId)}/toggle`,
    {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    },
  )
}

export async function runCronJob(jobId: string): Promise<void> {
  await fetchJSON<unknown>(
    `/api/comworker/cron/jobs/${encodeURIComponent(jobId)}/run`,
    { method: 'POST' },
  )
}

// ---------------------------------------------------------------------------
// Agent functions
// ---------------------------------------------------------------------------

export async function listAgents(options: { force?: boolean } = {}): Promise<AgentListResult> {
  if (!options.force && agentsCache) return agentsCache
  if (!options.force && agentsRequest) return agentsRequest

  agentsRequest = fetchJSON<AgentListResult>('/api/comworker/agents')
    .then(result => {
      agentsCache = result
      return result
    })
    .finally(() => {
      agentsRequest = null
    })

  return agentsRequest
}

export function invalidateAgentsCache(): void {
  agentsCache = null
  agentsRequest = null
}

function buildRandomAgentId(): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replace(/-/g, '').slice(0, 10)
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
  return `assistant-${random}`
}

export async function createAgent(input: CreateAgentInput): Promise<CreateAgentResult> {
  const displayName = input.displayName.trim()
  let result: CreateAgentResult | null = null
  let agentId = input.agentId?.trim() || buildRandomAgentId()

  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      result = await fetchJSON<CreateAgentResult>('/api/comworker/agents', {
        method: 'POST',
        body: JSON.stringify({
          name: agentId,
          displayName,
          description: input.description?.trim() || undefined,
          workspace: `profiles/${agentId}/workspace`,
          avatar: input.avatar?.trim() || undefined,
        }),
      })
      break
    } catch (err) {
      if (!(err instanceof Error) || !err.message.includes('already exists') || input.agentId) {
        throw err
      }
      agentId = buildRandomAgentId()
    }
  }

  if (!result) throw new Error('创建 Agent 失败')

  if (displayName && displayName !== agentId) {
    await fetchJSON(`/api/comworker/agents/${encodeURIComponent(agentId)}`, {
      method: 'PUT',
      body: JSON.stringify({ name: displayName, avatar: input.avatar?.trim() || undefined }),
    })
  }

  const description = input.description?.trim()
  if (description) {
    fetchJSON(`/api/comworker/agents/${encodeURIComponent(agentId)}/files/IDENTITY.md`, {
      method: 'PUT',
      body: JSON.stringify({
        content: `# ${displayName || agentId}\n\n${description}\n`,
      }),
    }).catch(() => {})
  }

  invalidateAgentsCache()
  return { ...result, agentId: result.agentId || agentId }
}

export async function updateAgentName(agentId: string, displayName: string, avatar?: string): Promise<{ ok: boolean; agentId: string }> {
  const result = await fetchJSON<{ ok: boolean; agentId: string }>(`/api/comworker/agents/${encodeURIComponent(agentId)}`, {
    method: 'PUT',
    body: JSON.stringify({ name: displayName.trim(), avatar: avatar?.trim() || undefined }),
  })
  invalidateAgentsCache()
  return result
}

export async function generateAgentIcon(
  name: string,
  description: string,
  seed = '',
  previousIconId = '',
): Promise<AgentIconResult> {
  try {
    const result = await fetchJSON<AgentIconResult>('/api/comworker/agents/icon', {
      method: 'POST',
      body: JSON.stringify({ name, description, seed, previousIconId }),
    })
    if (result.url?.startsWith('/api/')) {
      const sourceUrl = `/api/comworker${result.url.slice('/api'.length)}`
      const token = getAccessToken()
      const imageRes = await fetch(sourceUrl, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!imageRes.ok) throw new Error(`图标加载失败 (${imageRes.status})`)
      const svg = await imageRes.text()
      return {
        ...result,
        svg,
        sourceUrl,
        dataUrl: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
      }
    }
    return result
  } catch {
    return buildLocalAgentIcon(name, description, seed)
  }
}

export async function getAgentFile(agentId: string, name: string): Promise<AgentFileResult> {
  return fetchJSON<AgentFileResult>(
    `/api/comworker/agents/${encodeURIComponent(agentId)}/files/${encodeURIComponent(name)}`,
  )
}

export async function setAgentFile(agentId: string, name: string, content: string): Promise<AgentFileResult> {
  return fetchJSON<AgentFileResult>(
    `/api/comworker/agents/${encodeURIComponent(agentId)}/files/${encodeURIComponent(name)}`,
    {
      method: 'PUT',
      body: JSON.stringify({ content }),
    },
  )
}

export async function deleteAgent(agentId: string): Promise<void> {
  await fetchJSON<unknown>(`/api/comworker/agents/${encodeURIComponent(agentId)}?delete_files=true`, {
    method: 'DELETE',
  })
  invalidateAgentsCache()
}

// ---------------------------------------------------------------------------
// Session functions
// ---------------------------------------------------------------------------

export async function listSessions(options: { force?: boolean } = {}): Promise<Session[]> {
  if (!options.force && sessionsCache) return sessionsCache
  if (!options.force && sessionsRequest) return sessionsRequest

  sessionsRequest = fetchJSON<Session[]>('/api/comworker/sessions')
    .then(result => {
      sessionsCache = result
      return result
    })
    .finally(() => {
      sessionsRequest = null
    })

  return sessionsRequest
}

export function invalidateSessionsCache(): void {
  sessionsCache = null
  sessionsRequest = null
}

export async function getSession(key: string): Promise<SessionDetail> {
  return fetchJSON<SessionDetail>(`/api/comworker/sessions/${encodeURIComponent(key)}`)
}

export async function deleteSession(key: string): Promise<void> {
  await fetchJSON<unknown>(`/api/comworker/sessions/${encodeURIComponent(key)}`, {
    method: 'DELETE',
  })
  invalidateSessionsCache()
}

export async function updateSessionTitle(
  key: string,
  title: string,
): Promise<{ ok: boolean; key: string; title: string | null }> {
  const result = await fetchJSON<{ ok: boolean; key: string; title: string | null }>(
    `/api/comworker/sessions/${encodeURIComponent(key)}/title`,
    {
      method: 'PUT',
      body: JSON.stringify({ title }),
    },
  )
  invalidateSessionsCache()
  return result
}

export async function generateSessionTitle(
  key: string,
  message: string,
): Promise<{ ok: boolean; key: string; title: string | null }> {
  const result = await fetchJSON<{ ok: boolean; key: string; title: string | null }>(
    `/api/comworker/sessions/${encodeURIComponent(key)}/title-summary`,
    {
      method: 'POST',
      body: JSON.stringify({ message }),
    },
  )
  invalidateSessionsCache()
  return result
}

// ---------------------------------------------------------------------------
// Chat functions
// ---------------------------------------------------------------------------

export interface SessionContext {
  type: 'skill' | 'connector'
  id?: string
  name?: string
}

export async function sendChatMessage(
  sessionKey: string,
  message: string,
  model?: string,
  title?: string,
  context?: SessionContext | null,
): Promise<{ ok: boolean; runId: string | null; title?: string | null }> {
  const body: Record<string, unknown> = { message, model }
  if (title) body.title = title
  if (context) body.context = context
  const result = await fetchJSON<{ ok: boolean; runId: string | null; title?: string | null }>(
    `/api/comworker/sessions/${encodeURIComponent(sessionKey)}/messages`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
  invalidateSessionsCache()
  return result
}

export interface UserConnector {
  id: string
  name: string
  display_name: string
  description: string
  examples: string
  icon?: string
  transport: string
  credential_strategy: string
  needs_auth: boolean
  required_keys: string[]
  enabled: boolean
  locked: boolean
}

export async function listUserConnectors(): Promise<UserConnector[]> {
  const data = await fetchJSON<{ connectors: UserConnector[] }>('/api/connectors')
  return data.connectors || []
}

export async function enableConnector(id: string): Promise<{ ok: boolean; enabled: boolean }> {
  return fetchJSON<{ ok: boolean; enabled: boolean }>(`/api/connectors/${encodeURIComponent(id)}/enable`, {
    method: 'PUT',
  })
}

export async function disableConnector(id: string): Promise<{ ok: boolean; enabled: boolean }> {
  return fetchJSON<{ ok: boolean; enabled: boolean }>(`/api/connectors/${encodeURIComponent(id)}/disable`, {
    method: 'PUT',
  })
}

export async function listModels(): Promise<ModelsResult> {
  return fetchJSON<ModelsResult>('/api/comworker/models')
}

export async function updateModelsConfig(params: {
  providers?: Record<string, unknown>
  defaultModel?: string
}): Promise<void> {
  await fetchJSON<unknown>('/api/comworker/models/config', {
    method: 'PUT',
    body: JSON.stringify(params),
  })
}

export interface ModelConnectionTestResult {
  ok: boolean
  status: number
  message: string
  suggestion?: string
  durationMs?: number
}

export async function testModelConnection(params: {
  baseUrl: string
  apiKey?: string
  api?: string
  model: string
}): Promise<ModelConnectionTestResult> {
  // The probe is server-side; non-2xx is returned as a normal 200 JSON body
  // (not an HTTP error), so we use a tolerant fetch instead of fetchJSON.
  const token = getAccessToken()
  const res = await fetch('/api/comworker/models/test', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    throw new Error(await parseErrorMessage(res))
  }
  return res.json() as Promise<ModelConnectionTestResult>
}

export async function listSlashCommands(agentId?: string): Promise<SlashCommandsResult> {
  const params = agentId ? `?agentId=${encodeURIComponent(agentId)}` : ''
  return fetchJSON<SlashCommandsResult>(`/api/comworker/commands${params}`)
}

export async function waitForAgentRun(
  runId: string,
  timeoutMs = 25000,
): Promise<AgentRunWaitResult> {
  const params = new URLSearchParams({ timeoutMs: String(timeoutMs) })
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs + 5000)
  try {
    return await fetchJSON<AgentRunWaitResult>(
      `/api/comworker/runs/${encodeURIComponent(runId)}/wait?${params.toString()}`,
      { signal: controller.signal },
    )
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      return {
        runId,
        status: 'timeout',
        startedAt: null,
        endedAt: null,
        error: null,
      }
    }
    throw err
  } finally {
    window.clearTimeout(timeoutId)
  }
}

export function getRunEventsStreamUrl(runId: string): string {
  const token = getAccessToken()
  const params = new URLSearchParams()
  if (token) params.set('token', token)
  const suffix = params.toString()
  return `/api/comworker/runs/${encodeURIComponent(runId)}/events${suffix ? `?${suffix}` : ''}`
}

export async function abortAgentRun(
  runId: string,
  sessionKey: string,
): Promise<{ ok?: boolean }> {
  return fetchJSON<{ ok?: boolean }>(`/api/comworker/runs/${encodeURIComponent(runId)}/abort`, {
    method: 'POST',
    body: JSON.stringify({ sessionKey }),
  })
}

export async function respondRunApproval(
  runId: string,
  choice: string,
): Promise<{ run_id?: string; choice?: string; resolved?: number }> {
  return fetchJSON<{ run_id?: string; choice?: string; resolved?: number }>(
    `/api/comworker/runs/${encodeURIComponent(runId)}/approval`,
    {
      method: 'POST',
      body: JSON.stringify({ choice }),
    },
  )
}

export async function abortActiveSessionRun(sessionKey: string): Promise<{ ok?: boolean }> {
  return fetchJSON<{ ok?: boolean }>(
    `/api/comworker/sessions/${encodeURIComponent(sessionKey)}/abort-active`,
    { method: 'POST' },
  )
}

// ---------------------------------------------------------------------------
// File upload
// ---------------------------------------------------------------------------

export async function uploadFileToWorkspace(
  file: File,
  uploadDir: string,
): Promise<{ name?: string; path?: string; file_id?: string; url?: string }> {
  const token = getAccessToken()
  const formData = new FormData()
  formData.append('file', file)
  formData.append('path', uploadDir)

  const res = await fetch(`${API_URL}/api/comworker/filemanager/upload`, {
    method: 'POST',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {},
    body: formData,
  })

  if (!res.ok) {
    throw new Error(await parseErrorMessage(res))
  }

  return res.json() as Promise<{ path: string }>
}

// ---------------------------------------------------------------------------
// File manager
// ---------------------------------------------------------------------------

export async function browseFiles(path = ''): Promise<BrowseResult> {
  const params = path ? `?path=${encodeURIComponent(path)}` : ''
  return fetchJSON<BrowseResult>(`/api/comworker/filemanager/browse${params}`)
}

export async function uploadFile(file: File, targetDir = ''): Promise<FileEntry> {
  const token = getAccessToken()
  const formData = new FormData()
  formData.append('file', file)
  if (targetDir) formData.append('path', targetDir)

  const res = await fetch(`${API_URL}/api/comworker/filemanager/upload`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  })

  if (!res.ok) {
    throw new Error(await parseErrorMessage(res))
  }

  return res.json() as Promise<FileEntry>
}

export async function deleteFile(path: string): Promise<void> {
  await fetchJSON<unknown>(`/api/comworker/filemanager/delete?path=${encodeURIComponent(path)}`, {
    method: 'DELETE',
  })
}

export async function createDirectory(path: string): Promise<void> {
  await fetchJSON<unknown>(`/api/comworker/filemanager/mkdir?path=${encodeURIComponent(path)}`, {
    method: 'POST',
  })
}

export async function writeManagedFile(path: string, content: string): Promise<FileEntry> {
  try {
    return await fetchJSON<FileEntry>('/api/comworker/filemanager/write', {
      method: 'PUT',
      body: JSON.stringify({ path, content }),
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : ''
    if (!message.includes('Cannot PUT /api/filemanager/write')) {
      throw err
    }

    const normalizedPath = path.replace(/\\/g, '/')
    const slashIndex = normalizedPath.lastIndexOf('/')
    const fileName = slashIndex >= 0 ? normalizedPath.slice(slashIndex + 1) : normalizedPath
    const targetDir = slashIndex >= 0 ? normalizedPath.slice(0, slashIndex) : ''
    if (!fileName) throw err

    const file = new File([content], fileName, { type: 'text/plain;charset=utf-8' })
    return uploadFile(file, targetDir)
  }
}

export async function downloadManagedFile(entry: FileEntry): Promise<void> {
  const token = getAccessToken()
  const res = await fetch(
    `${API_URL}/api/comworker/filemanager/download?path=${encodeURIComponent(entry.path)}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  )

  if (!res.ok) {
    throw new Error(await parseErrorMessage(res))
  }

  const blob = await res.blob()
  const blobUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = entry.name
  link.click()
  URL.revokeObjectURL(blobUrl)
}

/** Open a workspace file (uploads, generated articles, etc.) in a new tab.
 *  Accepts paths like ~/.comworker/profiles/main/workspace/uploads/foo.md or
 *  /opt/data/profiles/main/workspace/uploads/foo.md. */
export async function openWorkspaceFile(path: string): Promise<void> {
  const token = getAccessToken()
  const res = await fetch(
    `${API_URL}/api/comworker/filemanager/download?path=${encodeURIComponent(path)}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  )

  if (!res.ok) {
    throw new Error(await parseErrorMessage(res))
  }

  const blob = await res.blob()
  const blobUrl = URL.createObjectURL(blob)
  window.open(blobUrl, '_blank', 'noopener,noreferrer')
  // Keep the blob URL alive long enough for the new tab to load; revoking
  // immediately can break PDFs on some browsers.
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60000)
}

// ---------------------------------------------------------------------------
// Knowledge base
// ---------------------------------------------------------------------------

export async function listKnowledge(agentId: string): Promise<KnowledgeListResult> {
  return fetchJSON<KnowledgeListResult>(
    `/api/comworker/knowledge/list?agentId=${encodeURIComponent(agentId)}`,
  )
}

export async function readKnowledge(agentId: string, path: string): Promise<KnowledgeReadResult> {
  return fetchJSON<KnowledgeReadResult>(
    `/api/comworker/knowledge/read?agentId=${encodeURIComponent(agentId)}&path=${encodeURIComponent(path)}`,
  )
}

export async function searchKnowledge(agentId: string, query: string): Promise<{ results: KnowledgeSearchResult[] }> {
  return fetchJSON<{ results: KnowledgeSearchResult[] }>(
    `/api/comworker/knowledge/search?agentId=${encodeURIComponent(agentId)}&q=${encodeURIComponent(query)}`,
  )
}

export async function getKnowledgeGraph(agentId: string): Promise<KnowledgeGraphResult> {
  return fetchJSON<KnowledgeGraphResult>(
    `/api/comworker/knowledge/graph?agentId=${encodeURIComponent(agentId)}`,
  )
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export interface InstalledSkill {
  name: string
  description?: string
  source?: string
  path?: string
  disabled?: boolean
}

export interface RecommendedSkill {
  name: string
  description: string
  category: string
}

export interface RecommendedCategory {
  id: string
  name: string
  name_en?: string
  icon?: string
  description?: string
  order?: number
  skills: RecommendedSkill[]
}

export async function listInstalledSkills(): Promise<InstalledSkill[]> {
  return fetchJSON<InstalledSkill[]>('/api/comworker/skills')
}

export async function toggleInstalledSkill(name: string, enabled: boolean): Promise<void> {
  await fetchJSON<unknown>(`/api/comworker/skills/${encodeURIComponent(name)}/toggle`, {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  })
}

export async function deleteInstalledSkill(name: string): Promise<void> {
  const safePath = name.split('/').map(encodeURIComponent).join('/')
  await fetchJSON<unknown>(`/api/comworker/skills/${safePath}`, { method: 'DELETE' })
}

export async function installSkill(slug: string): Promise<{ ok: boolean; output?: string; name?: string }> {
  return fetchJSON<{ ok: boolean; output?: string; name?: string }>('/api/comworker/marketplaces/skills/install', {
    method: 'POST',
    body: JSON.stringify({ slug }),
  })
}

export async function getRecommendedSkills(): Promise<{ categories: RecommendedCategory[] }> {
  return fetchJSON<{ categories: RecommendedCategory[] }>('/api/comworker/marketplaces/recommended')
}

export async function installRecommendedSkill(category: string, skillName: string): Promise<{ ok: boolean; name: string }> {
  return fetchJSON<{ ok: boolean; name: string }>('/api/comworker/marketplaces/recommended/install', {
    method: 'POST',
    body: JSON.stringify({ category, skillName }),
  })
}

export function downloadSkillUrl(name: string): string {
  const safePath = name.split('/').map(encodeURIComponent).join('/')
  return `${API_URL}/api/comworker/skills/${safePath}/download`
}

export type SkillScopeType = 'global' | 'builtin' | 'agent'

export interface SkillScope {
  id: string
  type: SkillScopeType
  label: string
  path: string
  agentId?: string
  writable: boolean
}

export interface SkillInfo {
  name: string
  description: string
  source: string
  scope: string
  scopeType: SkillScopeType
  scopeLabel: string
  agentId?: string
  available: boolean
  disabled: boolean
  writable: boolean
  path: string
  dirPath: string
  category?: string
  title?: string
  owner?: 'admin' | 'user'
}

export interface SkillFileInfo {
  name: string
  path: string
  size: number
  modified: string
  editable: boolean
}

export interface SkillSearchResult {
  slug: string
  url: string
  installs: string
  sizeLabel?: string
}

export interface GitSkillInfo {
  name: string
  description: string
  relativePath: string
}

export interface GitScanResult {
  repo: string
  repoName: string
  skills: GitSkillInfo[]
  cacheKey: string
}

export interface SkillDetail {
  name: string
  description: string
  category: string
  markdown: string
  meta: Record<string, unknown>
}

function skillTargetBody(scope: SkillScope) {
  return scope.type === 'agent' ? { scope: 'agent', agentId: scope.agentId } : { scope: scope.type }
}

function skillTargetQuery(scope: SkillScope): string {
  const params = new URLSearchParams({ scope: scope.type })
  if (scope.agentId) params.set('agentId', scope.agentId)
  return params.toString()
}

export async function listSkillScopes(): Promise<SkillScope[]> {
  return fetchJSON<SkillScope[]>('/api/comworker/skills/scopes')
}

export async function listSkills(scope?: SkillScope): Promise<SkillInfo[]> {
  const query = scope ? `?${skillTargetQuery(scope)}` : '?all=1'
  return fetchJSON<SkillInfo[]>(`/api/comworker/skills${query}`)
}

export async function deleteSkill(skill: SkillInfo): Promise<void> {
  await fetchJSON<unknown>(
    `/api/comworker/skills/${encodeURIComponent(skill.name)}?${skillTargetQuery({
      id: skill.scope,
      type: skill.scopeType,
      label: skill.scopeLabel,
      path: '',
      agentId: skill.agentId,
      writable: skill.writable,
    })}`,
    { method: 'DELETE' },
  )
}

export async function setSkillDisabled(skill: SkillInfo, disabled: boolean): Promise<{ ok?: boolean; name?: string; disabled: boolean }> {
  return fetchJSON<{ ok?: boolean; name?: string; disabled: boolean }>(
    `/api/comworker/skills/${encodeURIComponent(skill.name)}/disabled`,
    {
      method: 'PUT',
      body: JSON.stringify({
        ...skillTargetBody({
          id: skill.scope,
          type: skill.scopeType,
          label: skill.scopeLabel,
          path: '',
          agentId: skill.agentId,
          writable: skill.writable,
        }),
        disabled,
      }),
    },
  )
}

export async function uploadSkillZip(file: File, scope: SkillScope): Promise<SkillInfo> {
  const token = getAccessToken()
  const formData = new FormData()
  formData.append('file', file)
  formData.append('scope', scope.type)
  if (scope.agentId) formData.append('agentId', scope.agentId)

  const res = await fetch(`${API_URL}/api/comworker/skills/upload`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  })
  if (!res.ok) throw new Error(await parseErrorMessage(res))
  return res.json() as Promise<SkillInfo>
}

export async function downloadSkill(skill: SkillInfo): Promise<void> {
  const token = getAccessToken()
  const scope: SkillScope = {
    id: skill.scope,
    type: skill.scopeType,
    label: skill.scopeLabel,
    path: '',
    agentId: skill.agentId,
    writable: skill.writable,
  }
  const res = await fetch(
    `${API_URL}/api/comworker/skills/${encodeURIComponent(skill.name)}/download?${skillTargetQuery(scope)}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  )
  if (!res.ok) throw new Error(await parseErrorMessage(res))
  const blob = await res.blob()
  const blobUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = `${skill.name}.zip`
  link.click()
  URL.revokeObjectURL(blobUrl)
}

export interface InstalledSkills {
  user: string[]
  managed: string[]
}

export async function getInstalledSkills(): Promise<InstalledSkills> {
  const data = await fetchJSON<InstalledSkills>('/api/comworker/skills/installed')
  return { user: data.user || [], managed: data.managed || [] }
}

export async function markSkillInstalled(name: string): Promise<InstalledSkills> {
  const data = await fetchJSON<InstalledSkills>('/api/comworker/skills/installed', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  return { user: data.user || [], managed: data.managed || [] }
}

export async function unmarkSkillInstalled(name: string): Promise<InstalledSkills> {
  const data = await fetchJSON<InstalledSkills>(
    `/api/comworker/skills/installed/${encodeURIComponent(name)}`,
    { method: 'DELETE' },
  )
  return { user: data.user || [], managed: data.managed || [] }
}

export async function listSkillFiles(skill: SkillInfo): Promise<{ skill: SkillInfo; files: SkillFileInfo[] }> {
  const scope: SkillScope = {
    id: skill.scope,
    type: skill.scopeType,
    label: skill.scopeLabel,
    path: '',
    agentId: skill.agentId,
    writable: skill.writable,
  }
  return fetchJSON<{ skill: SkillInfo; files: SkillFileInfo[] }>(
    `/api/comworker/skills/${encodeURIComponent(skill.name)}/files?${skillTargetQuery(scope)}`,
  )
}

export async function getSkillFile(skill: SkillInfo, path: string): Promise<{ path: string; name: string; content: string }> {
  const scope: SkillScope = {
    id: skill.scope,
    type: skill.scopeType,
    label: skill.scopeLabel,
    path: '',
    agentId: skill.agentId,
    writable: skill.writable,
  }
  return fetchJSON<{ path: string; name: string; content: string }>(
    `/api/comworker/skills/${encodeURIComponent(skill.name)}/files/content?${skillTargetQuery(scope)}&path=${encodeURIComponent(path)}`,
  )
}

export async function writeSkillFile(skill: SkillInfo, path: string, content: string): Promise<void> {
  await fetchJSON<unknown>(`/api/comworker/skills/${encodeURIComponent(skill.name)}/files/content`, {
    method: 'PUT',
    body: JSON.stringify({
      ...skillTargetBody({
        id: skill.scope,
        type: skill.scopeType,
        label: skill.scopeLabel,
        path: '',
        agentId: skill.agentId,
        writable: skill.writable,
      }),
      path,
      content,
    }),
  })
}

export async function searchSkills(query: string, limit = 10): Promise<{ results: SkillSearchResult[] }> {
  return fetchJSON<{ results: SkillSearchResult[] }>('/api/comworker/marketplaces/skills/search', {
    method: 'POST',
    body: JSON.stringify({ query, limit }),
  })
}

export async function installSkillFromSearch(slug: string, scope: SkillScope): Promise<{ ok: boolean; output: string; name: string }> {
  return fetchJSON<{ ok: boolean; output: string; name: string }>('/api/comworker/marketplaces/skills/install', {
    method: 'POST',
    body: JSON.stringify({ slug, ...skillTargetBody(scope) }),
  })
}

export async function scanGitSkills(url: string): Promise<GitScanResult> {
  return fetchJSON<GitScanResult>('/api/comworker/marketplaces/git/scan-skills', {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
}

export async function installGitSkills(cacheKey: string, skillNames: string[], scope: SkillScope): Promise<{ ok: boolean; installed: string[]; errors: string[] }> {
  return fetchJSON<{ ok: boolean; installed: string[]; errors: string[] }>('/api/comworker/marketplaces/git/install-skills', {
    method: 'POST',
    body: JSON.stringify({ cacheKey, skillNames, ...skillTargetBody(scope) }),
  })
}

// ---------------------------------------------------------------------------
// Channels
// ---------------------------------------------------------------------------

export interface ChannelAccountSnapshot {
  accountId: string
  name?: string | null
  enabled?: boolean | null
  configured?: boolean | null
  linked?: boolean | null
  running?: boolean | null
  connected?: boolean | null
  reconnectAttempts?: number | null
  lastConnectedAt?: number | null
  lastError?: string | null
  mode?: string
  webhookUrl?: string
  [key: string]: unknown
}

export interface ChannelMetaEntry {
  id: string
  label: string
  detailLabel: string
  systemImage?: string
}

export interface ChannelsStatusResult {
  ts: number
  channelOrder: string[]
  channelLabels: Record<string, string>
  channelDetailLabels?: Record<string, string>
  channelSystemImages?: Record<string, string>
  channelMeta?: ChannelMetaEntry[]
  channels: Record<string, unknown>
  channelAccounts: Record<string, ChannelAccountSnapshot[]>
  channelDefaultAccountId: Record<string, string>
}

export async function getChannelsStatus(probe = false): Promise<ChannelsStatusResult> {
  const params = probe ? '?probe=true' : ''
  return fetchJSON<ChannelsStatusResult>(`/api/comworker/channels/status${params}`)
}

export async function getConfiguredChannels(): Promise<{ success: boolean; channels: string[] }> {
  return fetchJSON<{ success: boolean; channels: string[] }>('/api/comworker/channels/configured')
}

export async function getChannelConfig(channelType: string): Promise<{ config: Record<string, unknown> | null }> {
  return fetchJSON<{ config: Record<string, unknown> | null }>(`/api/comworker/channels/${encodeURIComponent(channelType)}/config`)
}

export async function saveChannelConfig(channelType: string, config: Record<string, unknown>): Promise<{ ok: boolean }> {
  return fetchJSON<{ ok: boolean }>(`/api/comworker/channels/${encodeURIComponent(channelType)}/config`, {
    method: 'PUT',
    body: JSON.stringify(config),
  })
}

export async function deleteChannelConfig(channelType: string): Promise<{ ok: boolean }> {
  return fetchJSON<{ ok: boolean }>(`/api/comworker/channels/${encodeURIComponent(channelType)}/config`, { method: 'DELETE' })
}

export interface PluginInfo {
  name: string
  description: string
  source: string
  version?: string
  installedAt?: string
  enabled?: boolean
  agents: Array<{ name: string; description: string; model: string | null }>
  commands: Array<{ name: string; description: string; argument_hint: string | null }>
  skills: string[]
}

export async function listPlugins(): Promise<PluginInfo[]> {
  return fetchJSON<PluginInfo[]>('/api/comworker/plugins')
}

export async function installPlugin(spec: string): Promise<{ ok: boolean; output: string }> {
  return fetchJSON<{ ok: boolean; output: string }>('/api/comworker/plugins/install', {
    method: 'POST',
    body: JSON.stringify({ spec }),
  })
}

export async function uninstallPlugin(name: string): Promise<{ ok: boolean }> {
  return fetchJSON<{ ok: boolean }>(`/api/comworker/plugins/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
}

export async function logoutChannel(channelType: string, accountId?: string): Promise<Record<string, unknown>> {
  return fetchJSON<Record<string, unknown>>(`/api/comworker/channels/${encodeURIComponent(channelType)}/logout`, {
    method: 'POST',
    body: JSON.stringify({ accountId }),
  })
}

// ---------------------------------------------------------------------------
// System Settings (网关状态 / 容器 / 网关配置 / 维护)
// 原 frontend SystemSettings 页面的后端接口，迁移到客户端 5174 设置页。
// ---------------------------------------------------------------------------

export async function getStatus(): Promise<Record<string, unknown>> {
  return fetchJSON<Record<string, unknown>>('/api/comworker/status')
}

export interface ContainerPort {
  container_port: string
  host_port: string | null
}

export interface ContainerInfo {
  container_name: string | null
  status: string
  docker_id: string | null
  created_at: string | null
  ports?: ContainerPort[]
}

export async function getContainerInfo(): Promise<ContainerInfo> {
  return fetchJSON<ContainerInfo>('/api/comworker/container/info')
}

