import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { useOutletContext } from 'react-router-dom'
import {
  ArrowLeft,
  Check,
  Download,
  FileText,
  GitBranch,
  Inbox,
  Loader2,
  Package,
  Plus,
  RefreshCw,
  Save,
  Search,
  SearchX,
  Sparkles,
  Upload,
  X,
} from 'lucide-react'
import ClearableInput from '../components/ui/ClearableInput.tsx'
import IconButton from '../components/ui/IconButton.tsx'
import { useToast } from '../components/ui/Toast.tsx'
import type { LayoutOutletContext } from '../components/Layout.tsx'
import {
  browseFiles,
  deleteSkill,
  downloadSkill,
  getInstalledSkills,
  getSkillFile,
  installGitSkills,
  listSkillFiles,
  listSkillScopes,
  listSkills,
  markSkillInstalled,
  scanGitSkills,
  setSkillDisabled,
  unmarkSkillInstalled,
  uploadSkillZip,
  writeManagedFile,
  writeSkillFile,
} from '../lib/api.ts'
import type { BrowseFileResult, GitScanResult, SkillFileInfo, SkillInfo, SkillScope } from '../lib/api.ts'

const skillsCacheTtlMs = 30_000
const skillsCacheStorageKey = 'comworker_simple_front_skills_cache_v4'
type SkillsCache = { scopes: SkillScope[]; skills: SkillInfo[]; at: number }
let skillsCache: SkillsCache | null = null

const builtinAgentNames: Record<string, string> = {
  main: '主助手',
  manager: '经理',
  programmer: '程序员',
  researcher: '研究员',
  hr: 'HR',
  doctor: '医生',
}

// Resolve a skill's category. Prefer the backend-provided `category` field
// (dedicated mode). Fall back to deriving it from `path`:
//   - container mode: path is "<category>/<skill>" relative to the skills
//     root (no "skills" segment), so the first segment is the category.
//   - dedicated mode path may be "skills/<category>/<skill>" or
//     "profiles/<agent>/skills/<category>/<skill>": take the segment after
//     the last "skills".
function resolveSkillCategory(skill: SkillInfo): string {
  if (skill.owner === 'user') return '自装技能'
  if (skill.category && skill.category.trim()) return skill.category.trim()
  return '内置技能'
}

function readStoredSkillsCache(): SkillsCache | null {
  if (skillsCache) return skillsCache
  try {
    const raw = sessionStorage.getItem(skillsCacheStorageKey)
    if (!raw) return null
    const parsed = JSON.parse(raw) as SkillsCache | null
    if (!parsed || !Array.isArray(parsed.scopes) || !Array.isArray(parsed.skills) || typeof parsed.at !== 'number') return null
    skillsCache = parsed
    return parsed
  } catch {
    return null
  }
}

function writeSkillsCache(cache: SkillsCache): void {
  skillsCache = cache
  try {
    sessionStorage.setItem(skillsCacheStorageKey, JSON.stringify(cache))
  } catch {
    // Best-effort UI cache only; keep the in-memory cache even if storage is unavailable.
  }
}

function clearSkillsCache(): void {
  skillsCache = null
  try {
    sessionStorage.removeItem(skillsCacheStorageKey)
  } catch {
    // Ignore storage failures.
  }
}

