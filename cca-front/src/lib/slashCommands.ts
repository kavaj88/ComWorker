import type { SlashCommandInfo } from './api'

export type SlashCommandCategory =
  | 'status'
  | 'session'
  | 'management'
  | 'options'
  | 'tools'
  | 'media'
  | 'skills'
  | 'docks'
  | 'other'

export interface SlashCommandItem {
  name: string
  description: string
  argsHint?: string
  category: SlashCommandCategory
  scope: 'text' | 'native' | 'both'
  source: 'builtin' | 'skill'
  aliases?: string[]
  skillName?: string | null
}

const CATEGORY_ORDER: SlashCommandCategory[] = [
  'status',
  'session',
  'management',
  'options',
  'tools',
  'media',
  'skills',
  'docks',
  'other',
]

export const CATEGORY_LABELS: Record<SlashCommandCategory, string> = {
  status: '状态',
  session: '会话',
  management: '管理',
  options: '选项',
  tools: '工具',
  media: '媒体',
  skills: '技能',
  docks: '面板',
  other: '其他',
}

export const CATEGORY_STYLES: Record<SlashCommandCategory, {
  header: string
  active: string
  command: string
  badge: string
}> = {
  status: {
    header: 'text-emerald-700',
    active: 'bg-emerald-50',
    command: 'text-emerald-700',
    badge: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  },
  session: {
    header: 'text-cyan-700',
    active: 'bg-cyan-50',
    command: 'text-cyan-700',
    badge: 'bg-cyan-50 text-cyan-700 border-cyan-100',
  },
  management: {
    header: 'text-rose-700',
    active: 'bg-rose-50',
    command: 'text-rose-700',
    badge: 'bg-rose-50 text-rose-700 border-rose-100',
  },
  options: {
    header: 'text-amber-700',
    active: 'bg-amber-50',
    command: 'text-amber-700',
    badge: 'bg-amber-50 text-amber-700 border-amber-100',
  },
  tools: {
    header: 'text-blue-700',
    active: 'bg-blue-50',
    command: 'text-blue-700',
    badge: 'bg-blue-50 text-blue-700 border-blue-100',
  },
  media: {
    header: 'text-pink-700',
    active: 'bg-pink-50',
    command: 'text-pink-700',
    badge: 'bg-pink-50 text-pink-700 border-pink-100',
  },
  skills: {
    header: 'text-violet-700',
    active: 'bg-violet-50',
    command: 'text-violet-700',
    badge: 'bg-violet-50 text-violet-700 border-violet-100',
  },
  docks: {
    header: 'text-indigo-700',
    active: 'bg-indigo-50',
    command: 'text-indigo-700',
    badge: 'bg-indigo-50 text-indigo-700 border-indigo-100',
  },
  other: {
    header: 'text-slate-600',
    active: 'bg-slate-50',
    command: 'text-slate-700',
    badge: 'bg-slate-50 text-slate-700 border-slate-100',
  },
}

const COMMAND_LABELS: Record<string, { description: string; argsHint?: string }> = {
  sessions: { description: '浏览并恢复历史会话' },
  sethome: { description: '将当前对话设为首页频道' },
  goal: { description: '设置长期目标，Hermes 会跨轮次持续处理直到完成', argsHint: '[内容 | 暂停 | 恢复 | 清除 | 状态]' },
  title: { description: '设置当前会话标题', argsHint: '[名称]' },
  status: { description: '显示当前会话信息' },
  new: { description: '开始一个新会话', argsHint: '[名称]' },
  branch: { description: '从当前会话创建分支，探索另一条路径', argsHint: '[名称]' },
  stop: { description: '终止所有正在运行的后台进程' },
  topic: { description: '启用或查看 Telegram 私信主题会话', argsHint: '[关闭 | 帮助 | 会话 ID]' },
  resume: { description: '恢复一个已命名的历史会话', argsHint: '[名称]' },
  retry: { description: '重试上一条消息' },
  undo: { description: '移除最近一轮用户和助手消息' },
  steer: { description: '在下一次工具调用后注入提示，不中断当前任务', argsHint: '<提示词>' },
  agents: { description: '显示活跃 Agent 和运行中的任务' },
  compress: { description: '手动压缩对话上下文', argsHint: '[关注主题]' },
  rollback: { description: '列出或恢复文件系统检查点', argsHint: '[编号]' },
  queue: { description: '排队一条下一轮执行的提示，不中断当前任务', argsHint: '<提示词>' },
  restart: { description: '等待当前任务结束后重启网关' },
  approve: { description: '批准一个等待中的高风险命令', argsHint: '[本会话 | 始终]' },
  deny: { description: '拒绝一个等待中的高风险命令' },
  background: { description: '在后台运行一条提示', argsHint: '<提示词>' },
  subgoal: { description: '为当前目标添加或管理额外验收条件', argsHint: '[内容 | 移除 N | 清除]' },
}

