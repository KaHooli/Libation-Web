import { useState } from "react";
import { KeyRound, Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { authApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";

/**
 * Shown instead of the app while `must_change_password` is set.
 *
 * The account is on a password printed to the container log, so nothing else
 * is reachable until it has been replaced. Changing it revokes every session,
 * which signs the user out — that is the intended end of this flow, not a
 * failure, so the copy says so up front.
 */
export function ChangePasswordRequiredPage() {
  const { user, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (next.length < 8) { setError("New password must be at least 8 characters."); return; }
    if (next !== confirm) { setError("Passwords do not match."); return; }
    if (next === current) { setError("Choose a password different from the generated one."); return; }
    setLoading(true);
    try {
      await authApi.changePassword(current, next);
      setDone(true);
      // The change already revoked every session server-side; sign out so the
      // client is not left holding a token the server no longer honours.
      setTimeout(() => { logout(); }, 1600);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail ?? "Failed to change password.";
      setError(msg);
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-900 px-4 py-10">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-sm p-6 space-y-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-900/40">
            <KeyRound className="h-5 w-5 text-amber-600 dark:text-amber-400" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
              Choose a password
            </h1>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Signed in as <strong>{user?.username}</strong>
            </p>
          </div>
        </div>

        {done ? (
          <Alert variant="success">
            Password changed. Signing you out so you can sign back in with it…
          </Alert>
        ) : (
          <>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              This account is still using the password generated at first
              startup and printed to the container log. Pick your own to
              continue — you will be signed out and can sign straight back in.
            </p>

            <form onSubmit={submit} className="space-y-4">
              {error && <Alert variant="error">{error}</Alert>}
              <div>
                <Label htmlFor="fp-current">Generated password</Label>
                <Input
                  id="fp-current" type="password" autoComplete="current-password"
                  value={current} onChange={e => setCurrent(e.target.value)} required
                  placeholder="From docker logs"
                />
              </div>
              <div>
                <Label htmlFor="fp-new">New password</Label>
                <Input
                  id="fp-new" type="password" autoComplete="new-password"
                  value={next} onChange={e => setNext(e.target.value)} required minLength={8}
                  placeholder="At least 8 characters"
                />
              </div>
              <div>
                <Label htmlFor="fp-confirm">Confirm new password</Label>
                <Input
                  id="fp-confirm" type="password" autoComplete="new-password"
                  value={confirm} onChange={e => setConfirm(e.target.value)} required
                />
              </div>
              <Button type="submit" loading={loading} className="w-full">
                Set password
              </Button>
            </form>

            <button
              onClick={() => logout()}
              className="w-full text-center text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
            >
              Sign out instead
            </button>
          </>
        )}

        {loading && !done && (
          <div className="flex justify-center">
            <Loader2 className="h-4 w-4 animate-spin text-slate-400" />
          </div>
        )}
      </div>
    </div>
  );
}
