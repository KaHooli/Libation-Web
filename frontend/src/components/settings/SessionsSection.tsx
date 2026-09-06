import { useState, useEffect } from "react";
import { MonitorSmartphone, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { authApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";

interface SessionItem {
  id: number;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  user_agent: string | null;
  ip_address: string | null;
}

export function SessionsSection() {
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
