import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import {
  Users, Plus, CheckCircle, ExternalLink,
  Copy, ChevronRight, Loader2, Globe, AlertCircle, Trash2, User, Info, X,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { cn } from "@/lib/utils";

interface Account {
  account_id: string;
  name: string;
  locale: string;
  scan_library: boolean;
  authenticated: boolean;
  owner_name: string | null;
  owner_username: string | null;
  auto_download: boolean;
  added_by_user_id: number | null;
}

type Step = "idle" | "form" | "url" | "completing" | "done";

// `value` MUST be the AudibleApi `Locale.Name`, not the ISO country code.
// LibationCli resolves `--locale` via `Localization.Get(name)`, which matches on
// Locale.Name and falls back to `Locale.Empty` (blank TopDomain/CountryCode) for
// anything it does not recognise — producing a broken sign-in URL such as
// `https://www.amazon./ap/signin`. Only "us" and "uk" happen to have a Name that
// is identical to their country code, which is why every other marketplace broke.
const LOCALES = [
  { value: "us", label: "United States" },
  { value: "uk", label: "United Kingdom" },
  { value: "germany", label: "Germany" },
  { value: "france", label: "France" },
  { value: "canada", label: "Canada" },
  { value: "australia", label: "Australia" },
  { value: "japan", label: "Japan" },
  { value: "italy", label: "Italy" },
  { value: "spain", label: "Spain" },
  { value: "india", label: "India" },
  { value: "brazil", label: "Brazil" },
];

function LocaleBadge({ locale }: { locale: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 uppercase">
      <Globe className="h-3 w-3" />
      {locale}
    </span>
  );
}

function StatusTooltip({ authenticated }: { authenticated: boolean }) {
  return (
    <div className="relative group inline-flex shrink-0">
      {authenticated ? (
        <CheckCircle className="h-4 w-4 text-green-500 cursor-default" />
      ) : (
        <AlertCircle className="h-4 w-4 text-amber-400 cursor-default" />
      )}
      <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 hidden group-hover:block z-10 pointer-events-none">
        <span className="rounded-md bg-slate-800 px-2 py-1 text-xs text-white whitespace-nowrap shadow-lg block">
          {authenticated ? "Authenticated" : "Not authenticated — re-login required"}
        </span>
        <span className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-slate-800 block" />
      </span>
    </div>
  );
}

export function AccountsPage() {
  const { user } = useAuth();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [step, setStep] = useState<Step>("idle");
  const [email, setEmail] = useState("");
  const [locale, setLocale] = useState("us");
  const [sessionId, setSessionId] = useState("");
  const [loginUrl, setLoginUrl] = useState("");
  const [responseUrl, setResponseUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [showNextStep, setShowNextStep] = useState(false);
  const [togglingAutoDownload, setTogglingAutoDownload] = useState<string | null>(null);
  const [ownerNameInput, setOwnerNameInput] = useState("");
  const [savingOwnerName, setSavingOwnerName] = useState(false);

  const fetchAccounts = () => {
    setLoading(true);
    api.get("/accounts")
      .then(r => setAccounts(r.data))
      .catch(() => setAccounts([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchAccounts(); }, []);

  useEffect(() => {
    const myAccount = accounts.find(a => a.added_by_user_id === user?.id);
    setOwnerNameInput(myAccount?.owner_name ?? "");
  }, [accounts, user?.id]);

  const handleStartLogin = async () => {
    if (!email.trim()) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post("/accounts/login/start", { email: email.trim(), locale });
      setSessionId(data.session_id);
      setLoginUrl(data.login_url);
      setStep("url");
    } catch (e: any) {
      setError(e.response?.data?.detail || "Failed to start login. Check that LibationCli is installed.");
    } finally {
      setBusy(false);
    }
  };

  const handleCompleteLogin = async () => {
    if (!responseUrl.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api.post("/accounts/login/complete", {
        session_id: sessionId,
        response_url: responseUrl.trim(),
      });
      setStep("done");
      fetchAccounts();
      // Kick off a library scan silently in the background
      api.post("/downloads/scan").catch(() => {});
    } catch (e: any) {
      setError(e.response?.data?.detail || "Failed to complete login. Make sure you pasted the correct URL.");
    } finally {
      setBusy(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(loginUrl).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleRemove = async (accountId: string) => {
    if (!confirm("Remove this Audible account? You will need to re-authenticate to use it again.")) return;
    setRemoving(accountId);
    try {
      await api.delete(`/accounts/${encodeURIComponent(accountId)}`);
      setAccounts(prev => prev.filter(a => a.account_id !== accountId));
    } catch (e: any) {
      alert(e.response?.data?.detail || "Failed to remove account.");
    } finally {
      setRemoving(null);
    }
  };

  const toggleAutoDownload = async (accountId: string, current: boolean) => {
    setTogglingAutoDownload(accountId);
    try {
      await api.patch(`/accounts/${encodeURIComponent(accountId)}/auto-download`, {
        auto_download: !current,
      });
      setAccounts(prev =>
        prev.map(a => a.account_id === accountId ? { ...a, auto_download: !current } : a)
      );
    } catch (e: any) {
      alert(e.response?.data?.detail || "Failed to update auto-download.");
    } finally {
      setTogglingAutoDownload(null);
    }
  };

  const resetFlow = () => {
    setStep("idle");
    setEmail("");
    setLocale("us");
    setSessionId("");
    setLoginUrl("");
    setResponseUrl("");
    setError("");
  };

  const canToggleAutoDownload = (acc: Account) =>
    user?.is_admin || acc.added_by_user_id === user?.id;

  const saveOwnerName = async () => {
    const trimmed = ownerNameInput.trim();
    if (!trimmed) return;
    setSavingOwnerName(true);
    try {
      await api.patch("/auth/me", { owner_name: trimmed });
      fetchAccounts();
    } catch {}
    finally { setSavingOwnerName(false); }
  };

  return (
    <div className="max-w-2xl space-y-6">
      {/* Owner name banner */}
      {!loading && accounts.some(a => a.added_by_user_id === user?.id && !a.owner_name) && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40 px-4 py-3">
          <AlertCircle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
          <p className="text-sm text-amber-800 dark:text-amber-300">
            Fill in owner name to use split libraries.
          </p>
        </div>
      )}

      {/* Post-connection banner */}
      {showNextStep && (
        <div className="flex items-start gap-3 rounded-xl border border-brand-200 bg-brand-50 dark:border-brand-800 dark:bg-brand-950/40 px-4 py-3">
          <Info className="h-4 w-4 text-brand-600 dark:text-brand-400 shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-brand-800 dark:text-brand-300">Account connected!</p>
            <p className="text-xs text-brand-700 dark:text-brand-400 mt-0.5">
              A library scan has been started automatically. Your audiobooks will appear on the{" "}
              <Link to="/liberate" className="underline font-semibold hover:text-brand-900 dark:hover:text-brand-200">
                Liberate
              </Link>
              {" "}page once the scan completes.
            </p>
          </div>
          <button
            onClick={() => setShowNextStep(false)}
            className="text-brand-400 hover:text-brand-600 dark:hover:text-brand-200 shrink-0"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Audible Accounts</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Connect your Audible accounts to scan and download your library.
          </p>
        </div>
        {step === "idle" && (
          <button
            onClick={() => setStep("form")}
            className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 transition-colors"
          >
            <Plus className="h-4 w-4" />
            Add Account
          </button>
        )}
      </div>

      {/* Add account flow */}
      {step !== "idle" && (
        <div className="rounded-xl border border-slate-200 bg-white overflow-hidden">
          {/* Step indicator */}
          <div className="flex border-b border-slate-100">
            {[
              { key: "form", label: "1. Sign-in details" },
              { key: "url", label: "2. Open login URL" },
              { key: "completing", label: "3. Paste response" },
            ].map(({ key, label }, i) => {
              const active = step === key || (step === "completing" && key === "url") || (step === "done" && i < 3);
              const done = (step === "url" && i === 0) || (step === "completing" && i <= 1) || step === "done";
              return (
                <div
                  key={key}
                  className={cn(
                    "flex-1 px-4 py-3 text-xs font-medium",
                    done ? "text-brand-600" : active ? "text-slate-800" : "text-slate-400"
                  )}
                >
                  {label}
                </div>
              );
            })}
          </div>

          <div className="p-5">
            {/* Step 1: Form */}
            {step === "form" && (
              <div className="space-y-4">
                <div className="space-y-1">
                  <label className="text-sm font-medium text-slate-700">Audible email</label>
                  <input
                    type="email"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && handleStartLogin()}
                    placeholder="you@example.com"
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-sm font-medium text-slate-700">Marketplace / locale</label>
                  <select
                    value={locale}
                    onChange={e => setLocale(e.target.value)}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
                  >
                    {LOCALES.map(l => (
                      <option key={l.value} value={l.value}>{l.label} ({l.value})</option>
                    ))}
                  </select>
                </div>
                {error && <p className="text-sm text-red-600">{error}</p>}
                <div className="flex gap-3 pt-1">
                  <button
                    onClick={handleStartLogin}
                    disabled={busy || !email.trim()}
                    className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ChevronRight className="h-4 w-4" />}
                    {busy ? "Generating URL…" : "Get Login URL"}
                  </button>
                  <button onClick={resetFlow} className="rounded-lg px-4 py-2 text-sm text-slate-600 hover:bg-slate-50 transition-colors">
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Step 2: Login URL */}
            {step === "url" && (
              <div className="space-y-4">
                <p className="text-sm text-slate-600">
                  Open the link below in your browser and sign in to Audible. After signing in,
                  copy the full URL from your browser's address bar and paste it in the next step.
                </p>
                <div className="flex gap-2">
                  <input
                    readOnly
                    value={loginUrl}
                    className="flex-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700 font-mono truncate"
                  />
                  <button
                    onClick={handleCopy}
                    className={cn(
                      "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-medium transition-colors shrink-0",
                      copied
                        ? "border-green-200 bg-green-50 text-green-700"
                        : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                    )}
                  >
                    {copied ? <CheckCircle className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    {copied ? "Copied!" : "Copy"}
                  </button>
                  <a
                    href={loginUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors shrink-0"
                  >
                    <ExternalLink className="h-4 w-4" />
                    Open
                  </a>
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={() => setStep("completing")}
                    className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 transition-colors"
                  >
                    <ChevronRight className="h-4 w-4" />
                    I've signed in
                  </button>
                  <button onClick={resetFlow} className="rounded-lg px-4 py-2 text-sm text-slate-600 hover:bg-slate-50 transition-colors">
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Step 3: Response URL */}
            {step === "completing" && (
              <div className="space-y-4">
                <p className="text-sm text-slate-600">
                  Paste the URL from your browser's address bar after signing in.
                  It typically starts with <code className="bg-slate-100 px-1 rounded text-xs">https://</code> or <code className="bg-slate-100 px-1 rounded text-xs">audible://</code>.
                </p>
                <textarea
                  value={responseUrl}
                  onChange={e => setResponseUrl(e.target.value)}
                  placeholder="Paste the response URL here…"
                  rows={3}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"
                />
                {error && <p className="text-sm text-red-600">{error}</p>}
                <div className="flex gap-3">
                  <button
                    onClick={handleCompleteLogin}
                    disabled={busy || !responseUrl.trim()}
                    className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle className="h-4 w-4" />}
                    {busy ? "Connecting…" : "Connect Account"}
                  </button>
                  <button onClick={resetFlow} className="rounded-lg px-4 py-2 text-sm text-slate-600 hover:bg-slate-50 transition-colors">
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Done */}
            {step === "done" && (
              <div className="flex items-center gap-3 py-2">
                <CheckCircle className="h-6 w-6 text-green-500 shrink-0" />
                <div>
                  <p className="text-sm font-medium text-slate-800">Account connected!</p>
                  <p className="text-xs text-slate-500">A library scan has been started automatically.</p>
                </div>
                <button
                  onClick={() => { setShowNextStep(true); resetFlow(); }}
                  className="ml-auto rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 transition-colors"
                >
                  Done
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Account list */}
      {loading ? (
        <div className="space-y-2">
          {[1, 2].map(i => (
            <div key={i} className="h-16 rounded-xl bg-slate-100 animate-pulse" />
          ))}
        </div>
      ) : accounts.length === 0 ? (
        <div className="flex flex-col items-center py-16 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-100 mb-4">
            <Users className="h-7 w-7 text-slate-300" />
          </div>
          <p className="text-sm font-medium text-slate-600">No accounts connected</p>
          <p className="text-xs text-slate-400 mt-1">Add an Audible account to get started.</p>
        </div>
      ) : (
        <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white overflow-hidden">
          {accounts.map(acc => (
            <div key={acc.account_id} className="px-5 py-4">
              <div className="flex items-center gap-4">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-100 text-brand-700 text-sm font-semibold">
                  {acc.name?.[0]?.toUpperCase() || acc.account_id[0]?.toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-900 truncate">{acc.name || acc.account_id}</p>
                  <p className="text-xs text-slate-500 truncate">{acc.account_id}</p>
                </div>
                <LocaleBadge locale={acc.locale} />
                <StatusTooltip authenticated={acc.authenticated} />
                <button
                  onClick={() => handleRemove(acc.account_id)}
                  disabled={removing === acc.account_id}
                  title="Remove account"
                  className="p-1 rounded hover:bg-red-50 text-slate-300 hover:text-red-500 transition-colors shrink-0 disabled:opacity-50"
                >
                  {removing === acc.account_id
                    ? <Loader2 className="h-4 w-4 animate-spin" />
                    : <Trash2 className="h-4 w-4" />}
                </button>
              </div>

              {/* Owner row */}
              <div className="mt-2 ml-[3.25rem] flex items-center gap-1.5">
                <User className="h-3.5 w-3.5 text-slate-300 shrink-0" />
                <span className="text-xs text-slate-400">Owner:</span>
                {acc.added_by_user_id === user?.id ? (
                  <input
                    type="text"
                    value={ownerNameInput}
                    onChange={e => setOwnerNameInput(e.target.value)}
                    onBlur={saveOwnerName}
                    onKeyDown={e => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                    placeholder="Fill in your first name"
                    className="w-36 text-xs rounded border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 px-1.5 py-0.5 text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-brand-500 placeholder:text-white dark:placeholder:text-white"
                  />
                ) : acc.owner_name ? (
                  <span className="text-xs font-medium text-slate-700">{acc.owner_name}</span>
                ) : acc.owner_username ? (
                  <span className="text-xs text-slate-500">{acc.owner_username}</span>
                ) : (
                  <span className="text-xs italic text-slate-300">Unassigned</span>
                )}
                {acc.added_by_user_id === user?.id && savingOwnerName && (
                  <Loader2 className="h-3 w-3 animate-spin text-slate-400" />
                )}
              </div>

              {/* Auto-download toggle — visible to admin or the user who added this account */}
              {canToggleAutoDownload(acc) && (
                <div className="mt-2 ml-[3.25rem] flex items-center gap-2">
                  <button
                    onClick={() => toggleAutoDownload(acc.account_id, acc.auto_download)}
                    disabled={togglingAutoDownload === acc.account_id}
                    className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-50 ${
                      acc.auto_download ? "bg-brand-600" : "bg-slate-200"
                    }`}
                    role="switch"
                    aria-checked={acc.auto_download}
                  >
                    <span className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${acc.auto_download ? "translate-x-4" : "translate-x-0"}`} />
                  </button>
                  <span className="text-xs text-slate-500">Auto-download new books</span>
                  {togglingAutoDownload === acc.account_id && (
                    <Loader2 className="h-3 w-3 animate-spin text-slate-400" />
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
