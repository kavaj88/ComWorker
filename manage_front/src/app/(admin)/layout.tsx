"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { isLoggedIn } from "@/lib/auth";
import {
  getSetupStatus,
  changePassword,
  completeSetup,
  type SetupStatus,
} from "@/lib/api";
import { Sidebar } from "@/components/sidebar";
import { Header } from "@/components/header";

const STEP_LABELS: Record<string, string> = {
  change_admin_password: "修改 admin 初始密码",
  add_model_key: "配置模型 API Key",
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);
  const [setup, setSetup] = useState<SetupStatus | null>(null);

  // 在管理端内联完成初始化（修改初始管理员密码）所需的本地状态
  const [pwOld, setPwOld] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [pwConfirm, setPwConfirm] = useState("");
  const [pwSubmitting, setPwSubmitting] = useState(false);
  const [pwError, setPwError] = useState<string | null>(null);
  const [pwChanged, setPwChanged] = useState(false);

  useEffect(() => {
    if (!isLoggedIn()) {
      router.replace("/login");
    } else {
      setChecked(true);
    }
  }, [router]);

  // 初始化未完成时，网关的 require_setup_complete 会拦截 admin 路由，
  // 除模型配置外的所有管理接口都返回 403 setup_required。必须在管理端
  // 显式提示，否则管理员只会看到一连串无法理解的失败。
  useEffect(() => {
    if (!checked) return;
    let cancelled = false;
    const load = () => {
      getSetupStatus()
        .then((status) => {
          if (!cancelled) setSetup(status);
        })
        .catch(() => {
          /* 状态拉取失败不应阻断页面渲染 */
        });
    };
    load();
    const timer = setInterval(load, 60000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [checked]);

  // 修改初始管理员密码（必做步骤）。该接口不依赖 setup 守卫，可在锁定时调用。
  const handleChangePassword = async () => {
    setPwError(null);
    if (!pwOld || !pwNew) {
      setPwError("请填写当前密码和新密码");
      return;
    }
    if (pwNew !== pwConfirm) {
      setPwError("两次输入的新密码不一致");
      return;
    }
    if (pwNew.length < 6) {
      setPwError("新密码至少需要 6 个字符");
      return;
    }
    setPwSubmitting(true);
    try {
      await changePassword(pwOld, pwNew);
      setPwChanged(true);
      setPwOld("");
      setPwNew("");
      setPwConfirm("");
      // 改密即满足唯一 REQUIRED 步骤，自动完成初始化以解锁管理端；
      // 若因并发等问题自动完成失败，则刷新状态，由下方按钮兜底。
      try {
        const fresh = await completeSetup();
        setSetup(fresh);
      } catch {
        const fresh = await getSetupStatus();
        setSetup(fresh);
      }
    } catch (err) {
      setPwError(err instanceof Error ? err.message : "修改密码失败");
    } finally {
      setPwSubmitting(false);
    }
  };

  // 兜底：若自动完成初始化未生效，管理员可手动点「完成初始化」。
  const handleFinishSetup = async () => {
    setPwError(null);
    setPwSubmitting(true);
    try {
      const fresh = await completeSetup();
      setSetup(fresh);
    } catch (err) {
      setPwError(err instanceof Error ? err.message : "完成初始化失败");
    } finally {
      setPwSubmitting(false);
    }
  };

  if (!checked) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-gray-500">加载中...</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-gray-50">
      <Sidebar />
      <div className="flex-1 flex flex-col">
        <Header />
        {setup && !setup.is_complete && !setup.is_dismissed && (
          <SetupBanner
            setup={setup}
            pwOld={pwOld}
            pwNew={pwNew}
            pwConfirm={pwConfirm}
            pwSubmitting={pwSubmitting}
            pwError={pwError}
            pwChanged={pwChanged}
            setPwOld={setPwOld}
            setPwNew={setPwNew}
            setPwConfirm={setPwConfirm}
            onChangePassword={handleChangePassword}
            onFinish={handleFinishSetup}
          />
        )}
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}

function clientSetupUrl(): string {
  const configured = process.env.NEXT_PUBLIC_CLIENT_URL;
  if (configured) return configured.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:3000`;
  }
  return "";
}

function SetupBanner({
  setup,
  pwOld,
  pwNew,
  pwConfirm,
  pwSubmitting,
  pwError,
  pwChanged,
  setPwOld,
  setPwNew,
  setPwConfirm,
  onChangePassword,
  onFinish,
}: {
  setup: SetupStatus;
  pwOld: string;
  pwNew: string;
  pwConfirm: string;
  pwSubmitting: boolean;
  pwError: string | null;
  pwChanged: boolean;
  setPwOld: (v: string) => void;
  setPwNew: (v: string) => void;
  setPwConfirm: (v: string) => void;
  onChangePassword: () => void;
  onFinish: () => void;
}) {
  const needsPassword = setup.missing_steps.includes("change_admin_password");
  const needsModel = setup.missing_recommended_steps.includes("add_model_key");

  return (
    <div className="mx-6 mt-6 rounded-md border border-amber-300 bg-amber-50 p-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 text-amber-600" aria-hidden>
          ⚠️
        </span>
        <div className="flex-1 text-sm text-amber-900">
          <p className="font-semibold">平台初始化未完成，管理功能已被锁定</p>
          <p className="mt-1">
            为保障首次部署安全，网关会在初始化完成前拦截所有管理端接口（403 setup_required），
            因此创建用户、技能、连接器、能力配置等功能暂时不可用。请先在下方面板修改初始管理员密码以解锁。
          </p>

          {/* 改密码面板：仅在还需要改密码时显示表单；否则显示完成按钮 */}
          <div className="mt-3 rounded-md border border-amber-200 bg-white/70 p-3">
            {needsPassword ? (
              <>
                <p className="mb-2 text-xs font-medium text-amber-800">
                  步骤 1 / 必做：修改初始管理员密码
                </p>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                  <input
                    type="password"
                    value={pwOld}
                    onChange={(e) => setPwOld(e.target.value)}
                    placeholder="当前密码（首次启动打印在网关日志）"
                    className="rounded border border-amber-300 px-2 py-1.5 text-xs outline-none focus:border-amber-500"
                  />
                  <input
                    type="password"
                    value={pwNew}
                    onChange={(e) => setPwNew(e.target.value)}
                    placeholder="新密码（至少 6 个字符）"
                    className="rounded border border-amber-300 px-2 py-1.5 text-xs outline-none focus:border-amber-500"
                  />
                  <input
                    type="password"
                    value={pwConfirm}
                    onChange={(e) => setPwConfirm(e.target.value)}
                    placeholder="再次输入新密码"
                    className="rounded border border-amber-300 px-2 py-1.5 text-xs outline-none focus:border-amber-500"
                  />
                </div>
                <div className="mt-2 flex items-center gap-3">
                  <button
                    type="button"
                    onClick={onChangePassword}
                    disabled={pwSubmitting}
                    className="inline-flex items-center gap-1 rounded bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-60"
                  >
                    {pwSubmitting ? "处理中..." : "修改密码并解锁"}
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="mb-2 text-xs font-medium text-emerald-700">
                  ✓ 初始密码已修改
                </p>
                <button
                  type="button"
                  onClick={onFinish}
                  disabled={pwSubmitting}
                  className="inline-flex items-center gap-1 rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
                >
                  {pwSubmitting ? "处理中..." : "完成初始化"}
                </button>
              </>
            )}

            {pwError && <p className="mt-2 text-xs text-red-600">{pwError}</p>}

            <p className="mt-3 text-[11px] leading-relaxed text-amber-700">
              可选：也可前往{" "}
              <a
                href={`${clientSetupUrl()}/setup`}
                className="underline underline-offset-2 hover:text-amber-900"
                target="_blank"
                rel="noreferrer"
              >
                客户端初始化向导
              </a>
              （端口 3000）一并配置模型 API Key —— 不配置也不影响管理后台解锁，可稍后在「模型设置」补充。
            </p>
          </div>

          <p className="mt-2 text-xs text-amber-700">
            待完成步骤：
            {setup.missing_steps.length > 0
              ? setup.missing_steps.map((step) => STEP_LABELS[step] || step).join("、")
              : "无（必做项均已满足）"}
            {needsModel && "；推荐项：配置模型 API Key（可选）"}
          </p>
        </div>
      </div>
    </div>
  );
}
