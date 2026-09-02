import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle, CheckCircle2, Library, Loader2, PlugZap, RefreshCw, SkipForward,
} from "lucide-react";
import { chaptarrApi, type ChaptarrImportRecord, type ChaptarrSettingsData } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";

const IMPORT_MODES: { value: ChaptarrSettingsData["import_mode"]; label: string; desc: string }[] = [
  { value: "auto", label: "Auto", desc: "Let Chaptarr decide" },
  { value: "copy", label: "Copy", desc: "Leave Libation's copy in place" },
  { value: "move", label: "Move", desc: "Hand the file over to Chaptarr" },
];

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: () => void; label: string }) {
  return (
    <button
      onClick={onChange}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
        checked ? "bg-brand-600" : "bg-slate-200 dark:bg-slate-600"
      }`}
    >
      <span
        className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
          checked ? "translate-x-4" : "translate-x-0"
        }`}
      />
    </button>
  );
}

function StatusIcon({ status }: { status: ChaptarrImportRecord["status"] }) {
  if (status === "running") return <Loader2 className="h-4 w-4 shrink-0 animate-spin text-brand-500" />;
  if (status === "complete") return <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />;
  if (status === "skipped") return <SkipForward className="h-4 w-4 shrink-0 text-slate-400" />;
  return <AlertCircle className="h-4 w-4 shrink-0 text-red-500" />;
}

