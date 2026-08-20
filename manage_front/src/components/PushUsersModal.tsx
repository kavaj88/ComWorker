"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2, Check, Search, Users } from "lucide-react";
import { getUsers } from "@/lib/api";
import type { UserSummary } from "@/types";

export default function PushUsersModal({
  open,
  title = "推送给指定用户",
  confirmLabel = "推送",
  onClose,
  onPush,
}: {
  open: boolean;
  title?: string;
  confirmLabel?: string;
  onClose: () => void;
  onPush: (userIds: string[]) => Promise<void>;
}) {
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setErr("");
    setSelected(new Set());
    getUsers(1, 300, search)
      .then((res) => {
        if (active) setUsers(res.items || []);
      })
      .catch(() => {
        if (active) setUsers([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open, search]);

  const allSelected = useMemo(
    () => users.length > 0 && users.every((u) => selected.has(u.id)),
    [users, selected],
  );

  if (!open) return null;

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => {
      if (users.length > 0 && users.every((u) => prev.has(u.id))) {
        return new Set();
      }
      return new Set(users.map((u) => u.id));
    });
  }

  async function confirm() {
    if (selected.size === 0) return;
    setBusy(true);
    setErr("");
    try {
      await onPush(Array.from(selected));
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "推送失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-1">{title}</h2>
        <p className="text-xs text-gray-500 mb-3">已选 {selected.size} 个用户（将并发推送到其容器内）</p>
        <input
          autoFocus
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索用户名 / 邮箱..."
          className="w-full rounded border px-2 py-1.5 text-sm mb-2"
        />
        {loading ? (
          <p className="text-sm text-muted-foreground">加载中...</p>
        ) : users.length === 0 ? (
          <p className="text-sm text-muted-foreground">没有匹配的用户。</p>
        ) : (
          <div className="max-h-72 overflow-y-auto divide-y">
            <label className="flex items-center gap-2 px-2 py-2 hover:bg-muted rounded cursor-pointer">
              <input type="checkbox" checked={allSelected} onChange={toggleAll} />
              <span className="text-sm font-medium text-gray-600">全选 / 取消全选（{users.length}）</span>
            </label>
            {users.map((u) => (
              <label key={u.id} className="flex items-center gap-2 px-2 py-2 hover:bg-muted rounded cursor-pointer">
                <span className="relative flex h-4 w-4 items-center justify-center">
                  <input type="checkbox" checked={selected.has(u.id)} onChange={() => toggle(u.id)} className="peer sr-only" />
                  <span className="h-4 w-4 rounded border border-gray-300 peer-checked:bg-blue-600 peer-checked:border-blue-600 flex items-center justify-center">
                    {selected.has(u.id) && <Check size={12} className="text-white" />}
                  </span>
                </span>
                <span className="text-sm flex-1">
                  <b>{u.username}</b>
                  {u.email ? <span className="text-muted-foreground ml-2">{u.email}</span> : null}
                </span>
                <span className="text-[10px] text-muted-foreground">{u.role}</span>
              </label>
            ))}
          </div>
        )}
        {err && <div className="mt-2 text-sm text-red-600">{err}</div>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded border px-3 py-1.5 text-sm">取消</button>
          <button
            onClick={confirm}
            disabled={selected.size === 0 || busy}
            className="inline-flex items-center gap-1 rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-40"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Users size={14} />}
            {confirmLabel}（{selected.size}）
          </button>
        </div>
      </div>
    </div>
  );
}
