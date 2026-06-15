import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Sidebar, MobileMenuButton } from "./Sidebar";

const PAGE_TITLES: Record<string, string> = {
  "/": "Library",
  "/downloads": "Downloads",
  "/accounts": "Accounts",
  "/settings": "Settings",
};

export function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { pathname } = useLocation();
  const title = PAGE_TITLES[pathname] ?? "Libation";

  return (
    <div className="flex h-screen bg-slate-50 overflow-hidden">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
        {/* Top bar */}
        <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 shrink-0">
          <MobileMenuButton onClick={() => setSidebarOpen(true)} />
          <h1 className="text-base font-semibold text-slate-900">{title}</h1>
        </header>

        {/* Main content */}
        <main className="flex-1 overflow-y-auto p-4 md:p-6 scrollbar-thin">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
