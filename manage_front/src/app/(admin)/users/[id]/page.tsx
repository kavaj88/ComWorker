"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { toast } from "sonner";
import {
  getUserDataFootprint, getUserDataIntegrity, getUserConnectors, getUserAgents, getUserContainer,
  recreateUserContainer, destroyUserContainer, backupUserContainer, restoreUserContainer,
  updateUser, resetPassword, getUserConfiguredModels, type ConfiguredModels,
  type CatalogSkill, type Agent, type Connector,
  applyUserCapabilities,
  getAdminUserSkills, adminInstallManagedSkill, adminUninstallUserSkill,
  listAgents, adminAssignAgentToUser, adminUnassignAgentFromUser,
  getAdminConnectors, adminInstallConnectorForUser, adminUninstallConnectorForUser,
  getAdminCatalogSkills,
  getUserUsage,
  setUserProviderDisabled,
  getUserKnowledge, readUserKnowledge, searchUserKnowledge, getUserKnowledgeGraph,
  writeUserKnowledge, mkdirUserKnowledge, deleteUserKnowledge, uploadUserKnowledge, downloadUserKnowledge,
} from "@/lib/api";
import { UserCapabilitiesDialog } from "@/components/user-capabilities-dialog";
import { UserModelsDialog } from "@/components/user-models-dialog";
import { SearchableSelect } from "@/components/ui/searchable-select";
import { ResizableModal } from "@/components/ui/resizable-modal";
import {
  BookOpen, Search, FilePlus, FolderPlus, Save, Trash2, Download, Upload,
  GitBranch, Pencil, RefreshCw, X, Loader2, Folder, FileText, Link as LinkIcon,
  Bot, Package, Check, AlertTriangle, Plug,
} from "lucide-react";
import type {
  DataFootprint, DataIntegrity, UserConnectorItem, UserAgentItem, UserContainerDetail,
  UserSummary, KnowledgeListResult, KnowledgeReadResult, KnowledgeSearchResult,
  KnowledgeGraphResult, KnowledgePageMeta, KnowledgeDirectoryMeta,
} from "@/types";

const TABS = [
  { key: "account", label: "账号" },
  { key: "skills", label: "技能" },
  { key: "agents", label: "专家" },
  { key: "connectors", label: "连接器" },
  { key: "knowledge", label: "知识库" },
  { key: "models", label: "模型" },
  { key: "container", label: "容器" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function UserDetailPage() {
  const params = useParams();
  const userId = String(params.id);
  const router = useRouter();
  const [tab, setTab] = useState<TabKey>("account");
  const dialogUser = { id: userId } as UserSummary;

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <Button variant="outline" size="sm" onClick={() => router.push("/users")}>← 返回用户列表</Button>
        <h2 className="text-2xl font-bold">用户详情</h2>
        <Badge variant="secondary" className="font-mono">{userId}</Badge>
      </div>

      <div className="flex gap-1 border-b mb-6 flex-wrap">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors " +
              (tab === t.key
                ? "border-gray-900 text-gray-900"
                : "border-transparent text-gray-500 hover:text-gray-800")
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "account" && <AccountTab userId={userId} dialogUser={dialogUser} />}
      {tab === "skills" && <SkillsTab userId={userId} />}
      {tab === "agents" && <AgentsTab userId={userId} />}
      {tab === "connectors" && <ConnectorsTab userId={userId} />}
      {tab === "knowledge" && <KnowledgeTab userId={userId} />}
      {tab === "models" && <ModelsTab dialogUser={dialogUser} />}
      {tab === "container" && <ContainerTab userId={userId} />}
    </div>
  );
}

