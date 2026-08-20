import { getAccessToken, getRefreshToken, setTokens, clearTokens } from "./auth";
import type {
  TokenResponse,
  CreateUserRequest,
  CreateUserResponse,
  BulkCreateUsersResponse,
  PaginatedUsers,
  UsageSummary,
  UsageHistory,
  PaginatedAuditLogs,
  Capability,
  CapabilityDefault,
  CapabilityState,
  UserModelsResponse,
  DataFootprint,
  DataIntegrity,
  UserConnectorItem,
  UserAgentItem,
  UserContainerDetail,
  KnowledgeListResult,
  KnowledgeReadResult,
  KnowledgeSearchResult,
  KnowledgeGraphResult,
} from "@/types";

// In development, call Gateway directly. In production (Docker), use relative URL to hit Next.js proxy.
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

async function parseErrorMessage(res: Response): Promise<string> {
  const err = await res.json().catch(() => null) as { detail?: string; message?: string } | null;
  return err?.detail || err?.message || `Request failed: ${res.status}`;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAccessToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  let res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  // Try refresh on 401
  if (res.status === 401 && token) {
    const refreshToken = getRefreshToken();
    if (refreshToken) {
      const refreshRes = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (refreshRes.ok) {
        const data: TokenResponse = await refreshRes.json();
        setTokens(data);
        headers["Authorization"] = `Bearer ${data.access_token}`;
        res = await fetch(`${API_BASE}${path}`, { ...options, headers });
      } else {
        clearTokens();
        window.location.href = "/login";
        throw new Error("Session expired");
      }
    } else {
      clearTokens();
      window.location.href = "/login";
      throw new Error("Session expired");
    }
  }

  if (!res.ok) {
    throw new Error(await parseErrorMessage(res));
  }
  return res.json();
}

// Auth
export async function login(username: string, password: string): Promise<TokenResponse> {
  return request<TokenResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

// API Token（供程序化调用 Agent，有效期 365 天）
export interface ApiTokenResponse {
  api_token: string;
  expires_in_days: number;
}

export async function generateApiToken(): Promise<ApiTokenResponse> {
  return request<ApiTokenResponse>("/api/auth/api-token", {
    method: "POST",
  });
}

// Users
export async function getUsers(page = 1, pageSize = 20, search = ""): Promise<PaginatedUsers> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (search) params.set("search", search);
  return request<PaginatedUsers>(`/api/admin/users?${params}`);
}

export async function updateUser(userId: string, data: { role?: string; quota_tier?: string; runtime_mode?: string; is_active?: boolean }) {
  return request(`/api/admin/users/${userId}`, { method: "PUT", body: JSON.stringify(data) });
}

export async function createUser(data: CreateUserRequest): Promise<CreateUserResponse> {
  return request<CreateUserResponse>(`/api/admin/users`, { method: "POST", body: JSON.stringify(data) });
}

