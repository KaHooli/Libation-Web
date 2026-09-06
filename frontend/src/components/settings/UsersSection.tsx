import { useState, useEffect } from "react";
import { Users, ShieldAlert, RefreshCw, Loader2, Plus, Trash2, Crown } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { api, usersApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";

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

export function UserPermissionsSection() {
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

export function UserManagementSection() {
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