// ─── 账号 tab：资料 + 密码 + 能力 + 数据足迹 + token 统计 ──────────────────
function AccountTab({ userId, dialogUser }: { userId: string; dialogUser: UserSummary }) {
  const [footprint, setFootprint] = useState<DataFootprint | null>(null);
  const [integrity, setIntegrity] = useState<DataIntegrity | null>(null);
  const [usage, setUsage] = useState<{ tokens_today: number; tokens_this_week: number; tokens_total: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [capOpen, setCapOpen] = useState(false);

  // profile edit
  const [role, setRole] = useState("user");
  const [tier, setTier] = useState("free");
  const [runtimeMode, setRuntimeMode] = useState("dedicated");
  const [active, setActive] = useState(true);

  // password
  const [pwdOpen, setPwdOpen] = useState(false);
  const [newPwd, setNewPwd] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [fp, ig, us] = await Promise.all([
        getUserDataFootprint(userId),
        getUserDataIntegrity(userId),
        getUserUsage(userId).catch(() => null),
      ]);
      setFootprint(fp);
      setIntegrity(ig);
      setUsage(us);
    } catch (e) {
      toast.error("加载数据足迹失败: " + (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => { load(); }, [load]);

  async function saveProfile() {
    try {
      await updateUser(userId, { role, quota_tier: tier, runtime_mode: runtimeMode, is_active: active });
      toast.success("账号已更新");
    } catch (e) {
      toast.error("更新失败: " + (e as Error).message);
    }
  }

  async function doResetPwd() {
    if (newPwd.length < 8) { toast.error("密码至少8位"); return; }
    try {
      await resetPassword(userId, newPwd);
      toast.success("密码已重置");
      setPwdOpen(false); setNewPwd("");
    } catch (e) {
      toast.error("重置失败: " + (e as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="pt-6">
          <p className="mb-3 text-sm text-gray-500">该用户的历史数据量与持久化状态（重建/更新不丢失）。</p>
          {loading || !footprint ? (
            <p className="text-gray-500">加载中…</p>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Stat label="容器状态" value={footprint.container_status ?? "无"} />
              <Stat label="数据卷" value={footprint.volume_present ? "存在" : "缺失"} ok={footprint.volume_present} />
              <Stat label="配置持久(DB)" value={footprint.user_config_present ? "已持久" : "未配置"} ok={footprint.user_config_present} />
              <Stat label="聊天会话" value={String(footprint.sessions_count)} />
              <Stat label="知识库文档" value={String(footprint.kb_docs_count)} />
              <Stat label="连接器" value={String(footprint.connectors_count)} />
              <Stat label="已装技能(托管)" value={String(footprint.skills_managed.length)} />
              <Stat label="自装技能" value={String(footprint.skills_user.length)} />
            </div>
          )}
          <div className="mt-4">
            <Button size="sm" variant="outline" onClick={load}>刷新</Button>
            <Button size="sm" variant="outline" className="ml-2" onClick={() => setCapOpen(true)}>管理能力</Button>
          </div>
          <div className="mt-5 border-t pt-4">
            <div className="mb-2 text-xs text-gray-500">Token 用量（来自用量统计：今日 / 本周近 7 天 / 累计）</div>
            <div className="grid grid-cols-3 gap-3">
              <Stat label="今日" value={usage ? usage.tokens_today.toLocaleString() : "—"} />
              <Stat label="本周（近7天）" value={usage ? usage.tokens_this_week.toLocaleString() : "—"} />
              <Stat label="累计" value={usage ? usage.tokens_total.toLocaleString() : "—"} />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>数据完整性</CardTitle>
          <CardDescription>镜像/容器更新或重建后核对历史数据是否完整。</CardDescription>
        </CardHeader>
        <CardContent>
          {integrity ? (
            <div className="space-y-2">
              <Badge variant={integrity.healthy ? "default" : "destructive"}>
                {integrity.healthy ? "完整" : "异常"}
              </Badge>
              {integrity.checks.map((c) => (
                <div key={c.name} className="flex items-center gap-2 text-sm">
                  <span className={c.ok ? "text-green-600" : "text-red-600"}>{c.ok ? "✓" : "✗"}</span>
                  <span className="font-mono text-xs">{c.name}</span>
                  <span className="text-gray-500">{c.detail}</span>
                </div>
              ))}
            </div>
          ) : <p className="text-gray-500">加载中…</p>}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6 space-y-4">
          <div>
            <Label>角色</Label>
            <Select value={role} onValueChange={(v: string | null) => v && setRole(v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="user">user</SelectItem>
                <SelectItem value="admin">admin</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>配额等级</Label>
            <Select value={tier} onValueChange={(v: string | null) => v && setTier(v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="free">free</SelectItem>
                <SelectItem value="basic">basic</SelectItem>
                <SelectItem value="pro">pro</SelectItem>
              </SelectContent>
            </Select>
            <p className="mt-1 text-xs text-gray-500">
              控制该用户可用的资源与上限：<span className="font-medium">free</span> 基础额度；
              <span className="font-medium">basic</span> 标准额度；<span className="font-medium">pro</span> 高阶额度（更高并发 / 更多用量）。
            </p>
          </div>
          <div>
            <Label>运行模式</Label>
            <Select value={runtimeMode} onValueChange={(v: string | null) => v && setRuntimeMode(v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="dedicated">dedicated</SelectItem>
                <SelectItem value="shared">shared</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <Label>账号状态</Label>
            <Select value={active ? "active" : "disabled"} onValueChange={(v: string | null) => setActive(v === "active")}>
              <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="active">正常</SelectItem>
                <SelectItem value="disabled">禁用</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex gap-2">
            <Button onClick={saveProfile}>保存账号</Button>
            <Button variant="outline" onClick={() => setPwdOpen(true)}>重置密码</Button>
          </div>
        </CardContent>
      </Card>

      <UserCapabilitiesDialog user={capOpen ? dialogUser : null} onClose={() => setCapOpen(false)} />

      <Dialog open={pwdOpen} onOpenChange={(open) => { if (!open) { setPwdOpen(false); setNewPwd(""); } }}>
        <DialogContent>
          <DialogHeader><DialogTitle>重置密码</DialogTitle></DialogHeader>
          <div className="space-y-2">
            <Label>新密码</Label>
            <Input type="password" value={newPwd} onChange={(e) => setNewPwd(e.target.value)} placeholder="至少8位" />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setPwdOpen(false); setNewPwd(""); }}>取消</Button>
            <Button onClick={doResetPwd}>确认重置</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Stat({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className={"text-lg font-semibold " + (ok === false ? "text-red-600" : ok === true ? "text-green-600" : "")}>{value}</div>
    </div>
  );
}

// ─── 技能 tab ─────────────────────────────────────────────────────────────
function SkillsTab({ userId }: { userId: string }) {
  const [skills, setSkills] = useState<{ user: string[]; managed: string[] }>({ user: [], managed: [] });
  const [catalog, setCatalog] = useState<string[]>([]);
  const [catalogMap, setCatalogMap] = useState<Record<string, CatalogSkill>>({});
  const [loading, setLoading] = useState(true);
  const [pick, setPick] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, c] = await Promise.all([getAdminUserSkills(userId), getAdminCatalogSkills()]);
      setSkills(s);
      setCatalogMap(c.skills || {});
      setCatalog(Object.keys(c.skills || {}));
    } catch (e) {
      toast.error("加载技能失败: " + (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => { load(); }, [load]);

  async function install() {
    if (!pick) return;
    try {
      await adminInstallManagedSkill(userId, pick);
      toast.success(`已安装 ${pick}`);
      setPick("");
      load();
    } catch (e) { toast.error("安装失败: " + (e as Error).message); }
  }
  async function uninstall(name: string) {
    try {
      await adminUninstallUserSkill(userId, name);
      toast.success(`已卸载 ${name}`);
      load();
    } catch (e) { toast.error("卸载失败: " + (e as Error).message); }
  }

  const renderSkillCard = (name: string, managed: boolean) => {
    const meta = catalogMap[name];
    return (
      <div key={name} className="flex flex-col gap-3 rounded-lg border p-3">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-purple-100 text-purple-600">
            <Package size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <span className="block truncate text-sm font-semibold text-gray-900">{meta?.title || name}</span>
              {managed && <Badge variant="secondary" className="shrink-0">托管</Badge>}
            </div>
            <p className="mt-1 line-clamp-3 text-xs leading-5 text-gray-500">{meta?.description || "暂无描述"}</p>
          </div>
        </div>
        <div className="flex justify-end">
          <Button size="sm" variant="ghost" className="text-red-500 px-2" onClick={() => uninstall(name)}>卸载</Button>
        </div>
      </div>
    );
  };

  return (
    <Card>
      <CardHeader>
        <CardDescription>该用户已安装/托管的技能，可安装或卸载。</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? <p className="text-gray-500">加载中…</p> : (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <SearchableSelect
                value={pick}
                onValueChange={(v) => setPick(v)}
                placeholder="选择技能安装…"
                options={catalog.map((n) => ({ value: n, label: catalogMap[n]?.title || n }))}
                className="w-64"
              />
              <Button onClick={install} disabled={!pick}>安装</Button>
            </div>
            <div className="space-y-4">
              <div>
                <h4 className="text-sm font-medium mb-2">自装技能（用户可卸载）</h4>
                {skills.user.length === 0 ? (
                  <p className="text-xs text-gray-400">无</p>
                ) : (
                  <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {skills.user.map((n) => renderSkillCard(n, false))}
                  </div>
                )}
              </div>
              <div>
                <h4 className="text-sm font-medium mb-2">托管技能（系统推送）</h4>
                {skills.managed.length === 0 ? (
                  <p className="text-xs text-gray-400">无</p>
                ) : (
                  <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {skills.managed.map((n) => renderSkillCard(n, true))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── 专家 tab ─────────────────────────────────────────────────────────────
function AgentsTab({ userId }: { userId: string }) {
  const [agents, setAgents] = useState<UserAgentItem[]>([]);
  const [allAgentMap, setAllAgentMap] = useState<Record<string, Agent>>({});
  const [loading, setLoading] = useState(true);
  const [pick, setPick] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [a, all] = await Promise.all([getUserAgents(userId), listAgents()]);
      setAgents(a);
      const map: Record<string, Agent> = {};
      for (const x of all.agents) map[x.agent_id] = x;
      setAllAgentMap(map);
    } catch (e) { toast.error("加载专家失败: " + (e as Error).message); }
    finally { setLoading(false); }
  }, [userId]);
  useEffect(() => { load(); }, [load]);

  async function assign() {
    if (!pick) return;
    try { await adminAssignAgentToUser(userId, pick); toast.success("已分配专家"); setPick(""); load(); }
    catch (e) { toast.error("分配失败: " + (e as Error).message); }
  }

  async function unassign(agentId: string) {
    try { await adminUnassignAgentFromUser(userId, agentId); toast.success(`已移除专家 ${agentId}`); load(); }
    catch (e) { toast.error("移除失败: " + (e as Error).message); }
  }

  const allAgents = Object.values(allAgentMap);

  return (
    <Card>
      <CardHeader>
        <CardDescription>该用户已分配的专家（agent），可移除。</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? <p className="text-gray-500">加载中…</p> : (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <SearchableSelect
                value={pick}
                onValueChange={(v) => setPick(v)}
                placeholder="分配专家…"
                options={allAgents.map((a) => ({ value: a.agent_id, label: `${a.name}（${a.agent_id}）` }))}
                className="w-64"
              />
              <Button onClick={assign} disabled={!pick}>分配</Button>
            </div>
            {agents.length === 0 ? (
              <p className="text-xs text-gray-400">该用户尚未分配专家</p>
            ) : (
              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {agents.map((a) => {
                  const meta = allAgentMap[a.agent_id];
                  return (
                    <div key={a.agent_id} className="flex flex-col gap-3 rounded-lg border p-3">
                      <div className="flex items-start gap-3">
                        <span className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-2xl bg-gray-100 text-xl">
                          {meta?.avatar ? <img src={meta.avatar} alt="" className="h-full w-full object-cover" /> : <Bot className="text-blue-600" />}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className="block truncate text-base font-semibold text-gray-900">{meta?.name || a.agent_id}</span>
                            <Badge variant={a.system ? "secondary" : "outline"} className="shrink-0">{a.system ? "系统" : "自定义"}</Badge>
                          </div>
                          <p className="mt-1 line-clamp-2 text-xs leading-5 text-gray-500">{meta?.description || "暂无描述"}</p>
                        </div>
                      </div>
                      <div className="text-xs text-gray-400 font-mono truncate">{a.agent_id}</div>
                      <div className="flex justify-end">
                        <Button
                          size="sm"
                          variant={a.system ? "outline" : "ghost"}
                          className={a.system ? "" : "text-red-500 px-2"}
                          onClick={() => unassign(a.agent_id)}
                        >
                          {a.system ? "移除" : "取消分配"}
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── 连接器 tab ───────────────────────────────────────────────────────────
function ConnectorsTab({ userId }: { userId: string }) {
  const [items, setItems] = useState<UserConnectorItem[]>([]);
  const [catalogMap, setCatalogMap] = useState<Record<string, Connector>>({});
  const [loading, setLoading] = useState(true);
  const [pick, setPick] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [c, allC] = await Promise.all([getUserConnectors(userId), getAdminConnectors()]);
      setItems(c);
      const map: Record<string, Connector> = {};
      for (const x of allC.connectors) map[x.id] = x;
      setCatalogMap(map);
    } catch (e) { toast.error("加载连接器失败: " + (e as Error).message); }
    finally { setLoading(false); }
  }, [userId]);
  useEffect(() => { load(); }, [load]);

  async function install() {
    if (!pick) return;
    try { await adminInstallConnectorForUser(userId, pick); toast.success("已安装连接器"); setPick(""); load(); }
    catch (e) { toast.error("安装失败: " + (e as Error).message); }
  }
  async function uninstall(rowId: string) {
    try { await adminUninstallConnectorForUser(userId, rowId); toast.success("已移除连接器"); load(); }
    catch (e) { toast.error("移除失败: " + (e as Error).message); }
  }

  return (
    <Card>
      <CardHeader>
        <CardDescription>该用户已启用/已装的 MCP 连接器（卡片展示，可移除）。</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? <p className="text-gray-500">加载中…</p> : (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <SearchableSelect
                value={pick}
                onValueChange={(v) => setPick(v)}
                placeholder="安装连接器…"
                options={Object.values(catalogMap).map((c) => ({ value: c.id, label: c.display_name || c.name }))}
                className="w-64"
              />
              <Button onClick={install} disabled={!pick}>安装</Button>
            </div>
            {items.length === 0 ? (
              <p className="text-xs text-gray-400">该用户尚未安装任何连接器</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {items.map((it) => {
                  const meta = it.connector_id ? catalogMap[it.connector_id] : undefined;
                  return (
                    <div key={it.id} className="flex flex-col gap-3 rounded-lg border p-3">
                      <div className="flex items-start gap-3">
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-100 text-emerald-600 text-lg">
                          {meta?.icon ? meta.icon : <Plug size={18} />}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className="block truncate text-sm font-semibold text-gray-900">{meta?.display_name || it.name}</span>
                            <Badge variant={it.enabled ? "default" : "secondary"} className="shrink-0">{it.enabled ? "启用" : "关闭"}</Badge>
                          </div>
                          <p className="mt-1 line-clamp-3 text-xs leading-5 text-gray-500">{meta?.description || (it.personal ? "个人自定义连接器" : "暂无描述")}</p>
                        </div>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-400">{it.personal ? "个人" : "平台"}</span>
                        <Button size="sm" variant="ghost" className="text-red-500 px-2" onClick={() => uninstall(it.id)}>移除</Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── 知识库 tab：管理端直接管理用户知识库（复用 hermes_knowledge，默认 main agent） ─
type KBTreeNode = { name: string; path: string; folders: KBTreeNode[]; pages: KnowledgePageMeta[] };

function kbChildPath(parent: string, name: string): string {
  return parent ? `${parent}/${name}` : name;
}

function buildKBTree(pages: KnowledgePageMeta[], dirs: KnowledgeDirectoryMeta[] = []): KBTreeNode {
  const root: KBTreeNode = { name: "root", path: "", folders: [], pages: [] };
  const ensure = (parts: string[]): KBTreeNode => {
    let cur = root;
    for (const f of parts) {
      let child = cur.folders.find((c) => c.name === f);
      if (!child) {
        child = { name: f, path: cur.path ? `${cur.path}/${f}` : f, folders: [], pages: [] };
        cur.folders.push(child);
      }
      cur = child;
    }
    return cur;
  };
  dirs.forEach((d) => ensure(d.path.split("/").filter(Boolean)));
  for (const p of pages) ensure(p.path.split("/").filter(Boolean).slice(0, -1)).pages.push(p);
  const sort = (n: KBTreeNode) => {
    n.folders.sort((a, b) => a.name.localeCompare(b.name));
    n.pages.sort((a, b) => a.title.localeCompare(b.title));
    n.folders.forEach(sort);
  };
  sort(root);
  return root;
}

function formatKBSize(bytes?: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const regex = /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;
  let last = 0; let m; let i = 0;
  while ((m = regex.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[2] !== undefined) nodes.push(<strong key={`${keyPrefix}-b${i}`}>{m[2]}</strong>);
    else if (m[3] !== undefined) nodes.push(<em key={`${keyPrefix}-i${i}`}>{m[3]}</em>);
    else if (m[4] !== undefined) nodes.push(<code key={`${keyPrefix}-c${i}`} className="rounded bg-gray-100 px-1 text-[12px] text-purple-700">{m[4]}</code>);
    else if (m[5] !== undefined) nodes.push(<a key={`${keyPrefix}-a${i}`} href={m[6]} target="_blank" rel="noreferrer" className="text-blue-600 underline">{m[5]}</a>);
    last = regex.lastIndex; i += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function MarkdownPreview({ content }: { content: string }) {
  const lines = content.split("\n");
  const out: React.ReactNode[] = [];
  let inCode = false; const codeBuf: string[] = []; let key = 0;
  const flushCode = () => {
    out.push(
      <pre key={`code${key++}`} className="bg-gray-900 text-gray-100 rounded p-3 text-xs overflow-x-auto my-2">{codeBuf.join("\n")}</pre>,
    );
    codeBuf.length = 0;
  };
  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) { inCode = false; flushCode(); } else { inCode = true; }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }
    if (!line.trim()) { out.push(<div key={`sp${key++}`} className="h-2" />); continue; }
    if (line.startsWith("### ")) out.push(<h3 key={`h3${key++}`} className="text-base font-semibold mt-3">{renderInline(line.slice(4), `h3${key}`)}</h3>);
    else if (line.startsWith("## ")) out.push(<h2 key={`h2${key++}`} className="text-lg font-bold mt-3">{renderInline(line.slice(3), `h2${key}`)}</h2>);
    else if (line.startsWith("# ")) out.push(<h1 key={`h1${key++}`} className="text-xl font-bold mt-2">{renderInline(line.slice(2), `h1${key}`)}</h1>);
    else if (line.startsWith("- ")) out.push(<div key={`li${key++}`} className="pl-4 flex gap-2"><span>•</span><span>{renderInline(line.slice(2), `li${key}`)}</span></div>);
    else if (/^\d+\.\s/.test(line)) {
      const num = line.match(/^\d+\./)?.[0] ?? "";
      out.push(<div key={`ol${key++}`} className="pl-4 flex gap-2"><span>{num}</span><span>{renderInline(line.replace(/^\d+\.\s/, ""), `ol${key}`)}</span></div>);
    } else if (line.startsWith("> ")) out.push(<blockquote key={`bq${key++}`} className="border-l-2 border-gray-300 pl-3 text-gray-600">{renderInline(line.slice(2), `bq${key}`)}</blockquote>);
    else out.push(<p key={`p${key++}`} className="my-1 leading-6">{renderInline(line, `p${key}`)}</p>);
  }
  if (inCode) flushCode();
  return <div className="text-sm">{out}</div>;
}

function KBGraphPreview({ graph, onSelect }: { graph: KnowledgeGraphResult; onSelect: (p: string) => void }) {
  const nodes = graph.nodes.slice(0, 18);
  if (nodes.length === 0) return <p className="text-sm text-gray-500">暂无图谱数据</p>;
  const w = 520; const h = 260; const r = 96;
  const layout = nodes.map((n, i) => {
    const a = (Math.PI * 2 * i) / nodes.length;
    return { ...n, x: w / 2 + Math.cos(a) * r, y: h / 2 + Math.sin(a) * r };
  });
  const byId = new Map(layout.map((n) => [n.id, n]));
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-64 rounded border bg-gray-50">
      {graph.edges.map((e, i) => {
        const s = byId.get(e.source); const t = byId.get(e.target);
        if (!s || !t) return null;
        return <line key={`${e.source}-${e.target}-${i}`} x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke="#94a3b8" strokeOpacity="0.55" />;
      })}
      {layout.map((n) => (
        <g key={n.id} className="cursor-pointer" onClick={() => onSelect(n.id)}>
          <circle cx={n.x} cy={n.y} r="15" className="fill-blue-100 stroke-blue-500" strokeWidth="1.5" />
          <text x={n.x} y={n.y + 32} textAnchor="middle" className="fill-gray-700 text-[11px]">{n.title.length > 12 ? `${n.title.slice(0, 12)}…` : n.title}</text>
        </g>
      ))}
    </svg>
  );
}

function KnowledgeTab({ userId }: { userId: string }) {
  const [agentId, setAgentId] = useState("main");
  const [agents, setAgents] = useState<UserAgentItem[]>([]);
  const [knowledge, setKnowledge] = useState<KnowledgeListResult | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [pageData, setPageData] = useState<KnowledgeReadResult | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeSearchResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [pageLoading, setPageLoading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [creating, setCreating] = useState<{ type: "folder" | "file"; parent: string } | null>(null);
  const [newName, setNewName] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorContent, setEditorContent] = useState("");
  const [editorSaving, setEditorSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);
  const [graph, setGraph] = useState<KnowledgeGraphResult | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadAgents = useCallback(async () => {
    try { setAgents(await getUserAgents(userId)); } catch { /* 忽略，默认 main */ }
  }, [userId]);

  const loadKnowledge = useCallback(async (keepSelection = false) => {
    setLoading(true); setLoadError(null);
    try {
      const data = await getUserKnowledge(userId, agentId);
      setKnowledge(data);
      if (!keepSelection || !selectedPath || !data.pages.some((p) => p.path === selectedPath)) {
        setSelectedPath(data.pages[0]?.path ?? null);
      }
    } catch (e) { setLoadError((e as Error).message); setKnowledge(null); }
    finally { setLoading(false); }
  }, [userId, agentId, selectedPath]);

  useEffect(() => { loadAgents(); }, [loadAgents]);
  useEffect(() => { loadKnowledge(); }, [loadKnowledge]);

  const pages = knowledge?.pages ?? [];
  const directories = knowledge?.directories ?? [];
  const attachments = knowledge?.attachments ?? [];
  const tree = buildKBTree(pages, directories);
  const selectedPage = pageData?.page?.path === selectedPath ? pageData.page : pages.find((p) => p.path === selectedPath) ?? null;
  const selectedContent = pageData?.page?.path === selectedPath ? pageData.content : "";

  useEffect(() => {
    if (!selectedPath) { setPageData(null); setPageLoading(false); return; }
    let cancelled = false;
    setPageData(null); setPageError(null); setPageLoading(true);
    readUserKnowledge(userId, selectedPath, agentId)
      .then((d) => { if (!cancelled) setPageData(d); })
      .catch((e) => { if (!cancelled) toast.error("读取文档失败: " + (e as Error).message); })
      .finally(() => { if (!cancelled) setPageLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPath, agentId, userId]);

  // 搜索（防抖）
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) { setResults([]); setSearching(false); return; }
    let cancelled = false; const timer = setTimeout(() => {
      setSearching(true);
      searchUserKnowledge(userId, trimmed, agentId)
        .then((d) => { if (!cancelled) setResults(d.results); })
        .catch((e) => { if (!cancelled) toast.error("搜索失败: " + (e as Error).message); })
        .finally(() => { if (!cancelled) setSearching(false); });
    }, 220);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [query, agentId, userId]);

  async function handleNew() {
    const name = newName.trim();
    if (!name) return;
    const rel = kbChildPath(creating?.parent ?? "", name);
    try {
      if (creating?.type === "folder") {
        await mkdirUserKnowledge(userId, rel, agentId);
        toast.success(`已创建文件夹 ${rel}`);
      } else {
        const fileName = name.toLowerCase().endsWith(".md") ? name : `${name}.md`;
        const title = fileName.replace(/\.md$/i, "");
        const fpath = kbChildPath(creating?.parent ?? "", fileName);
        await writeUserKnowledge(userId, fpath, `---\ntitle: ${title}\ntags: []\nsummary: \n---\n\n# ${title}\n\n`, agentId);
        toast.success(`已创建文档 ${fpath}`);
        setSelectedPath(fpath);
      }
      setCreating(null); setNewName("");
      loadKnowledge(true);
    } catch (e) { toast.error("创建失败: " + (e as Error).message); }
  }

  async function handleSave() {
    if (!selectedPath) return;
    setEditorSaving(true);
    try {
      await writeUserKnowledge(userId, selectedPath, editorContent, agentId);
      toast.success("已保存");
      setEditorOpen(false);
      loadKnowledge(true);
      const d = await readUserKnowledge(userId, selectedPath, agentId);
      setPageData(d);
    } catch (e) { toast.error("保存失败: " + (e as Error).message); }
    finally { setEditorSaving(false); }
  }

  async function handleDelete(path: string) {
    setDeleting(path);
    try {
      await deleteUserKnowledge(userId, path, agentId);
      if (selectedPath === path || selectedPath?.startsWith(`${path}/`)) { setSelectedPath(null); setPageData(null); }
      toast.success(`已删除 ${path}`);
      loadKnowledge();
    } catch (e) { toast.error("删除失败: " + (e as Error).message); }
    finally { setDeleting(null); }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      for (const f of Array.from(files)) {
        await uploadUserKnowledge(userId, f, agentId, creating?.parent ?? "");
      }
      toast.success("上传完成");
      loadKnowledge(true);
    } catch (e) { toast.error("上传失败: " + (e as Error).message); }
    finally { setUploading(false); if (fileInputRef.current) fileInputRef.current.value = ""; }
  }

  async function handleDownload() {
    if (!selectedPage) return;
    try {
      const blob = await downloadUserKnowledge(userId, selectedPage.path, agentId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = selectedPage.name; a.click();
      URL.revokeObjectURL(url);
      toast.success("已下载");
    } catch (e) { toast.error("下载失败: " + (e as Error).message); }
  }

  async function openGraph() {
    setGraphOpen(true);
    if (graph) return;
    try { setGraph(await getUserKnowledgeGraph(userId, agentId)); }
    catch (e) { toast.error("图谱加载失败: " + (e as Error).message); }
  }

  function openEditor() {
    if (!selectedPage) return;
    setEditorContent(selectedContent || "");
    setEditorOpen(true);
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <div>
          <CardDescription>基于容器卷的 Markdown 知识库（全文检索，对话中自动注入）。数据落在同名数据卷，重建/更新不丢失。</CardDescription>
          <p className="mt-1 text-xs text-gray-500">
            服务器路径：
            <span className="font-mono">{knowledge?.knowledgeRoot || `/opt/data/profiles/${agentId}/workspace/knowledge`}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={agentId} onValueChange={(v: string | null) => v && setAgentId(v)}>
            <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
            <SelectContent>
              {agents.length === 0 && <SelectItem value="main">main（默认）</SelectItem>}
              {agents.map((a) => <SelectItem key={a.agent_id} value={a.agent_id}>{a.agent_id}{a.system ? "（系统）" : ""}</SelectItem>)}
            </SelectContent>
          </Select>
          <Button size="sm" variant="outline" onClick={() => loadKnowledge(true)}><RefreshCw size={15} /></Button>
          <Button size="sm" variant="outline" onClick={openGraph}><GitBranch size={15} className="text-purple-600" />图谱</Button>
          <label className="inline-flex items-center gap-2 bg-blue-600 text-white rounded px-3 py-2 text-sm cursor-pointer hover:bg-blue-700">
            {uploading ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}上传
            <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleUpload} />
          </label>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 lg:grid-cols-[300px_minmax(0,1fr)_260px] gap-4">
          {/* 目录树 */}
          <div className="rounded border p-2 min-h-[360px] flex flex-col">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-gray-500">资源管理器</span>
              <div className="flex gap-1">
                <Button size="sm" variant="ghost" className="h-7 px-2" onClick={() => { setCreating({ type: "file", parent: "" }); setNewName(""); }}><FilePlus size={14} /></Button>
                <Button size="sm" variant="ghost" className="h-7 px-2" onClick={() => { setCreating({ type: "folder", parent: "" }); setNewName(""); }}><FolderPlus size={14} /></Button>
              </div>
            </div>
            <div className="relative mb-2">
              <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索…" className="pl-7 h-8 text-sm" />
              {searching && <Loader2 size={14} className="absolute right-2 top-1/2 -translate-y-1/2 animate-spin text-blue-500" />}
            </div>
            <div className="flex-1 overflow-y-auto text-sm">
              {query.trim() ? (
                results.length === 0 && !searching ? <p className="text-gray-400 text-xs p-2">无匹配</p> :
                  results.map((r, i) => (
                    <button key={`${r.path}-${r.line}-${i}`} onClick={() => setSelectedPath(r.path)} className="w-full text-left rounded px-2 py-1.5 hover:bg-gray-100">
                      <div className="text-xs text-blue-600">{r.path}:{r.line}</div>
                      <div className="line-clamp-2 text-xs text-gray-500">{r.text}</div>
                    </button>
                  ))
              ) : loading ? <p className="text-gray-400 text-xs p-2">加载中…</p> :
                loadError ? <p className="text-red-500 text-xs p-2">加载失败: {loadError}</p> :
                  (
                    <>
                      {creating?.parent === "" && (
                        <div className="flex items-center gap-1 mb-1 px-1">
                          <Input autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") handleNew(); if (e.key === "Escape") setCreating(null); }} placeholder={creating.type === "folder" ? "文件夹名" : "文档名.md"} className="h-7 text-xs" />
                          <Button size="sm" className="h-7 px-2" onClick={handleNew}>建</Button>
                        </div>
                      )}
                      {pages.length === 0 && directories.length === 0 && attachments.length === 0 && !creating ?
                        <p className="text-gray-400 text-xs p-2">暂无知识库文件</p> : null}
                      <KBTree node={tree} selected={selectedPath} creating={creating} newName={newName}
                        onSelect={(p) => setSelectedPath(p)} onCreateAt={(t, p) => { setCreating({ type: t, parent: p }); setNewName(""); }}
                        onDelete={(p) => handleDelete(p)} deleting={deleting}
                        onName={(v) => setNewName(v)} onCreate={handleNew} onCancel={() => setCreating(null)} />
                      {attachments.length > 0 && (
                        <div className="mt-3 border-t pt-2">
                          <div className="text-xs text-gray-500 mb-1">附件</div>
                          {attachments.map((f) => (
                            <div key={f.path} className="flex items-center gap-2 px-2 py-1 text-xs text-gray-500">
                              <FileText size={13} className="text-slate-400" />{f.name}<span className="ml-auto">{formatKBSize(f.size)}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )
              }
            </div>
          </div>

          {/* 预览 */}
          <div className="rounded border p-4 min-h-[360px] flex flex-col">
            <div className="flex items-center justify-between mb-2 min-h-9">
              <div className="min-w-0">
                <div className="font-medium truncate">{selectedPage?.title || "选择一篇文档"}</div>
                <div className="text-xs text-gray-400 truncate">{selectedPage ? `${selectedPage.path} · ${formatKBSize(selectedPage.size)}` : knowledge?.knowledgeRoot}</div>
              </div>
              {selectedPage && (
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={openEditor}><Pencil size={15} /></Button>
                  <Button size="sm" variant="ghost" onClick={handleDownload}><Download size={15} /></Button>
                </div>
              )}
            </div>
            <div className="flex-1 overflow-y-auto">
              {pageLoading ? <p className="text-gray-400 text-sm">读取中…</p> :
                pageError ? <p className="text-red-500 text-sm">{pageError}</p> :
                  !selectedPage ? <p className="text-gray-400 text-sm">从左侧选择文档查看</p> :
                    <MarkdownPreview content={selectedContent} />}
            </div>
          </div>

          {/* 元数据 + 反链 */}
          <div className="space-y-4">
            <div className="rounded border p-3">
              <div className="text-xs font-semibold text-gray-500 mb-2">元数据</div>
              {selectedPage ? (
                <div className="space-y-1.5 text-xs">
                  {selectedPage.type && <MetaRow label="类型" value={selectedPage.type} />}
                  {selectedPage.domain && <MetaRow label="领域" value={selectedPage.domain} />}
                  {selectedPage.status && <MetaRow label="状态" value={selectedPage.status} />}
                  {selectedPage.updated && <MetaRow label="更新" value={selectedPage.updated.slice(0, 10)} />}
                  {selectedPage.summary && <p className="rounded bg-gray-100 px-2 py-1 text-gray-500">{selectedPage.summary}</p>}
                  <div className="flex flex-wrap gap-1">
                    {selectedPage.tags.length ? selectedPage.tags.map((t) => <span key={t} className="rounded-full bg-blue-100 px-2 py-0.5 text-blue-600">#{t}</span>) : <span className="text-gray-400">无标签</span>}
                  </div>
                </div>
              ) : <p className="text-gray-400 text-xs">选择文档查看属性</p>}
            </div>
            <div className="rounded border p-3">
              <div className="text-xs font-semibold text-gray-500 mb-2 flex items-center gap-1"><LinkIcon size={13} />反向链接</div>
              {pageData?.page?.path === selectedPath && pageData.backlinks.length ? (
                pageData.backlinks.map((b) => (
                  <button key={b} onClick={() => setSelectedPath(b)} className="block w-full truncate rounded border px-2 py-1 text-left text-xs text-gray-500 hover:border-blue-400 hover:text-gray-800 mb-1">{b}</button>
                ))
              ) : <p className="text-gray-400 text-xs">无</p>}
            </div>
          </div>
        </div>
      </CardContent>

      {/* 编辑器弹窗（可拖动 + 可缩放） */}
      <ResizableModal
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        title={`编辑：${selectedPage?.name}`}
        initialWidth={920}
        initialHeight={660}
        footer={
          <>
            <Button variant="outline" onClick={() => setEditorOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={editorSaving}>{editorSaving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}保存</Button>
          </>
        }
      >
        <textarea value={editorContent} onChange={(e) => setEditorContent(e.target.value)} spellCheck={false}
          className="h-full min-h-[460px] w-full rounded border p-3 font-mono text-sm outline-none focus:border-blue-400 resize-none" />
      </ResizableModal>

      {/* 图谱弹窗 */}
      <Dialog open={graphOpen} onOpenChange={setGraphOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>知识图谱</DialogTitle></DialogHeader>
          {graph ? <KBGraphPreview graph={graph} onSelect={(p) => { setSelectedPath(p); setGraphOpen(false); }} /> : <p className="text-sm text-gray-500">加载中…</p>}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function MetaRow({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="flex justify-between gap-2 border-b border-gray-100 pb-1">
      <span className="text-gray-400">{label}</span>
      <span className="font-medium truncate">{value}</span>
    </div>
  );
}

function KBTree({
  node, selected, creating, newName, onSelect, onCreateAt, onDelete, deleting, onName, onCreate, onCancel, depth = 0,
}: {
  node: KBTreeNode; selected: string | null; creating: { type: "folder" | "file"; parent: string } | null; newName: string;
  onSelect: (p: string) => void; onCreateAt: (t: "folder" | "file", p: string) => void;
  onDelete: (p: string) => void; deleting: string | null; onName: (v: string) => void; onCreate: () => void; onCancel: () => void; depth?: number;
}) {
  return (
    <div className="space-y-0.5">
      {node.path && (
        <div className="group flex items-center gap-1 rounded px-1 py-1 hover:bg-gray-100" style={{ paddingLeft: depth * 14 }}>
          <Folder size={14} className="text-amber-500 shrink-0" />
          <span className="flex-1 truncate text-gray-600">{node.name}</span>
          <div className="hidden group-hover:flex gap-0.5">
            <button className="p-1 hover:text-blue-600" onClick={() => onCreateAt("file", node.path)}><FilePlus size={13} /></button>
            <button className="p-1 hover:text-blue-600" onClick={() => onCreateAt("folder", node.path)}><FolderPlus size={13} /></button>
            <button className="p-1 hover:text-red-500" disabled={deleting === `profiles/main/workspace/knowledge/${node.path}`} onClick={() => onDelete(node.path)}><Trash2 size={13} /></button>
          </div>
        </div>
      )}
      {creating?.parent === node.path && (
        <div className="flex items-center gap-1 mb-1" style={{ paddingLeft: (depth + 1) * 14 }}>
          <Input autoFocus value={newName} onChange={(e) => onName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") onCreate(); if (e.key === "Escape") onCancel(); }} placeholder={creating.type === "folder" ? "文件夹名" : "文档名.md"} className="h-7 text-xs" />
          <Button size="sm" className="h-7 px-2" onClick={onCreate}>建</Button>
        </div>
      )}
      {node.pages.map((p) => (
        <div key={p.path} className={`group flex items-center gap-1 rounded px-1 py-1 ${selected === p.path ? "bg-blue-50 border border-blue-200" : ""}`} style={{ paddingLeft: (depth + 1) * 14 }}>
          <FileText size={14} className="text-blue-500 shrink-0" />
          <button className="flex-1 truncate text-left" onClick={() => onSelect(p.path)} title={p.path}>{p.name}</button>
          <button className="p-1 hover:text-red-500 hidden group-hover:block" disabled={deleting === `profiles/main/workspace/knowledge/${p.path}`} onClick={() => onDelete(p.path)}><Trash2 size={13} /></button>
        </div>
      ))}
      {node.folders.map((c) => (
        <KBTree key={c.path} node={c} selected={selected} creating={creating} newName={newName}
          onSelect={onSelect} onCreateAt={onCreateAt} onDelete={onDelete} deleting={deleting} onName={onName} onCreate={onCreate} onCancel={onCancel} depth={depth + 1} />
      ))}
    </div>
  );
}

// ─── 模型 tab：区分"用户实际配置"与"平台可见性/默认"两层 ──────────────────
function ModelsTab({ dialogUser }: { dialogUser: UserSummary }) {
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState<ConfiguredModels | null>(null);
  const [cfgLoading, setCfgLoading] = useState(true);
  const [cfgError, setCfgError] = useState<string | null>(null);

  const loadCfg = useCallback(async () => {
    setCfgLoading(true);
    setCfgError(null);
    try {
      setCfg(await getUserConfiguredModels(dialogUser.id));
    } catch (e) {
      setCfgError((e as Error).message);
    } finally {
      setCfgLoading(false);
    }
  }, [dialogUser.id]);

  useEffect(() => { loadCfg(); }, [loadCfg]);

  async function toggleDisable(name: string, disabled: boolean) {
    try {
      await setUserProviderDisabled(dialogUser.id, name, disabled);
      toast.success(disabled ? `已禁用 ${name}` : `已启用 ${name}`);
      loadCfg();
    } catch (e) { toast.error("操作失败: " + (e as Error).message); }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardDescription>
            来自该用户个人容器 <code>config.yaml</code>（即他在客户端「AI 模型」页添加的提供商 / 密钥 / 模型）。
            这与下方管理员设置的「平台可见性与默认」是<b>两层独立数据</b>：此处展示用户自己配了什么；密钥已掩码，仅显示是否已配置。
            可在此直接禁用某个提供商（写入 DB，容器重建后持久生效）。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {cfgLoading ? (
            <p className="text-gray-500">加载中…</p>
          ) : cfgError ? (
            <p className="text-sm text-red-600">加载失败：{cfgError}</p>
          ) : (
            <div className="space-y-3">
              <div className="text-sm">
                默认模型：
                <span className="font-medium">{cfg?.defaultModel || "（未设置，跟随平台默认）"}</span>
              </div>
              {!cfg || cfg.providers.length === 0 ? (
                <p className="text-xs text-gray-400">该用户尚未在客户端配置任何模型提供商。</p>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {cfg.providers.map((p) => (
                    <div key={p.name} className={"flex flex-col rounded-lg border p-3 " + (p.disabled ? "opacity-60 bg-gray-50" : "")}>
                      <div className="flex items-center justify-between gap-2">
                        <span className={"font-medium truncate " + (p.disabled ? "line-through" : "")}>{p.name}</span>
                        <div className="flex items-center gap-1 shrink-0">
                          {p.disabled && <Badge variant="destructive" className="shrink-0">已禁用</Badge>}
                          {p.system ? (
                            <Badge variant="secondary" className="shrink-0">平台默认</Badge>
                          ) : (
                            <Badge
                              variant={p.hasApiKey ? "default" : "outline"}
                              className={"shrink-0 " + (p.hasApiKey ? "" : "text-amber-600")}
                            >
                              {p.hasApiKey ? "已配密钥" : "未配密钥"}
                            </Badge>
                          )}
                        </div>
                      </div>
                      <div className="text-xs text-gray-500 mt-1 break-all">
                        {p.api || "openai-completions"}{p.baseUrl ? ` · ${p.baseUrl}` : ""}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1">
                        {p.models.map((m) => (
                          <Badge key={m.id} variant="outline" className="text-[11px]">
                            {m.name || m.id}
                          </Badge>
                        ))}
                      </div>
                      {!p.system && (
                        <div className="mt-2 flex justify-end">
                          <Button
                            size="sm"
                            variant={p.disabled ? "outline" : "ghost"}
                            className={p.disabled ? "" : "text-red-500 px-2"}
                            onClick={() => toggleDisable(p.name, !p.disabled)}
                          >
                            {p.disabled ? "启用" : "禁用"}
                          </Button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>模型权限（平台层）</CardTitle>
          <CardDescription>管理该用户可见的模型供应商与默认模型（管理员设置，作用于所有用户 / 指定用户）。</CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={() => setOpen(true)}>配置模型可见性与默认</Button>
          <UserModelsDialog user={open ? dialogUser : null} onClose={() => setOpen(false)} />
        </CardContent>
      </Card>
    </div>
  );
}

// ─── 容器 tab（数据保全核心） ──────────────────────────────────────────────
function ContainerTab({ userId }: { userId: string }) {
  const [detail, setDetail] = useState<UserContainerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [wipeOpen, setWipeOpen] = useState(false);
  const [wipeConfirm, setWipeConfirm] = useState(false);
  const [applyOpen, setApplyOpen] = useState(false);
  const [recreateOpen, setRecreateOpen] = useState(false);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setDetail(await getUserContainer(userId)); }
    catch (e) { toast.error("加载容器详情失败: " + (e as Error).message); }
    finally { setLoading(false); }
  }, [userId]);
  useEffect(() => { load(); }, [load]);

  async function recreate() {
    setBusy(true);
    try { await recreateUserContainer(userId); toast.success("已保留数据重建容器"); load(); }
    catch (e) { toast.error("重建失败: " + (e as Error).message); }
    finally { setBusy(false); }
  }
  async function destroy(wipe: boolean) {
    setBusy(true);
    try { await destroyUserContainer(userId, wipe, true); toast.success(wipe ? "已销毁并清理数据" : "已销毁容器（数据卷保留）"); load(); }
    catch (e) { toast.error("销毁失败: " + (e as Error).message); }
    finally { setBusy(false); setWipeOpen(false); setWipeConfirm(false); }
  }
  async function backup() {
    try {
      const blob = await backupUserContainer(userId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `user-${userId}-data-backup.tar.gz`;
      a.click(); URL.revokeObjectURL(url);
      toast.success("备份已下载");
    } catch (e) { toast.error("备份失败: " + (e as Error).message); }
  }
  async function restore() {
    if (!restoreFile) return;
    setBusy(true);
    try { await restoreUserContainer(userId, restoreFile); toast.success("已从备份恢复"); setRestoreFile(null); load(); }
    catch (e) { toast.error("恢复失败: " + (e as Error).message); }
    finally { setBusy(false); }
  }
  async function hotApply() {
    setBusy(true);
    try {
      const res = await applyUserCapabilities(userId);
      toast.success(`已热应用（toolsets: ${res.toolsets.join(", ") || "无"}）`);
      setApplyOpen(false); load();
    } catch (e) { toast.error("热应用失败: " + (e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="pt-6">
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm space-y-2">
            <p className="font-medium text-blue-900">「保留数据重建」是什么意思？</p>
            <p className="text-blue-800/90">
              用最新的平台镜像<b>重新创建</b>该用户的 Docker 容器，但<b>完整保留其命名数据卷</b>——聊天历史、知识库、技能、个人配置都在卷里，不会丢失。常用于：
            </p>
            <ul className="list-disc pl-5 text-blue-800/90 space-y-1">
              <li>平台 / 镜像更新后，让新代码对该用户生效；</li>
              <li>容器异常崩溃或配置需要重算时恢复；</li>
              <li>能力、连接器、模型等配置变更后应用。</li>
            </ul>
            <p className="text-blue-800/90">
              与之相对，「销毁并清理数据」会连数据卷一起删除（<b className="text-red-600">永久丢失</b>），请谨慎使用。用户的持久化策略是「命名卷 + 数据库双写」，正常重建 / 镜像更新都不会丢历史数据。
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>容器操作</CardTitle>
          <CardDescription>对应该用户的运行时容器。</CardDescription>
        </CardHeader>
        <CardContent>
        {loading || !detail ? <p className="text-gray-500">加载中…</p> : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
              <KV k="状态" v={detail.status} />
              <KV k="运行时状态" v={detail.state ?? "-"} />
              <KV k="容器ID" v={detail.docker_id ?? "-"} />
              <KV k="镜像" v={detail.image ?? "-"} />
              <KV k="内部地址" v={detail.internal_host ?? "-"} />
              <KV k="创建时间" v={detail.created_at ? new Date(detail.created_at).toLocaleString() : "-"} />
              <KV k="配置键" v={(detail.user_config_keys || []).join(", ") || "无"} />
            </div>
            {detail.mounts && detail.mounts.length > 0 && (
              <div>
                <div className="text-xs text-gray-500 mb-1">挂载卷</div>
                <div className="flex flex-wrap gap-2">
                  {detail.mounts.map((m) => <Badge key={m} variant="outline" className="font-mono text-xs">{m}</Badge>)}
                </div>
              </div>
            )}
            <div className="flex flex-wrap gap-2 pt-2">
              <Button onClick={() => setRecreateOpen(true)} disabled={busy}>保留数据重建</Button>
              <Button variant="outline" onClick={() => setApplyOpen(true)} disabled={busy}>热应用能力配置（重启容器）</Button>
              <Button variant="outline" onClick={backup} disabled={busy}>下载备份</Button>
              <label className="inline-flex items-center gap-2">
                <input type="file" accept=".tar.gz,.tgz,.tar" onChange={(e) => setRestoreFile(e.target.files?.[0] ?? null)} className="text-xs" />
                <Button variant="outline" onClick={restore} disabled={busy || !restoreFile}>恢复备份</Button>
              </label>
              <Button variant="destructive" onClick={() => setWipeOpen(true)} disabled={busy}>销毁并清理数据</Button>
            </div>
          </div>
        )}
        </CardContent>
      </Card>

      <Dialog open={wipeOpen} onOpenChange={setWipeOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>销毁并清理数据（永久丢失）</DialogTitle></DialogHeader>
          <p className="text-sm text-red-600">
            此操作将删除容器<strong>以及其数据卷</strong>（聊天历史、知识库、技能、配置全部永久丢失），不可恢复。仅「保留数据重建」更安全。
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={wipeConfirm} onChange={(e) => setWipeConfirm(e.target.checked)} />
            我确认要永久删除该用户的数据卷
          </label>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setWipeOpen(false); setWipeConfirm(false); }}>取消</Button>
            <Button variant="destructive" disabled={!wipeConfirm || busy} onClick={() => destroy(true)}>确认销毁并清理</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={applyOpen} onOpenChange={setApplyOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>热应用能力配置（重启容器）</DialogTitle></DialogHeader>
          <p className="text-sm text-gray-600">
            将重算该用户的能力配置并<b>重启容器</b>，可能中断进行中的对话 / cron 任务。用户自配的密钥与配置会保留。确认继续？
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setApplyOpen(false)}>取消</Button>
            <Button disabled={busy} onClick={hotApply}>{busy ? "应用中…" : "确认重启"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={recreateOpen} onOpenChange={setRecreateOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>保留数据重建容器</DialogTitle></DialogHeader>
          <p className="text-sm text-gray-600">
            <AlertTriangle size={15} className="mr-1 inline text-amber-500" />
            将用最新镜像重新创建该用户的容器，<b>保留其数据卷</b>（聊天、知识库、技能、配置不丢）。
            容器会短暂重启，进行中的对话 / cron 任务可能中断。确认继续？
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRecreateOpen(false)}>取消</Button>
            <Button disabled={busy} onClick={() => { setRecreateOpen(false); recreate(); }}>{busy ? "重建中…" : "确认重建"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded border p-2">
      <div className="text-xs text-gray-500">{k}</div>
      <div className="font-medium break-all">{v}</div>
    </div>
  );
}