export async function bulkCreateUsers(file: File): Promise<BulkCreateUsersResponse> {
  const token = getAccessToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_BASE}/api/admin/users/bulk`, { method: "POST", headers, body: form });
  if (res.status === 401 && token) {
    const refreshToken = getRefreshToken();
    if (refreshToken) {
      const refreshRes = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (refreshRes.ok) {
        const data: TokenResponse = await refreshRes.json();
        setTokens(data);
        headers["Authorization"] = `Bearer ${data.access_token}`;
        const retry = await fetch(`${API_BASE}/api/admin/users/bulk`, { method: "POST", headers, body: form });
        if (!retry.ok) throw new Error(await parseErrorMessage(retry));
        return retry.json();
      }
    }
    clearTokens();
    window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.json();
}

export async function getUserModels(userId: string): Promise<UserModelsResponse> {
  return request<UserModelsResponse>(`/api/admin/users/${userId}/models`);
}

export async function updateUserModels(
  userId: string,
  data: { providers?: Record<string, boolean>; defaultModel?: string | null },
): Promise<UserModelsResponse> {
  return request<UserModelsResponse>(`/api/admin/users/${userId}/models`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

// Read-only view of the user's *actual* model config (providers/keys they added
// in the client). Distinct from the platform visibility/defaults above.
export interface ConfiguredProvider {
  name: string;
  baseUrl: string;
  api: string;
  hasApiKey: boolean;
  system: boolean;
  disabled?: boolean;
  models: { id: string; name?: string }[];
}
export interface ConfiguredModels {
  defaultModel: string;
  providers: ConfiguredProvider[];
  models: { id: string; name?: string }[];
}
export interface UserUsage {
  tokens_today: number;
  tokens_this_week: number;
  tokens_total: number;
}
export async function getUserConfiguredModels(userId: string): Promise<ConfiguredModels> {
  return request<ConfiguredModels>(`/api/admin/users/${userId}/models/configured`);
}
export async function getUserUsage(userId: string): Promise<UserUsage> {
  return request<UserUsage>(`/api/admin/users/${userId}/usage`);
}
export async function setUserProviderDisabled(userId: string, provider: string, disabled: boolean) {
  return request(`/api/admin/users/${userId}/models/provider`, {
    method: "PUT",
    body: JSON.stringify({ provider, disabled }),
  });
}

export async function resetPassword(userId: string, newPassword: string) {
  return request(`/api/admin/users/${userId}/password`, {
    method: "PUT",
    body: JSON.stringify({ new_password: newPassword }),
  });
}

// Containers
export async function pauseContainer(userId: string) {
  return request(`/api/admin/users/${userId}/container/pause`, { method: "POST" });
}

export async function resumeContainer(userId: string) {
  return request(`/api/admin/users/${userId}/container/resume`, { method: "POST" });
}

export async function destroyContainer(userId: string) {
  return request(`/api/admin/users/${userId}/container`, { method: "DELETE" });
}

export async function syncAllContainerStatuses(): Promise<{ updated: number; message: string }> {
  return request(`/api/admin/containers/sync`, { method: "POST" });
}

export async function syncContainerStatus(userId: string): Promise<{ status: string; docker_id: string }> {
  return request(`/api/admin/users/${userId}/container/sync`, { method: "POST" });
}

// Usage
export async function getUsageSummary(): Promise<UsageSummary> {
  return request<UsageSummary>("/api/admin/usage/summary");
}

export async function getUsageHistory(days = 30, userId?: string): Promise<UsageHistory> {
  const params = new URLSearchParams({ days: String(days) });
  if (userId) params.set("user_id", userId);
  return request<UsageHistory>(`/api/admin/usage/history?${params}`);
}

// Audit
export async function getAuditLogs(
  page = 1,
  pageSize = 20,
  userId?: string,
  action?: string,
  requestId?: string,
): Promise<PaginatedAuditLogs> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (userId) params.set("user_id", userId);
  if (action) params.set("action", action);
  if (requestId) params.set("request_id", requestId);
  return request<PaginatedAuditLogs>(`/api/admin/audit?${params}`);
}

export interface AdminModelItem {
  id: string;
  name?: string;
  enabled?: boolean;
}

export interface AdminProviderConfig {
  id?: string;
  name: string;
  providerType: string;
  api?: string;
  baseUrl?: string;
  apiKey?: string;
  apiKeyMasked?: string;
  configured?: boolean;
  enabled: boolean;
  isDefault?: boolean;
  models: AdminModelItem[];
}

export interface AdminModelsConfig {
  models: Array<{ id: string; name: string; provider: string; providerName: string }>;
  configuredModel: string;
  configuredProviders: Record<string, AdminProviderConfig>;
}

export async function getModelsConfig(): Promise<AdminModelsConfig> {
  return request<AdminModelsConfig>("/api/admin/models");
}

export async function updateModelsConfig(data: {
  defaultModel?: string;
  providers?: Record<string, AdminProviderConfig>;
}): Promise<AdminModelsConfig & { ok: boolean }> {
  return request<AdminModelsConfig & { ok: boolean }>("/api/admin/models", {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

// Connectivity probe — same backend endpoint the client uses, so admin and
// client share one source of truth for "can this provider actually answer?".
export interface ModelConnectionTestResult {
  ok: boolean;
  status: number;
  message: string;
  suggestion?: string;
  durationMs?: number;
}

export async function testModelConnection(data: {
  baseUrl: string;
  apiKey?: string;
  api?: string;
  model: string;
}): Promise<ModelConnectionTestResult> {
  return request<ModelConnectionTestResult>("/api/comworker/models/test", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function getCapabilityRegistry(): Promise<{ capabilities: Capability[] }> {
  return request<{ capabilities: Capability[] }>("/api/admin/capabilities/registry");
}

export async function getCapabilityDefaults(): Promise<{ defaults: CapabilityDefault[] }> {
  return request<{ defaults: CapabilityDefault[] }>("/api/admin/capabilities/defaults");
}

export async function putCapabilityDefaults(
  defaults: { capability: string; default_inject: boolean }[],
): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/api/admin/capabilities/defaults", {
    method: "PUT",
    body: JSON.stringify({ defaults }),
  });
}

export async function getUserCapabilities(
  userId: string,
): Promise<{ states: CapabilityState[]; user_caps: { capability: string; enabled: boolean }[] }> {
  return request(`/api/admin/users/${userId}/capabilities`);
}

export async function putUserCapabilities(
  userId: string,
  data: { capabilities: { capability: string; enabled: boolean }[]; remove: string[] },
): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/admin/users/${userId}/capabilities`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function bulkSetCapability(data: {
  capability: string;
  enabled: boolean;
  target: { user_ids?: string[]; all?: boolean; tiers?: string[] };
}): Promise<{ ok: boolean; affected: number }> {
  return request<{ ok: boolean; affected: number }>("/api/admin/capabilities/bulk", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function applyUserCapabilities(
  userId: string,
): Promise<{ ok: boolean; applied: boolean; toolsets: string[] }> {
  return request<{ ok: boolean; applied: boolean; toolsets: string[] }>(
    `/api/admin/users/${userId}/capabilities/apply`,
    { method: "POST" },
  );
}

export async function bulkApplyCapabilities(target: {
  user_ids?: string[];
  all?: boolean;
  tiers?: string[];
}): Promise<{
  ok: boolean;
  applied: string[];
  applied_count: number;
  skipped: { user_id: string; reason: string }[];
  skipped_count: number;
  failed: { user_id: string; error: string }[];
  failed_count: number;
}> {
  return request("/api/admin/capabilities/bulk-apply", {
    method: "POST",
    body: JSON.stringify({ target }),
  });
}

// Platform skills (admin)
export interface PlatformSkill {
  name: string;
  title?: string;
  description: string;
  enabled: boolean;
  created_at: string;
  installed: boolean;
}

export async function getPlatformSkills(): Promise<{ skills: PlatformSkill[] }> {
  return request<{ skills: PlatformSkill[] }>("/api/admin/skills");
}

export async function uploadPlatformSkill(file: File): Promise<{ ok: boolean; name: string }> {
  const token = getAccessToken();
  const formData = new FormData();
  formData.append("file", file);
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/api/admin/skills/upload`, {
    method: "POST",
    headers,
    body: formData,
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.json();
}

export async function deletePlatformSkill(name: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/admin/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export async function togglePlatformSkill(name: string, enabled: boolean): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/admin/skills/${encodeURIComponent(name)}/toggle?enabled=${enabled}`, { method: "PUT" });
}

// C 方案: 管理员把技能安装到指定用户 (A) / 推送给所有用户 (B)
export async function adminInstallSkillToUser(
  userId: string,
  name: string,
  source: "auto" | "platform" | "builtin" = "auto",
): Promise<{ ok: boolean; user_id: string; name: string }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/skills/install`, {
    method: "POST",
    body: JSON.stringify({ name, source }),
  });
}

export async function adminPushSkillToAllUsers(
  name: string,
): Promise<{ ok: boolean; name: string; total: number; pushed: number; failed: { user_id: string; error: string }[] }> {
  return request(`/api/admin/skills/${encodeURIComponent(name)}/push`, { method: "POST" });
}

// 内置 catalog 技能管理（仓库 + 按用户安装/卸载 + 默认安装开关）

export interface CatalogSkill {
  name: string
  title?: string
  category?: string
  description?: string
  tags?: string[]
  overridden?: boolean
}

export interface SkillEditBody {
  title?: string | null
  description?: string | null
  category?: string | null
  content?: string | null
}

export async function updateSkill(name: string, body: SkillEditBody): Promise<{ ok: boolean; name: string; kind: string }> {
  return request(`/api/admin/skills/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteSkill(name: string): Promise<{ ok: boolean; name: string; kind: string }> {
  return request(`/api/admin/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export interface CatalogSkillsResponse {
  categories: string[]
  skills: Record<string, CatalogSkill>
}

export interface UserInstalledSkills {
  user: string[]
  managed: string[]
}

export async function getAdminCatalogSkills(): Promise<CatalogSkillsResponse> {
  return request<CatalogSkillsResponse>("/api/admin/skills/catalog");
}

export async function getDefaultInstall(): Promise<{
  enabled: boolean;
  overrides?: Record<string, boolean>;
}> {
  return request<{ enabled: boolean; overrides?: Record<string, boolean> }>(
    "/api/admin/skills/default-install",
  );
}

export async function setDefaultInstall(enabled: boolean): Promise<{ enabled: boolean }> {
  return request<{ enabled: boolean }>("/api/admin/skills/default-install", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

export async function setSkillDefaultInstall(
  name: string,
  enabled: boolean | null,
): Promise<{ ok: boolean; name: string; enabled: boolean | null; overrides: Record<string, boolean> }> {
  return request(
    `/api/admin/skills/${encodeURIComponent(name)}/default-install`,
    {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    },
  );
}

export async function getAdminUserSkills(userId: string): Promise<UserInstalledSkills> {
  return request<UserInstalledSkills>(`/api/admin/users/${encodeURIComponent(userId)}/skills`);
}

export async function adminInstallManagedSkill(
  userId: string,
  name: string,
): Promise<{ ok: boolean; user_id: string; name: string; user: string[]; managed: string[] }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/skills/install`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function adminUninstallUserSkill(
  userId: string,
  name: string,
): Promise<{ ok: boolean; user_id: string; name: string; user: string[]; managed: string[] }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/skills/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

// ─── Platform MCP connectors (admin-managed) ───────────────────────────────

export interface Connector {
  id: string
  name: string
  display_name: string
  description: string
  icon?: string | null
  transport: string
  config_json: Record<string, unknown>
  cli_config_json?: Record<string, unknown> | null
  credential_strategy: string
  examples?: string | null
  has_shared_credential: boolean
  is_builtin: boolean
  is_default: boolean
  is_mandatory: boolean
  status: string
  created_at?: string | null
  updated_at?: string | null
}

export async function getAdminConnectors(): Promise<{ connectors: Connector[] }> {
  return request<{ connectors: Connector[] }>("/api/admin/connectors");
}

export async function createConnector(
  payload: Partial<Connector> & { name: string; display_name: string },
): Promise<{ ok: boolean; connector: Connector }> {
  return request("/api/admin/connectors", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateConnector(
  id: string,
  payload: Partial<Connector>,
): Promise<{ ok: boolean; connector: Connector }> {
  return request(`/api/admin/connectors/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export async function deleteConnector(id: string): Promise<{ ok: boolean }> {
  return request(`/api/admin/connectors/${id}`, { method: "DELETE" });
}

export async function setConnectorDefault(
  id: string,
  enabled: boolean,
): Promise<{ ok: boolean; is_default: boolean }> {
  return request(`/api/admin/connectors/${id}/default`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

export async function setConnectorMandatory(
  id: string,
  enabled: boolean,
): Promise<{ ok: boolean; is_mandatory: boolean }> {
  return request(`/api/admin/connectors/${id}/mandatory`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}

export async function pushConnectorToAllUsers(
  id: string,
): Promise<{ ok: boolean; name: string; total: number; pushed: number; failed: { user_id: string; error: string }[] }> {
  return request(`/api/admin/connectors/${id}/push`, { method: "POST" });
}

export async function adminInstallConnectorForUser(
  userId: string,
  connectorId: string,
): Promise<{ ok: boolean; name: string; user_id: string }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/connectors/${encodeURIComponent(connectorId)}/install`, {
    method: "POST",
  });
}

export async function adminUninstallConnectorForUser(
  userId: string,
  rowId: string,
): Promise<{ ok: boolean; name: string; user_id: string }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/connectors/${encodeURIComponent(rowId)}`, {
    method: "DELETE",
  });
}

// ─── Agent / 专家 templates (admin-managed) ────────────────────────────────

export interface Agent {
  id: string;
  agent_id: string;
  name: string;
  description: string;
  avatar?: string | null;
  system_prompt: string;
  builtin: boolean;
  readonly: boolean;
  system: boolean;
  is_default: boolean;
  is_enabled: boolean;
}

export async function listAgents(): Promise<{ agents: Agent[] }> {
  return request<{ agents: Agent[] }>("/api/admin/agents");
}

export async function createAgent(body: {
  agent_id: string;
  name: string;
  description?: string;
  avatar?: string | null;
  system_prompt?: string;
  is_default?: boolean;
  is_enabled?: boolean;
}): Promise<{ ok: boolean; agent_id: string; name: string }> {
  return request("/api/admin/agents", { method: "POST", body: JSON.stringify(body) });
}

export async function updateAgent(
  agentId: string,
  body: {
    name?: string;
    description?: string;
    avatar?: string | null;
    system_prompt?: string;
    is_default?: boolean;
    is_enabled?: boolean;
  },
): Promise<{ ok: boolean }> {
  return request(`/api/admin/agents/${encodeURIComponent(agentId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteAgent(agentId: string): Promise<{ ok: boolean }> {
  return request(`/api/admin/agents/${encodeURIComponent(agentId)}`, { method: "DELETE" });
}

export async function adminPushAgentToAllUsers(
  agentId: string,
): Promise<{ ok: boolean; agent_id: string; total: number; pushed: number; failed: { user_id: string; error: string }[] }> {
  return request(`/api/admin/agents/${encodeURIComponent(agentId)}/push`, { method: "POST" });
}

export async function adminAssignAgentToUser(
  userId: string,
  agentId: string,
): Promise<{ ok: boolean; user_id: string; agent_id: string; status: string }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/agents/${encodeURIComponent(agentId)}/assign`, {
    method: "POST",
  });
}

export async function adminUnassignAgentFromUser(
  userId: string,
  agentId: string,
): Promise<{ ok: boolean; user_id: string; agent_id: string; status: string }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/agents/${encodeURIComponent(agentId)}/unassign`, {
    method: "POST",
  });
}

// ─── 用户级 list/detail + 数据保全 + 指定用户推送 ───────────────────────

export async function getUserDataFootprint(userId: string): Promise<DataFootprint> {
  return request<DataFootprint>(`/api/admin/users/${encodeURIComponent(userId)}/data-footprint`);
}

export async function getUserDataIntegrity(userId: string): Promise<DataIntegrity> {
  return request<DataIntegrity>(`/api/admin/users/${encodeURIComponent(userId)}/data-integrity`);
}

export async function getUserConnectors(userId: string): Promise<UserConnectorItem[]> {
  return request<UserConnectorItem[]>(`/api/admin/users/${encodeURIComponent(userId)}/connectors`);
}

export async function getUserAgents(userId: string): Promise<UserAgentItem[]> {
  return request<UserAgentItem[]>(`/api/admin/users/${encodeURIComponent(userId)}/agents`);
}

export async function getUserContainer(userId: string): Promise<UserContainerDetail> {
  return request<UserContainerDetail>(`/api/admin/users/${encodeURIComponent(userId)}/container`);
}

export async function recreateUserContainer(userId: string): Promise<{ ok: boolean; preserved: boolean }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/container/recreate`, { method: "POST" });
}

export async function destroyUserContainer(
  userId: string,
  wipe_data: boolean,
  confirm: boolean,
): Promise<{ ok: boolean; wiped: boolean }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/container/destroy`, {
    method: "POST",
    body: JSON.stringify({ wipe_data, confirm }),
  });
}

export async function backupUserContainer(userId: string): Promise<Blob> {
  const token = getAccessToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/api/admin/users/${encodeURIComponent(userId)}/container/backup`, { headers });
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.blob();
}

export async function restoreUserContainer(userId: string, file: File): Promise<{ ok: boolean }> {
  const token = getAccessToken();
  const formData = new FormData();
  formData.append("file", file);
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/api/admin/users/${encodeURIComponent(userId)}/container/restore`, {
    method: "POST",
    headers,
    body: formData,
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.json();
}

type PushResult = { ok: boolean; name: string; total: number; pushed: number; failed: { user_id: string; error: string }[] };

export async function pushSkillToUsers(name: string, userIds: string[]): Promise<PushResult> {
  return request(`/api/admin/skills/${encodeURIComponent(name)}/push-users`, {
    method: "POST",
    body: JSON.stringify({ user_ids: userIds }),
  });
}

export async function pushConnectorToUsers(id: string, userIds: string[]): Promise<PushResult> {
  return request(`/api/admin/connectors/${encodeURIComponent(id)}/push-users`, {
    method: "POST",
    body: JSON.stringify({ user_ids: userIds }),
  });
}

export async function pushAgentToUsers(agentId: string, userIds: string[]): Promise<PushResult> {
  return request(`/api/admin/agents/${encodeURIComponent(agentId)}/push-users`, {
    method: "POST",
    body: JSON.stringify({ user_ids: userIds }),
  });
}

// ─── 用户知识库管理（管理端） ───────────────────────────────────────────────
function kbAgent(agentId?: string): string {
  return agentId && agentId.trim() ? `&agent_id=${encodeURIComponent(agentId)}` : "";
}

export async function getUserKnowledge(userId: string, agentId?: string): Promise<KnowledgeListResult> {
  return request<KnowledgeListResult>(
    `/api/admin/users/${encodeURIComponent(userId)}/knowledge?${kbAgent(agentId).replace(/^&/, "")}`,
  );
}

export async function readUserKnowledge(userId: string, path: string, agentId?: string): Promise<KnowledgeReadResult> {
  return request<KnowledgeReadResult>(
    `/api/admin/users/${encodeURIComponent(userId)}/knowledge/page?path=${encodeURIComponent(path)}${kbAgent(agentId)}`,
  );
}

export async function searchUserKnowledge(userId: string, q: string, agentId?: string): Promise<{ results: KnowledgeSearchResult[] }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/knowledge/search?q=${encodeURIComponent(q)}${kbAgent(agentId)}`);
}

export async function getUserKnowledgeGraph(userId: string, agentId?: string): Promise<KnowledgeGraphResult> {
  return request<KnowledgeGraphResult>(
    `/api/admin/users/${encodeURIComponent(userId)}/knowledge/graph?${kbAgent(agentId).replace(/^&/, "")}`,
  );
}

export async function writeUserKnowledge(userId: string, path: string, content: string, agentId?: string): Promise<KnowledgeReadResult> {
  return request<KnowledgeReadResult>(`/api/admin/users/${encodeURIComponent(userId)}/knowledge/page`, {
    method: "POST",
    body: JSON.stringify({ path, content, agent_id: agentId }),
  });
}

export async function mkdirUserKnowledge(userId: string, path: string, agentId?: string): Promise<{ ok: boolean }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/knowledge/mkdir`, {
    method: "POST",
    body: JSON.stringify({ path, agent_id: agentId }),
  });
}

export async function deleteUserKnowledge(userId: string, path: string, agentId?: string): Promise<{ ok: boolean }> {
  return request(`/api/admin/users/${encodeURIComponent(userId)}/knowledge/delete`, {
    method: "POST",
    body: JSON.stringify({ path, agent_id: agentId }),
  });
}

export async function uploadUserKnowledge(userId: string, file: File, agentId?: string, parent?: string): Promise<{ path: string; name: string }> {
  const token = getAccessToken();
  const formData = new FormData();
  formData.append("file", file);
  if (agentId && agentId.trim()) formData.append("agent_id", agentId);
  if (parent && parent.trim()) formData.append("parent", parent);
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/api/admin/users/${encodeURIComponent(userId)}/knowledge/upload`, {
    method: "POST",
    headers,
    body: formData,
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.json();
}

export async function downloadUserKnowledge(userId: string, path: string, agentId?: string): Promise<Blob> {
  const token = getAccessToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(
    `${API_BASE}/api/admin/users/${encodeURIComponent(userId)}/knowledge/file?path=${encodeURIComponent(path)}${kbAgent(agentId)}`,
    { headers },
  );
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.blob();
}

// ─── Platform branding config ───────────────────────────────────────────────

export interface PlatformConfig {
  name: string;
  logo: string | null;
}

export async function getPlatformConfig(): Promise<PlatformConfig> {
  return request("/api/admin/platform-config");
}

export async function updatePlatformConfig(body: {
  name: string;
  logo?: string | null;
}): Promise<{ ok: boolean; name: string }> {
  return request("/api/admin/platform-config", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function uploadPlatformLogo(file: File): Promise<{ ok: boolean; logo: string }> {
  const token = getAccessToken();
  const formData = new FormData();
  formData.append("file", file);
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/api/admin/platform-config/logo`, {
    method: "POST",
    headers,
    body: formData,
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.json();
}

