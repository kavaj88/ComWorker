import { request } from "./api";

export interface GatewayToken {
  id: string;
  name: string;
  user_id: string;
  username: string;
  token_hint: string;
  allowed_models: string[];
  allowed_agents: string[];
  allowed_protocols: string[];
  enabled: boolean;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
  last_used_at: string | null;
  retain_content: boolean;
  retention_days: number;
  /** 完整明文令牌（仅单条查询返回；旧令牌可能为 null） */
  secret?: string | null;
}
export interface GatewayOptions {
  users: { id: string; username: string }[];
  models: {
    id: string;
    name: string;
    provider_id: string;
    protocols: string[];
  }[];
  gateway_path: string;
}
export interface GatewayRequest {
  id: string;
  token_id: string;
  token_name: string;
  user_id: string;
  username: string;
  model: string;
  provider_id: string;
  upstream_model: string;
  protocol: string;
  status_code: number | null;
  outcome: string;
  started_at: string;
  duration_ms: number | null;
  first_byte_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  usage: unknown;
  error_type: string | null;
  content_state?: string;
  request_content?: unknown;
  response_content?: unknown;
}
export interface UsageGroup {
  name: string | null;
  requests: number;
  input_tokens: number | null;
  output_tokens: number | null;
}
export interface GatewayUsage {
  requests: number;
  errors: number;
  input_tokens: number | null;
  output_tokens: number | null;
  known_usage_requests: number;
  log_queue_dropped: number;
  daily: {
    date: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
  }[];
  by_token: UsageGroup[];
  by_model: UsageGroup[];
  by_user: UsageGroup[];
}
export interface GatewayRoute {
  provider_id: string;
  name: string;
  protocols: string[];
}
export const protocols: Record<string, string> = {
  chat_completions: "OpenAI Chat Completions",
  responses: "OpenAI Responses",
  anthropic_messages: "Anthropic Messages",
};
export const gateway = <T>(path: string, method = "GET", body?: unknown) =>
  request<T>(`/api/admin/gateway${path}`, {
    method,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });

/** 获取令牌完整明文（管理员可见）。旧令牌可能返回 null。 */
export async function getTokenSecret(id: string): Promise<string | null> {
  const data = await gateway<{ secret: string | null }>(
    `/tokens/${encodeURIComponent(id)}`,
  );
  return data.secret;
}
export const formatTime = (value: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "—";
