import { useState, useEffect } from "react";
import { KeyRound, Loader2, Lock, ShieldCheck, ShieldAlert, CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";

interface OidcSettings {
  enabled: boolean;
  issuer: string;
  client_id: string;
  client_secret_set: boolean;
  redirect_url: string;
  scopes: string;
  provider_name: string;
  username_claim: string;
  email_claim: string;
  groups_claim: string;
  admin_group: string;
  auto_create_users: boolean;
  env_locked: string[];
  configured: boolean;
  sso_has_worked: boolean;
  password_login_enabled: boolean;
  password_login_forced: boolean;
}

interface ProviderCheck {
  issuer: string;
  authorization_endpoint: string | null;
  token_endpoint: string | null;
  signing_keys: number;
  scopes_supported: string[] | null;
}

const TEXT_FIELDS: { key: keyof OidcSettings; label: string; hint?: string; placeholder?: string }[] = [
  { key: "issuer", label: "Issuer URL", placeholder: "https://auth.example.com",
    hint: "The provider's base URL. Everything else is discovered from it." },
  { key: "client_id", label: "Client ID" },
  { key: "redirect_url", label: "Redirect URL",
    placeholder: "https://libation.example.com/api/auth/oidc/callback",
    hint: "Required behind a reverse proxy — otherwise the callback URL is derived from the request, which would use the internal host." },
  { key: "provider_name", label: "Button label", hint: "Shown on the sign-in page." },
  { key: "scopes", label: "Scopes" },
  { key: "username_claim", label: "Username claim" },
  { key: "email_claim", label: "Email claim" },
  { key: "groups_claim", label: "Groups claim" },
  { key: "admin_group", label: "Admin group",
    hint: "Members are made admin on every sign-in, and non-members have it removed. Leave blank to manage admin in-app." },
];

export function OidcSection() {
  const [data, setData] = useState<OidcSettings | null>(null);
  const [secret, setSecret] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [check, setCheck] = useState<ProviderCheck | null>(null);

  const load = async () => {
    try {
      const { data } = await api.get("/auth/oidc/settings");
      setData(data);
    } catch { setError("Could not load single sign-on settings."); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const locked = (field: string) => data?.env_locked.includes(field) ?? false;

  const save = async () => {
    if (!data) return;
    setSaving(true); setError(""); setSuccess(""); setCheck(null);
    try {
      const body: Record<string, unknown> = {
        enabled: data.enabled,
        auto_create_users: data.auto_create_users,
      };
      for (const { key } of TEXT_FIELDS) body[key] = data[key];
      // Only send the secret when one was typed. Omitted means "keep the
      // stored one" — the field is never populated, so sending it blank on an
      // unrelated edit would silently clear it.
      if (secret) body.client_secret = secret;
      const { data: updated } = await api.put("/auth/oidc/settings", body);
      setData(updated);
      setSecret("");
      setSuccess("Saved.");
      setTimeout(() => setSuccess(""), 3000);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail ?? "Failed to save.";
      setError(msg);
    } finally { setSaving(false); }
  };

  const clearSecret = async () => {
    setSaving(true); setError("");
    try {
      const { data: updated } = await api.put("/auth/oidc/settings", { client_secret: "" });
      setData(updated);
    } catch { setError("Failed to clear the client secret."); }
    finally { setSaving(false); }
  };

  const test = async () => {
    setTesting(true); setError(""); setCheck(null);
    try {
      const { data } = await api.post("/auth/oidc/test");
      setCheck(data);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail ?? "Could not reach the provider.";
      setError(msg);
    } finally { setTesting(false); }
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
  if (!data) return <Alert variant="error">{error || "Unavailable."}</Alert>;

  const set = (patch: Partial<OidcSettings>) => setData({ ...data, ...patch });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="h-5 w-5 text-brand-600" />
          Single sign-on (OIDC)
        </CardTitle>
        <CardDescription>
          Sign in through Authelia, Authentik, Keycloak, Pocket ID or any other
          OpenID Connect provider.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}
        {success && <Alert variant="success">{success}</Alert>}

        <PasswordLoginState data={data} />

        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300 cursor-pointer">
          <input
            type="checkbox"
            checked={data.enabled}
            disabled={locked("enabled")}
            onChange={e => set({ enabled: e.target.checked })}
            className="rounded border-slate-300 disabled:opacity-50"
          />
          Enable single sign-on
          {locked("enabled") && <EnvBadge />}
        </label>

        <div className="grid gap-4 sm:grid-cols-2">
          {TEXT_FIELDS.map(({ key, label, hint, placeholder }) => (
            <div key={key} className={key === "issuer" || key === "redirect_url" ? "sm:col-span-2" : ""}>
              <Label htmlFor={`oidc-${key}`} className="flex items-center gap-1.5">
                {label}
                {locked(key) && <EnvBadge />}
              </Label>
              <Input
                id={`oidc-${key}`}
                value={String(data[key] ?? "")}
                placeholder={placeholder}
                disabled={locked(key)}
                onChange={e => set({ [key]: e.target.value } as Partial<OidcSettings>)}
              />
              {hint && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p>}
            </div>
          ))}

          <div className="sm:col-span-2">
            <Label htmlFor="oidc-secret" className="flex items-center gap-1.5">
              Client secret
              {locked("client_secret") && <EnvBadge />}
            </Label>
            <div className="flex gap-2">
              <Input
                id="oidc-secret"
                type="password"
                value={secret}
                disabled={locked("client_secret")}
                placeholder={data.client_secret_set ? "•••••••• (stored)" : "Not set"}
                onChange={e => setSecret(e.target.value)}
              />
              {data.client_secret_set && !locked("client_secret") && (
                <Button variant="outline" size="sm" onClick={clearSecret} loading={saving}>
                  Clear
                </Button>
              )}
            </div>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Stored encrypted and never sent back to this page. Leave blank to keep the current one.
            </p>
          </div>
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300 cursor-pointer">
          <input
            type="checkbox"
            checked={data.auto_create_users}
            disabled={locked("auto_create_users")}
            onChange={e => set({ auto_create_users: e.target.checked })}
            className="rounded border-slate-300 disabled:opacity-50"
          />
          Create accounts automatically on first sign-in
          {locked("auto_create_users") && <EnvBadge />}
        </label>

        <div className="flex gap-2 flex-wrap">
          <Button onClick={save} loading={saving}>Save</Button>
          <Button variant="outline" onClick={test} loading={testing} disabled={!data.configured}>
            Test connection
          </Button>
        </div>

        {check && (
          <div className="rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/30 p-3 space-y-1">
            <p className="flex items-center gap-2 text-sm font-medium text-emerald-800 dark:text-emerald-300">
              <CheckCircle2 className="h-4 w-4" /> Reached the provider
            </p>
            <dl className="text-xs text-emerald-800/80 dark:text-emerald-300/80 space-y-0.5">
              <div>Issuer: <span className="font-mono">{check.issuer}</span></div>
              <div>Signing keys published: {check.signing_keys}</div>
              {check.scopes_supported && (
                <div>Scopes: <span className="font-mono">{check.scopes_supported.join(" ")}</span></div>
              )}
            </dl>
            <p className="text-xs text-emerald-800/70 dark:text-emerald-300/70 pt-1">
              This proves the provider is reachable and its keys are readable. It does
              not check the client secret or the redirect URL — only a real sign-in does.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function EnvBadge() {
  return (
    <span
      title="Set by an environment variable, so it cannot be edited here"
      className="inline-flex items-center gap-1 rounded bg-slate-100 dark:bg-slate-700 px-1.5 py-0.5 text-[10px] font-medium text-slate-500 dark:text-slate-400"
    >
      <Lock className="h-2.5 w-2.5" /> env
    </span>
  );
}

/**
 * Explains the password-login state rather than just reflecting it.
 *
 * The rule is deliberately not "SSO is configured", so a bare toggle would
 * look broken to anyone who had filled the form in and expected password
 * sign-in to switch off immediately.
 */
function PasswordLoginState({ data }: { data: OidcSettings }) {
  if (data.password_login_forced) {
    return (
      <div className="flex items-start gap-2.5 rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2.5">
        <ShieldCheck className="h-4 w-4 shrink-0 mt-0.5 text-slate-400" />
        <p className="text-xs text-slate-600 dark:text-slate-400">
          Password sign-in is pinned <strong>{data.password_login_enabled ? "on" : "off"}</strong> by
          the <code className="font-mono">ALLOW_PASSWORD_LOGIN</code> environment variable, so
          single sign-on will not change it.
        </p>
      </div>
    );
  }
  if (!data.configured) {
    return (
      <div className="flex items-start gap-2.5 rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2.5">
        <ShieldCheck className="h-4 w-4 shrink-0 mt-0.5 text-slate-400" />
        <p className="text-xs text-slate-600 dark:text-slate-400">
          Password sign-in is <strong>on</strong>. Fill in the issuer, client ID and secret to
          offer single sign-on as well.
        </p>
      </div>
    );
  }
  if (!data.sso_has_worked) {
    return (
      <div className="flex items-start gap-2.5 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 px-3 py-2.5">
        <ShieldAlert className="h-4 w-4 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400" />
        <p className="text-xs text-amber-800 dark:text-amber-300">
          Password sign-in stays <strong>on</strong> until someone has actually signed in
          through the provider. Sign out and use the single sign-on button once to confirm it
          works — a wrong client secret or redirect URL still passes the connection test, so
          switching password sign-in off any earlier is how you get locked out.
        </p>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/30 px-3 py-2.5">
      <ShieldCheck className="h-4 w-4 shrink-0 mt-0.5 text-emerald-600 dark:text-emerald-400" />
      <p className="text-xs text-emerald-800 dark:text-emerald-300">
        Single sign-on is working, so password sign-in is now <strong>off</strong>. Set{" "}
        <code className="font-mono">ALLOW_PASSWORD_LOGIN=true</code> to keep both available —
        that is also how you get back in if the provider becomes unreachable.
      </p>
    </div>
  );
}
