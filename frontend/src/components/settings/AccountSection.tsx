import { useState } from "react";
import { ShieldCheck, ShieldOff, KeyRound, ShieldAlert } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { authApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";

// ── 2FA ──────────────────────────────────────────────────────────────────────

export function TwoFactorSection() {
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

// ── Change username ──────────────────────────────────────────────────────────

export function ChangeUsernameSection() {
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

// ── Change password ──────────────────────────────────────────────────────────

export function ChangePasswordSection() {
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

// ── Update credentials (default-credential users only) ───────────────────────

export function UpdateCredentialsSection() {
  const { logout } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (username.trim().length < 3) { setError("New username must be at least 3 characters."); return; }
    if (password.length < 8) { setError("New password must be at least 8 characters."); return; }
    if (password !== confirmPassword) { setError("Passwords do not match."); return; }
    if (!currentPassword) { setError("Current password is required."); return; }
    setLoading(true);
    try {
      await authApi.changeUsername(username.trim(), currentPassword);
      await authApi.changePassword(currentPassword, password);
      await logout();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail ?? "Failed to update credentials.";
      setError(msg);
      setLoading(false);
    }
  };

  return (
    <Card className="border-amber-200 dark:border-amber-800">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="h-5 w-5 text-amber-500" />
          Update credentials
        </CardTitle>
        <CardDescription>
          Change your username and password together in one step. You will be signed out when done.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4 max-w-sm">
          {error && <Alert variant="error">{error}</Alert>}
          <div>
            <Label htmlFor="uc-username">New username</Label>
            <Input id="uc-username" value={username} onChange={e => setUsername(e.target.value)} required minLength={3} placeholder="Choose a username" />
          </div>
          <div>
            <Label htmlFor="uc-password">New password</Label>
            <Input id="uc-password" type="password" value={password} onChange={e => setPassword(e.target.value)} required minLength={8} placeholder="At least 8 characters" />
          </div>
          <div>
            <Label htmlFor="uc-confirm">Confirm new password</Label>
            <Input id="uc-confirm" type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor="uc-current">Current password</Label>
            <Input id="uc-current" type="password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} required placeholder="admin" />
          </div>
          <Button type="submit" loading={loading}>Update &amp; sign out</Button>
        </form>
      </CardContent>
    </Card>
  );
}
