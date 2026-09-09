"use client";
import { useEffect, useState } from "react";
import {
  gateway,
  GatewayOptions,
  GatewayRequest,
  GatewayToken,
  GatewayUsage,
  UsageGroup,
  protocols,
  formatTime,
} from "@/lib/gateway-api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { selectClass } from "./token-editor";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";

export function RequestPanel({
  options,
  tokens,
}: {
  options: GatewayOptions;
  tokens: GatewayToken[];
}) {
  const [filters, setFilters] = useState({
    token_id: "",
    user_id: "",
    model: "",
    protocol: "",
    status: "",
    days: "7",
  });
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{
    items: GatewayRequest[];
    total: number;
  } | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const query = new URLSearchParams({
    ...filters,
    page: String(page),
    page_size: "20",
  }).toString();
  useEffect(() => {
    let active = true;
    gateway<{ items: GatewayRequest[]; total: number }>(`/requests?${query}`)
      .then((data) => {
        if (active) setResult(data);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [query, revision]);
  function change(key: keyof typeof filters, value: string) {
    setResult(null);
    setError("");
    setFilters({ ...filters, [key]: value });
    setPage(1);
  }
  const fields: {
    key: keyof typeof filters;
    label: string;
    items: [string, string][];
  }[] = [
    {
      key: "token_id",
      label: "全部令牌",
      items: tokens.map((t) => [t.id, t.name]),
    },
    {
      key: "user_id",
      label: "全部用户",
      items: options.users.map((u) => [u.id, u.username]),
    },
    {
      key: "model",
      label: "全部模型",
      items: options.models.map((m) => [m.id, m.name]),
    },
    { key: "protocol", label: "全部协议", items: Object.entries(protocols) },
    {
      key: "status",
      label: "全部结果",
      items: [
        ["success", "成功"],
        ["error", "错误"],
        ["cancelled", "已取消"],
      ],
    },
  ];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {fields.map((field) => (
          <select
            key={field.key}
            aria-label={field.label}
            className={selectClass}
            value={filters[field.key]}
            onChange={(e) => change(field.key, e.target.value)}
          >
            <option value="">{field.label}</option>
            {field.items.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        ))}
        <select
          aria-label="时间范围"
          className={selectClass}
          value={filters.days}
          onChange={(e) => change("days", e.target.value)}
        >
          {[1, 7, 30, 90].map((d) => (
            <option key={d} value={d}>
              最近 {d} 天
            </option>
          ))}
        </select>
        <Button
          variant="outline"
          onClick={() => {
            setResult(null);
            setError("");
            setRevision(revision + 1);
          }}
        >
          刷新
        </Button>
      </div>
      {error ? (
        <p role="alert" className="text-red-600">
          {error}
        </p>
      ) : !result ? (
        <p role="status" className="p-10 text-center text-gray-500">
          正在加载请求记录…
        </p>
      ) : (
        <>
          <div className="overflow-auto rounded-xl border bg-white">
            <table className="w-full text-sm text-left">
              <thead className="bg-gray-50 text-gray-500">
                <tr>
                  {[
                    "时间 / 令牌",
                    "用户 / 模型",
                    "协议",
                    "结果",
                    "耗时",
                    "输入 / 输出",
                    "详情",
                  ].map((h) => (
                    <th key={h} className="p-3 whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.items.map((row) => (
                  <tr key={row.id} className="border-t">
                    <td className="p-3 whitespace-nowrap">
                      {formatTime(row.started_at)}
                      <div className="text-xs text-gray-500">
                        {row.token_name || "—"}
                      </div>
                    </td>
                    <td className="p-3">
                      {row.username}
                      <div className="text-xs text-gray-500 break-all">
                        {row.model}
                      </div>
                    </td>
                    <td className="p-3">
                      {protocols[row.protocol] || row.protocol}
                    </td>
                    <td className="p-3">
                      {{ success: "成功", error: "错误", cancelled: "取消" }[
                        row.outcome
                      ] || row.outcome}{" "}
                      {row.status_code ?? "—"}
                    </td>
                    <td className="p-3 whitespace-nowrap">
                      {row.duration_ms == null
                        ? "未知"
                        : `${row.duration_ms} ms`}
                    </td>
                    <td className="p-3 whitespace-nowrap">
                      {row.input_tokens ?? "未知"} /{" "}
                      {row.output_tokens ?? "未知"}
                    </td>
                    <td className="p-3">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setSelected(row.id)}
                      >
                        查看
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!result.items.length && (
              <p className="p-12 text-center text-gray-500">
                此筛选条件下暂无请求记录
              </p>
            )}
          </div>
          <div className="flex justify-between items-center text-sm">
            <span>
              共 {result.total} 条 · 第 {page} 页
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={page <= 1}
                onClick={() => {
                  setResult(null);
                  setPage(page - 1);
                }}
              >
                上一页
              </Button>
              <Button
                variant="outline"
                disabled={page * 20 >= result.total}
                onClick={() => {
                  setResult(null);
                  setPage(page + 1);
                }}
              >
                下一页
              </Button>
            </div>
          </div>
        </>
      )}
      {selected && (
        <RequestDetails
          key={selected}
          id={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
function RequestDetails({ id, onClose }: { id: string; onClose: () => void }) {
  const [data, setData] = useState<GatewayRequest | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    gateway<GatewayRequest>(`/requests/${encodeURIComponent(id)}`)
      .then((d) => {
        if (active) setData(d);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [id]);
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-3xl max-h-[90vh] overflow-auto">
        <DialogTitle>请求详情</DialogTitle>
        {error ? (
          <p role="alert">{error}</p>
        ) : !data ? (
          <p>加载中…</p>
        ) : (
          <>
            <p className="text-xs break-all">请求 ID：{data.id}</p>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              {Object.entries({
                用户: data.username,
                令牌: data.token_name,
                模型: data.model,
                上游模型: data.upstream_model,
                供应商: data.provider_id,
                协议: protocols[data.protocol] || data.protocol,
                状态码: data.status_code ?? "未知",
                结果: data.outcome,
                耗时:
                  data.duration_ms == null ? "未知" : `${data.duration_ms} ms`,
                首字节:
                  data.first_byte_ms == null
                    ? "未知"
                    : `${data.first_byte_ms} ms`,
                输入用量: data.input_tokens ?? "未知",
                输出用量: data.output_tokens ?? "未知",
                错误类型: data.error_type || "无",
              }).map(([label, value]) => (
                <div key={label}>
                  <dt className="text-gray-500">{label}</dt>
                  <dd className="break-all">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="rounded-md bg-amber-50 p-3 text-amber-800">
              正文：
              {{
                disabled: "未开启留存",
                available: "可查看",
                expired: "已过保留期",
                truncated: "超出大小限制，内容已截断",
              }[data.content_state || ""] || "不可用"}
            </p>
            {["available", "truncated"].includes(data.content_state || "") &&
              (
                [
                  ["请求正文", data.request_content],
                  ["响应正文", data.response_content],
                ] as const
              ).map(([label, value]) => (
                <div key={label}>
                  <h3 className="mb-2 font-medium">{label}</h3>
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-gray-100 p-3 text-xs">
                    {typeof value === "string"
                      ? value
                      : JSON.stringify(value ?? null, null, 2)}
                  </pre>
                </div>
              ))}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
export function UsagePanel({ tokens }: { tokens: GatewayToken[] }) {
  const [days, setDays] = useState("7");
  const [token, setToken] = useState("");
  const [data, setData] = useState<GatewayUsage | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    gateway<GatewayUsage>(
      `/usage?${new URLSearchParams({ days, token_id: token })}`,
    )
      .then((d) => {
        if (active) setData(d);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [days, token, revision]);
  function reset() {
    setData(null);
    setError("");
  }
  return (
    <div className="space-y-5">
      <div className="flex gap-2">
        <select
          aria-label="统计时间范围"
          className={selectClass}
          value={days}
          onChange={(e) => {
            reset();
            setDays(e.target.value);
          }}
        >
          {[1, 7, 30, 90].map((d) => (
            <option key={d} value={d}>
              最近 {d} 天
            </option>
          ))}
        </select>
        <select
          aria-label="统计令牌"
          className={selectClass}
          value={token}
          onChange={(e) => {
            reset();
            setToken(e.target.value);
          }}
        >
          <option value="">全部令牌</option>
          {tokens.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <Button
          variant="outline"
          onClick={() => {
            reset();
            setRevision(revision + 1);
          }}
        >
          刷新
        </Button>
      </div>
      {error ? (
        <p role="alert" className="text-red-600">
          {error}
        </p>
      ) : !data ? (
        <p role="status">加载用量中…</p>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {Object.entries({
              总请求: data.requests,
              错误请求: data.errors,
              已知输入用量: data.input_tokens,
              已知输出用量: data.output_tokens,
            }).map(([label, value]) => (
              <div key={label} className="rounded-xl border bg-white p-5">
                <p className="text-sm text-gray-500">{label}</p>
                <p className="mt-2 text-3xl tabular-nums font-semibold">
                  {value == null ? "未知" : value.toLocaleString()}
                </p>
              </div>
            ))}
          </div>
          <p className="text-xs text-gray-500">
            {data.known_usage_requests} / {data.requests}{" "}
            次请求包含已知用量；未知用量不作为零用量。日志队列丢弃：
            {data.log_queue_dropped} 条。
          </p>
          <div className="rounded-xl border bg-white p-5">
            <h3 className="font-medium mb-4">每日调用趋势</h3>
            {data.requests ? (
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.daily}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="date" fontSize={11} />
                    <YAxis allowDecimals={false} fontSize={11} />
                    <Tooltip />
                    <Bar
                      dataKey="requests"
                      name="请求数"
                      fill="#334155"
                      radius={[4, 4, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="py-12 text-center text-gray-500">
                该时间范围暂无调用数据
              </p>
            )}
          </div>
          <div className="grid lg:grid-cols-3 gap-4">
            <Ranking title="令牌排行" rows={data.by_token} />
            <Ranking title="模型排行" rows={data.by_model} />
            <Ranking title="用户排行" rows={data.by_user} />
          </div>
        </>
      )}
    </div>
  );
}
function Ranking({ title, rows }: { title: string; rows: UsageGroup[] }) {
  return (
    <section className="rounded-xl border bg-white p-4">
      <h3 className="font-medium mb-3">{title}</h3>
      {!rows.length ? (
        <p className="text-sm text-gray-500">暂无数据</p>
      ) : (
        <ol className="space-y-3">
          {[...rows]
            .sort((a, b) => b.requests - a.requests)
            .map((row, index) => (
              <li key={`${row.name}-${index}`} className="text-sm">
                <div className="flex justify-between gap-2">
                  <span className="break-all">
                    {index + 1}. {row.name}
                  </span>
                  <span className="shrink-0">{row.requests} 次</span>
                </div>
                <p className="text-xs text-gray-500">
                  输入{" "}
                  {row.input_tokens == null
                    ? "未知"
                    : row.input_tokens.toLocaleString()}{" "}
                  · 输出{" "}
                  {row.output_tokens == null
                    ? "未知"
                    : row.output_tokens.toLocaleString()}
                </p>
              </li>
            ))}
        </ol>
      )}
    </section>
  );
}
