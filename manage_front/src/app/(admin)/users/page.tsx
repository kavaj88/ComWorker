"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { getUsers, createUser, bulkCreateUsers, bulkSetCapability, bulkApplyCapabilities } from "@/lib/api";
import type { UserSummary, PaginatedUsers, CreateUserResponse, BulkCreateUsersResponse, BulkImportDetail } from "@/types";
import { toast } from "sonner";

export default function UsersPage() {
  const [data, setData] = useState<PaginatedUsers | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");

  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [createUsername, setCreateUsername] = useState("");
  const [createEmail, setCreateEmail] = useState("");
  const [createPassword, setCreatePassword] = useState("");
  const [createRole, setCreateRole] = useState("user");
  const [createTier, setCreateTier] = useState("free");
  const [createRuntimeMode, setCreateRuntimeMode] = useState("dedicated");
  const [createResult, setCreateResult] = useState<string | null>(null);

  // Bulk import
  const [showBulkImport, setShowBulkImport] = useState(false);
  const [bulkFile, setBulkFile] = useState<File | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [bulkResult, setBulkResult] = useState<BulkCreateUsersResponse | null>(null);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkCap, setBulkCap] = useState("web_search");
  const [showBulkAll, setShowBulkAll] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkApplyScope, setBulkApplyScope] = useState<"selected" | "all" | null>(null);
  const [bulkApplyBusy, setBulkApplyBusy] = useState(false);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getUsers(page, 20, search);
      setData(result);
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  const router = useRouter();

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  async function runBulk(scope: "selected" | "all", enabled: boolean) {
    setBulkBusy(true);
    try {
      const target = scope === "all" ? { all: true } : { user_ids: Array.from(selectedIds) };
      const res = await bulkSetCapability({ capability: bulkCap, enabled, target });
      toast.success(`已${enabled ? "开启" : "关闭"} ${res.affected} 个用户的「${bulkCap}」（对新建/重建容器生效）`);
      setSelectedIds(new Set());
      if (scope === "all") setShowBulkAll(false);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBulkBusy(false);
    }
  }

  async function handleBulkApply() {
    const scope = bulkApplyScope;
    if (!scope) return;
    setBulkApplyBusy(true);
    try {
      const target = scope === "all" ? { all: true } : { user_ids: Array.from(selectedIds) };
      const res = await bulkApplyCapabilities(target);
      const parts = [`应用 ${res.applied_count}`, `跳过 ${res.skipped_count}`];
      if (res.failed_count) parts.push(`失败 ${res.failed_count}`);
      toast.success(`热应用完成：${parts.join("，")}`);
      setSelectedIds(new Set());
      setBulkApplyScope(null);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBulkApplyBusy(false);
    }
  }

  async function handleCreateUser() {
    if (!createUsername.trim() || !createEmail.trim()) {
      toast.error("请填写用户名和邮箱");
      return;
    }
    if (createPassword && createPassword.length < 8) {
      toast.error("密码至少8位（留空则由系统生成）");
      return;
    }
    try {
      const res: CreateUserResponse = await createUser({
        username: createUsername.trim(),
        email: createEmail.trim(),
        password: createPassword || undefined,
        role: createRole,
        quota_tier: createTier,
        runtime_mode: createRuntimeMode,
      });
      if (res.initial_password) {
        toast.success("用户已创建，初始口令已生成（请抄送用户，首次登录需修改）");
        setCreateResult(res.initial_password);
      } else {
        toast.success("用户已创建");
        setShowCreate(false);
        resetCreateForm();
        fetchUsers();
      }
    } catch (err) {
      toast.error("创建失败", { description: err instanceof Error ? err.message : "" });
    }
  }

  function resetCreateForm() {
    setCreateUsername("");
    setCreateEmail("");
    setCreatePassword("");
    setCreateRole("user");
    setCreateTier("free");
    setCreateRuntimeMode("dedicated");
    setCreateResult(null);
  }

  async function handleBulkImport() {
    if (!bulkFile) {
      toast.error("请先选择 CSV 文件");
      return;
    }
    setImportBusy(true);
    try {
      const res = await bulkCreateUsers(bulkFile);
      setBulkResult(res);
      if (res.created > 0) fetchUsers();
      toast.success(`导入完成：新建 ${res.created}，跳过 ${res.skipped}，失败 ${res.failed}`);
    } catch (err) {
      toast.error("批量导入失败", { description: err instanceof Error ? err.message : "" });
    } finally {
      setImportBusy(false);
    }
  }

  const totalPages = data ? Math.ceil(data.total / 20) : 0;

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">用户管理</h2>

      <div className="mb-4 flex items-center gap-4">
        <Input
          placeholder="搜索用户名或邮箱..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          className="max-w-sm"
        />
        <Button onClick={() => setShowCreate(true)}>添加用户</Button>
        <Button variant="outline" onClick={() => { setShowBulkImport(true); setBulkResult(null); setBulkFile(null); }}>批量导入</Button>
      </div>

      {loading ? (
        <p className="text-gray-500">加载中...</p>
      ) : (
        <>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <Select value={bulkCap} onValueChange={(v: string | null) => v && setBulkCap(v)}>
              <SelectTrigger className="w-40"><SelectValue>{(v: string | null) => (v === "web_search" ? "联网搜索" : (v ?? ""))}</SelectValue></SelectTrigger>
              <SelectContent>
                <SelectItem value="web_search">联网搜索</SelectItem>
              </SelectContent>
            </Select>
            <Button size="sm" variant="outline" disabled={selectedIds.size === 0 || bulkBusy} onClick={() => runBulk("selected", true)}>为选中开启</Button>
            <Button size="sm" variant="outline" disabled={selectedIds.size === 0 || bulkBusy} onClick={() => runBulk("selected", false)}>为选中关闭</Button>
            <span className="text-xs text-gray-400">{selectedIds.size > 0 ? `已选 ${selectedIds.size} 个` : ""}</span>
            <Button size="sm" variant="outline" disabled={bulkBusy} onClick={() => setShowBulkAll(true)}>应用到全部…</Button>
            <span className="text-xs text-gray-400">|</span>
            <Button size="sm" variant="outline" disabled={selectedIds.size === 0 || bulkApplyBusy} onClick={() => setBulkApplyScope("selected")}>热应用选中…</Button>
            <Button size="sm" variant="outline" disabled={bulkApplyBusy} onClick={() => setBulkApplyScope("all")}>热应用全部…</Button>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10"><input type="checkbox" checked={!!data && data.items.length > 0 && data.items.every((u) => selectedIds.has(u.id))} onChange={() => { if (!data) return; const all = data.items.every((u) => selectedIds.has(u.id)); setSelectedIds(all ? new Set() : new Set(data.items.map((u) => u.id))); }} /></TableHead>
                <TableHead>用户名</TableHead>
                <TableHead>邮箱</TableHead>
                <TableHead>角色</TableHead>
                <TableHead>配额</TableHead>
                <TableHead>运行模式</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>今日用量</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.items.map((user) => (
                <TableRow key={user.id}>
                  <TableCell className="w-10"><input type="checkbox" checked={selectedIds.has(user.id)} onChange={() => toggleSelect(user.id)} /></TableCell>
                  <TableCell className="font-medium">{user.username}</TableCell>
                  <TableCell>{user.email}</TableCell>
                  <TableCell>
                    <Badge variant={user.role === "admin" ? "default" : "secondary"}>
                      {user.role}
                    </Badge>
                  </TableCell>
                  <TableCell>{user.quota_tier}</TableCell>
                  <TableCell>
                    <Badge variant={user.runtime_mode === "shared" ? "secondary" : "outline"}>
                      {user.runtime_mode}
                    </Badge>
                    {user.shared_agent_id ? (
                      <div className="text-xs text-muted-foreground mt-1">
                        {user.shared_agent_id}
                      </div>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    <Badge variant={user.is_active ? "default" : "destructive"}>
                      {user.is_active ? "正常" : "禁用"}
                    </Badge>
                  </TableCell>
                  <TableCell>{user.tokens_used_today.toLocaleString()}</TableCell>
                  <TableCell>{user.created_at ? new Date(user.created_at).toLocaleDateString() : "-"}</TableCell>
                  <TableCell>
                    <Button size="sm" variant="outline" onClick={() => router.push(`/users/${user.id}`)}>管理</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <div className="flex items-center justify-between mt-4">
            <p className="text-sm text-gray-500">共 {data?.total ?? 0} 个用户</p>
            <div className="space-x-2">
              <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button>
              <span className="text-sm">{page} / {totalPages}</span>
              <Button size="sm" variant="outline" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>下一页</Button>
            </div>
          </div>
        </>
      )}

      {/* Create User Dialog */}
      <Dialog open={showCreate} onOpenChange={(open) => { if (!open) { setShowCreate(false); setCreateUsername(""); setCreateEmail(""); setCreatePassword(""); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>添加用户</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>用户名 *</Label>
              <Input value={createUsername} onChange={(e) => setCreateUsername(e.target.value)} placeholder="用户名" />
            </div>
            <div>
              <Label>邮箱 *</Label>
              <Input value={createEmail} onChange={(e) => setCreateEmail(e.target.value)} placeholder="email@example.com" />
            </div>
            <div>
              <Label>密码（留空则系统自动生成强口令，用户首次登录需修改）</Label>
              <Input type="password" value={createPassword} onChange={(e) => setCreatePassword(e.target.value)} placeholder="留空=系统生成" />
            </div>
            {createResult ? (
              <div className="rounded-md border border-amber-300 bg-amber-50 p-3">
                <p className="text-sm font-medium text-amber-800">已生成初始口令（请抄送用户，关闭后不可再查看）：</p>
                <code className="mt-1 block break-all rounded bg-white px-2 py-1 text-sm text-amber-900">{createResult}</code>
              </div>
            ) : null}
            <div>
              <Label>角色</Label>
              <Select value={createRole} onValueChange={(v: string | null) => v && setCreateRole(v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="user">user</SelectItem>
                  <SelectItem value="admin">admin</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>配额等级</Label>
              <Select value={createTier} onValueChange={(v: string | null) => v && setCreateTier(v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="free">free</SelectItem>
                  <SelectItem value="basic">basic</SelectItem>
                  <SelectItem value="pro">pro</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>运行模式</Label>
              <Select value={createRuntimeMode} onValueChange={(v: string | null) => v && setCreateRuntimeMode(v)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="dedicated">dedicated</SelectItem>
                  <SelectItem value="shared">shared</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            {createResult ? (
              <Button onClick={() => { setShowCreate(false); resetCreateForm(); fetchUsers(); }}>完成</Button>
            ) : (
              <>
                <Button variant="outline" onClick={() => { setShowCreate(false); resetCreateForm(); }}>取消</Button>
                <Button onClick={handleCreateUser}>创建</Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk Import Dialog */}
      <Dialog open={showBulkImport} onOpenChange={(open) => { if (!open) { setShowBulkImport(false); setBulkResult(null); setBulkFile(null); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>批量导入用户</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-gray-600">
              CSV 表头：<code>username,email,password?,role?,quota_tier?,runtime_mode?</code>。
              password 留空则系统生成强口令并强制首次登录改密；已存在用户自动跳过。
            </p>
            <Input type="file" accept=".csv,text/csv" onChange={(e) => setBulkFile(e.target.files?.[0] ?? null)} />
            {bulkResult ? (
              <div className="space-y-2">
                <p className="text-sm">共 {bulkResult.total} 行：新建 <b>{bulkResult.created}</b>，跳过 <b>{bulkResult.skipped}</b>，失败 <b>{bulkResult.failed}</b></p>
                <div className="max-h-60 overflow-auto rounded border p-2 text-xs">
                  {bulkResult.details.map((d: BulkImportDetail, i: number) => (
                    <div key={i} className="flex items-center gap-2 py-0.5">
                      <span className={`inline-block w-14 shrink-0 ${d.status === "created" ? "text-green-600" : d.status === "skipped" ? "text-gray-400" : "text-red-600"}`}>{d.status}</span>
                      <span className="font-medium">{d.username}</span>
                      {d.initial_password ? <code className="text-amber-700">初始口令: {d.initial_password}</code> : null}
                      {d.reason ? <span className="text-gray-400">({d.reason})</span> : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setShowBulkImport(false); setBulkResult(null); setBulkFile(null); }}>关闭</Button>
            <Button disabled={importBusy || !bulkFile} onClick={handleBulkImport}>{importBusy ? "导入中…" : "开始导入"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={showBulkAll} onOpenChange={setShowBulkAll}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>应用到全部用户</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-gray-600">
            将对全部用户批量设置「{bulkCap}」。批量变更只对<b>新建/重建</b>容器生效；若随后触发大规模热补丁/重建，建议分批进行以免资源尖峰。确认继续？
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowBulkAll(false)}>取消</Button>
            <Button onClick={() => runBulk("all", true)}>确认开启</Button>
            <Button variant="outline" onClick={() => runBulk("all", false)}>确认关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={bulkApplyScope !== null} onOpenChange={(open) => !open && setBulkApplyScope(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>热应用能力配置（逐个重启容器）</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-gray-600">
            将对{bulkApplyScope === "all" ? "全部" : "选中"}用户重算能力配置并<b>逐个重启容器</b>，可能中断进行中的对话/cron。用户自配的密钥与配置会保留；未运行的容器将被跳过。确认继续？
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBulkApplyScope(null)}>取消</Button>
            <Button disabled={bulkApplyBusy} onClick={handleBulkApply}>{bulkApplyBusy ? "应用中…" : "确认热应用"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
