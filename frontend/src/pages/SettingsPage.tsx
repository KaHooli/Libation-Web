import { useState, useEffect, useCallback, useRef } from "react";
import {
  ShieldCheck, ShieldOff, KeyRound, Loader2, Users, MonitorSmartphone,
  Sliders, Trash2, Plus, RefreshCw, Crown, BookOpen, ShieldAlert,
  Info, CheckCircle, ArrowUpCircle, RotateCcw, ScrollText, Download,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/context/AuthContext";
import { api, authApi, usersApi, settingsApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";

// ── 2FA Section ─────────────────────────────────────────────────────────────

function TwoFactorSection() {
  const { user, refreshUser } = useAuth();
  const [step, setStep] = useState<"idle" | "setup" | "disable">("idle");
  const [qrImage, setQrImage] = useState("");
  const [secret, setSecret] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  const startSetup = async () => {
    setError(""); setSuccess(""); setLoading(true);
    try {
      const { data } = await authApi.setup2fa();
      setSecret(data.secret);
      setQrImage(data.qr_image);
      setStep("setup");
    } catch { setError("Failed to start 2FA setup."); }
    finally { setLoading(false); }
  };

  const confirmEnable = async () => {
    if (!code || code.length !== 6) { setError("Enter the 6-digit code."); return; }
    setError(""); setLoading(true);
    try {
      await authApi.enable2fa(secret, code);
      setSuccess("Two-factor authentication enabled.");
      setStep("idle"); setCode(""); setQrImage(""); setSecret("");
      await refreshUser();
    } catch { setError("Invalid code. Try again."); }
    finally { setLoading(false); }
  };

  const confirmDisable = async () => {
    if (!code || code.length !== 6) { setError("Enter the 6-digit code."); return; }
    setError(""); setLoading(true);
    try {
      await authApi.disable2fa(code);
      setSuccess("Two-factor authentication disabled. Please sign in again.");
      setStep("idle"); setCode("");
      await refreshUser();
    } catch { setError("Invalid code. Try again."); }
    finally { setLoading(false); }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-brand-600" />
          Two-factor authentication
        </CardTitle>
        <CardDescription>
          {user?.totp_enabled
            ? "2FA is currently enabled using an authenticator app."
            : "Add an extra layer of security to your account."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}
        {success && <Alert variant="success">{success}</Alert>}

        {step === "idle" && (
          user?.totp_enabled ? (
            <Button variant="danger" onClick={() => { setStep("disable"); setError(""); setSuccess(""); }}>
              <ShieldOff className="h-4 w-4" /> Disable 2FA
            </Button>
          ) : (
            <Button onClick={startSetup} loading={loading}>
              <ShieldCheck className="h-4 w-4" /> Set up 2FA
            </Button>
          )
        )}

        {step === "setup" && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Scan the QR code with your authenticator app (Google Authenticator, Authy, etc.),
              then enter the 6-digit code to confirm.
            </p>
            {qrImage && (
              <div className="flex justify-center">
                <img
                  src={`data:image/png;base64,${qrImage}`}
                  alt="2FA QR code"
                  className="h-44 w-44 rounded-lg border border-slate-200 dark:border-slate-600"
                />
              </div>
            )}
            <div>
              <Label htmlFor="setup-code">Verification code</Label>
              <Input id="setup-code" type="text" inputMode="numeric" maxLength={6} placeholder="000000"
                value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
            </div>
            <div className="flex gap-2">
              <Button onClick={confirmEnable} loading={loading}>Confirm &amp; enable</Button>
              <Button variant="outline" onClick={() => { setStep("idle"); setCode(""); setError(""); }}>Cancel</Button>
            </div>
          </div>
        )}

        {step === "disable" && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Enter the current 6-digit code from your authenticator app to disable 2FA.
            </p>
            <div>
              <Label htmlFor="disable-code">Authenticator code</Label>
              <Input id="disable-code" type="text" inputMode="numeric" maxLength={6} placeholder="000000"
                value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
            </div>
            <div className="flex gap-2">
              <Button variant="danger" onClick={confirmDisable} loading={loading}>Disable 2FA</Button>
              <Button variant="outline" onClick={() => { setStep("idle"); setCode(""); setError(""); }}>Cancel</Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Change Username Section ──────────────────────────────────────────────────

function ChangeUsernameSection() {
  const { user, refreshUser } = useAuth();
  const [newUsername, setNewUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(""); setSuccess("");
    if (newUsername.trim().length < 3) { setError("Username must be at least 3 characters."); return; }
    if (newUsername.trim() === user?.username) { setError("That is already your username."); return; }
    setLoading(true);
    try {
      await authApi.changeUsername(newUsername.trim(), password);
      await refreshUser();
      setSuccess("Username updated.");
      setNewUsername(""); setPassword("");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail ?? "Failed to update username.";
      setError(msg);
    } finally { setLoading(false); }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-brand-600" />
          Change username
        </CardTitle>
        <CardDescription>Current username: <strong>{user?.username}</strong></CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4 max-w-sm">
          {error && <Alert variant="error">{error}</Alert>}
          {success && <Alert variant="success">{success}</Alert>}
          <div>
            <Label htmlFor="new-username">New username</Label>
            <Input id="new-username" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} required minLength={3} />
          </div>
          <div>
            <Label htmlFor="cu-password">Current password</Label>
            <Input id="cu-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          <Button type="submit" loading={loading}>Update username</Button>
        </form>
      </CardContent>
    </Card>
  );
}

// ── Change Password Section ──────────────────────────────────────────────────

function ChangePasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(""); setSuccess("");
    if (next.length < 8) { setError("New password must be at least 8 characters."); return; }
    if (next !== confirm) { setError("Passwords do not match."); return; }
    setLoading(true);
    try {
      await authApi.changePassword(current, next);
      setSuccess("Password changed. You will be signed out of other sessions.");
      setCurrent(""); setNext(""); setConfirm("");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail ?? "Failed to change password.";
      setError(msg);
    } finally { setLoading(false); }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="h-5 w-5 text-brand-600" />
          Change password
        </CardTitle>
        <CardDescription>Update your account password.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4 max-w-sm">
          {error && <Alert variant="error">{error}</Alert>}
          {success && <Alert variant="success">{success}</Alert>}
          <div>
            <Label htmlFor="current-pw">Current password</Label>
            <Input id="current-pw" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor="new-pw">New password</Label>
            <Input id="new-pw" type="password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={8} />
          </div>
          <div>
            <Label htmlFor="confirm-pw">Confirm new password</Label>
            <Input id="confirm-pw" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
          </div>
          <Button type="submit" loading={loading}>Update password</Button>
        </form>
      </CardContent>
    </Card>
  );
}

// ── Session Management Section ───────────────────────────────────────────────

interface SessionItem {
  id: number;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  user_agent: string | null;
  ip_address: string | null;
}

function SessionsSection() {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [revoking, setRevoking] = useState<number | null>(null);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await authApi.listSessions();
      setSessions(data);
    } catch { setError("Failed to load sessions."); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const revoke = async (id: number) => {
    setRevoking(id);
    try {
      await authApi.revokeSession(id);
      setSessions(s => s.filter(x => x.id !== id));
    } catch { setError("Failed to revoke session."); }
    finally { setRevoking(null); }
  };

  const revokeAll = async () => {
    setRevoking(-1);
    try {
      await authApi.revokeAllSessions();
      setSessions([]);
    } catch { setError("Failed to revoke sessions."); }
    finally { setRevoking(null); }
  };

  const fmt = (iso: string) => new Date(iso).toLocaleDateString(undefined, {
    month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit",
  });

  const shortUA = (ua: string | null) => {
    if (!ua) return "Unknown device";
    if (ua.includes("Firefox")) return "Firefox";
    if (ua.includes("Chrome")) return "Chrome";
    if (ua.includes("Safari")) return "Safari";
    if (ua.includes("curl")) return "curl";
    return ua.slice(0, 40);
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <MonitorSmartphone className="h-5 w-5 text-brand-600" />
              Active sessions
            </CardTitle>
            <CardDescription>Your currently active login sessions.</CardDescription>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={load}>
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
            {sessions.length > 0 && (
              <Button variant="danger" size="sm" onClick={revokeAll} loading={revoking === -1}>
                Revoke all
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {error && <Alert variant="error" className="mb-4">{error}</Alert>}
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          </div>
        ) : sessions.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400 py-4 text-center">No active sessions found.</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-700">
            {sessions.map(s => (
              <li key={s.id} className="flex items-center gap-3 py-3">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{shortUA(s.user_agent)}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {s.ip_address ?? "IP unknown"} · Last used {fmt(s.last_used_at)}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => revoke(s.id)}
                  loading={revoking === s.id}
                  className="shrink-0 text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ── Libation Settings Section ────────────────────────────────────────────────

interface LibationSettingsData {
  decrypt_to_lossy: boolean | null;
  split_files_by_chapter: boolean | null;
  download_episodes: boolean | null;
  create_cue_sheet: boolean | null;
  save_cover_art_to_file: boolean | null;
  allow_audiobook_overwrite: boolean | null;
  strip_audible_brand_audio: boolean | null;
  strip_unabridged: boolean | null;
}

const LIBATION_TOGGLES: { key: keyof LibationSettingsData; label: string; desc: string }[] = [
  { key: "decrypt_to_lossy", label: "Download as MP3 (lossy)", desc: "Converts to MP3 instead of keeping lossless AAX/FLAC" },
  { key: "split_files_by_chapter", label: "Split by chapter", desc: "Creates one file per chapter instead of a single file" },
  { key: "download_episodes", label: "Download episodes", desc: "Also downloads podcast-style episodic content" },
  { key: "create_cue_sheet", label: "Create .cue sheet", desc: "Generate a cue sheet alongside the audio file" },
  { key: "save_cover_art_to_file", label: "Save cover art", desc: "Save cover art as a separate image file" },
  { key: "allow_audiobook_overwrite", label: "Allow overwrite", desc: "Re-download and overwrite existing files" },
  { key: "strip_audible_brand_audio", label: "Strip Audible branding", desc: "Remove Audible intro and outro audio" },
  { key: "strip_unabridged", label: 'Strip "Unabridged" from titles', desc: 'Remove the word "Unabridged" from file names' },
];

function LibationSettingsSection() {
  const [data, setData] = useState<LibationSettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    settingsApi.getLibation()
      .then(r => setData(r.data))
      .catch(() => setError("Could not load settings. Connect an Audible account and scan first."))
      .finally(() => setLoading(false));
  }, []);

  const toggle = async (key: keyof LibationSettingsData) => {
    if (!data) return;
    const newVal = !data[key];
    const updated = { ...data, [key]: newVal };
    setData(updated);
    setSaving(true); setError(""); setSuccess("");
    try {
      await settingsApi.updateLibation(updated);
      setSuccess("Settings saved.");
      setTimeout(() => setSuccess(""), 3000);
    } catch { setError("Failed to save."); }
    finally { setSaving(false); }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="py-8 flex justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sliders className="h-5 w-5 text-brand-600" />
          Libation download settings
        </CardTitle>
        <CardDescription>
          Controls Libation's download behavior. Settings are read from and written to{" "}
          <code className="text-xs bg-slate-100 dark:bg-slate-700 px-1 rounded">/config/appsettings.json</code>.
          {saving && <span className="ml-2 text-xs text-brand-600"> Saving…</span>}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <Alert variant="error">{error}</Alert>}
        {success && <Alert variant="success">{success}</Alert>}
        {!data ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No appsettings.json found yet. Settings will appear after you connect an Audible account.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-700">
            {LIBATION_TOGGLES.map(({ key, label, desc }) => (
              <li key={key} className="flex items-center justify-between gap-4 py-3">
                <div>
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{label}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">{desc}</p>
                </div>
                <button
                  onClick={() => toggle(key)}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
                    data[key] ? "bg-brand-600" : "bg-slate-200 dark:bg-slate-600"
                  }`}
                  role="switch"
                  aria-checked={!!data[key]}
                >
                  <span
                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                      data[key] ? "translate-x-4" : "translate-x-0"
                    }`}
                  />
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ── User Management Section (admin only) ────────────────────────────────────

interface UserPermissions {
  can_download: boolean;
  can_scan: boolean;
  can_manage_accounts: boolean;
  can_liberate: boolean;
  can_remove_downloads: boolean;
}

const DEFAULT_PERMISSIONS: UserPermissions = {
  can_download: true,
  can_scan: true,
  can_manage_accounts: true,
  can_liberate: true,
  can_remove_downloads: false,
};

const PERM_LABELS: { key: keyof UserPermissions; label: string }[] = [
  { key: "can_download", label: "Download" },
  { key: "can_scan", label: "Scan" },
  { key: "can_manage_accounts", label: "Manage accounts" },
  { key: "can_liberate", label: "Liberate" },
  { key: "can_remove_downloads", label: "Remove downloads" },
];

interface UserItem {
  id: number;
  username: string;
  is_active: boolean;
  is_admin: boolean;
  totp_enabled: boolean;
  created_at: string;
  permissions?: UserPermissions | null;
  download_cap?: number | null;
  owner_name?: string | null;
  audible_account_id?: string | null;
}

function OwnerInfoCell({
  userId, initialName, initialAccountId, accounts, onSaved,
}: {
  userId: number;
  initialName: string | null | undefined;
  initialAccountId: string | null | undefined;
  accounts: { account_id: string; name: string }[];
  onSaved: () => void;
}) {
  const [name, setName] = useState(initialName ?? "");
  const [saving, setSaving] = useState(false);

  const saveName = async () => {
    const trimmed = name.trim();
    if (trimmed === (initialName ?? "")) return;
    setSaving(true);
    try { await usersApi.update(userId, { owner_name: trimmed }); }
    finally { setSaving(false); }
  };

  const saveAccount = async (newId: string) => {
    setSaving(true);
    try {
      await usersApi.update(userId, { audible_account_id: newId || null });
      onSaved();
    } finally { setSaving(false); }
  };

  return (
    <div className="flex items-center gap-3 mt-1 flex-wrap">
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-slate-400 dark:text-slate-500 shrink-0">Owner Name:</span>
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          onBlur={saveName}
          onKeyDown={e => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
          placeholder="First name"
          className="w-24 rounded border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 px-1.5 py-0.5 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-brand-500 placeholder:text-slate-300 dark:placeholder:text-slate-500"
        />
      </div>
      {accounts.length > 0 && (
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-slate-400 dark:text-slate-500 shrink-0">Audible Account:</span>
          <select
            value={initialAccountId ?? ""}
            onChange={e => saveAccount(e.target.value)}
            className="rounded border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 px-1.5 py-0.5 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            <option value="">— None —</option>
            {accounts.map(a => (
              <option key={a.account_id} value={a.account_id}>{a.name || a.account_id}</option>
            ))}
          </select>
        </div>
      )}
      {saving && <Loader2 className="h-3 w-3 animate-spin text-slate-400" />}
    </div>
  );
}

function PermissionRow({ user, onSaved }: { user: UserItem; onSaved: () => void }) {
  const perms: UserPermissions = { ...DEFAULT_PERMISSIONS, ...(user.permissions ?? {}) };
  const [cap, setCap] = useState(String(user.download_cap ?? ""));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const togglePerm = async (key: keyof UserPermissions) => {
    setSaving(true); setError("");
    try {
      await usersApi.updatePermissions(user.id, { [key]: !perms[key] });
      onSaved();
    } catch { setError("Failed to save."); }
    finally { setSaving(false); }
  };

  const saveCap = async () => {
    setSaving(true); setError("");
    try {
      const val = cap.trim() === "" ? null : parseInt(cap, 10);
      await usersApi.updatePermissions(user.id, { download_cap: isNaN(val as number) ? null : val });
      onSaved();
    } catch { setError("Failed to save."); }
    finally { setSaving(false); }
  };

  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-4 space-y-3 bg-slate-50/50 dark:bg-slate-800/50">
      <div className="flex items-center gap-2">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-100 dark:bg-brand-900/40 text-brand-700 dark:text-brand-400 text-xs font-semibold">
          {user.username[0].toUpperCase()}
        </div>
        <span className="text-sm font-semibold text-slate-800 dark:text-slate-200">{user.username}</span>
        {saving && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />}
        {error && <span className="text-xs text-red-500">{error}</span>}
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-2">
        {PERM_LABELS.map(({ key, label }) => (
          <label key={key} className="flex items-center gap-1.5 cursor-pointer select-none">
            <button
              onClick={() => togglePerm(key)}
              className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
                perms[key] ? "bg-brand-600" : "bg-slate-200 dark:bg-slate-600"
              }`}
              role="switch"
              aria-checked={perms[key]}
            >
              <span className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-150 ease-in-out ${perms[key] ? "translate-x-4" : "translate-x-0"}`} />
            </button>
            <span className="text-xs text-slate-600 dark:text-slate-300">{label}</span>
          </label>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <label className="text-xs text-slate-500 dark:text-slate-400 shrink-0">Download cap (per 12h):</label>
        <input
          type="number"
          min={0}
          placeholder="unlimited"
          value={cap}
          onChange={e => setCap(e.target.value)}
          onBlur={saveCap}
          onKeyDown={e => { if (e.key === "Enter") saveCap(); }}
          className="w-24 rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 px-2 py-1 text-xs text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-brand-500"
        />
        <span className="text-xs text-slate-400">blank = unlimited</span>
      </div>
    </div>
  );
}

function UserPermissionsSection() {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await usersApi.list();
      setUsers(data.filter((u: UserItem) => !u.is_admin));
    } catch { setError("Failed to load users."); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-brand-600" />
              User permissions
            </CardTitle>
            <CardDescription>
              Per-user feature access and download caps. Admins always have full access.
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <Alert variant="error">{error}</Alert>}
        {loading ? (
          <div className="flex justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-slate-400" /></div>
        ) : users.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400 py-4 text-center">No non-admin users yet.</p>
        ) : (
          users.map(u => <PermissionRow key={u.id} user={u} onSaved={load} />)
        )}
      </CardContent>
    </Card>
  );
}

function UserManagementSection() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<UserItem[]>([]);
  const [accounts, setAccounts] = useState<{ account_id: string; name: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newIsAdmin, setNewIsAdmin] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const [usersRes, accountsRes] = await Promise.all([usersApi.list(), api.get("/accounts")]);
      setUsers(usersRes.data);
      setAccounts(accountsRes.data);
    } catch { setError("Failed to load users."); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const createUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError("");
    if (newUsername.length < 3) { setCreateError("Username must be at least 3 characters."); return; }
    if (newPassword.length < 8) { setCreateError("Password must be at least 8 characters."); return; }
    setCreating(true);
    try {
      await usersApi.create(newUsername, newPassword, newIsAdmin);
      setNewUsername(""); setNewPassword(""); setNewIsAdmin(false); setShowCreate(false);
      await load();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "Failed to create user.";
      setCreateError(msg);
    } finally { setCreating(false); }
  };

  const toggleActive = async (u: UserItem) => {
    try {
      await usersApi.update(u.id, { is_active: !u.is_active });
      setUsers(us => us.map(x => x.id === u.id ? { ...x, is_active: !u.is_active } : x));
    } catch { setError("Failed to update user."); }
  };

  const deleteUser = async (id: number) => {
    if (!confirm("Delete this user? This cannot be undone.")) return;
    try {
      await usersApi.delete(id);
      setUsers(us => us.filter(x => x.id !== id));
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "Failed to delete user.";
      setError(msg);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Users className="h-5 w-5 text-brand-600" />
              User management
            </CardTitle>
            <CardDescription>Create and manage accounts that can access this instance.</CardDescription>
          </div>
          <Button size="sm" onClick={() => setShowCreate(s => !s)}>
            <Plus className="h-3.5 w-3.5" /> Add user
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}

        {showCreate && (
          <form onSubmit={createUser} className="rounded-lg border border-slate-200 dark:border-slate-700 p-4 space-y-3 bg-slate-50 dark:bg-slate-700/40">
            <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">New user</p>
            {createError && <Alert variant="error">{createError}</Alert>}
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="nu-user">Username</Label>
                <Input id="nu-user" value={newUsername} onChange={e => setNewUsername(e.target.value)} required placeholder="username" />
              </div>
              <div>
                <Label htmlFor="nu-pass">Password</Label>
                <Input id="nu-pass" type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} required placeholder="min 8 characters" />
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300 cursor-pointer">
              <input type="checkbox" checked={newIsAdmin} onChange={e => setNewIsAdmin(e.target.checked)}
                className="rounded border-slate-300" />
              Grant admin privileges
            </label>
            <div className="flex gap-2">
              <Button type="submit" size="sm" loading={creating}>Create user</Button>
              <Button type="button" variant="outline" size="sm" onClick={() => setShowCreate(false)}>Cancel</Button>
            </div>
          </form>
        )}

        {loading ? (
          <div className="flex justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-slate-400" /></div>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-700">
            {users.map(u => (
              <li key={u.id} className="flex items-center gap-3 py-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-100 dark:bg-brand-900/40 text-brand-700 dark:text-brand-400 text-sm font-semibold">
                  {u.username[0].toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{u.username}</p>
                    {u.is_admin && <span title="Admin"><Crown className="h-3.5 w-3.5 text-amber-500" /></span>}
                    {!u.is_active && <span className="text-xs text-red-500 font-medium">disabled</span>}
                  </div>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    Joined {new Date(u.created_at).toLocaleDateString()} · {u.totp_enabled ? "2FA on" : "No 2FA"}
                  </p>
                  <OwnerInfoCell userId={u.id} initialName={u.owner_name} initialAccountId={u.audible_account_id} accounts={accounts} onSaved={load} />
                </div>
                {u.id !== me?.id && (
                  <div className="flex gap-1.5 shrink-0">
                    <Button variant="outline" size="sm" onClick={() => toggleActive(u)} className="text-xs">
                      {u.is_active ? "Disable" : "Enable"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => deleteUser(u.id)}
                      className="text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ── About / Update Section ───────────────────────────────────────────────────

interface UpdateStatus {
  installed_version: string | null;
  latest_version: string | null;
  up_to_date: boolean | null;
  asset_found?: boolean;
  published_at?: string | null;
  release_notes_url?: string | null;
  has_rollback?: boolean;
  error?: string;
}

function AboutSection() {
  const { user } = useAuth();
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/updates/status");
      setStatus(data);
    } catch { setError("Could not fetch version info."); }
    finally { setLoading(false); }
  };

  const checkNow = async () => {
    setChecking(true); setError(""); setMessage("");
    try {
      const { data } = await api.post("/updates/check");
      setStatus(data);
    } catch { setError("Could not reach GitHub."); }
    finally { setChecking(false); }
  };

  const pollUntilReady = async () => {
    // Give uvicorn time to shut down
    await new Promise(r => setTimeout(r, 3000));
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const res = await fetch("/api/health");
        if (res.ok) {
          setRestarting(false);
          setInstalling(false);
          setMessage("Done! CLI updated successfully.");
          await fetchStatus();
          return;
        }
      } catch { /* still restarting */ }
    }
    setRestarting(false);
    setInstalling(false);
    setError("Server did not come back in time — please refresh the page.");
  };

  const install = async () => {
    setInstalling(true); setError(""); setMessage("");
    try {
      const { data } = await api.post("/updates/install");
      setMessage(data.message);
      setRestarting(true);
      await pollUntilReady();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail ?? "Update failed.");
      setInstalling(false);
    }
  };

  const rollback = async () => {
    if (!confirm("Roll back to the previous CLI version?")) return;
    setInstalling(true); setError(""); setMessage("");
    try {
      const { data } = await api.post("/updates/rollback");
      setMessage(data.message);
      setRestarting(true);
      await pollUntilReady();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail ?? "Rollback failed.");
      setInstalling(false);
    }
  };

  useEffect(() => { fetchStatus(); }, []);

  const fmtDate = (iso: string | null | undefined) =>
    iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Info className="h-5 w-5 text-brand-600" />
          About
        </CardTitle>
        <CardDescription>LibationCLI version and update management.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}
        {message && !restarting && <Alert variant="success">{message}</Alert>}

        {restarting && (
          <div className="flex items-center gap-3 rounded-xl border border-brand-200 bg-brand-50 dark:border-brand-800 dark:bg-brand-950/30 px-4 py-3 text-sm text-brand-700 dark:text-brand-300">
            <Loader2 className="h-4 w-4 animate-spin shrink-0" />
            Server restarting — please wait…
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          </div>
        ) : (
          <dl className="text-sm divide-y divide-slate-100 dark:divide-slate-700">
            <div className="flex items-center justify-between py-2.5">
              <dt className="text-slate-500 dark:text-slate-400">Installed CLI version</dt>
              <dd className="font-mono font-semibold text-slate-800 dark:text-slate-200">
                {status?.installed_version ? `v${status.installed_version}` : "Unknown"}
              </dd>
            </div>
            <div className="flex items-center justify-between py-2.5">
              <dt className="text-slate-500 dark:text-slate-400">Latest available</dt>
              <dd className="font-mono font-semibold text-slate-800 dark:text-slate-200">
                {status?.error
                  ? <span className="text-xs text-slate-400 font-normal">Unavailable</span>
                  : status?.latest_version ? `v${status.latest_version}` : "—"}
              </dd>
            </div>
            {status?.published_at && (
              <div className="flex items-center justify-between py-2.5">
                <dt className="text-slate-500 dark:text-slate-400">Latest released</dt>
                <dd className="text-slate-700 dark:text-slate-300">{fmtDate(status.published_at)}</dd>
              </div>
            )}
            <div className="flex items-center justify-between py-2.5">
              <dt className="text-slate-500 dark:text-slate-400">Status</dt>
              <dd>
                {status?.up_to_date === true && (
                  <span className="inline-flex items-center gap-1.5 text-xs font-medium text-green-600 dark:text-green-400">
                    <CheckCircle className="h-3.5 w-3.5" /> Up to date
                  </span>
                )}
                {status?.up_to_date === false && (
                  <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400">
                    <ArrowUpCircle className="h-3.5 w-3.5" /> Update available
                  </span>
                )}
                {status?.up_to_date === null && (
                  <span className="text-xs text-slate-400">Unable to check</span>
                )}
              </dd>
            </div>
          </dl>
        )}

        {/* Admin-only controls */}
        {user?.is_admin && !loading && (
          <div className="flex flex-wrap gap-2 pt-2 border-t border-slate-100 dark:border-slate-700">
            <Button variant="outline" size="sm" onClick={checkNow}
              loading={checking} disabled={installing || restarting}>
              <RefreshCw className="h-3.5 w-3.5" /> Check for updates
            </Button>

            {status?.up_to_date === false && status.asset_found && (
              <Button size="sm" onClick={install}
                loading={installing} disabled={restarting}>
                <ArrowUpCircle className="h-3.5 w-3.5" />
                Update to v{status.latest_version}
              </Button>
            )}

            {status?.has_rollback && (
              <Button variant="outline" size="sm" onClick={rollback}
                disabled={installing || restarting}>
                <RotateCcw className="h-3.5 w-3.5" /> Roll back
              </Button>
            )}

            {status?.release_notes_url && (
              <a href={status.release_notes_url} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-700 px-3 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-600 transition-colors">
                Release notes ↗
              </a>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Logs Section (admin only) ────────────────────────────────────────────────

const LOG_LEVELS = ["ALL", "INFO", "WARN", "ERROR", "DEBUG"] as const;
type LogLevel = typeof LOG_LEVELS[number];
const LOG_LINE_COUNTS = [100, 200, 500, 1000] as const;

function logLineColor(line: string): string {
  if (line.includes("[ERROR]")) return "text-red-400";
  if (line.includes("[WARN ]")) return "text-amber-400";
  if (line.includes("[DEBUG]")) return "text-slate-500";
  return "text-slate-300";
}

function LogsSection() {
  const [lines, setLines] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [level, setLevel] = useState<LogLevel>("ALL");
  const [lineCount, setLineCount] = useState<number>(200);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchLogs = useCallback(async (scrollToBottom = false) => {
    setLoading(true);
    setFetchError(null);
    try {
      const { data } = await api.get("/logs", { params: { lines: lineCount, level: level.toLowerCase() } });
      setLines(data.lines);
      setTotal(data.total);
      setTruncated(data.truncated);
      if (scrollToBottom) setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    } catch { setFetchError("Failed to load logs."); }
    finally { setLoading(false); }
  }, [lineCount, level]);

  useEffect(() => { fetchLogs(true); }, [fetchLogs]);

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (autoRefresh) intervalRef.current = setInterval(() => fetchLogs(false), 5000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [autoRefresh, fetchLogs]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <ScrollText className="h-5 w-5 text-brand-600" />
              Server logs
            </CardTitle>
            <CardDescription>
              {truncated ? `Showing last ${lines.length} of ${total} lines` : `${total} line${total !== 1 ? "s" : ""}`}
              {" · "}/config/logs/libation-web.log
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <a
              href="/api/logs/download"
              download="libation-web.log"
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
            >
              <Download className="h-3.5 w-3.5" /> Download
            </a>
            <button
              onClick={() => fetchLogs(false)}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} /> Refresh
            </button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-0.5 rounded-lg border border-slate-200 dark:border-slate-700 p-1">
            {LOG_LEVELS.map((l) => (
              <button
                key={l}
                onClick={() => setLevel(l)}
                className={cn(
                  "px-2.5 py-0.5 text-xs font-semibold rounded-md transition-colors",
                  level === l ? "bg-brand-600 text-white" : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-white"
                )}
              >
                {l}
              </button>
            ))}
          </div>
          <select
            value={lineCount}
            onChange={(e) => setLineCount(Number(e.target.value))}
            className="text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-500"
          >
            {LOG_LINE_COUNTS.map((n) => <option key={n} value={n}>{n} lines</option>)}
          </select>
          <button
            onClick={() => setAutoRefresh((v) => !v)}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-lg border transition-colors",
              autoRefresh
                ? "border-brand-500 bg-brand-50 dark:bg-brand-900/20 text-brand-600 dark:text-brand-400"
                : "border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
            )}
          >
            <span className={cn("inline-block h-1.5 w-1.5 rounded-full", autoRefresh ? "bg-brand-500 animate-pulse" : "bg-slate-400")} />
            Auto-refresh
          </button>
        </div>

        {/* Log output */}
        <div className="rounded-lg bg-slate-950 border border-slate-800 overflow-hidden">
          <div className="h-96 overflow-y-auto p-3 font-mono text-xs leading-relaxed">
            {fetchError ? (
              <span className="text-red-400">{fetchError}</span>
            ) : lines.length === 0 && !loading ? (
              <span className="text-slate-500">No log entries found.</span>
            ) : (
              <>
                {truncated && (
                  <div className="text-slate-600 mb-2 select-none">— {total - lines.length} earlier lines not shown —</div>
                )}
                {lines.map((line, i) => (
                  <div key={i} className={cn("whitespace-pre-wrap break-all", logLineColor(line))}>{line}</div>
                ))}
                <div ref={bottomRef} />
              </>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── API Docs Section ─────────────────────────────────────────────────────────

function ApiDocsSection() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookOpen className="h-5 w-5 text-brand-600" />
          API documentation
        </CardTitle>
        <CardDescription>
          Interactive API docs are built into Libation Web UI via FastAPI.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex gap-3 flex-wrap">
        <a
          href="/docs"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-700 px-4 py-2 text-sm font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-600 transition-colors shadow-sm"
        >
          Swagger UI
        </a>
        <a
          href="/redoc"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-700 px-4 py-2 text-sm font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-600 transition-colors shadow-sm"
        >
          ReDoc
        </a>
      </CardContent>
    </Card>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export function SettingsPage() {
  const { user } = useAuth();

  return (
    <div className="max-w-2xl space-y-6">
      {user?.is_admin && <LibationSettingsSection />}
      {user?.is_admin && <UserManagementSection />}
      {user?.is_admin && <UserPermissionsSection />}
      <SessionsSection />
      <TwoFactorSection />
      <ChangeUsernameSection />
      <ChangePasswordSection />
      <AboutSection />
      {user?.is_admin && <LogsSection />}
      {user?.is_admin && <ApiDocsSection />}
    </div>
  );
}
