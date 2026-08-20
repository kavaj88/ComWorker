"use client";

import { useEffect, useState } from "react";
import { getUsers } from "@/lib/api";
import type { UserSummary } from "@/types";

export default function UserPickerModal({
  open,
  title = "选择用户",
  onClose,
  onSelect,
}: {
  open: boolean;
  title?: string;
  onClose: () => void;
  onSelect: (user: UserSummary) => void;
}) {
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    getUsers(1, 200, search)
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

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-3">{title}</h2>
        <input
          autoFocus
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索用户名 / 邮箱..."
          className="w-full rounded border px-2 py-1.5 text-sm mb-3"
        />
        {loading ? (
          <p className="text-sm text-muted-foreground">加载中...</p>
        ) : users.length === 0 ? (
          <p className="text-sm text-muted-foreground">没有匹配的用户。</p>
        ) : (
          <div className="max-h-80 overflow-y-auto divide-y">
            {users.map((u) => (
              <button
                key={u.id}
                onClick={() => onSelect(u)}
                className="w-full text-left px-2 py-2 hover:bg-muted rounded flex items-center justify-between"
              >
                <span className="text-sm">
                  <b>{u.username}</b>
                  {u.email ? <span className="text-muted-foreground ml-2">{u.email}</span> : null}
                </span>
                <span className="text-[10px] text-muted-foreground">{u.role}</span>
              </button>
            ))}
          </div>
        )}
        <div className="mt-4 flex justify-end">
          <button onClick={onClose} className="rounded border px-3 py-1.5 text-sm">
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
