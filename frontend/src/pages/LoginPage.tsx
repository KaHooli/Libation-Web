import { useState, useRef, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Eye, EyeOff, KeyRound } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { authApi, OIDC_LOGIN_URL, type AuthConfig } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";

/** Until the server answers, assume password-only — the common case, and it
 *  avoids flashing an SSO button that then disappears. */
const ASSUMED: AuthConfig = {
  password_login_enabled: true,
  oidc_enabled: false,
  oidc_provider_name: "SSO",
};

export function LoginPage() {
  const navigate = useNavigate();
  const { login } = useAuth();
  const [searchParams] = useSearchParams();

  const [config, setConfig] = useState<AuthConfig>(ASSUMED);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  // An SSO failure comes back as a query parameter, because the provider
  // redirects the browser here rather than returning to our code.
  const [error, setError] = useState(searchParams.get("sso_error") ?? "");
  const [loading, setLoading] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    authApi.config()
      .then(({ data }) => setConfig(data))
      .catch(() => { /* keep the assumed config; the form still works */ });
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setError("");
    setLoading(true);

    try {
      const { data } = await authApi.login(username.trim(), password);

      if (data.requires_2fa) {
        navigate("/auth/2fa", { state: { temp_token: data.temp_token } });
        return;
      }

      login(data.access_token, data.user);
      navigate("/");
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail ?? "Invalid username or password";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const { password_login_enabled, oidc_enabled, oidc_provider_name } = config;

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-100 to-slate-200 px-4 py-8">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <img src="/favicon.svg" alt="Libation" className="h-14 w-14 rounded-2xl shadow-lg mb-4" />
          <h1 className="text-2xl font-bold text-slate-900">Libation</h1>
          <p className="text-sm text-slate-500 mt-1">Sign in to your audiobook library</p>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-slate-200 bg-white shadow-sm p-7">
          {error && <Alert variant="error">{error}</Alert>}

          {oidc_enabled && (
            <a
              href={OIDC_LOGIN_URL}
              className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-3 text-sm font-medium text-white transition-colors hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:ring-offset-2"
            >
              <KeyRound className="h-4 w-4" />
              Sign in with {oidc_provider_name}
            </a>
          )}

          {oidc_enabled && password_login_enabled && (
            <div className="my-5 flex items-center gap-3">
              <span className="h-px flex-1 bg-slate-200" />
              <span className="text-xs uppercase tracking-wide text-slate-400">or</span>
              <span className="h-px flex-1 bg-slate-200" />
            </div>
          )}

          {password_login_enabled ? (
            <form onSubmit={handleSubmit} className="space-y-5 mt-5" noValidate>
              <div>
                <Label htmlFor="username">Username</Label>
                <Input
                  id="username"
                  type="text"
                  autoComplete="username"
                  autoFocus={!oidc_enabled}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="admin"
                  required
                />
              </div>

              <div>
                <Label htmlFor="password">Password</Label>
                <div className="relative">
                  <Input
                    ref={passwordRef}
                    id="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="pr-10"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors"
                    tabIndex={-1}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <Button type="submit" size="lg" className="w-full mt-2" loading={loading}>
                Sign in
              </Button>
            </form>
          ) : (
            <p className="mt-5 text-center text-xs text-slate-500">
              Password sign-in is disabled for this server.
            </p>
          )}
        </div>

        <p className="text-center text-xs text-slate-400 mt-6">
          Libation — Liberate your audiobook library
        </p>
      </div>
    </div>
  );
}
