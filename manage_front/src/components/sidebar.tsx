"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  Users,
  Server,
  BarChart3,
  ScrollText,
  Brain,
  Plug,
  Wrench,
  Bot,
  Code2,
  Settings,
} from "lucide-react";

const navItems = [
  { href: "/dashboard", label: "仪表盘", icon: LayoutDashboard },
  { href: "/users", label: "用户管理", icon: Users },
  { href: "/containers", label: "容器管理", icon: Server },
  { href: "/skills", label: "技能管理", icon: Wrench },
  { href: "/connectors-catalog", label: "连接器管理", icon: Plug },
  { href: "/agents", label: "专家管理", icon: Bot },
  { href: "/models", label: "模型配置", icon: Brain },
  { href: "/capabilities", label: "能力配置", icon: Plug },
  { href: "/api-access", label: "API 访问", icon: Code2 },
  { href: "/platform-config", label: "平台配置", icon: Settings },
  { href: "/usage", label: "用量统计", icon: BarChart3 },
  { href: "/audit", label: "审计日志", icon: ScrollText },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 border-r bg-white min-h-screen p-4">
      <div className="mb-8">
        <h1 className="text-xl font-bold">ComWorker Admin</h1>
      </div>
      <nav className="space-y-1">
        {navItems.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              pathname === item.href
                ? "bg-gray-100 text-gray-900"
                : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
            )}
          >
            <item.icon className="h-4 w-4" />
            {item.label}
          </Link>
        ))}
      </nav>
    </aside>
  );
}
