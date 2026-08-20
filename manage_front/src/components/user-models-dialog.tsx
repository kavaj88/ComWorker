"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { getUserModels, updateUserModels } from "@/lib/api";
import type { UserSummary, UserModelItem } from "@/types";
import { toast } from "sonner";

export function UserModelsDialog({
  user,
  onClose,
}: {
  user: UserSummary | null;
  onClose: () => void;
}) {
  const [providers, setProviders] = useState<UserModelItem[]>([]);
  const [allowed, setAllowed] = useState<Record<string, boolean>>({});
  const [defaultModel, setDefaultModel] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!user) return;
    setLoading(true);
    getUserModels(user.id)
      .then((data) => {
        setProviders(data.providers);
        const a: Record<string, boolean> = {};
        for (const p of data.providers) a[p.id] = p.allowed;
        setAllowed(a);
        setDefaultModel(data.defaultModel || "");
      })
      .catch((e) => toast.error(e.message))
      .finally(() => setLoading(false));
  }, [user]);

  // Default-model options: all enabled models of providers the user may use,
  // plus the currently selected default (so it stays selectable even if the
  // admin is mid-toggle).
  const defaultOptions = useMemo(() => {
    const opts: { id: string; label: string }[] = [];
    for (const p of providers) {
      if (!allowed[p.id]) continue;
      for (const m of p.models) {
        if (m.enabled === false) continue;
        const mid = `${p.id}/${m.id}`;
        opts.push({ id: mid, label: `${m.name || m.id} (${mid})` });
      }
    }
    if (defaultModel && !opts.some((o) => o.id === defaultModel)) {
      opts.push({ id: defaultModel, label: `${defaultModel} (当前)` });
    }
    return opts;
  }, [providers, allowed, defaultModel]);

  function toggle(pid: string, val: boolean) {
    setAllowed((prev) => ({ ...prev, [pid]: val }));
  }

  async function save() {
    if (!user) return;
    setSaving(true);
    try {
      const res = await updateUserModels(user.id, {
        providers: allowed,
        defaultModel: defaultModel || null,
      });
      setProviders(res.providers);
      setDefaultModel(res.defaultModel || "");
      const a: Record<string, boolean> = {};
      for (const p of res.providers) a[p.id] = p.allowed;
      setAllowed(a);
      toast.success("已保存该用户的模型权限");
      onClose();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={!!user} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>模型权限: {user?.username}</DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-8 text-gray-500">
            <Loader2 className="animate-spin mr-2" /> 加载中…
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-gray-600">
              勾选即代表该用户<b>可使用</b>对应供应商下的全部模型；不勾选则对其隐藏（不影响其他用户）。保存后立即生效，无需重启容器。
            </p>

            <div className="space-y-2 max-h-64 overflow-auto pr-1">
              {providers.map((p) => (
                <div
                  key={p.id}
                  className="flex items-center justify-between rounded-md border px-3 py-2"
                >
                  <div className="flex flex-col">
                    <span className="font-medium">{p.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {p.models.filter((m) => m.enabled !== false).length} 个模型 · {p.id}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {allowed[p.id] ? (
                      <Badge variant="default">可用</Badge>
                    ) : (
                      <Badge variant="secondary">已隐藏</Badge>
                    )}
                    <input
                      type="checkbox"
                      className="h-5 w-5 cursor-pointer accent-primary"
                      checked={!!allowed[p.id]}
                      onChange={(e) => toggle(p.id, e.target.checked)}
                      aria-label={`${p.name} 可用`}
                    />
                  </div>
                </div>
              ))}
            </div>

            <div>
              <Label>该用户默认模型</Label>
              <Select value={defaultModel || "__none__"} onValueChange={(v) => setDefaultModel(v === "__none__" || v === null ? "" : v)}>
                <SelectTrigger className="mt-1">
                  <SelectValue placeholder="跟随平台默认" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">跟随平台默认</SelectItem>
                  {defaultOptions.map((o) => (
                    <SelectItem key={o.id} value={o.id}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">
                不选则使用平台默认模型；用户自己在客户端选择的模型仍优先于此处。
              </p>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button disabled={loading || saving} onClick={save}>
            {saving ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
