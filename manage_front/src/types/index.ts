export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user_id: string;
  username: string;
  role: string;
}

export interface UserSummary {
  id: string;
  username: string;
  email: string;
  role: string;
  quota_tier: string;
  runtime_mode: string;
  is_active: boolean;
  created_at: string;
  container_status: string | null;
  container_docker_id: string | null;
  container_created_at: string | null;
  shared_agent_id: string | null;
  shared_agent_status: string | null;
  tokens_used_today: number;
}

export interface CreateUserRequest {
  username: string;
  email: string;
  password?: string; // 留空则系统生成强口令并强制首次登录改密
  role?: string;
  quota_tier?: string;
  runtime_mode?: string;
}

export interface CreateUserResponse {
  ok: boolean;
  user_id: string;
  must_change_password: boolean;
  initial_password: string | null; // 仅系统自动生成时返回
  container_status: string;
}

export interface BulkImportDetail {
  username: string;
  status: "created" | "skipped" | "failed";
  reason?: string;
  must_change_password?: boolean;
  initial_password?: string | null;
}

export interface BulkCreateUsersResponse {
  total: number;
  created: number;
  skipped: number;
  failed: number;
  details: BulkImportDetail[];
}

export interface UserModelItem {
  id: string;
  name: string;
  allowed: boolean;
  models: { id: string; name?: string; enabled?: boolean }[];
}

export interface UserModelsResponse {
  providers: UserModelItem[];
  defaultModel: string | null;
}

export interface PaginatedUsers {
  items: UserSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface UsageSummary {
  total_tokens_today: number;
  total_users: number;
  active_containers: number;
}

export interface DailyUsage {
  date: string;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
}

export interface ModelUsage {
  model: string;
  total_tokens: number;
}

export interface UsageHistory {
  daily: DailyUsage[];
  by_model: ModelUsage[];
}

export interface AuditLogItem {
  id: string;
  user_id: string | null;
  username: string | null;
  action: string;
  resource: string | null;
  detail: string | null;
  ip: string | null;
  user_agent: string | null;
  request_id: string | null;
  status_code: number | null;
  created_at: string;
}

export interface PaginatedAuditLogs {
  items: AuditLogItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface Capability {
  capability: string;
  label: string;
  toolsets: string[];
  env_key: string;
  requires_key: boolean;
  platform_key_configured: boolean;
  placeholder?: boolean;
}

export interface CapabilityDefault {
  capability: string;
  default_inject: boolean;
  config?: Record<string, unknown>;
}

export interface CapabilityState {
  capability: string;
  label: string;
  user_override: boolean;
  user_enabled: boolean | null;
  default_inject: boolean;
  effective_enabled: boolean;
  source: "user" | "default" | "off" | "placeholder";
  platform_key_configured: boolean;
  placeholder?: boolean;
}

// ─── 用户级管理（用户详情页） ─────────────────────────────────────────────
export interface DataFootprint {
  user_id: string;
  container_status: string | null;
  user_config_present: boolean;
  volume_present: boolean;
  connectors_count: number;
  connectors: { id: string; name: string; connector_id: string | null; enabled: boolean; personal: boolean }[];
  skills_user: string[];
  skills_managed: string[];
  sessions_count: number;
  kb_docs_count: number;
}

export interface IntegrityCheck {
  name: string;
  ok: boolean;
  detail?: string;
}

export interface DataIntegrity {
  healthy: boolean;
  checks: IntegrityCheck[];
}

export interface UserConnectorItem {
  id: string;
  name: string;
  connector_id: string | null;
  enabled: boolean;
  personal: boolean;
  has_credential: boolean;
}

export interface UserAgentItem {
  agent_id: string;
  system: boolean;
}

export interface UserContainerDetail {
  user_id: string;
  status: string;
  docker_id: string | null;
  internal_host: string | null;
  created_at: string | null;
  user_config_keys: string[];
  image?: string;
  state?: string;
  mounts?: string[];
  created?: string;
}

// ─── 用户知识库（管理端按 user 管理，默认 main agent） ──────────────────────
export interface KnowledgePageMeta {
  path: string;
  name: string;
  title: string;
  type?: string | null;
  domain?: string | null;
  status?: string | null;
  tags: string[];
  summary?: string | null;
  created?: string | null;
  updated?: string | null;
  size?: number;
  modified?: string | null;
  wikilinks?: string[];
}

export interface KnowledgeDirectoryMeta {
  path: string;
  name: string;
  modified?: string | null;
}

export interface KnowledgeAttachmentMeta {
  path: string;
  name: string;
  size?: number;
  modified?: string | null;
}

export interface KnowledgeListResult {
  agentId: string;
  knowledgeRoot: string;
  exists: boolean;
  pages: KnowledgePageMeta[];
  directories: KnowledgeDirectoryMeta[];
  attachments: KnowledgeAttachmentMeta[];
}

export interface KnowledgeReadResult {
  page: KnowledgePageMeta;
  content: string;
  backlinks: string[];
}

export interface KnowledgeSearchResult {
  path: string;
  title: string;
  line: number;
  text: string;
}

export interface KnowledgeGraphNode {
  id: string;
  title: string;
  type?: string | null;
  tags?: string[];
}

export interface KnowledgeGraphEdge {
  source: string;
  target: string;
}

export interface KnowledgeGraphResult {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
}