export function buildSlashCommandItems(commands: SlashCommandInfo[]): SlashCommandItem[] {
  return [...commands]
    .map((command) => {
      const label = COMMAND_LABELS[command.name]
      return {
        name: command.name,
        description: label?.description || localizeSkillDescription(command.description, command.skill_name || command.name),
        argsHint: label?.argsHint || command.argument_hint || undefined,
        category: normalizeCategory(command.category),
        scope: command.scope,
        source: command.source,
        aliases: command.aliases,
        skillName: command.skill_name,
      }
    })
    .sort((a, b) => {
      const categoryDiff = CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category)
      if (categoryDiff !== 0) return categoryDiff
      return a.name.localeCompare(b.name)
    })
}

function localizeSkillDescription(description: string, skillName: string): string {
  const invokeMatch = description.match(/^Invoke the (.+) skill$/i)
  if (invokeMatch) return '调用 ' + invokeMatch[1] + ' 技能'
  return description || '调用 ' + skillName + ' 技能'
}

export function filterSlashCommands(commands: SlashCommandItem[], query: string): SlashCommandItem[] {
  const normalized = normalizeSearchText(query)
  if (!normalized) return commands

  return [...commands]
    .map(command => {
      const candidates = [command.name, ...(command.aliases || [])].map(normalizeSearchText)
      const searchableName = normalizeSearchText(command.name)
      const searchableSkill = normalizeSearchText(command.skillName || '')
      const searchableDescription = normalizeSearchText(command.description)
      let score = 0

      if (candidates.some(value => value === normalized)) score += 100
      if (candidates.some(value => value.startsWith(normalized))) score += 60
      if (normalized.length >= 3 && candidates.some(value => value.includes(normalized))) score += 38
      if (normalized.length >= 3 && searchableSkill && searchableSkill.includes(normalized)) score += 32
      if (normalized.length >= 3 && searchableDescription.includes(normalized)) score += 18

      if (normalized.length >= 3) {
        const fuzzyScore = Math.max(
          0,
          ...[searchableName, searchableSkill, ...(command.aliases || []).map(normalizeSearchText)]
            .filter(Boolean)
            .map(value => fuzzyMatchScore(value, normalized)),
        )
        score += fuzzyScore
      }

      if (command.source === 'builtin') score += 2
      return { command, score }
    })
    .filter(entry => entry.score >= (normalized.length <= 2 ? 60 : 16))
    .sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score
      const categoryDiff = CATEGORY_ORDER.indexOf(a.command.category) - CATEGORY_ORDER.indexOf(b.command.category)
      if (categoryDiff !== 0) return categoryDiff
      return a.command.name.localeCompare(b.command.name)
    })
    .slice(0, 24)
    .map(entry => entry.command)
}

function normalizeSearchText(value: string): string {
  return value.trim().toLowerCase().replace(/^\/+/, '').replace(/[\s_-]+/g, '')
}

function fuzzyMatchScore(value: string, query: string): number {
  if (!value || !query) return 0
  let queryIndex = 0
  let firstMatch = -1
  let lastMatch = -1
  let consecutive = 0
  let bestConsecutive = 0

  for (let index = 0; index < value.length && queryIndex < query.length; index += 1) {
    if (value[index] !== query[queryIndex]) continue
    if (firstMatch < 0) firstMatch = index
    if (lastMatch === index - 1) consecutive += 1
    else consecutive = 1
    bestConsecutive = Math.max(bestConsecutive, consecutive)
    lastMatch = index
    queryIndex += 1
  }

  if (queryIndex !== query.length) return 0
  const span = Math.max(1, lastMatch - firstMatch + 1)
  const compactness = Math.max(0, 18 - (span - query.length))
  const prefixBonus = firstMatch === 0 ? 16 : 0
  return 8 + compactness + prefixBonus + bestConsecutive * 2
}

export function getSlashQuery(input: string): string | null {
  if (!input.startsWith('/')) return null
  const firstLine = input.split('\n')[0] ?? ''
  const withoutSlash = firstLine.slice(1)
  if (/\s/.test(withoutSlash)) return null
  return withoutSlash
}

function normalizeCategory(category: string): SlashCommandCategory {
  switch (category) {
    case 'status':
    case 'session':
    case 'management':
    case 'options':
    case 'tools':
    case 'media':
    case 'skills':
    case 'docks':
      return category
    default:
      return 'other'
  }
}
