"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronsUpDown, Search } from "lucide-react";
import { Input } from "@/components/ui/input";

export interface SearchableOption {
  value: string;
  label: string;
}

/**
 * 轻量可搜索下拉（不依赖 cmdk / Popover）。点击触发器弹出搜索框 + 过滤列表，
 * 支持按 label / value 模糊匹配。点击外部自动关闭。
 */
export function SearchableSelect({
  value,
  onValueChange,
  placeholder = "请选择…",
  options,
  className = "w-64",
}: {
  value: string;
  onValueChange: (v: string) => void;
  placeholder?: string;
  options: SearchableOption[];
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const selected = options.find((o) => o.value === value);
  const term = q.trim().toLowerCase();
  const filtered = term
    ? options.filter(
        (o) =>
          o.label.toLowerCase().includes(term) || o.value.toLowerCase().includes(term),
      )
    : options;

  return (
    <div className={className + " relative"} ref={ref}>
      <button
        type="button"
        onClick={() => {
          setOpen((o) => !o);
          setQ("");
        }}
        className="flex h-9 w-full items-center justify-between rounded-md border border-gray-300 bg-white px-3 py-1 text-sm shadow-sm hover:border-gray-400"
      >
        <span className={selected ? "truncate" : "truncate text-gray-400"}>
          {selected ? selected.label : placeholder}
        </span>
        <ChevronsUpDown size={15} className="ml-2 shrink-0 text-gray-400" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-md border border-gray-200 bg-white shadow-lg">
          <div className="relative p-2">
            <Search size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" />
            <Input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索…"
              className="pl-7 h-8 text-sm"
            />
          </div>
          <div className="max-h-56 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="px-3 py-2 text-xs text-gray-400">无匹配</div>
            ) : (
              filtered.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  onClick={() => {
                    onValueChange(o.value);
                    setOpen(false);
                    setQ("");
                  }}
                  className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-gray-100"
                >
                  <span className="truncate">{o.label}</span>
                  {o.value === value && (
                    <Check size={15} className="ml-2 shrink-0 text-blue-600" />
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
