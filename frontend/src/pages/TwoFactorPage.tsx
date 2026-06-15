import { useState, useRef, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { ShieldCheck, ArrowLeft } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { authApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

export function TwoFactorPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();
  const tempToken: string = (location.state as { temp_token?: string })?.temp_token ?? "";

  const [digits, setDigits] = useState<string[]>(Array(6).fill(""));
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => {
    if (!tempToken) navigate("/login", { replace: true });
    else inputRefs.current[0]?.focus();
  }, [tempToken, navigate]);

  const handleChange = (index: number, value: string) => {
    const cleaned = value.replace(/\D/g, "").slice(-1);
    const next = [...digits];
    next[index] = cleaned;
    setDigits(next);

    if (cleaned && index < 5) {
      inputRefs.current[index + 1]?.focus();
    }

    if (next.every((d) => d !== "") && next.filter(Boolean).length === 6) {
      submitCode(next.join(""));
    }
  };

  const handleKeyDown = (index: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace" && !digits[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const pasted = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, 6);
    if (pasted.length === 6) {
      const arr = pasted.split("");
      setDigits(arr);
      submitCode(pasted);
    }
  };

  const submitCode = async (code: string) => {
    setError("");
    setLoading(true);
    try {
      const { data } = await authApi.verify2fa(tempToken, code);
      login(data.access_token, data.user);
      navigate("/");
    } catch {
      setError("Invalid code. Please try again.");
      setDigits(Array(6).fill(""));
      setTimeout(() => inputRefs.current[0]?.focus(), 50);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-100 to-slate-200 px-4 py-8">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-600 shadow-lg mb-4">
            <ShieldCheck className="h-7 w-7 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-slate-900">Two-factor auth</h1>
          <p className="text-sm text-slate-500 mt-1 text-center">
            Enter the 6-digit code from your authenticator app
          </p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white shadow-sm p-7">
          {error && <Alert variant="error" className="mb-5">{error}</Alert>}

          <div className="flex justify-center gap-2 mb-6" onPaste={handlePaste}>
            {digits.map((d, i) => (
              <input
                key={i}
                ref={(el) => { inputRefs.current[i] = el; }}
                type="text"
                inputMode="numeric"
                maxLength={2}
                value={d}
                onChange={(e) => handleChange(i, e.target.value)}
                onKeyDown={(e) => handleKeyDown(i, e)}
                className="h-12 w-10 rounded-lg border border-slate-300 bg-white text-center text-xl font-bold text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent transition-shadow"
                aria-label={`Digit ${i + 1}`}
              />
            ))}
          </div>

          <Button
            size="lg"
            className="w-full"
            loading={loading}
            onClick={() => submitCode(digits.join(""))}
            disabled={digits.some((d) => !d)}
          >
            Verify
          </Button>
        </div>

        <button
          onClick={() => navigate("/login")}
          className="flex items-center gap-1.5 mx-auto mt-5 text-sm text-slate-500 hover:text-slate-700 transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to sign in
        </button>
      </div>
    </div>
  );
}
