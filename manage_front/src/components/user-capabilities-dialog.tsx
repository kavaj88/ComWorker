"use client";

import { useEffect, useState } from "react";
import { Loader2, Save } from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getUserCapabilities, putUserCapabilities } from "@/lib/api";
import type { UserSummary, CapabilityState } from "@/types";
import { toast } from "sonner";

type Choice = "default" | "on" | "off";

const CHOICE_LABEL: Record<Choice, string> = {
  default: "跟随默认",
  on: "强制开启",
  off: "强制关闭",
};

export function UserCapabilitiesDialog({
  user,
  onClose,
}: {
  user: UserSummary | null;
  onClose: () => void;
}) {
  const [states, setStates] = useState<CapabilityState[]>([]);
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [hadOverride, setHadOverride] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!user) return;
    setLoading(true);
    getUserCapabilities(user.id)
      .then((data) => {
        setStates(data.states);
        const c: Record<string, Choice> = {};
        const h: Record<string, boolean> = {};
        for (const s of data.states) {
          h[s.capability] = s.user_override;
          c[s.capability] = s.user_override ? (s.user_enabled ? "on" : "off") : "default";
        }
        setChoices(c);
        setHadOverride(h);
      })
      .catch((e) => toast.error(e.message))
      .finally(() => setLoading(false));
  }, [user]);

  async function save() {
    if (!user) return;
    setSaving(true);
    const capabilities: { capability: string; enabled: boolean }[] = [];
    const remove: string[] = [];
    for (const s of states) {
      const choice = choices[s.capability];
      if (choice === "on") capabilities.push({ capability: s.capability, enabled: true });
      else if (choice === "off") capabilities.push({ capability: s.capability, enabled: false });
      else if (hadOverride[s.capability]) remove.push(s.capability);
    }
    try {
      await putUserCapabilities(user.id, { capabilities, remove });
      toast.success("已保存。对该用户新建/重建容器生效；已有容器需重建或热补丁。");
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
          <DialogTitle>能力配置 · {user?.username}</DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center gap-2 py-8 text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中...
          </div>
        ) : (
          <div className="space-y-3 py-2">
            <p className="text-xs text-gray-500">
              「跟随默认」继承平台默认注入；「强制开启/关闭」对该用户覆盖默认。变更对该用户的<b>新建/重建</b>容器生效。
            </p>
            {states.map((s) => (
              <div key={s.capability} className="flex items-center justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{s.label}</span>
                    {s.placeholder ? (
                      <Badge variant="secondary">尚未实现</Badge>
                    ) : (
                      <Badge variant={s.effective_enabled ? "default" : "secondary"}>
                        {s.effective_enabled ? "已生效" : "未生效"}
                      </Badge>
                    )}
                    <span className="text-xs text-gray-400">
                      {s.placeholder
                        ? "路线图占位项"
                        : s.source === "user"
                          ? "来自用户覆盖"
                          : s.source === "default"
                            ? "来自默认"
                            : "未开启"}
                    </span>
                  </div>
                  {!s.placeholder && !s.platform_key_configured && (
                    <span className="text-xs text-amber-600">平台密钥未配置</span>
                  )}
                </div>
                <Select
                  value={choices[s.capability]}
                  disabled={!!s.placeholder}
                  onValueChange={(v) => setChoices((p) => ({ ...p, [s.capability]: v as Choice }))}
                >
                  <SelectTrigger className="w-32">
                    <SelectValue>{(v: string | null) => (v ? CHOICE_LABEL[v as Choice] ?? v : "")}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="default">跟随默认</SelectItem>
                    <SelectItem value="on">强制开启</SelectItem>
                    <SelectItem value="off">强制关闭</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            ))}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={save} disabled={saving || loading}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Save className="h-4 w-4 mr-2" />}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