function getFreshSkillsCache(): SkillsCache | null {
  const cache = readStoredSkillsCache()
  if (!cache || Date.now() - cache.at >= skillsCacheTtlMs) return null
  return cache
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function getAgentDisplayName(agent: { id: string; name?: string | null; identity?: { name?: string } } | undefined, agentId: string): string {
  return agent?.identity?.name || agent?.name || builtinAgentNames[agentId] || agentId
}

function legacySkillFilePath(skill: SkillInfo, filePath: string): string | null {
  const normalized = skill.path.replace(/\\/g, '/')
  const marker = '/.comworker/'
  const markerIndex = normalized.toLowerCase().indexOf(marker)
  if (markerIndex < 0) return null
  const skillRoot = normalized.slice(0, normalized.length - 'SKILL.md'.length).replace(/\/+$/, '')
  const target = `${skillRoot}/${filePath}`.replace(/\\/g, '/')
  const relative = target.slice(markerIndex + marker.length)
  return relative || null
}

function SkillSkeleton() {
  return (
    <div className="min-h-0 flex-1 space-y-2 overflow-hidden" aria-hidden="true">
      {Array.from({ length: 7 }).map((_, index) => (
        <div key={index} className="rounded-lg border border-light-border bg-light-card p-3">
          <div className="flex items-center gap-3">
            <span className="skeleton-shimmer h-9 w-9 rounded-lg" />
            <span className="skeleton-shimmer h-4 flex-1 rounded-full" />
            <span className="skeleton-shimmer h-4 w-16 rounded-full" />
          </div>
        </div>
      ))}
    </div>
  )
}

function SkillFileSkeleton() {
  return (
    <div className="min-h-0 flex-1 overflow-hidden" aria-hidden="true">
      <div className="space-y-0">
        {Array.from({ length: 8 }).map((_, index) => (
          <div key={index} className="flex items-center gap-3 border-b border-light-border/70 px-4 py-3">
            <span className="skeleton-shimmer h-5 w-5 shrink-0 rounded-md" />
            <span className="min-w-0 flex-1 space-y-2">
              <span className="skeleton-shimmer block h-3.5 w-4/5 rounded-full" />
              <span className="skeleton-shimmer block h-3 w-16 rounded-full" />
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function gitErrorMessage(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err || '')
  if (/Repository not found|not found/i.test(raw)) {
    return 'Git 仓库不存在或地址不可访问，请检查地址是否正确。例如 .git 后面不要追加多余字符。'
  }
  if (/Failed to clone repo|failed to clone|Could not connect|timed out|unable to access/i.test(raw)) {
    return '无法克隆这个 Git 仓库，请检查仓库地址、网络连接，或改用可访问的 Gitee/内网镜像。'
  }
  return raw || 'Git 仓库扫描失败，请检查地址后重试。'
}

export default function SkillStore() {
  const { agents, openMobileSidebar } = useOutletContext<LayoutOutletContext>()
  const [scopes, setScopes] = useState<SkillScope[]>(() => readStoredSkillsCache()?.scopes || [])
  const [skills, setSkills] = useState<SkillInfo[]>(() => readStoredSkillsCache()?.skills || [])
  const [loading, setLoading] = useState(() => !readStoredSkillsCache())
  const [filterQuery, setFilterQuery] = useState('')
  const [uploading, setUploading] = useState(false)
  const [, setTogglingSkillKey] = useState('')
  const [activeModal, setActiveModal] = useState<'git' | null>(null)
  const [gitUrl, setGitUrl] = useState('')
  const [gitScanning, setGitScanning] = useState(false)
  const [gitScan, setGitScan] = useState<GitScanResult | null>(null)
  const [gitSelected, setGitSelected] = useState<Set<string>>(new Set())
  const [gitInstalling, setGitInstalling] = useState(false)
  const [selectedSkill, setSelectedSkill] = useState<SkillInfo | null>(null)
  const [activeCategory, setActiveCategory] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'store' | 'installed'>('store')
  const [installedSet, setInstalledSet] = useState<Set<string>>(() => new Set())
  const [managedSet, setManagedSet] = useState<Set<string>>(() => new Set())
  const [, setInstallMarking] = useState('')
  const [skillFiles, setSkillFiles] = useState<SkillFileInfo[]>([])
  const [filesLoading, setFilesLoading] = useState(false)
  const [editorFile, setEditorFile] = useState<{ skill: SkillInfo; path: string; name: string; content: string; originalContent: string } | null>(null)
  const [editorLoading, setEditorLoading] = useState(false)
  const [editorSaving, setEditorSaving] = useState(false)
  const uploadRef = useRef<HTMLInputElement>(null)
  const filesRequestSeq = useRef(0)
  const toast = useToast()
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(() => new Set())

  const selectedScope = useMemo(() => scopes.find(s => s.type === 'global') || scopes[0], [scopes])

  // The store tab always lists ALL catalog skills; installed ones are shown
  // with a greyed "已安装" badge (and no install button). The installed tab
  // lists only what the user has installed (user ∪ managed); managed skills
  // are pinned by an admin and cannot be uninstalled from the client.
  const storeSkills = useMemo(
    () => skills.slice().sort((a, b) => a.name.localeCompare(b.name)),
    [skills],
  )
  const installedSkills = useMemo(
    () => skills.filter(skill => installedSet.has(skill.name)).sort((a, b) => a.name.localeCompare(b.name)),
    [skills, installedSet],
  )
  const baseSkills = activeTab === 'installed' ? installedSkills : storeSkills

  // Category filter bar: sorted list of { category, count } derived from the
  // current view, plus the active category selection (null = all).
  const categoryList = useMemo(() => {
    const counts = new Map<string, number>()
    for (const skill of baseSkills) {
      const category = resolveSkillCategory(skill)
      counts.set(category, (counts.get(category) || 0) + 1)
    }
    return [...counts.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([category, count]) => ({ category, count }))
  }, [baseSkills])

  // Page-level filter (like the connectors page): type in the box and the
  // current list is filtered in place. Matches name, title, description and
  // category; combined with the active category chip.
  const visibleSkills = useMemo(() => {
    let list = activeCategory ? baseSkills.filter(skill => resolveSkillCategory(skill) === activeCategory) : baseSkills
    const q = filterQuery.trim().toLowerCase()
    if (q) {
      list = list.filter(skill => {
        const category = resolveSkillCategory(skill)
        return skill.name.toLowerCase().includes(q)
          || (skill.title || '').toLowerCase().includes(q)
          || (skill.description || '').toLowerCase().includes(q)
          || category.toLowerCase().includes(q)
      })
    }
    return list
  }, [baseSkills, activeCategory, filterQuery])
  const canInstallToSelectedScope = Boolean(selectedScope?.writable)
  const editorDirty = editorFile ? editorFile.content !== editorFile.originalContent : false

  const skillKeyOf = (skill: SkillInfo) => `${skill.scope}-${skill.name}`
  // Admin-managed skills (built-in catalog skills + admin-pushed) are owned by
  // the admin console (manage_front /skills). On the client they may only be
  // enabled/disabled — install/uninstall/delete/download/edit are retracted.
  const isAdminManaged = (skill: SkillInfo) => skill.owner === 'admin' || managedSet.has(skill.name)
  const allVisibleSelected = visibleSkills.length > 0 && visibleSkills.every(skill => selectedKeys.has(skillKeyOf(skill)))
  const someVisibleSelected = visibleSkills.some(skill => selectedKeys.has(skillKeyOf(skill)))
  const anyVisibleSelfManaged = visibleSkills.some(skill => selectedKeys.has(skillKeyOf(skill)) && !isAdminManaged(skill))
  const installableSelected = visibleSkills.some(skill => selectedKeys.has(skillKeyOf(skill)) && !installedSet.has(skill.name) && !isAdminManaged(skill))
  const toggleCardSelect = (key: string) => {
    setSelectedKeys(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }
  const toggleSelectAll = () => {
    setSelectedKeys(prev => {
      const selectedAll = visibleSkills.length > 0 && visibleSkills.every(skill => prev.has(skillKeyOf(skill)))
      if (selectedAll) {
        const next = new Set(prev)
        for (const skill of visibleSkills) next.delete(skillKeyOf(skill))
        return next
      }
      const next = new Set(prev)
      for (const skill of visibleSkills) next.add(skillKeyOf(skill))
      return next
    })
  }

  const normalizeSkillScopes = useCallback((items: SkillScope[]): SkillScope[] => {
    return items.map(scope => {
      if (scope.type === 'global') {
        return { ...scope, label: '全局技能' }
      }
      if (scope.type === 'builtin') {
        return { ...scope, label: '内置技能' }
      }
      const agentId = scope.agentId || scope.id.replace(/^agent:/, '')
      const agent = agents.find(item => item.id === agentId)
      return {
        ...scope,
        agentId,
        label: `${getAgentDisplayName(agent, agentId)} 的技能`,
      }
    })
  }, [agents])

  const fallbackScopes = useMemo<SkillScope[]>(() => normalizeSkillScopes([
      {
        id: 'global',
        type: 'global',
        label: '全局技能',
        path: '~/.comworker/skills',
        writable: true,
      },
      {
        id: 'builtin',
        type: 'builtin',
        label: '内置技能',
        path: 'comworker/skills',
        writable: false,
      },
      ...agents
        .filter(agent => agent.id)
        .map(agent => ({
          id: `agent:${agent.id}`,
          type: 'agent' as const,
          agentId: agent.id,
          label: `${getAgentDisplayName(agent, agent.id)} 的技能`,
          path: `profiles/${agent.id}/skills`,
          writable: true,
        })),
    ]),
    [agents, normalizeSkillScopes],
  )

  const normalizeLegacySkill = useCallback((skill: SkillInfo, nextScopes: SkillScope[]): SkillInfo => {
    const source = skill.scopeType || (skill.source === 'builtin' ? 'builtin' : skill.source === 'workspace' ? 'agent' : 'global')
    const scope = nextScopes.find(item => item.id === skill.scope)
      || (source === 'agent'
        ? nextScopes.find(item => item.id === `agent:${skill.agentId || 'main'}`) || nextScopes.find(item => item.type === 'agent')
        : nextScopes.find(item => item.type === source))
    const scopeType = scope?.type || source
    return {
      ...skill,
      scope: scope?.id || skill.scope || source,
      scopeType,
      scopeLabel: scope?.label || (scopeType === 'builtin' ? '内置技能' : scopeType === 'agent' ? '主助手的技能' : '全局技能'),
      agentId: scopeType === 'agent' ? scope?.agentId || skill.agentId || 'main' : undefined,
      writable: scope?.writable ?? scopeType !== 'builtin',
      dirPath: skill.dirPath || skill.path.replace(/[\\/]+SKILL\.md$/, ''),
    }
  }, [])

  const readSkills = useCallback(async (options: { force?: boolean } = {}) => {
    const loadInstalledMarker = async () => {
      try {
        const { user, managed } = await getInstalledSkills()
        setInstalledSet(new Set([...user, ...managed]))
        setManagedSet(new Set(managed))
      } catch {
        // Installed marker is best-effort; ignore failures.
      }
    }

    const freshCache = getFreshSkillsCache()
    if (!options.force && freshCache) {
      setScopes(freshCache.scopes)
      setSkills(freshCache.skills)
      setLoading(false)
      await loadInstalledMarker()
      return
    }

    const cached = readStoredSkillsCache()
    if (!cached) setLoading(true)
    try {
      const [scopeSettled, skillResult] = await Promise.all([
        listSkillScopes().then(
          value => ({ ok: true as const, value }),
          err => ({ ok: false as const, error: err }),
        ),
        listSkills().catch(async () => listSkills(undefined)),
      ])
      const scopeResult = normalizeSkillScopes(scopeSettled.ok ? scopeSettled.value : fallbackScopes)
      const normalizedSkills = skillResult.map(skill => normalizeLegacySkill(skill, scopeResult))
      writeSkillsCache({ scopes: scopeResult, skills: normalizedSkills, at: Date.now() })
      setScopes(scopeResult)
      setSkills(normalizedSkills)
      await loadInstalledMarker()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '加载技能失败')
    } finally {
      setLoading(false)
    }
  }, [fallbackScopes, normalizeLegacySkill, normalizeSkillScopes])

  const refresh = useCallback(async () => {
    clearSkillsCache()
    await readSkills({ force: true })
  }, [readSkills])

  const refreshScopeSkills = useCallback(async (scope: SkillScope): Promise<SkillInfo[]> => {
    const scopeResult = normalizeSkillScopes(scopes.length > 0 ? scopes : fallbackScopes)
    const nextScopeSkills = (await listSkills(scope)).map(skill => normalizeLegacySkill(skill, scopeResult))
    setSkills(current => {
      const nextSkills = [
        ...current.filter(skill => skill.scope !== scope.id),
        ...nextScopeSkills,
      ]
      writeSkillsCache({
        scopes: scopeResult,
        skills: nextSkills,
        at: Date.now(),
      })
      return nextSkills
    })
    return nextScopeSkills
  }, [fallbackScopes, normalizeLegacySkill, normalizeSkillScopes, scopes])

  useEffect(() => {
    void readSkills()
  }, [readSkills])

  useEffect(() => {
    setSelectedKeys(new Set())
  }, [activeTab, activeCategory])

  const showErrorMessage = useCallback((text: string) => toast.error(text), [toast])

  const refreshSkillFiles = useCallback(async (skill: SkillInfo) => {
    const requestId = ++filesRequestSeq.current
    setFilesLoading(true)
    try {
      const result = await listSkillFiles(skill)
      if (requestId !== filesRequestSeq.current) return
      setSkillFiles(result.files)
    } catch (err) {
      if (requestId !== filesRequestSeq.current) return
      setSkillFiles([])
      toast.error(err instanceof Error ? err.message : '加载技能文件失败')
    } finally {
      if (requestId === filesRequestSeq.current) setFilesLoading(false)
    }
  }, [])

  const selectSkill = (skill: SkillInfo) => {
    // Clicking the already-selected skill (or its panel close button) toggles
    // the selection off so the user can dismiss the right-hand file panel.
    const isSame = selectedSkill?.scope === skill.scope && selectedSkill?.name === skill.name
    if (isSame) {
      filesRequestSeq.current += 1
      setSelectedSkill(null)
      setSkillFiles([])
      setEditorFile(null)
      setFilesLoading(false)
      return
    }
    setSelectedSkill(skill)
    setSkillFiles([])
    setEditorFile(null)
    void refreshSkillFiles(skill)
  }

  const handleUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file || !selectedScope) return
    setUploading(true)
    try {
      await uploadSkillZip(file, selectedScope)
      await refreshScopeSkills(selectedScope)
      toast.success(`已上传 ${file.name}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploading(false)
      if (uploadRef.current) uploadRef.current.value = ''
    }
  }

  const handleGitScan = async () => {
    if (!gitUrl.trim()) return
    setGitScanning(true)
    setGitScan(null)
    setGitSelected(new Set())
    try {
      const result = await scanGitSkills(gitUrl.trim())
      setGitScan(result)
      setGitSelected(new Set(result.skills.map(skill => skill.name)))
    } catch (err) {
      showErrorMessage(gitErrorMessage(err))
    } finally {
      setGitScanning(false)
    }
  }

  const handleDeleteSkill = async (skill: SkillInfo) => {
    if (!selectedScope) return
    const targetScope = selectedScope
    const deletingSelected = selectedSkill?.scope === skill.scope && selectedSkill.name === skill.name
    try {
      await deleteSkill(skill)
      clearSkillsCache()
      if (deletingSelected) {
        filesRequestSeq.current += 1
        setSelectedSkill(null)
        setSkillFiles([])
        setFilesLoading(false)
      }
      if (editorFile?.skill.scope === skill.scope && editorFile.skill.name === skill.name) {
        setEditorFile(null)
      }
      const nextSkills = await refreshScopeSkills(targetScope)
      toast.success(`已删除 ${skill.name}`)
      if (deletingSelected) {
        const nextSkill = [...nextSkills].sort((a, b) => a.name.localeCompare(b.name))[0] || null
        if (!nextSkill) return
        selectSkill(nextSkill)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const handleToggleSkill = async (skill: SkillInfo, nextEnabled: boolean) => {
    const key = `${skill.scope}:${skill.name}`
    setTogglingSkillKey(key)
    try {
      await setSkillDisabled(skill, !nextEnabled)
      setSkills(current => {
        const nextSkills = current.map(item => item.scope === skill.scope && item.name === skill.name
          ? { ...item, disabled: !nextEnabled }
          : item)
        writeSkillsCache({ scopes, skills: nextSkills, at: Date.now() })
        return nextSkills
      })
      setSelectedSkill(current => current && current.scope === skill.scope && current.name === skill.name
        ? { ...current, disabled: !nextEnabled }
        : current)
      toast.success(nextEnabled ? `已启用 ${skill.name}` : `已停用 ${skill.name}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '更新技能状态失败')
    } finally {
      setTogglingSkillKey('')
    }
  }

  const openInstallModal = (modal: 'git') => {
    if (!canInstallToSelectedScope) return
    setActiveModal(modal)
  }

  const handleInstallSkill = async (skill: SkillInfo) => {
    const key = skill.name
    setInstallMarking(key)
    try {
      const installed = await markSkillInstalled(skill.name)
      setInstalledSet(new Set([...installed.user, ...installed.managed]))
      setManagedSet(new Set(installed.managed))
      toast.success(`已安装 ${skill.title || skill.name}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '安装失败')
    } finally {
      setInstallMarking('')
    }
  }

  const handleUninstallSkill = async (skill: SkillInfo) => {
    const key = skill.name
    setInstallMarking(key)
    try {
      const installed = await unmarkSkillInstalled(skill.name)
      setInstalledSet(new Set([...installed.user, ...installed.managed]))
      setManagedSet(new Set(installed.managed))
      toast.success(`已卸载 ${skill.title || skill.name}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '卸载失败')
    } finally {
      setInstallMarking('')
    }
  }

  const installSelected = async () => {
    if (!selectedScope) return
    const targets = visibleSkills.filter(skill => selectedKeys.has(skillKeyOf(skill)) && !installedSet.has(skill.name) && !isAdminManaged(skill))
    if (targets.length === 0) return
    for (const skill of targets) {
      await handleInstallSkill(skill)
    }
    toast.success(`已安装 ${targets.length} 个技能`)
  }

  const enableSelected = async (nextEnabled: boolean) => {
    const targets = visibleSkills.filter(skill => selectedKeys.has(skillKeyOf(skill)))
    for (const skill of targets) {
      if (skill.disabled === !nextEnabled) await handleToggleSkill(skill, nextEnabled)
    }
    toast.success(`已${nextEnabled ? '启用' : '停用'} ${targets.length} 个技能`)
  }

  const uninstallSelected = async () => {
    const targets = visibleSkills.filter(skill => selectedKeys.has(skillKeyOf(skill)) && !isAdminManaged(skill))
    if (targets.length === 0) return
    for (const skill of targets) {
      await handleUninstallSkill(skill)
    }
    setSelectedKeys(new Set())
  }

  const downloadSelected = async () => {
    const targets = visibleSkills.filter(skill => selectedKeys.has(skillKeyOf(skill)) && !isAdminManaged(skill))
    if (targets.length === 0) return
    for (const skill of targets) {
      await downloadSkill(skill)
    }
    toast.success(`已下载 ${targets.length} 个技能`)
  }

  const deleteSelected = async () => {
    const targets = visibleSkills.filter(skill => selectedKeys.has(skillKeyOf(skill)) && skill.writable && !isAdminManaged(skill))
    if (targets.length === 0) return
  for (const skill of targets) {
      await handleDeleteSkill(skill)
    }
    setSelectedKeys(new Set())
  }

  const handleEditFile = async (file: SkillFileInfo) => {
    if (!selectedSkill || !file.editable || isAdminManaged(selectedSkill)) return
    setEditorLoading(true)
    try {
      let result: { path: string; name: string; content: string }
      try {
        result = await getSkillFile(selectedSkill, file.path)
      } catch (err) {
        const legacyPath = legacySkillFilePath(selectedSkill, file.path)
        if (!legacyPath) throw err
        const legacy = await browseFiles(legacyPath) as BrowseFileResult
        if (legacy.type !== 'file' || legacy.content === undefined) throw err
        result = { path: file.path, name: legacy.name, content: legacy.content }
      }
      setEditorFile({
        skill: selectedSkill,
        path: result.path,
        name: result.name,
        content: result.content,
        originalContent: result.content,
      })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '读取文件失败')
    } finally {
      setEditorLoading(false)
    }
  }

  const handleSaveEditor = useCallback(async () => {
    if (!editorFile || editorSaving || isAdminManaged(editorFile.skill)) return
    setEditorSaving(true)
    try {
      try {
        await writeSkillFile(editorFile.skill, editorFile.path, editorFile.content)
      } catch (err) {
        const legacyPath = legacySkillFilePath(editorFile.skill, editorFile.path)
        if (!legacyPath) throw err
        await writeManagedFile(legacyPath, editorFile.content)
      }
      setEditorFile(current => current ? { ...current, originalContent: current.content } : current)
      toast.success(`已保存 ${editorFile.name}`)
      await refreshSkillFiles(editorFile.skill)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setEditorSaving(false)
    }
  }, [editorFile, editorSaving, refreshSkillFiles])

  useEffect(() => {
    if (!editorFile) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        void handleSaveEditor()
      }
      if (event.key === 'Escape') setEditorFile(null)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [editorFile, handleSaveEditor])

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-light-bg">
      <div className="flex min-h-0 flex-1 flex-col px-4 py-5 sm:px-5 lg:px-6">
        <header className="mb-4 flex shrink-0 flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <button
              type="button"
              onClick={openMobileSidebar}
              className="mb-3 inline-flex cursor-pointer items-center gap-2 rounded-xl border border-light-border bg-light-card px-3 py-2 text-sm text-light-text-secondary shadow-sm transition-colors hover:bg-light-card-hover hover:text-light-text lg:hidden"
            >
              <ArrowLeft size={16} />
              菜单
            </button>
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-purple/10 text-accent-purple">
                <Sparkles size={22} />
              </span>
              <div>
                <h1 className="text-2xl font-bold leading-tight tracking-normal text-light-text sm:text-[28px]">技能</h1>
                <p className="mt-1 text-sm text-light-text-secondary">管理已安装技能，并从仓库安装、导入新的技能</p>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <IconButton label="刷新技能" onClick={() => void refresh()} tone="primary" className="border border-light-border bg-light-card shadow-sm">
              <RefreshCw size={17} />
            </IconButton>
            <button
              type="button"
              onClick={() => openInstallModal('git')}
              disabled={!canInstallToSelectedScope}
              title={canInstallToSelectedScope ? '从 Git 仓库导入' : '内置技能不支持导入'}
              className="inline-flex cursor-pointer items-center gap-2 rounded-xl border border-light-border bg-light-card px-4 py-2.5 text-sm font-medium text-light-text shadow-sm transition-colors hover:bg-light-card-hover disabled:cursor-not-allowed disabled:opacity-60"
            >
              <GitBranch size={17} />
              Git 导入
            </button>
            <label
              title={canInstallToSelectedScope ? '上传技能' : '内置技能不支持上传'}
              className={`inline-flex cursor-pointer items-center gap-2 rounded-xl bg-accent-blue px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-cyan-700 ${uploading || !canInstallToSelectedScope ? 'pointer-events-none opacity-60' : ''}`}
            >
              {uploading ? <Loader2 size={17} className="animate-spin" /> : <Upload size={17} />}
              上传技能
              <input ref={uploadRef} type="file" accept=".zip" className="hidden" onChange={handleUpload} disabled={!canInstallToSelectedScope} />
            </label>
          </div>
        </header>

        <div className={`grid min-h-0 flex-1 gap-4 overflow-hidden ${selectedSkill ? 'xl:grid-cols-[minmax(0,1fr)_360px]' : ''}`}>
          <main className="flex min-h-0 min-w-0 flex-col overflow-hidden">
            <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-light-border bg-light-card p-4 shadow-sm shadow-slate-200/40">
              <div className="mb-3 flex shrink-0 flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => { setActiveTab('store'); setActiveCategory(null) }}
                      className={`rounded-lg px-3 py-1 text-sm font-semibold transition-colors ${
                        activeTab === 'store'
                          ? 'bg-accent-blue text-white'
                          : 'text-light-text-secondary hover:bg-slate-100'
                      }`}
                    >
                      技能仓库
                    </button>
                    <button
                      type="button"
                      onClick={() => { setActiveTab('installed'); setActiveCategory(null) }}
                      className={`rounded-lg px-3 py-1 text-sm font-semibold transition-colors ${
                        activeTab === 'installed'
                          ? 'bg-accent-blue text-white'
                          : 'text-light-text-secondary hover:bg-slate-100'
                      }`}
                    >
                      已安装 ({installedSet.size})
                    </button>
                    <div className="relative ml-auto flex shrink-0 items-center">
                      <Search
                        className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-light-text-secondary"
                      />
                      <input
                        type="text"
                        value={filterQuery}
                        onChange={(e) => setFilterQuery(e.target.value)}
                        placeholder="搜索技能名称或描述"
                        className="h-8 w-44 rounded-lg border border-light-border bg-white pl-8 pr-3 text-sm text-light-text placeholder:text-light-text-secondary focus:border-accent-blue focus:outline-none sm:w-56"
                      />
                    </div>
                  </div>
                  <p className="mt-1 text-xs text-light-text-secondary">
                    {activeTab === 'installed'
                      ? '你已安装的技能（系统安装的不可卸载），可在此卸载或停用。'
                      : '浏览全部技能，已安装的显示为灰色「已安装」，未安装的点击「安装」。'}
                  </p>
                </div>
              </div>

              <div className="mb-3 flex shrink-0 items-center gap-2 overflow-x-auto pb-1">
                <button
                  type="button"
                  onClick={() => setActiveCategory(null)}
                  className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                    activeCategory === null
                      ? 'bg-accent-blue text-white'
                      : 'bg-slate-100 text-light-text-secondary hover:bg-slate-200'
                  }`}
                >
                  全部 {baseSkills.length}
                </button>
                {categoryList.map(({ category, count }) => (
                  <button
                    key={category}
                    type="button"
                    onClick={() => setActiveCategory(category)}
                    className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                      activeCategory === category
                        ? 'bg-accent-blue text-white'
                        : 'bg-slate-100 text-light-text-secondary hover:bg-slate-200'
                    }`}
                  >
                    {category} {count}
                  </button>
                ))}
              </div>

              <div className="mb-3 flex shrink-0 flex-wrap items-center gap-2">
                <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-light-text-secondary">
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-light-border accent-accent-blue"
                    checked={allVisibleSelected}
                    ref={el => { if (el) el.indeterminate = !allVisibleSelected && someVisibleSelected }}
                    onChange={() => toggleSelectAll()}
                    disabled={visibleSkills.length === 0}
                  />
                  全选
                </label>
                {selectedKeys.size > 0 && (
                  <button
                    type="button"
                    onClick={() => setSelectedKeys(new Set())}
                    className="text-xs text-light-text-secondary underline-offset-2 hover:text-light-text hover:underline"
                  >
                    取消选择 ({selectedKeys.size})
                  </button>
                )}
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  {activeTab === 'store' ? (
                    <button
                      type="button"
                      disabled={selectedKeys.size === 0 || !installableSelected}
                      onClick={() => void installSelected()}
                      title={!installableSelected ? '管理端管控的技能只能启用/禁用' : ''}
                      className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-accent-blue/30 bg-accent-blue/10 px-3 py-1.5 text-xs font-medium text-accent-blue transition-colors hover:bg-accent-blue hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <Plus size={13} /> 安装选中
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        disabled={selectedKeys.size === 0}
                        onClick={() => void enableSelected(true)}
                        className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-light-border bg-light-card px-3 py-1.5 text-xs font-medium text-light-text transition-colors hover:bg-light-card-hover disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        启用选中
                      </button>
                      <button
                        type="button"
                        disabled={selectedKeys.size === 0}
                        onClick={() => void enableSelected(false)}
                        className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-light-border bg-light-card px-3 py-1.5 text-xs font-medium text-light-text transition-colors hover:bg-light-card-hover disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        停用选中
                      </button>
                      <button
                        type="button"
                        disabled={selectedKeys.size === 0 || !anyVisibleSelfManaged}
                        onClick={() => void downloadSelected()}
                        title={!anyVisibleSelfManaged ? '管理端管控的技能只能启用/禁用' : ''}
                        className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-light-border bg-light-card px-3 py-1.5 text-xs font-medium text-light-text transition-colors hover:bg-light-card-hover disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        下载选中
                      </button>
                      <button
                        type="button"
                        disabled={selectedKeys.size === 0 || !anyVisibleSelfManaged}
                        onClick={() => void uninstallSelected()}
                        title={!anyVisibleSelfManaged ? '管理端管控的技能只能启用/禁用' : ''}
                        className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-accent-red/30 bg-accent-red/5 px-3 py-1.5 text-xs font-medium text-accent-red transition-colors hover:bg-accent-red/10 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        卸载选中
                      </button>
                      <button
                        type="button"
                        disabled={selectedKeys.size === 0 || !anyVisibleSelfManaged}
                        onClick={() => void deleteSelected()}
                        title={!anyVisibleSelfManaged ? '管理端管控的技能只能启用/禁用' : ''}
                        className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-accent-red/40 bg-accent-red/10 px-3 py-1.5 text-xs font-medium text-accent-red transition-colors hover:bg-accent-red disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        删除选中
                      </button>
                    </>
                  )}
                </div>
              </div>

              {loading ? (
                <SkillSkeleton />
              ) : baseSkills.length === 0 ? (
                <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2" aria-label="暂无技能">
                  <Inbox size={72} strokeWidth={1.5} className="text-light-text-secondary/45" aria-hidden="true" />
                  <p className="text-sm text-light-text-secondary">
                    {activeTab === 'installed' ? '还没有已安装的技能，去「技能仓库」安装吧。' : '当前分类下没有可安装的技能。'}
                  </p>
                </div>
              ) : visibleSkills.length === 0 ? (
                <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2" aria-label="无匹配技能">
                  <SearchX size={72} strokeWidth={1.5} className="text-light-text-secondary/45" aria-hidden="true" />
                  <p className="text-sm text-light-text-secondary">
                    {filterQuery.trim() ? `没有匹配「${filterQuery.trim()}」的技能。` : '当前分类下没有可安装的技能。'}
                  </p>
                </div>
              ) : (
                <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                  <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
                    {visibleSkills.map(skill => (
                      <div
                        key={`${skill.scope}-${skill.name}`}
                        role="button"
                        tabIndex={0}
                        onClick={() => selectSkill(skill)}
                        onKeyDown={event => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            selectSkill(skill)
                          }
                        }}
                        className={`group relative cursor-pointer rounded-lg border p-4 text-left transition-colors ${
                          selectedSkill?.scope === skill.scope && selectedSkill?.name === skill.name
                            ? 'border-accent-blue bg-accent-blue/5'
                            : 'border-light-border bg-light-card hover:border-accent-blue/40 hover:bg-light-card-hover/60'
                        }`}
                      >
                        <div className="flex items-start gap-3">
                          <input
                            type="checkbox"
                            checked={selectedKeys.has(skillKeyOf(skill))}
                            onClick={event => event.stopPropagation()}
                            onChange={() => toggleCardSelect(skillKeyOf(skill))}
                            className="mt-1 h-4 w-4 shrink-0 rounded border-light-border accent-accent-blue"
                            aria-label={`选择 ${skill.title || skill.name}`}
                          />
                          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-purple/10 text-accent-purple">
                            <Package size={18} />
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-start justify-between gap-2">
                              <span className="flex min-w-0 items-center gap-2">
                                <span className="block truncate text-sm font-semibold text-light-text">{skill.title || skill.name}</span>
                                {activeTab === 'installed' && skill.disabled && <span className="shrink-0 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-light-text-secondary">已停用</span>}
                              </span>
                              <div className="flex shrink-0 items-center gap-1">
                                {activeTab === 'store' ? (
                                  installedSet.has(skill.name) ? (
                                    <span className="inline-flex items-center rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-light-text-secondary">已安装</span>
                                  ) : null
                                ) : (
                                  <>
                                    {skill.disabled && (
                                      <span className="inline-flex items-center rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-light-text-secondary">已停用</span>
                                    )}
                                    {managedSet.has(skill.name) && (
                                      <span className="inline-flex h-7 items-center justify-center gap-1.5 rounded-lg border border-light-border bg-slate-50 px-3 text-xs font-medium text-light-text-secondary">
                                        系统安装
                                      </span>
                                    )}
                                    {isAdminManaged(skill) && !managedSet.has(skill.name) && (
                                      <span className="inline-flex h-7 items-center justify-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-3 text-xs font-medium text-amber-700">
                                        管理端管控
                                      </span>
                                    )}
                                  </>
                                )}
                              </div>
                            </div>
                            <p className="mt-1.5 line-clamp-3 text-xs leading-5 text-light-text-secondary">{skill.description || '暂无描述'}</p>
                          </div>
                        </div>
                        
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </section>
          </main>

          {selectedSkill && (
          <aside className="flex min-h-0 min-w-0 flex-col overflow-hidden">
            <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-light-border bg-light-card shadow-sm shadow-slate-200/40">
              <div className="border-b border-light-border px-4 py-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="truncate text-base font-semibold text-light-text">{selectedSkill.name}</h2>
                    <p className="mt-0.5 text-xs text-light-text-secondary">{selectedSkill.scopeLabel}</p>
                  </div>
                  <IconButton
                    label="取消选择"
                    onClick={() => selectSkill(selectedSkill)}
                    className="shrink-0 border border-light-border"
                  >
                    <X size={16} />
                  </IconButton>
                </div>
              </div>
              {filesLoading ? (
                <SkillFileSkeleton />
              ) : (
                <div className="min-h-0 flex-1 overflow-y-auto">
                  {skillFiles.map(file => (
                    <button
                      key={file.path}
                      type="button"
                      onClick={() => void handleEditFile(file)}
                      disabled={!file.editable || (selectedSkill ? isAdminManaged(selectedSkill) : false)}
                      className="flex w-full cursor-pointer items-center gap-3 border-b border-light-border/70 px-4 py-3 text-left transition-colors last:border-b-0 hover:bg-light-card-hover/70 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <FileText size={17} className="shrink-0 text-accent-blue" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-light-text">{file.path}</span>
                        <span className="block text-xs text-light-text-secondary">{formatSize(file.size)}</span>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </aside>
          )}
        </div>
      </div>

      {activeModal === 'git' && (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-3 py-4 sm:px-6 sm:py-6">
          <button type="button" className="absolute inset-0 cursor-default bg-slate-950/55 backdrop-blur-[2px]" aria-label="关闭 Git 导入" onClick={() => setActiveModal(null)} />
          <section role="dialog" aria-modal="true" aria-label="从 Git 仓库导入" className="relative flex h-[min(78vh,720px)] w-full max-w-[min(760px,calc(100vw-2rem))] flex-col overflow-hidden rounded-lg border border-light-border bg-light-card shadow-2xl shadow-slate-950/25">
            <header className="flex min-h-14 items-center justify-between gap-3 border-b border-light-border px-4 py-3">
              <div className="min-w-0">
                <h2 className="truncate text-base font-semibold text-light-text">从 Git 仓库导入</h2>
              </div>
              <IconButton label="关闭 Git 导入" onClick={() => setActiveModal(null)} className="border border-light-border">
                <X size={17} />
              </IconButton>
            </header>
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-4">
              {!canInstallToSelectedScope && (
                <div className="mb-3 shrink-0 rounded-lg border border-accent-yellow/25 bg-accent-yellow/10 px-3 py-2 text-sm text-amber-700">
                  当前是只读的内置技能位置，不支持导入。
                </div>
              )}
              <div className="flex shrink-0 flex-col gap-2 md:flex-row">
                <ClearableInput
                  value={gitUrl}
                  onValueChange={setGitUrl}
                  clearLabel="清空 Git 地址"
                  placeholder="https://github.com/user/repo.git"
                  className="min-w-0 flex-1 rounded-xl border border-light-border bg-light-card px-3 py-2 text-sm outline-none focus:border-accent-blue"
                />
                <button
                  type="button"
                  onClick={() => void handleGitScan()}
                  disabled={!canInstallToSelectedScope || gitScanning || !gitUrl.trim()}
                  className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-light-border px-4 py-2 text-sm font-medium text-light-text transition-colors hover:bg-light-card-hover disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {gitScanning ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
                  扫描
                </button>
              </div>
              <div className="mt-4 min-h-0 flex-1 overflow-hidden rounded-lg border border-light-border">
                {gitScanning ? (
                  <div className="flex h-full min-h-0 flex-col p-3">
                    <SkillSkeleton />
                  </div>
                ) : gitScan ? (
                  <div className="flex h-full min-h-0 flex-col">
                    <div className="flex shrink-0 flex-col gap-2 border-b border-light-border px-3 py-2 text-sm sm:flex-row sm:items-center sm:justify-between">
                      <label className="inline-flex min-w-0 cursor-pointer items-center gap-3 text-light-text-secondary">
                        <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border transition-colors ${
                          gitSelected.size === gitScan.skills.length && gitScan.skills.length > 0
                            ? 'border-accent-blue bg-accent-blue text-white'
                            : gitSelected.size > 0
                              ? 'border-accent-blue bg-accent-blue/10 text-accent-blue'
                              : 'border-light-border bg-light-card text-transparent'
                        }`}>
                          {gitSelected.size === gitScan.skills.length && gitScan.skills.length > 0 ? <Check size={12} /> : gitSelected.size > 0 ? <span className="h-0.5 w-2.5 rounded-full bg-current" /> : null}
                        </span>
                        <input
                          type="checkbox"
                          className="sr-only"
                          checked={gitSelected.size === gitScan.skills.length && gitScan.skills.length > 0}
                          disabled={gitInstalling || gitScan.skills.length === 0}
                          onChange={event => {
                            setGitSelected(event.target.checked ? new Set(gitScan.skills.map(skill => skill.name)) : new Set())
                          }}
                        />
                        <span className="truncate">发现 {gitScan.skills.length} 个技能</span>
                      </label>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          disabled={!canInstallToSelectedScope || gitInstalling || gitSelected.size === 0}
                          onClick={async () => {
                            if (!selectedScope || !gitScan) return
                            const targetScope = selectedScope
                            setGitInstalling(true)
                            try {
                              const result = await installGitSkills(gitScan.cacheKey, Array.from(gitSelected), targetScope)
                              if (result.errors.length > 0) showErrorMessage(`部分技能导入失败：${result.errors.join('；')}`)
                              await refreshScopeSkills(targetScope)
                              if (result.installed.length > 0) {
                                for (const name of result.installed) {
                                  try {
                                    const installed = await markSkillInstalled(name)
                                    setInstalledSet(new Set([...installed.user, ...installed.managed]))
                                    setManagedSet(new Set(installed.managed))
                                  } catch {
                                    // best-effort marker
                                  }
                                }
                                toast.success(`已安装 ${result.installed.length} 个技能到 ${targetScope.label}`)
                              }
                            } catch (err) {
                              showErrorMessage(err instanceof Error ? err.message : '导入失败，请稍后重试。')
                            } finally {
                              setGitInstalling(false)
                            }
                          }}
                          className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-accent-blue px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-cyan-700 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {gitInstalling ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                          安装选中
                        </button>
                      </div>
                    </div>
                    <div className="min-h-0 flex-1 overflow-y-auto">
                      {gitScan.skills.map(skill => (
                        <button
                          key={skill.name}
                          type="button"
                          onClick={() => setGitSelected(prev => {
                            const next = new Set(prev)
                            if (next.has(skill.name)) next.delete(skill.name)
                            else next.add(skill.name)
                            return next
                          })}
                          className="flex w-full cursor-pointer items-center gap-3 border-b border-light-border/70 px-3 py-2 text-left last:border-b-0 hover:bg-light-card-hover/70"
                        >
                          <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${gitSelected.has(skill.name) ? 'border-accent-blue bg-accent-blue text-white' : 'border-light-border'}`}>
                            {gitSelected.has(skill.name) && <Check size={12} />}
                          </span>
                          <span className="min-w-0">
                            <span className="block truncate text-sm text-light-text">{skill.name}</span>
                            <span className="block truncate text-xs text-light-text-secondary">{skill.description || skill.relativePath}</span>
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="flex h-full min-h-0 items-center justify-center" aria-label="暂无 Git 扫描结果">
                    <Inbox size={64} strokeWidth={1.5} className="text-light-text-secondary/45" aria-hidden="true" />
                  </div>
                )}
              </div>
            </div>
          </section>
        </div>
      )}

      {(editorFile || editorLoading) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-3 py-4 sm:px-6 sm:py-6">
          <button type="button" className="absolute inset-0 cursor-default bg-slate-950/55 backdrop-blur-[2px]" aria-label="关闭技能编辑器" onClick={() => setEditorFile(null)} />
          <section role="dialog" aria-modal="true" aria-label="编辑技能文件" className="relative flex h-[min(88vh,900px)] w-full max-w-[min(1440px,calc(100vw-2rem))] flex-col overflow-hidden rounded-lg border border-light-border bg-light-card shadow-2xl shadow-slate-950/25">
            <header className="flex min-h-14 items-center justify-between gap-3 border-b border-light-border px-4 py-3">
              <div className="min-w-0">
                <h2 className="truncate text-base font-semibold text-light-text">{editorFile?.name || '正在读取文件'}</h2>
                <p className="mt-0.5 truncate text-xs text-light-text-secondary">{editorFile?.path || '请稍候'}</p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {editorDirty && <span className="rounded-full bg-accent-yellow/10 px-2 py-1 text-xs font-medium text-amber-700">未保存</span>}
                <button type="button" disabled={!editorFile || editorSaving || !editorFile.skill.writable || isAdminManaged(editorFile.skill)} onClick={() => void handleSaveEditor()} className="inline-flex cursor-pointer items-center gap-2 rounded-xl bg-accent-blue px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-cyan-700 disabled:cursor-not-allowed disabled:opacity-60">
                  {editorSaving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                  保存
                </button>
                <IconButton label="关闭编辑器" onClick={() => setEditorFile(null)} className="border border-light-border">
                  <X size={17} />
                </IconButton>
              </div>
            </header>
            {editorLoading ? (
              <div className="flex flex-1 items-center justify-center gap-2 text-sm text-light-text-secondary">
                <Loader2 size={18} className="animate-spin text-accent-blue" />
                正在读取文件内容
              </div>
            ) : (
              <textarea
                value={editorFile?.content || ''}
                onChange={event => {
                  const value = event.target.value
                  setEditorFile(current => current ? { ...current, content: value } : current)
                }}
                spellCheck={false}
                autoFocus
                className="min-h-0 flex-1 resize-none border-0 bg-light-card px-4 py-4 font-mono text-sm leading-6 text-light-text outline-none"
              />
            )}
            <footer className="flex min-h-10 items-center justify-between gap-3 border-t border-light-border px-4 py-2 text-xs text-light-text-secondary">
              <span className="truncate">Ctrl+S 保存到技能目录</span>
            </footer>
          </section>
        </div>
      )}
    </div>
  )
}
