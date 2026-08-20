"use client";

import { useState } from "react";
import { Key, Copy, Check, RefreshCw } from "lucide-react";
import { generateApiToken } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const AGENT_EXAMPLE = `import json
import time
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE_URL = "http://YOUR_SERVER:8080"
API_TOKEN = "YOUR_API_TOKEN"  # 从前端 系统→API 页面生成


def api_request(path, method="GET", body=None, timeout=120):
    """发送认证 HTTP 请求。"""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        raise RuntimeError(f"API {method} {path} 失败 ({e.code}): {e.read().decode()}")


def call_agent(agent_id, message, session_key=None, poll_interval=2.0,
               poll_timeout=300, stable_seconds=15):
    """发送消息给 Agent 并轮询等待全部回复。

    Agent 可能产生多条回复（中间夹杂工具调用），需持续轮询直到
    消息数量稳定 stable_seconds 秒后才认为回复完成。

    Args:
        agent_id:        Agent ID（如 "main"）
        message:         用户消息
        session_key:     复用已有会话（为空则创建新会话）
        poll_interval:   轮询间隔（秒）
        poll_timeout:    最大等待时间（秒）
        stable_seconds:  消息数稳定多久后认为完成（秒）

    Returns:
        所有 assistant 回复文本列表，超时返回 None
    """
    if not session_key:
        session_key = f"agent:{agent_id}:session-{int(time.time() * 1000)}"
    encoded_key = session_key.replace(":", "%3A")

    # 获取发送前的消息数量
    try:
        before = api_request(f"/api/comworker/sessions/{encoded_key}")
        msg_count_before = len(before.get("messages", []))
    except RuntimeError:
        msg_count_before = 0

    # 发送消息
    result = api_request(
        f"/api/comworker/sessions/{encoded_key}/messages",
        method="POST",
        body={"message": message},
    )
    print(f"已发送, runId={result.get('runId')}")

    # 轮询等待回复（等消息数稳定 stable_seconds 秒）
    start = time.time()
    last_count = msg_count_before
    last_change = time.time()
    replies = []

    while time.time() - start < poll_timeout:
        time.sleep(poll_interval)
        try:
            session = api_request(f"/api/comworker/sessions/{encoded_key}")
        except RuntimeError:
            continue

        messages = session.get("messages", [])
        if len(messages) != last_count:
            last_count = len(messages)
            last_change = time.time()

        if len(messages) > msg_count_before:
            replies = [m.get("content", "") for m in messages[msg_count_before:]
                       if m.get("role") == "assistant"]

        if replies and (time.time() - last_change) >= stable_seconds:
            print(f"完成 ({time.time() - start:.1f}s)")
            return replies

        sys.stdout.write(f"\\r等待 Agent 回复... {int(time.time()-start)}s")
        sys.stdout.flush()

    print(f"\\n超时 ({poll_timeout}s)")
    return None


# ── 使用示例 ──────────────────────────────────────────────────────

replies = call_agent(agent_id="main", message="你好，请介绍一下自己")
if replies:
    for i, text in enumerate(replies):
        if len(replies) > 1:
            print(f"--- 回复 {i+1} ---")
        print(text)
else:
    print("无回复")
`;

const CLI_EXAMPLE = `# 使用 API Token 调用
python call_agent_api.py --api-token "eyJ..." --agent main -m "你好"

# 指定 Agent ID
python call_agent_api.py --api-token "eyJ..." --agent insurance -m "帮我分析保险方案"

# 复用已有会话（多轮对话）
python call_agent_api.py --api-token "eyJ..." --agent main -m "继续" --session "agent:main:session-123"

# 使用环境变量
export COMWORKER_API_TOKEN="eyJ..."
export COMWORKER_BASE_URL="http://your-server:8080"
python call_agent_api.py --agent main -m "你好"
`;

export default function ApiAccessPage() {
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleGenerate() {
    setLoading(true);
    setError(null);
    try {
      const res = await generateApiToken();
      setToken(res.api_token);
    } catch (e: unknown) {
      setError("生成失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  }

  function copyToClipboard(text: string, key: string) {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 2000);
  }

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">API 访问</h1>
        <p className="mt-1 text-sm text-gray-500">
          生成 API Token，通过 Python 脚本调用 Agent
        </p>
      </div>

      {error && (
        <div className="rounded-lg bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Token Section */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5 text-primary" />
            API Token
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-gray-500">
            API Token 有效期 365 天，用于程序化调用 Agent。
          </p>

          {token ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <code className="flex-1 rounded-lg bg-gray-100 px-4 py-3 text-sm text-green-600 font-mono break-all border border-gray-200">
                  {token}
                </code>
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() => copyToClipboard(token, "token")}
                  title="复制"
                >
                  {copied === "token" ? <Check className="text-green-600" /> : <Copy />}
                </Button>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-yellow-600">请妥善保存，Token 仅显示一次</span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleGenerate}
                  disabled={loading}
                  className="ml-auto flex items-center gap-1.5"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  重新生成
                </Button>
              </div>
            </div>
          ) : (
            <Button onClick={handleGenerate} disabled={loading}>
              {loading ? "生成中..." : "生成 API Token"}
            </Button>
          )}
        </CardContent>
      </Card>

      {/* CLI Usage */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>命令行调用</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => copyToClipboard(CLI_EXAMPLE, "cli")}
            >
              {copied === "cli" ? <><Check className="h-3.5 w-3.5 text-green-600" /> 已复制</> : <><Copy className="h-3.5 w-3.5" /> 复制</>}
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-gray-500">
            项目根目录下的 <code className="text-primary">call_agent_api.py</code> 可直接使用：
          </p>
          <pre className="rounded-lg bg-gray-100 border border-gray-200 p-4 text-sm text-gray-600 font-mono overflow-x-auto leading-relaxed">
            {CLI_EXAMPLE}
          </pre>
        </CardContent>
      </Card>

      {/* Python Example */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Python 调用示例</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => copyToClipboard(AGENT_EXAMPLE, "agent")}
            >
              {copied === "agent" ? <><Check className="h-3.5 w-3.5 text-green-600" /> 已复制</> : <><Copy className="h-3.5 w-3.5" /> 复制代码</>}
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-gray-500">
            发送消息给 Agent 并等待回复，可集成到自己的 Python 项目中。
          </p>
          <div className="text-xs text-gray-500 font-mono">
            端点: <code className="text-primary">POST /api/comworker/sessions/:key/messages</code>
            &nbsp;|&nbsp; 认证: <code className="text-primary">Bearer {'<API_TOKEN>'}</code>
          </div>
          <pre className="rounded-lg bg-gray-100 border border-gray-200 p-4 text-sm text-gray-600 font-mono overflow-x-auto max-h-[500px] overflow-y-auto leading-relaxed">
            {AGENT_EXAMPLE}
          </pre>
        </CardContent>
      </Card>
    </div>
  );
}
