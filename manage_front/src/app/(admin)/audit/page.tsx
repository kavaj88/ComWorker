"use client";

import { useEffect, useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { getAuditLogs } from "@/lib/api";
import type { PaginatedAuditLogs } from "@/types";

const ACTION_OPTIONS = [
  { value: "all", label: "全部操作" },
  { value: "login", label: "登录" },
  { value: "llm_call", label: "LLM 调用" },
  { value: "container_create", label: "容器创建" },
  { value: "container_pause", label: "容器暂停" },
  { value: "container_destroy", label: "容器销毁" },
];

function truncate(value: string | null | undefined, max = 60): string {
  if (!value) return "-";
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

export default function AuditPage() {
  const [data, setData] = useState<PaginatedAuditLogs | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [actionFilter, setActionFilter] = useState("all");
  const [requestIdFilter, setRequestIdFilter] = useState("");
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getAuditLogs(
        page,
        20,
        undefined,
        actionFilter === "all" ? undefined : actionFilter,
        requestIdFilter.trim() || undefined,
      ));
    } finally {
      setLoading(false);
    }
  }, [page, actionFilter, requestIdFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const totalPages = data ? Math.ceil(data.total / 20) : 0;

  const copy = async (value: string, rowId: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedId(rowId);
      window.setTimeout(() => setCopiedId(null), 1200);
    } catch {
      // clipboard might be blocked — silent fail
    }
  };

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">审计日志</h2>

      <div className="flex flex-wrap gap-3 mb-4">
        <Select value={actionFilter} onValueChange={(v: string | null) => { setActionFilter(v ?? "all"); setPage(1); }}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="全部操作" />
          </SelectTrigger>
          <SelectContent>
            {ACTION_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={requestIdFilter}
            onChange={(e) => { setRequestIdFilter(e.target.value); setPage(1); }}
            placeholder="按 Request-ID 过滤"
            className="h-9 rounded-md border px-3 text-sm font-mono w-72"
          />
          {requestIdFilter && (
            <Button size="sm" variant="ghost" onClick={() => { setRequestIdFilter(""); setPage(1); }}>
              清空
            </Button>
          )}
        </div>
      </div>

      {loading ? (
        <p className="text-gray-500">加载中...</p>
      ) : (
        <>
          <div className="rounded-md border overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[160px]">时间</TableHead>
                  <TableHead className="w-[120px]">用户</TableHead>
                  <TableHead className="w-[140px]">操作</TableHead>
                  <TableHead className="w-[140px]">资源</TableHead>
                  <TableHead className="w-[120px]">IP</TableHead>
                  <TableHead className="w-[80px]">状态</TableHead>
                  <TableHead className="w-[200px]">Request-ID</TableHead>
                  <TableHead>详情</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.items.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="whitespace-nowrap text-xs">
                      {log.created_at ? new Date(log.created_at).toLocaleString() : "-"}
                    </TableCell>
                    <TableCell className="text-sm">{log.username ?? "系统"}</TableCell>
                    <TableCell className="text-sm font-mono">{log.action}</TableCell>
                    <TableCell className="text-sm">{log.resource ?? "-"}</TableCell>
                    <TableCell className="font-mono text-xs">{log.ip ?? "-"}</TableCell>
                    <TableCell className="text-xs">
                      {log.status_code != null ? (
                        <span className={log.status_code >= 400 ? "text-red-600 font-medium" : "text-green-700"}>
                          {log.status_code}
                        </span>
                      ) : "-"}
                    </TableCell>
                    <TableCell>
                      {log.request_id ? (
                        <button
                          type="button"
                          onClick={() => copy(log.request_id!, log.id)}
                          className="font-mono text-xs underline-offset-2 hover:underline"
                          title="点击复制"
                        >
                          {copiedId === log.id ? "已复制" : truncate(log.request_id, 16)}
                        </button>
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </TableCell>
                    <TableCell className="max-w-md">
                      <div className="text-sm">{truncate(log.detail, 80)}</div>
                      {log.user_agent && (
                        <div className="text-[10px] text-gray-400 font-mono mt-0.5">
                          {truncate(log.user_agent, 80)}
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
                {data?.items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={8} className="text-center text-gray-500 py-8">
                      暂无审计日志
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between mt-4">
            <p className="text-sm text-gray-500">共 {data?.total ?? 0} 条记录</p>
            <div className="space-x-2">
              <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button>
              <span className="text-sm">{page} / {totalPages || 1}</span>
              <Button size="sm" variant="outline" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>下一页</Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}