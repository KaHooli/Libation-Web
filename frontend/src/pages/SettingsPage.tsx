import { useState } from "react";
import { ShieldCheck, ShieldOff, KeyRound, Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { authApi } from "@/lib/api";
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
            <p className="text-sm text-slate-600">
              Scan the QR code below with your authenticator app (Google Authenticator, Authy, etc.),
              then enter the 6-digit code to confirm.
            </p>
            {qrImage && (
              <div className="flex justify-center">
                <img
                  src={`data:image/png;base64,${qrImage}`}
                  alt="2FA QR code"
                  className="h-44 w-44 rounded-lg border border-slate-200"
                />
              </div>
            )}
            <div>
              <Label htmlFor="setup-code">Verification code</Label>
              <Input
                id="setup-code"
                type="text"
                inputMode="numeric"
                maxLength={6}
                placeholder="000000"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              />
            </div>
            <div className="flex gap-2">
              <Button onClick={confirmEnable} loading={loading}>Confirm &amp; enable</Button>
              <Button variant="outline" onClick={() => { setStep("idle"); setCode(""); setError(""); }}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {step === "disable" && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              Enter the current 6-digit code from your authenticator app to disable 2FA.
            </p>
            <div>
              <Label htmlFor="disable-code">Authenticator code</Label>
              <Input
                id="disable-code"
                type="text"
                inputMode="numeric"
                maxLength={6}
                placeholder="000000"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              />
            </div>
            <div className="flex gap-2">
              <Button variant="danger" onClick={confirmDisable} loading={loading}>Disable 2FA</Button>
              <Button variant="outline" onClick={() => { setStep("idle"); setCode(""); setError(""); }}>
                Cancel
              </Button>
            </div>
          </div>
        )}
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

// ── Page ────────────────────────────────────────────────────────────────────

export function SettingsPage() {
  return (
    <div className="max-w-2xl space-y-6">
      <TwoFactorSection />
      <ChangePasswordSection />
    </div>
  );
}
