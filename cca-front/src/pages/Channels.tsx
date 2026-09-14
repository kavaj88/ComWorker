import { useState, useEffect, useCallback, useRef } from 'react'
import {
  RefreshCw,
  AlertCircle,
  Loader2,
  ChevronDown,
  ChevronRight,
  X,
  Save,
  CheckCircle,
  Smartphone,
  PlugZap,
  Home,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type {
  ChannelsStatusResult,
  WeixinConversation,
  WeixinConversationsResult,
} from '../lib/api'
import {
  getChannelsStatus,
  getConfiguredChannels,
  getChannelConfig,
  saveChannelConfig,
  deleteChannelConfig,
  getWeixinConversations,
  setWeixinHomeChannel,
  getAccessToken,
} from '../lib/api'

// Static channel catalog — only real ComWorker-supported channels
type ChannelCatalogItem = {
  id: string
  label: string
  description: string
  icon: string
  featured?: boolean
  displayName?: string
  guideUrl?: string
  recommended?: boolean
}
const CHANNEL_CATALOG: ChannelCatalogItem[] = [
  { id: 'weixin', label: '微信', displayName: '微信助理集成', description: '扫码绑定（Hermes 微信网关），首次接入会显示二维码', icon: '🟩', featured: true, recommended: true },
  { id: 'telegram', label: 'Telegram', displayName: 'Telegram 助理集成', description: '通过 Telegram Bot 接入', icon: '✈️', featured: true },
  { id: 'discord', label: 'Discord', description: '通过 Discord Bot 接入', icon: '🎮' },
  { id: 'whatsapp', label: 'WhatsApp', description: '通过 WhatsApp Web (Baileys) 接入', icon: '📱' },
  { id: 'slack', label: 'Slack', description: '通过 Slack Bot 接入工作区', icon: '💜' },
  { id: 'signal', label: 'Signal', description: '通过 signal-cli 守护进程接入', icon: '🔒' },
  { id: 'imessage', label: 'iMessage', description: '通过 macOS iMessage 接入', icon: '💬' },
  { id: 'web', label: 'Web', description: '内嵌网页对话框', icon: '🌐' },
  { id: 'googlechat', label: 'Google Chat', description: '通过 Google Chat API 接入', icon: '💚' },
  { id: 'msteams', label: 'Microsoft Teams', description: '通过 Azure Bot 接入 Teams', icon: '🟦' },
  { id: 'feishu', label: '飞书 / Lark', displayName: '飞书 / Lark 助理集成', description: '通过飞书/Lark 机器人接入', icon: '📘', featured: true },
  { id: 'matrix', label: 'Matrix', description: '通过 Matrix 协议接入（支持 E2EE）', icon: '🔷' },
  { id: 'mattermost', label: 'Mattermost', description: '通过 Mattermost Bot 接入', icon: '🔵' },
  { id: 'irc', label: 'IRC', description: '通过 IRC 协议接入', icon: '📡' },
  { id: 'nostr', label: 'Nostr', description: '通过 Nostr 去中心化协议接入', icon: '🟣' },
  { id: 'bluebubbles', label: 'BlueBubbles', description: '通过 BlueBubbles 接入 iMessage', icon: '🫧' },
  { id: 'twitch', label: 'Twitch', description: '通过 Twitch IRC 接入直播聊天', icon: '💜' },
  { id: 'nextcloud-talk', label: 'Nextcloud Talk', description: '通过 Nextcloud Talk Bot 接入', icon: '☁️' },
  { id: 'synology-chat', label: 'Synology Chat', description: '通过 Synology Chat Bot 接入', icon: '🟢' },
  { id: 'zalo', label: 'Zalo', description: '通过 Zalo OA API 接入', icon: '🔵' },
  { id: 'qqbot', label: 'QQ', displayName: 'QQ 助理集成', description: '通过 QQ 机器人接入（需安装 QQBot 插件）', icon: '🐧', featured: true },
  { id: 'wecom', label: '企业微信', displayName: '企业微信助理集成', description: '通过企业微信 AI Bot WebSocket 接入（需安装 WeCom 插件）', icon: '💼', featured: true },
  { id: 'dingtalk', label: '钉钉', displayName: '钉钉助理集成', description: '通过钉钉企业机器人接入（需安装 DingTalk 插件）', icon: '💙', featured: true },
]

const CHANNEL_ICONS: Record<string, string> = Object.fromEntries(
  CHANNEL_CATALOG.map((ch) => [ch.id, ch.icon]),
)

const WEIXIN_CHANNEL_ID = 'weixin'
const WEIXIN_LOGIN_COMMAND = 'export PATH=/opt/hermes/.venv/bin:$PATH; cd /opt/hermes && python /opt/hermes/weixin_qr_login.py'

function base64UrlDecode(value: string): string {
  const base = value.replace(/-/g, '+').replace(/_/g, '/')
  const pad = base.length % 4 === 0 ? '' : '='.repeat(4 - (base.length % 4))
  return atob(base + pad)
}

function getTokenSubject(token: string): string {
  try {
    const parts = token.split('.')
    if (parts.length < 2) return 'anonymous'
    const payload = JSON.parse(base64UrlDecode(parts[1]))
    const sub = String(payload?.sub ?? '').trim()
    return sub || 'anonymous'
  } catch {
    return 'anonymous'
  }
}

function getWeixinTerminalSessionKey(token: string, attempt: number): string {
  return `weixin-login:${window.location.host}:${getTokenSubject(token)}:${attempt}`
}

// DM policy options shared across channels
const DM_POLICY_OPTIONS = [
  { value: 'pairing', label: 'pairing — 需配对验证' },
  { value: 'allowlist', label: 'allowlist — 仅白名单用户' },
  { value: 'open', label: 'open — 所有人可用' },
  { value: 'disabled', label: 'disabled — 禁用私聊' },
]

const GROUP_POLICY_OPTIONS = [
  { value: 'open', label: 'open — 所有群组' },
  { value: 'allowlist', label: 'allowlist — 仅白名单群组' },
  { value: 'disabled', label: 'disabled — 禁用群聊' },
]

const STREAMING_OPTIONS = [
  { value: '', label: '（默认）' },
  { value: 'off', label: 'off — 关闭流式' },
  { value: 'partial', label: 'partial — 部分流式' },
  { value: 'block', label: 'block — 按块流式' },
  { value: 'progress', label: 'progress — 进度指示' },
]

type FieldType = 'text' | 'password' | 'boolean' | 'select' | 'textarea' | 'number'

interface ChannelField {
  key: string
  label: string
  type: FieldType
  hint?: string
  required?: boolean
  options?: Array<{ value: string; label: string }>
}

// Channel config fields based on actual ComWorker source (types.*.ts)
const CHANNEL_CONFIG_FIELDS: Record<string, ChannelField[]> = {
  telegram: [
    { key: 'botToken', label: 'Bot Token', type: 'password', required: true, hint: '从 @BotFather 获取的 Bot Token' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS, hint: '控制谁可以私聊 Bot' },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的 Telegram 用户 ID（数字）' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
    { key: 'groupAllowFrom', label: '允许的群组', type: 'text', hint: '逗号分隔的群组 ID' },
    { key: 'streaming', label: '流式输出', type: 'select', options: STREAMING_OPTIONS },
  ],
  discord: [
    { key: 'token', label: 'Bot Token', type: 'password', required: true, hint: 'Discord Developer Portal 中的 Bot Token' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的 Discord 用户 ID' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
    { key: 'streaming', label: '流式输出', type: 'select', options: STREAMING_OPTIONS },
    { key: 'ackReaction', label: '确认表情', type: 'text', hint: '收到消息时回复的 emoji，如 👀' },
  ],
  slack: [
    { key: 'botToken', label: 'Bot Token', type: 'password', required: true, hint: 'xoxb-... 格式的 Bot Token' },
    { key: 'appToken', label: 'App Token', type: 'password', required: true, hint: 'xapp-... 格式（Socket Mode 需要）' },
    { key: 'mode', label: '连接模式', type: 'select', options: [
      { value: 'socket', label: 'socket — Socket Mode（推荐）' },
      { value: 'http', label: 'http — HTTP Webhook' },
    ] },
    { key: 'signingSecret', label: 'Signing Secret', type: 'password', hint: 'HTTP 模式下需要' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的 Slack 用户 ID' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
    { key: 'streaming', label: '流式输出', type: 'select', options: STREAMING_OPTIONS },
  ],
  whatsapp: [
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的号码', type: 'text', hint: '逗号分隔的 E.164 格式号码，如 +8613800138000' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
    { key: 'groupAllowFrom', label: '允许的群组', type: 'text', hint: '逗号分隔的群组 ID' },
    { key: 'selfChatMode', label: '自聊模式', type: 'boolean', hint: '使用同一手机号对话' },
    { key: 'debounceMs', label: '消息防抖 (ms)', type: 'number', hint: '合并快速连续消息的等待毫秒数' },
  ],
  signal: [
    { key: 'account', label: '账号号码', type: 'text', required: true, hint: 'E.164 格式号码，如 +8613800138000' },
    { key: 'httpUrl', label: 'signal-cli HTTP 地址', type: 'text', hint: '如 http://localhost:8080，signal-cli daemon 地址' },
    { key: 'autoStart', label: '自动启动 daemon', type: 'boolean' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的号码', type: 'text', hint: '逗号分隔的 E.164 格式号码' },
  ],
  imessage: [
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'cliPath', label: 'imsg 路径', type: 'text', hint: 'imsg 二进制文件路径' },
    { key: 'dbPath', label: '数据库路径', type: 'text', hint: 'Messages.app 数据库路径（可选覆盖）' },
    { key: 'service', label: '服务类型', type: 'select', options: [
      { value: 'auto', label: 'auto — 自动选择' },
      { value: 'imessage', label: 'iMessage' },
      { value: 'sms', label: 'SMS' },
    ] },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的联系人', type: 'text', hint: '逗号分隔的 handle 或 chat_id' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
    { key: 'includeAttachments', label: '包含附件', type: 'boolean' },
  ],
  web: [
    { key: 'enabled', label: '启用', type: 'boolean' },
  ],
  googlechat: [
    { key: 'serviceAccountFile', label: '服务账号文件', type: 'text', required: true, hint: 'Service Account JSON 文件路径' },
    { key: 'audienceType', label: 'Audience 类型', type: 'select', options: [
      { value: 'app-url', label: 'app-url — 应用 URL' },
      { value: 'project-number', label: 'project-number — 项目编号' },
    ] },
    { key: 'audience', label: 'Audience', type: 'text', hint: '应用 URL 或 GCP 项目编号' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的用户 ID 或邮箱' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
  ],
  msteams: [
    { key: 'appId', label: 'Azure Bot App ID', type: 'text', required: true, hint: 'Azure Bot Registration 的 App ID' },
    { key: 'appPassword', label: 'App Password', type: 'password', required: true, hint: 'Azure Bot 的 App Password / Client Secret' },
    { key: 'tenantId', label: 'Tenant ID', type: 'text', hint: 'Azure AD Tenant ID（可选，限定租户）' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的 AAD Object ID 或 UPN' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
    { key: 'requireMention', label: '群聊需要 @', type: 'boolean', hint: '群聊中是否需要 @ 提及才回复' },
  ],
  feishu: [
    { key: 'appId', label: 'App ID', type: 'text', required: true, hint: '飞书开放平台 App ID' },
    { key: 'appSecret', label: 'App Secret', type: 'password', required: true, hint: '飞书开放平台 App Secret' },
    { key: 'verificationToken', label: 'Verification Token', type: 'password', hint: '事件订阅验证 Token' },
    { key: 'encryptKey', label: 'Encrypt Key', type: 'password', hint: '消息加密密钥（可选）' },
    { key: 'domain', label: '域名', type: 'select', options: [
      { value: 'feishu', label: 'feishu — 飞书（国内）' },
      { value: 'lark', label: 'lark — Lark（海外）' },
    ] },
    { key: 'connectionMode', label: '连接方式', type: 'select', options: [
      { value: 'websocket', label: 'WebSocket（推荐）' },
      { value: 'webhook', label: 'Webhook' },
    ] },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的用户 ID' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
  ],
  matrix: [
    { key: 'homeserver', label: 'Homeserver URL', type: 'text', required: true, hint: '如 https://matrix.org' },
    { key: 'userId', label: '用户 ID', type: 'text', required: true, hint: '如 @bot:matrix.org' },
    { key: 'accessToken', label: 'Access Token', type: 'password', hint: '直接提供 Access Token（与密码二选一）' },
    { key: 'password', label: '密码', type: 'password', hint: '用于自动获取 Token（与 Access Token 二选一）' },
    { key: 'encryption', label: '端到端加密 (E2EE)', type: 'boolean' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
    { key: 'autoJoin', label: '自动加入', type: 'select', options: [
      { value: 'off', label: 'off — 不自动加入' },
      { value: 'allowlist', label: 'allowlist — 仅白名单房间' },
      { value: 'always', label: 'always — 自动加入所有邀请' },
    ] },
  ],
  mattermost: [
    { key: 'botToken', label: 'Bot Token', type: 'password', required: true, hint: 'Mattermost Bot Token' },
    { key: 'baseUrl', label: '服务器地址', type: 'text', required: true, hint: '如 https://mattermost.example.com' },
    { key: 'chatmode', label: '触发模式', type: 'select', options: [
      { value: 'oncall', label: 'oncall — @提及触发' },
      { value: 'onmessage', label: 'onmessage — 任何消息触发' },
      { value: 'onchar', label: 'onchar — 特定前缀触发' },
    ] },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的用户 ID 或 @username' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
    { key: 'requireMention', label: '需要 @提及', type: 'boolean' },
  ],
  irc: [
    { key: 'host', label: '服务器地址', type: 'text', required: true, hint: 'IRC 服务器主机名' },
    { key: 'port', label: '端口', type: 'number', hint: 'TLS 默认 6697，非 TLS 默认 6667' },
    { key: 'tls', label: '使用 TLS', type: 'boolean' },
    { key: 'nick', label: '昵称', type: 'text', required: true, hint: 'Bot 的 IRC 昵称' },
    { key: 'username', label: '用户名', type: 'text', hint: 'IRC USER 字段用户名' },
    { key: 'password', label: '服务器密码', type: 'password' },
    { key: 'channels', label: '频道', type: 'text', hint: '逗号分隔的频道名，如 #general,#bot' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的 IRC 昵称' },
  ],
  nostr: [
    { key: 'privateKey', label: '私钥', type: 'password', required: true, hint: 'Nostr 私钥（hex 格式）' },
    { key: 'relays', label: 'Relay 地址', type: 'text', required: true, hint: '逗号分隔的 Relay URL，如 wss://relay.damus.io' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的公钥或 npub' },
  ],
  bluebubbles: [
    { key: 'serverUrl', label: '服务器 URL', type: 'text', required: true, hint: 'BlueBubbles API 地址' },
    { key: 'password', label: 'API 密码', type: 'password', required: true, hint: 'BlueBubbles API 密码' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的标识符' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
  ],
  twitch: [
    { key: 'username', label: '用户名', type: 'text', required: true, hint: 'Twitch 用户名' },
    { key: 'accessToken', label: 'Access Token', type: 'password', required: true, hint: 'OAuth Access Token' },
    { key: 'clientId', label: 'Client ID', type: 'text', required: true, hint: 'Twitch App Client ID' },
    { key: 'clientSecret', label: 'Client Secret', type: 'password', hint: 'Token 刷新需要' },
    { key: 'refreshToken', label: 'Refresh Token', type: 'password', hint: 'Token 自动刷新' },
    { key: 'channel', label: '频道名', type: 'text', required: true, hint: '要加入的 Twitch 频道名' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'requireMention', label: '需要 @提及', type: 'boolean' },
  ],
  'nextcloud-talk': [
    { key: 'baseUrl', label: 'Nextcloud URL', type: 'text', required: true, hint: '如 https://cloud.example.com' },
    { key: 'botSecret', label: 'Bot Secret', type: 'password', required: true, hint: 'Bot Shared Secret' },
    { key: 'apiUser', label: 'API 用户', type: 'text', hint: '用于 Room 查询的用户名' },
    { key: 'apiPassword', label: 'API 密码', type: 'password', hint: 'API 用户的密码' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
  ],
  'synology-chat': [
    { key: 'token', label: 'Bot Token', type: 'password', required: true },
    { key: 'incomingUrl', label: 'Incoming Webhook URL', type: 'text', required: true, hint: 'Synology Chat 的 Incoming Webhook URL' },
    { key: 'nasHost', label: 'NAS 主机名', type: 'text', required: true, hint: 'NAS 地址' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: [
      { value: 'open', label: 'open — 所有人可用' },
      { value: 'allowlist', label: 'allowlist — 仅白名单' },
      { value: 'disabled', label: 'disabled — 禁用' },
    ] },
    { key: 'botName', label: 'Bot 显示名', type: 'text' },
  ],
  zalo: [
    { key: 'botToken', label: 'Bot Token', type: 'password', required: true, hint: 'Zalo OA Bot Token' },
    { key: 'webhookUrl', label: 'Webhook URL', type: 'text', hint: '需要 HTTPS' },
    { key: 'webhookSecret', label: 'Webhook Secret', type: 'password' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的 Zalo 用户 ID' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
  ],
  qqbot: [
    { key: 'appId', label: 'App ID', type: 'text', required: true, hint: 'QQ 开放平台的机器人 App ID' },
    { key: 'clientSecret', label: 'Client Secret', type: 'password', required: true, hint: 'QQ 开放平台的机器人密钥' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的用户 ID，* 表示所有人' },
  ],
  wecom: [
    { key: 'botId', label: 'Bot ID', type: 'text', required: true, hint: '企业微信 AI Bot 的 Bot ID' },
    { key: 'secret', label: 'Bot Secret', type: 'password', required: true, hint: '企业微信 AI Bot 的 Secret' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'websocketUrl', label: 'WebSocket 地址', type: 'text', hint: '默认 wss://openws.work.weixin.qq.com' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS, hint: '控制谁可以私聊 Bot' },
    { key: 'allowFrom', label: '允许的用户', type: 'text', hint: '逗号分隔的企业微信用户 ID' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
    { key: 'groupAllowFrom', label: '允许的群组', type: 'text', hint: '逗号分隔的群组 ID' },
    { key: 'sendThinkingMessage', label: '发送思考中提示', type: 'boolean', hint: '回复前显示"正在思考"占位消息' },
  ],
  dingtalk: [
    { key: 'clientId', label: 'Client ID', type: 'text', required: true, hint: '钉钉开放平台应用的 Client ID（AppKey）' },
    { key: 'clientSecret', label: 'Client Secret', type: 'password', required: true, hint: '钉钉开放平台应用的 Client Secret（AppSecret）' },
    { key: 'robotCode', label: 'Robot Code', type: 'text', required: true, hint: '钉钉机器人的 robotCode' },
    { key: 'corpId', label: 'Corp ID', type: 'text', hint: '企业的 corpId' },
    { key: 'agentId', label: 'Agent ID', type: 'text', hint: '钉钉应用的 agentId' },
    { key: 'enabled', label: '启用', type: 'boolean' },
    { key: 'dmPolicy', label: '私聊策略', type: 'select', options: DM_POLICY_OPTIONS, hint: '控制谁可以私聊机器人' },
    { key: 'groupPolicy', label: '群聊策略', type: 'select', options: GROUP_POLICY_OPTIONS },
    { key: 'messageType', label: '消息类型', type: 'select', options: [
      { value: 'text', label: 'text — 纯文本' },
      { value: 'markdown', label: 'markdown — Markdown 格式' },
    ], hint: '机器人回复的消息格式' },
    { key: 'debug', label: '调试模式', type: 'boolean', hint: '开启后输出详细日志' },
  ],
}

export default function Channels() {
  const [status, setStatus] = useState<ChannelsStatusResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)

  // Config modal state
  const [configChannel, setConfigChannel] = useState<string | null>(null)
  const [configData, setConfigData] = useState<Record<string, unknown>>({})
  const [configLoading, setConfigLoading] = useState(false)
  const [configSaving, setConfigSaving] = useState(false)

  // Collapsible "其他渠道" section
  const [showOthers, setShowOthers] = useState(false)

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  // Channels configured in comworker.json (may not have gateway accounts yet)
  const [configuredTypes, setConfiguredTypes] = useState<string[]>([])

  // Show restart hint after saving channel config
  const [showRestartHint, setShowRestartHint] = useState(false)
  const [weixinBindOpen, setWeixinBindOpen] = useState(false)
  const [weixinHome, setWeixinHome] = useState<WeixinConversationsResult | null>(null)
  const [weixinHomeLoading, setWeixinHomeLoading] = useState(false)
  const [settingHomeChatId, setSettingHomeChatId] = useState<string | null>(null)
  const navigate = useNavigate()

  const fetchStatus = useCallback(async (showLoader = false) => {
    if (showLoader) setLoading(true)
    setError('')
    try {
      const [statusResult, configuredResult] = await Promise.all([
        getChannelsStatus(true),
        getConfiguredChannels(),
      ])
      setStatus(statusResult)
      if (configuredResult.success && configuredResult.channels) {
        setConfiguredTypes(configuredResult.channels)
      }
    } catch (err: any) {
      setError(err?.message || '获取渠道状态失败')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    fetchStatus(true)
  }, [fetchStatus])

  const handleRefresh = () => {
    setRefreshing(true)
    fetchStatus()
  }

  const fetchWeixinHome = useCallback(async () => {
    setWeixinHomeLoading(true)
    try {
      const data = await getWeixinConversations()
      setWeixinHome(data)
    } catch {
      setWeixinHome({ conversations: [], home_channel: '', home_channel_name: '' })
    } finally {
      setWeixinHomeLoading(false)
    }
  }, [])

  useEffect(() => {
    if (configuredTypes.includes(WEIXIN_CHANNEL_ID)) {
      void fetchWeixinHome()
    } else {
      setWeixinHome(null)
    }
  }, [configuredTypes, fetchWeixinHome])

  const handleSetWeixinHome = async (chatId: string, name: string) => {
    setSettingHomeChatId(chatId)
    try {
      const res = await setWeixinHomeChannel(chatId, name)
      if (!res.ok) {
        setError(res.detail || '设置主频道失败')
        return
      }
      await fetchWeixinHome()
    } catch (err: any) {
      setError(err?.message || '设置主频道失败')
    } finally {
      setSettingHomeChatId(null)
    }
  }

  const openConfig = async (channelType: string) => {
    if (channelType === WEIXIN_CHANNEL_ID) {
      setWeixinBindOpen(true)
      return
    }
    setConfigChannel(channelType)
    setConfigLoading(true)
    try {
      const result = await getChannelConfig(channelType)
      setConfigData(result.config || {})
    } catch {
      setConfigData({})
    } finally {
      setConfigLoading(false)
    }
  }

  const handleSaveConfig = async (dataOverride?: Record<string, unknown>) => {
    if (!configChannel) return
    setConfigSaving(true)
    try {
      await saveChannelConfig(configChannel, dataOverride ?? configData)
      setConfigChannel(null)
      setShowRestartHint(true)
      // Refresh status after config change
      await fetchStatus()
    } catch (err: any) {
      setError(err?.message || '保存配置失败')
    } finally {
      setConfigSaving(false)
    }
  }

  const handleDeleteChannel = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteChannelConfig(deleteTarget)
      setConfiguredTypes((prev) => prev.filter((t) => t !== deleteTarget))
      setDeleteTarget(null)
      await fetchStatus()
    } catch (err: any) {
      setError(err?.message || '删除渠道失败')
    } finally {
      setDeleting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 size={28} className="animate-spin text-accent-blue" />
      </div>
    )
  }

  // Build channel list from status data + config + static catalog
  const channelAccounts = status?.channelAccounts || {}
  const channelLabels = status?.channelLabels || {}

  // Channels with gateway accounts (actively running in gateway)
  const gatewayChannels = (status?.channelOrder || []).filter(
    (ch) => channelAccounts[ch] && channelAccounts[ch].length > 0,
  )

  // Per-channel status: connected (gateway active) / configured (comworker.json only) / available (none)
  const channelStatus: Record<string, 'connected' | 'configured' | 'available'> = {}
  for (const ch of CHANNEL_CATALOG) {
    if (gatewayChannels.includes(ch.id)) channelStatus[ch.id] = 'connected'
    else if (configuredTypes.includes(ch.id)) channelStatus[ch.id] = 'configured'
    else channelStatus[ch.id] = 'available'
  }

  const featuredChannels = CHANNEL_CATALOG.filter((c) => c.featured)
  const otherChannels = CHANNEL_CATALOG.filter((c) => !c.featured)

  return (
    <div className="h-full overflow-y-auto bg-light-bg px-6 py-5 sm:px-8 lg:px-10">
      <div className="mx-auto max-w-5xl">
        <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-light-text">渠道管理</h1>
          <p className="mt-1 text-sm text-light-text-secondary">
            管理 AI Agent 的消息接入渠道
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex items-center gap-1.5 rounded-lg border border-light-border px-3 py-1.5 text-xs text-light-text-secondary hover:text-light-text transition-colors"
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-accent-red/10 p-3 text-sm text-accent-red flex items-center gap-2">
          <AlertCircle size={16} />
          {error}
        </div>
      )}

      {showRestartHint && (
        <div className="mb-4 rounded-lg bg-accent-green/10 p-3 text-sm text-accent-green flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle size={16} />
            <span>渠道配置已保存，需要重启网关才能生效</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate('/settings')}
              className="rounded-lg bg-accent-green px-3 py-1 text-xs font-medium text-white hover:bg-accent-green/90 transition-colors"
            >
              前往系统设置重启网关
            </button>
            <button
              onClick={() => setShowRestartHint(false)}
              className="text-accent-green/60 hover:text-accent-green transition-colors"
            >
              <X size={14} />
            </button>
          </div>
        </div>
      )}

      {/* Featured channels */}
      <div className="mb-8">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-light-text-secondary">
          主渠道
        </h2>
        <div className="flex flex-col gap-3">
          {featuredChannels.map((ch) => (
            <ChannelRow
              key={ch.id}
              entry={ch}
              status={channelStatus[ch.id] || 'available'}
              onConfig={() => openConfig(ch.id)}
              onDelete={() => setDeleteTarget(ch.id)}
            />
          ))}
        </div>
      </div>

      {/* Weixin home channel */}
      {configuredTypes.includes(WEIXIN_CHANNEL_ID) && (
        <div className="mb-8">
          <h2 className="mb-3 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wider text-light-text-secondary">
            <Home size={14} />
            微信主频道
          </h2>
          <div className="rounded-xl border border-light-border bg-white p-4">
            <p className="mb-3 text-xs text-light-text-secondary">
              主频道用于接收定时任务结果和跨平台消息。选择一个会话设为主频道后，微信端不再提示「设置主频道」。
            </p>
            {weixinHomeLoading ? (
              <div className="flex items-center gap-2 text-sm text-light-text-secondary">
                <Loader2 size={14} className="animate-spin" />
                加载会话中…
              </div>
            ) : weixinHome && weixinHome.conversations.length > 0 ? (
              <div className="flex flex-col gap-2">
                {weixinHome.conversations.map((c: WeixinConversation) => {
                  const isHome = c.chat_id === weixinHome.home_channel
                  return (
                    <div
                      key={c.chat_id}
                      className="flex items-center justify-between rounded-lg border border-light-border px-3 py-2"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm text-light-text">{c.name || c.chat_id}</span>
                          {c.type === 'group' && (
                            <span className="rounded bg-light-card px-1.5 py-0.5 text-[10px] text-light-text-secondary">
                              群
                            </span>
                          )}
                          {isHome && (
                            <span className="inline-flex items-center gap-1 rounded bg-accent-green/10 px-1.5 py-0.5 text-[10px] text-accent-green">
                              <CheckCircle size={10} />
                              当前主频道
                            </span>
                          )}
                        </div>
                        <div className="truncate text-[11px] text-light-text-secondary">{c.chat_id}</div>
                      </div>
                      {isHome ? (
                        <span className="text-xs text-accent-green">已设置</span>
                      ) : (
                        <button
                          onClick={() => handleSetWeixinHome(c.chat_id, c.name)}
                          disabled={settingHomeChatId === c.chat_id}
                          className="rounded-lg border border-light-border px-3 py-1 text-xs text-light-text-secondary hover:text-light-text transition-colors disabled:opacity-50"
                        >
                          {settingHomeChatId === c.chat_id ? (
                            <Loader2 size={12} className="animate-spin" />
                          ) : (
                            '设为主频道'
                          )}
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            ) : (
              <p className="text-sm text-light-text-secondary">
                暂无可用的微信会话。请先在微信里给该 bot 发送一条消息，会话才会出现在这里。
              </p>
            )}
          </div>
        </div>
      )}

      {/* Other channels (collapsible) */}
      <div>
        <button
          onClick={() => setShowOthers((v) => !v)}
          className="mb-3 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wider text-light-text-secondary transition-colors hover:text-light-text"
        >
          {showOthers ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          其他渠道（{otherChannels.length}）
        </button>
        {showOthers && (
          <div className="flex flex-col gap-3">
            {otherChannels.map((ch) => (
              <ChannelRow
                key={ch.id}
                entry={ch}
                status={channelStatus[ch.id] || 'available'}
                onConfig={() => openConfig(ch.id)}
                onDelete={() => setDeleteTarget(ch.id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Config modal */}
      {configChannel && (
        <ChannelConfigModal
          channelType={configChannel}
          channelLabel={channelLabels[configChannel] || CHANNEL_CATALOG.find((c) => c.id === configChannel)?.label || configChannel}
          configData={configData}
          loading={configLoading}
          saving={configSaving}
          onConfigChange={setConfigData}
          onSave={handleSaveConfig}
          onClose={() => setConfigChannel(null)}
        />
      )}

      {weixinBindOpen && (
        <WeixinBindModal
          onClose={() => setWeixinBindOpen(false)}
          onBound={() => {
            setWeixinBindOpen(false)
            void fetchStatus()
          }}
        />
      )}

      {/* Delete confirmation */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 px-4 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-2xl border border-light-border bg-white p-6 shadow-2xl shadow-slate-950/20">
            <h3 className="text-base font-semibold text-light-text mb-2">确认删除</h3>
            <p className="text-sm text-light-text-secondary mb-4">
              确定要删除渠道 <span className="font-medium text-light-text">{channelLabels[deleteTarget] || CHANNEL_CATALOG.find((c) => c.id === deleteTarget)?.label || deleteTarget}</span> 的配置？此操作不可撤销。
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setDeleteTarget(null)}
                className="rounded-lg border border-light-border px-4 py-1.5 text-sm text-light-text-secondary hover:text-light-text transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleDeleteChannel}
                disabled={deleting}
                className="rounded-lg bg-accent-red px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-red/90 transition-colors disabled:opacity-50"
              >
                {deleting ? <Loader2 size={14} className="animate-spin" /> : '删除'}
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
    </div>
  )
}

interface WeixinBindModalProps {
  onClose: () => void
  onBound: () => void
}

function WeixinBindModal({ onClose, onBound }: WeixinBindModalProps) {
  const [ws, setWs] = useState<WebSocket | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [connected, setConnected] = useState(false)
  const [running, setRunning] = useState(false)
  const [pluginReady, setPluginReady] = useState(false)
  const [output, setOutput] = useState('')
  const [error, setError] = useState('')
  const outputRef = useRef<HTMLDivElement | null>(null)
  const hadSessionRef = useRef(false)

  useEffect(() => {
    if (!outputRef.current) return
    outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [output])

  useEffect(() => {
    let disposed = false
    let nextWs: WebSocket | null = null

    const connect = async () => {
      const token = getAccessToken()
      if (!token) {
        setError('未登录或 token 已失效')
        return
      }

      setError('')
      setConnected(false)
      setRunning(true)
      setPluginReady(true)
      hadSessionRef.current = false

      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const wsUrl = `${proto}://${window.location.host}/api/comworker/terminal/ws?token=${encodeURIComponent(token)}`
      nextWs = new WebSocket(wsUrl)
      const sessionKey = getWeixinTerminalSessionKey(token, attempt)

      nextWs.onopen = () => {
        if (disposed) return
        setConnected(true)
        setRunning(true)
        nextWs?.send(JSON.stringify({
          type: 'init',
          session_key: sessionKey,
          command: WEIXIN_LOGIN_COMMAND,
        }))
      }

      nextWs.onclose = (event) => {
        if (disposed) return
        setConnected(false)
        setRunning(false)
        setWs((current) => (current === nextWs ? null : current))
        if (!hadSessionRef.current) {
          const reason = event.reason?.trim()
          setError(reason ? `微信绑定终端未就绪: ${reason}` : '微信绑定终端未就绪')
        }
      }

      nextWs.onerror = () => {
        if (disposed) return
        setOutput((prev) => `${prev}\n[error] terminal websocket error\n`)
      }

      nextWs.onmessage = (evt) => {
        if (disposed) return
        try {
          const msg = JSON.parse(String(evt.data))
          if (msg.type === 'session') {
            hadSessionRef.current = true
            const reused = Boolean(msg.reused)
            setOutput((prev) => `${prev}[session] ${String(msg.session_key ?? '')} ${reused ? '(reused)' : '(new)'}\n`)
          } else if (msg.type === 'output') {
            const chunk = String(msg.data ?? '')
            setOutput((prev) => prev + chunk)
          } else if (msg.type === 'started') {
            setOutput((prev) => `${prev}[started] ${String(msg.command ?? '')}\n`)
          } else if (msg.type === 'exit') {
            setRunning(false)
            setOutput((prev) => `${prev}\n[exit] code=${String(msg.code)} signal=${String(msg.signal)}\n`)
            if (String(msg.code ?? '') === '0') {
              onBound()
            }
          } else if (msg.type === 'error') {
            setRunning(false)
            setError(String(msg.message ?? '微信绑定失败'))
          }
        } catch {
          setOutput((prev) => prev + String(evt.data))
        }
      }

      setWs(nextWs)
    }

    void connect()

    return () => {
      disposed = true
      try { nextWs?.close() } catch { /* ignore */ }
    }
  }, [attempt, onBound])

  const handleClose = () => {
    try { ws?.close() } catch { /* ignore */ }
    onClose()
  }

  const qrLinkMatch = output.match(/https:\/\/liteapp\.weixin\.qq\.com\/q\/[^\s]+/)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 px-4 backdrop-blur-sm">
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-2xl border border-light-border bg-white shadow-2xl shadow-slate-950/20">
        <div className="flex items-center justify-between px-5 py-4 border-b border-light-border shrink-0">
          <div className="flex items-center gap-2">
            <Smartphone size={18} className="text-accent-green" />
            <div>
              <h3 className="text-base font-semibold text-light-text">绑定微信渠道</h3>
              <p className="text-xs text-light-text-secondary mt-0.5">
                直接发起微信登录并输出扫码二维码，不再重复安装插件
              </p>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="rounded-lg p-1 text-light-text-secondary hover:text-light-text transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 border-b border-light-border bg-light-card-hover/70 shrink-0">
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <span className={`inline-flex items-center gap-1 ${connected ? 'text-accent-green' : 'text-light-text-secondary'}`}>
              <span className={`inline-block h-2 w-2 rounded-full ${connected ? 'bg-accent-green' : 'bg-gray-500'}`} />
              {connected ? '终端已连接' : '终端未连接'}
            </span>
            <span className={`inline-flex items-center gap-1 ${running ? 'text-accent-yellow' : 'text-light-text-secondary'}`}>
              <PlugZap size={12} />
              {running ? '扫码流程进行中' : '等待重新发起'}
            </span>
            <span className={`inline-flex items-center gap-1 ${pluginReady ? 'text-accent-green' : 'text-light-text-secondary'}`}>
              <span className={`inline-block h-2 w-2 rounded-full ${pluginReady ? 'bg-accent-green' : 'bg-gray-500'}`} />
              {pluginReady ? '微信平台已就绪' : '微信平台就绪'}
            </span>
            <code className="rounded bg-light-card px-2 py-1 text-[11px] text-light-text-secondary">{WEIXIN_LOGIN_COMMAND}</code>
          </div>
          {qrLinkMatch && (
            <div className="mt-3 rounded-lg bg-accent-green/10 p-3 text-sm text-accent-green">
              浏览器扫码链接：
              <a
                href={qrLinkMatch[0]}
                target="_blank"
                rel="noreferrer"
                className="ml-1 underline break-all"
              >
                {qrLinkMatch[0]}
              </a>
            </div>
          )}
          {error && (
            <div className="mt-3 rounded-lg bg-accent-red/10 p-3 text-sm text-accent-red flex items-center gap-2">
              <AlertCircle size={16} />
              {error}
            </div>
          )}
        </div>

        <div
          ref={outputRef}
          className="flex-1 overflow-auto whitespace-pre-wrap bg-black px-5 py-4 font-mono text-xs leading-none text-green-200"
        >
          {output || '正在连接微信绑定终端...'}
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-light-border shrink-0">
          <button
            onClick={() => {
              try { ws?.close() } catch { /* ignore */ }
              setOutput('')
              setError('')
              setAttempt((value) => value + 1)
            }}
            className="rounded-lg border border-light-border px-4 py-1.5 text-sm text-light-text-secondary hover:text-light-text transition-colors"
          >
            重新生成二维码
          </button>
          <button
            onClick={() => setOutput('')}
            className="rounded-lg border border-light-border px-4 py-1.5 text-sm text-light-text-secondary hover:text-light-text transition-colors"
          >
            清空输出
          </button>
          <button
            onClick={handleClose}
            className="rounded-lg bg-accent-blue px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-blue/90 transition-colors"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}

// --- Sub components ---

function ChannelStatusBadge({ status }: { status: 'connected' | 'configured' | 'available' }) {
  if (status === 'connected') {
    return (
      <span className="flex items-center gap-1 text-[11px] text-accent-green">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent-green" />
        已连接
      </span>
    )
  }
  if (status === 'configured') {
    return (
      <span className="flex items-center gap-1 text-[11px] text-light-text-secondary">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-gray-400" />
        已配置
      </span>
    )
  }
  return (
    <span className="flex items-center gap-1 text-[11px] text-light-text-secondary">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-gray-300" />
      未配置
    </span>
  )
}

function ChannelRow({
  entry,
  status,
  onConfig,
  onDelete,
}: {
  entry: ChannelCatalogItem
  status: 'connected' | 'configured' | 'available'
  onConfig: () => void
  onDelete: () => void
}) {
  const isConfigured = status !== 'available'
  return (
    <div className="flex items-center gap-3 rounded-xl border border-light-border bg-light-card px-4 py-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-light-card-hover text-xl">
        {entry.icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-light-text">{entry.displayName || entry.label}</span>
          {entry.recommended && (
            <span className="rounded-full bg-accent-green/10 px-2 py-0.5 text-[10px] font-medium text-accent-green">
              推荐
            </span>
          )}
          <ChannelStatusBadge status={status} />
        </div>
        <div className="mt-0.5 truncate text-xs text-light-text-secondary">{entry.description}</div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {status === 'configured' && (
          <span className="text-[11px] text-accent-yellow">需重启网关</span>
        )}
        <button
          onClick={onConfig}
          className="rounded-lg border border-light-border px-3 py-1 text-xs text-light-text-secondary transition-colors hover:text-light-text"
        >
          配置
        </button>
        {isConfigured && (
          <button
            onClick={onDelete}
            className="rounded-lg border border-light-border px-3 py-1 text-xs text-accent-red transition-colors hover:bg-accent-red/10"
          >
            解绑
          </button>
        )}
      </div>
    </div>
  )
}

interface ChannelConfigModalProps {
  channelType: string
  channelLabel: string
  configData: Record<string, unknown>
  loading: boolean
  saving: boolean
  onConfigChange: (data: Record<string, unknown>) => void
  onSave: (dataOverride?: Record<string, unknown>) => void
  onClose: () => void
}

// Fields that store arrays (comma-separated in UI → array in JSON)
const ARRAY_FIELDS = new Set([
  'allowFrom', 'groupAllowFrom', 'channels', 'relays',
  'autoJoinAllowlist', 'allowedUserIds',
])

function ChannelConfigModal({
  channelType,
  channelLabel,
  configData,
  loading,
  saving,
  onConfigChange,
  onSave,
  onClose,
}: ChannelConfigModalProps) {
  const knownFields = CHANNEL_CONFIG_FIELDS[channelType]

  const updateField = (key: string, value: unknown) => {
    onConfigChange({ ...configData, [key]: value })
  }

  // For channels without predefined fields, allow raw JSON editing
  const [rawMode, setRawMode] = useState(!knownFields)
  const [rawJson, setRawJson] = useState(JSON.stringify(configData, null, 2))

  useEffect(() => {
    if (!knownFields) {
      setRawJson(JSON.stringify(configData, null, 2))
    }
  }, [configData, knownFields])

  const handleRawSave = () => {
    try {
      const parsed = JSON.parse(rawJson)
      onConfigChange(parsed)
      onSave(parsed)
    } catch {
      alert('JSON 格式错误')
    }
  }

  const getDisplayValue = (field: ChannelField): string => {
    const val = configData[field.key]
    if (val === undefined || val === null) return ''
    if (Array.isArray(val)) return val.join(', ')
    return String(val)
  }

  const handleTextChange = (field: ChannelField, raw: string) => {
    if (ARRAY_FIELDS.has(field.key)) {
      if (raw === '') {
        updateField(field.key, [])
      } else {
        updateField(field.key, raw.split(',').map((s) => s.trim()).filter(Boolean))
      }
    } else if (field.type === 'number') {
      const num = parseInt(raw, 10)
      updateField(field.key, isNaN(num) ? undefined : num)
    } else {
      updateField(field.key, raw)
    }
  }

  const renderField = (field: ChannelField) => {
    switch (field.type) {
      case 'boolean':
        return (
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={configData[field.key] !== false && configData[field.key] !== undefined}
              onChange={(e) => updateField(field.key, e.target.checked)}
              className="rounded border-light-border"
            />
            <span className="text-sm text-light-text">
              {configData[field.key] !== false && configData[field.key] !== undefined ? '启用' : '禁用'}
            </span>
          </label>
        )

      case 'select':
        return (
          <select
            value={(configData[field.key] as string) || ''}
            onChange={(e) => updateField(field.key, e.target.value || undefined)}
            className="w-full rounded-lg border border-light-border bg-light-card-hover px-3 py-2 text-sm text-light-text outline-none focus:border-accent-blue"
          >
            <option value="">（未设置）</option>
            {field.options?.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        )

      case 'textarea':
        return (
          <textarea
            value={getDisplayValue(field)}
            onChange={(e) => handleTextChange(field, e.target.value)}
            rows={4}
            placeholder={field.hint}
            className="w-full rounded-lg border border-light-border bg-light-card-hover px-3 py-2 text-sm text-light-text font-mono outline-none focus:border-accent-blue placeholder:text-light-text-secondary resize-none"
          />
        )

      default:
        return (
          <input
            type={field.type === 'password' ? 'password' : 'text'}
            value={getDisplayValue(field)}
            onChange={(e) => handleTextChange(field, e.target.value)}
            placeholder={field.hint}
            className="w-full rounded-lg border border-light-border bg-light-card-hover px-3 py-2 text-sm text-light-text outline-none focus:border-accent-blue placeholder:text-light-text-secondary"
          />
        )
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 px-4 backdrop-blur-sm">
      <div className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-2xl border border-light-border bg-white shadow-2xl shadow-slate-950/20">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-light-border shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-xl">{CHANNEL_ICONS[channelType] || '💬'}</span>
            <h3 className="text-base font-semibold text-light-text">
              配置 {channelLabel}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-light-text-secondary hover:text-light-text transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 size={24} className="animate-spin text-accent-blue" />
            </div>
          ) : rawMode ? (
            <div>
              <label className="block text-xs font-medium text-light-text-secondary mb-1">
                JSON 配置
              </label>
              <textarea
                value={rawJson}
                onChange={(e) => setRawJson(e.target.value)}
                rows={16}
                className="w-full rounded-lg border border-light-border bg-light-card-hover px-3 py-2 text-sm text-light-text font-mono outline-none focus:border-accent-blue placeholder:text-light-text-secondary resize-none"
                placeholder="{}"
              />
              {knownFields && (
                <button
                  onClick={() => setRawMode(false)}
                  className="mt-2 text-xs text-accent-blue hover:underline"
                >
                  切换到表单模式
                </button>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              {knownFields?.map((field) => (
                <div key={field.key}>
                  <label className="block text-xs font-medium text-light-text-secondary mb-1">
                    {field.label}
                    {field.required && <span className="text-accent-red ml-0.5">*</span>}
                  </label>
                  {renderField(field)}
                  {field.hint && field.type !== 'boolean' && field.type !== 'select' && (
                    <p className="mt-0.5 text-[11px] text-light-text-secondary">{field.hint}</p>
                  )}
                </div>
              ))}
              <button
                onClick={() => {
                  setRawJson(JSON.stringify(configData, null, 2))
                  setRawMode(true)
                }}
                className="text-xs text-accent-blue hover:underline"
              >
                切换到 JSON 模式（高级）
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-light-border shrink-0">
          <button
            onClick={onClose}
            className="rounded-lg border border-light-border px-4 py-1.5 text-sm text-light-text-secondary hover:text-light-text transition-colors"
          >
            取消
          </button>
          <button
            onClick={rawMode ? handleRawSave : () => onSave()}
            disabled={saving}
            className="flex items-center gap-1.5 rounded-lg bg-accent-blue px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-blue/90 transition-colors disabled:opacity-50"
          >
            {saving ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Save size={14} />
            )}
            保存
          </button>
        </div>
      </div>
    </div>
  )
}