export function ChaptarrSection() {
  const [data, setData] = useState<ChaptarrSettingsData | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [testResult, setTestResult] = useState<string | null>(null);
  const [imports, setImports] = useState<ChaptarrImportRecord[]>([]);

  const loadImports = useCallback(async () => {
    try {
      const { data } = await chaptarrApi.listImports(15);
      setImports(data);
    } catch { /* history is best-effort */ }
  }, []);

  useEffect(() => {
    chaptarrApi.getSettings()
      .then(r => setData(r.data))
      .catch(() => setError("Could not load Chaptarr settings."))
      .finally(() => setLoading(false));
    loadImports();
  }, [loadImports]);

  // Poll while anything is still working through Chaptarr's command queue.
  const hasRunning = imports.some(i => i.status === "running");
  const loadImportsRef = useRef(loadImports);
  loadImportsRef.current = loadImports;
  useEffect(() => {
    if (!hasRunning) return;
    const t = setInterval(() => loadImportsRef.current(), 3000);
    return () => clearInterval(t);
  }, [hasRunning]);

  const save = async (patch: Partial<ChaptarrSettingsData> & { api_key?: string }) => {
    setSaving(true); setError(""); setSuccess(""); setTestResult(null);
    try {
      const { data } = await chaptarrApi.updateSettings(patch);
      setData(data);
      if (patch.api_key !== undefined) setApiKey("");
      setSuccess("Saved.");
      setTimeout(() => setSuccess(""), 3000);
    } catch {
      setError("Failed to save Chaptarr settings.");
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    setTesting(true); setError(""); setTestResult(null);
    try {
      const { data } = await chaptarrApi.test();
      const folders = data.root_folders.map(f => f.path).filter(Boolean).join(", ");
      setTestResult(
        `Connected to ${data.app_name} ${data.version}.` +
        (folders ? ` Root folders: ${folders}` : " No root folders configured in Chaptarr yet.")
      );
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || "Could not reach Chaptarr.");
    } finally {
      setTesting(false);
    }
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
          <Library className="h-5 w-5 text-brand-600" />
          Chaptarr import
        </CardTitle>
        <CardDescription>
          Push downloaded audiobooks into a self-hosted{" "}
          <a href="https://github.com/Chaptarr/chaptarr" target="_blank" rel="noopener noreferrer"
             className="text-brand-600 hover:underline">Chaptarr</a>{" "}
          library. Books are matched by their Audible ASIN, so they import even when Chaptarr
          isn't monitoring for them. Both containers must see the same audiobooks volume.
          {saving && <span className="ml-2 text-xs text-brand-600">Saving…</span>}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}
        {success && <Alert variant="success">{success}</Alert>}
        {testResult && <Alert variant="success">{testResult}</Alert>}

        {data && (
          <>
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Enable Chaptarr integration</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Turn off to stop all pushes without losing the connection details.
                </p>
              </div>
              <Toggle checked={data.enabled} label="Enable Chaptarr integration"
                      onChange={() => save({ enabled: !data.enabled })} />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="chaptarr-url">Chaptarr URL</Label>
              <Input
                id="chaptarr-url"
                placeholder="http://chaptarr:8787"
                defaultValue={data.url}
                onBlur={e => e.target.value.trim() !== data.url && save({ url: e.target.value.trim() })}
              />
              <p className="text-xs text-slate-500 dark:text-slate-400">
                As reachable from this container — a Docker service name usually beats localhost.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="chaptarr-key">API key</Label>
              <div className="flex gap-2">
                <Input
                  id="chaptarr-key"
                  type="password"
                  autoComplete="off"
                  placeholder={data.api_key_set ? "•••••••• (stored)" : "From Chaptarr → Settings → General"}
                  value={apiKey}
                  onChange={e => setApiKey(e.target.value)}
                />
                <Button size="md" variant="outline" disabled={!apiKey.trim()}
                        onClick={() => save({ api_key: apiKey.trim() })}>
                  Save key
                </Button>
                {data.api_key_set && (
                  <Button size="md" variant="ghost" onClick={() => save({ api_key: "" })}>
                    Clear
                  </Button>
                )}
              </div>
            </div>

            <div className="flex items-center justify-between gap-4 pt-1">
              <div>
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Import automatically after download</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Every finished download is handed to Chaptarr straight away.
                </p>
              </div>
              <Toggle checked={data.auto_import} label="Import automatically after download"
                      onChange={() => save({ auto_import: !data.auto_import })} />
            </div>

            <div className="space-y-1.5">
              <Label>Import mode</Label>
              <div className="flex flex-wrap gap-2">
                {IMPORT_MODES.map(m => (
                  <button
                    key={m.value}
                    onClick={() => save({ import_mode: m.value })}
                    title={m.desc}
                    className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                      data.import_mode === m.value
                        ? "border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:text-brand-100"
                        : "border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700"
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Only applies when the file sits outside Chaptarr's root folders. If the shared volume
                <em> is</em> a Chaptarr root folder, the file is linked where it already lies.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label>Path mapping (optional)</Label>
              <div className="flex items-center gap-2">
                <Input
                  aria-label="Path as Libation sees it"
                  placeholder="/audiobooks"
                  defaultValue={data.path_from}
                  onBlur={e => e.target.value.trim() !== data.path_from && save({ path_from: e.target.value.trim() })}
                />
                <span className="text-slate-400 shrink-0">→</span>
                <Input
                  aria-label="Path as Chaptarr sees it"
                  placeholder="/books"
                  defaultValue={data.path_to}
                  onBlur={e => e.target.value.trim() !== data.path_to && save({ path_to: e.target.value.trim() })}
                />
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Leave both blank when the volume is mounted at the same path in both containers.
              </p>
            </div>

            <div className="flex flex-wrap gap-2 pt-1">
              <Button size="sm" variant="outline" onClick={testConnection} loading={testing}>
                <PlugZap className="h-3.5 w-3.5" /> Test connection
              </Button>
              <Button size="sm" variant="ghost" onClick={loadImports}>
                <RefreshCw className="h-3.5 w-3.5" /> Refresh history
              </Button>
            </div>
          </>
        )}

        <div className="pt-2 border-t border-slate-100 dark:border-slate-700">
          <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Recent imports</p>
          <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
            Shows what Chaptarr reported for each command. Chaptarr's own History view has the
            per-file detail if a book doesn't show up in its library afterwards.
          </p>
          {imports.length === 0 ? (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Nothing pushed to Chaptarr yet. Select books on the Liberate page and choose
              “Send to Chaptarr”, or turn on automatic import above.
            </p>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-700">
              {imports.map(i => (
                <li key={i.id} className="flex items-start gap-2.5 py-2">
                  <StatusIcon status={i.status} />
                  <div className="min-w-0">
                    <p className="truncate text-sm text-slate-800 dark:text-slate-200">
                      {i.book_title || i.book_id}
                    </p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {i.matched_by === "asin" && "Matched by ASIN · "}
                      {i.matched_by === "folder_scan" && "Folder scan fallback · "}
                      {i.message || i.status}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
