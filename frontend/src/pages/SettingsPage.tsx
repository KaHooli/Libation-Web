import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import {
  UserCog, Sliders, Plug, Users, ServerCog, ShieldAlert, KeyRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/context/AuthContext";
import { api } from "@/lib/api";
import { ChaptarrSection } from "@/components/settings/ChaptarrSection";
import {
  TwoFactorSection, ChangeUsernameSection, ChangePasswordSection, UpdateCredentialsSection,
} from "@/components/settings/AccountSection";
import { SessionsSection } from "@/components/settings/SessionsSection";
import { LibationSettingsSection } from "@/components/settings/LibationSettingsSection";
import { UserManagementSection, UserPermissionsSection } from "@/components/settings/UsersSection";
import { LogsSection } from "@/components/settings/LogsSection";
import { AboutSection, ApiDocsSection } from "@/components/settings/SystemSection";
import { OidcSection } from "@/components/settings/OidcSection";

// ── Tabs ────────────────────────────────────────────────────────────────────

type TabId = "account" | "library" | "integrations" | "users" | "signin" | "system";

interface TabDef {
  id: TabId;
  label: string;
  icon: LucideIcon;
  /** Admin-only tabs are hidden outright rather than shown empty. */
  adminOnly?: boolean;
}

const TABS: TabDef[] = [
  { id: "account", label: "Account", icon: UserCog },
  { id: "library", label: "Library", icon: Sliders, adminOnly: true },
  { id: "integrations", label: "Integrations", icon: Plug, adminOnly: true },
  { id: "users", label: "Users", icon: Users, adminOnly: true },
  { id: "signin", label: "Sign-in", icon: KeyRound, adminOnly: true },
  { id: "system", label: "System", icon: ServerCog },
];

export function SettingsPage() {
  const { user } = useAuth();
  const [usingDefaults, setUsingDefaults] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    api.get("/auth/default-credentials")
      .then(({ data }) => setUsingDefaults(data.using_default_credentials))
      .catch(() => {});
  }, []);

  const visibleTabs = TABS.filter(t => !t.adminOnly || user?.is_admin);

  // The tab lives in the URL so a particular section can be linked to and
  // survives a reload. An unknown or now-forbidden id falls back to the first
  // tab this user can actually see rather than rendering nothing.
  const requested = searchParams.get("tab") as TabId | null;
  const active = visibleTabs.some(t => t.id === requested)
    ? (requested as TabId)
    : visibleTabs[0].id;

  const selectTab = (id: TabId) => {
    const next = new URLSearchParams(searchParams);
    next.set("tab", id);
    setSearchParams(next, { replace: true });
  };

  return (
    <div className="max-w-2xl space-y-6">
      {/* Sub-section navigation */}
      <div className="-mx-1 overflow-x-auto px-1 pb-1">
        <div className="flex w-max gap-1 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-1">
          {visibleTabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => selectTab(id)}
              aria-current={active === id ? "page" : undefined}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors whitespace-nowrap",
                active === id
                  ? "bg-brand-600 text-white"
                  : "text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white hover:bg-slate-50 dark:hover:bg-slate-700"
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* The default-credentials warning follows you onto whichever tab you are
          on — it is the one thing that should not be possible to tab away from
          and forget about. */}
      {usingDefaults && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40 px-4 py-3">
          <ShieldAlert className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
          <p className="text-sm text-amber-800 dark:text-amber-300">
            You're using default credentials — set a new username and password in{" "}
            <button
              onClick={() => selectTab("account")}
              className="font-semibold underline underline-offset-2 hover:text-amber-900 dark:hover:text-amber-200"
            >
              Account
            </button>.
          </p>
        </div>
      )}

      {active === "account" && (
        <>
          {usingDefaults && <UpdateCredentialsSection />}
          <TwoFactorSection />
          <ChangeUsernameSection />
          <ChangePasswordSection />
          <SessionsSection />
        </>
      )}

      {active === "library" && <LibationSettingsSection />}

      {active === "integrations" && <ChaptarrSection />}

      {active === "users" && (
        <>
          <UserManagementSection />
          <UserPermissionsSection />
        </>
      )}

      {active === "signin" && <OidcSection />}

      {active === "system" && (
        <>
          <AboutSection />
          {user?.is_admin && <LogsSection />}
          {user?.is_admin && <ApiDocsSection />}
        </>
      )}
    </div>
  );
}
