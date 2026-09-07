import { useRef, useState } from "react";
import {
  Archive, Download, Upload, AlertTriangle, CheckCircle2, Loader2, ShieldAlert,
} from "lucide-react";
import {
  backupApi, type BackupSection as SectionId, type RestoreReport, type SettingsBackupDoc,
} from "@/lib/api";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";

const SECTION_LABELS: Record<SectionId, string> = {
  chaptarr: "Chaptarr integration",
  oidc: "Sign-in (SSO)",
  libation: "Library download options",
};

const ALL_SECTIONS: SectionId[] = ["chaptarr", "oidc", "libation"];

/** Which sections a parsed file actually carries. Restoring a section a file
 *  does not have would write nothing and report success, which reads as a lie. */
function sectionsIn(doc: SettingsBackupDoc): SectionId[] {
  return ALL_SECTIONS.filter((s) => doc[s] && typeof doc[s] === "object");
}

export function BackupSection() {
  const [includeSecrets, setIncludeSecrets] = useState(true);
  const [downloading, setDownloading] = useState(false);

  const [file, setFile] = useState<{ name: string; doc: SettingsBackupDoc } | null>(null);
  const [chosen, setChosen] = useState<SectionId[]>([]);
  const [restoring, setRestoring] = useState(false);
  const [report, setReport] = useState<RestoreReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setFile(null);
    setChosen([]);
    setReport(null);
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const doDownload = async () => {
    setDownloading(true);
    setError(null);
    try {
      await backupApi.download(includeSecrets);
    } catch {
      setError("Could not produce the backup file.");
    } finally {
      setDownloading(false);
    }
  };

  const onPick = async (picked: File | undefined) => {
    setReport(null);
    setError(null);
    if (!picked) return;
    try {
      const doc = JSON.parse(await picked.text()) as SettingsBackupDoc;
      if (doc?.format !== "libation-web-settings") {
        setError("That file is not a Libation Web settings backup.");
        setFile(null);
        return;
      }
      setFile({ name: picked.name, doc });
      setChosen(sectionsIn(doc));
    } catch {
      setError("That file could not be read as JSON.");
      setFile(null);
    }
  };

  const doRestore = async () => {
    if (!file || chosen.length === 0) return;
    setRestoring(true);
    setError(null);
    try {
      const { data } = await backupApi.restore(file.doc, chosen);
      setReport(data);
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : "The restore failed.");
    } finally {
      setRestoring(false);
    }
  };

  const available = file ? sectionsIn(file.doc) : [];
  const missing = file?.doc.secrets_omitted ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Archive className="h-5 w-5 text-brand-600" />
          Backup &amp; restore
        </CardTitle>
        <CardDescription>
          Save the Chaptarr connection, SSO configuration and Libation download
          options to a file, and put them back on a rebuilt container.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-6">
        {/* ── Export ─────────────────────────────────────────────────────── */}
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200">Download a backup</h4>

          <label className="flex items-start gap-2.5 cursor-pointer">
            <input
              type="checkbox"
              checked={includeSecrets}
              onChange={(e) => setIncludeSecrets(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-slate-300 dark:border-slate-600 text-brand-600 focus:ring-brand-500"
            />
            <span className="text-sm text-slate-700 dark:text-slate-300">
              Include secrets
              <span className="block text-xs text-slate-500 dark:text-slate-400">
                The Chaptarr API key and the OIDC client secret. Leave this on for
                a backup you intend to restore — without them a restore leaves you
                retyping both.
              </span>
            </span>
          </label>

          {includeSecrets ? (
            <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40 px-3 py-2.5">
              <ShieldAlert className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
              <p className="text-xs text-amber-800 dark:text-amber-300">
                This file will contain those secrets <strong>in plain text</strong>.
                Store it as you would a password — not in a shared folder, a chat
                message or a public repository.
              </p>
            </div>
          ) : (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Safe to share. Restoring it leaves the existing keys on the target
              install untouched, so an unconfigured install stays unconfigured
              until you enter them.
            </p>
          )}

          <p className="text-xs text-slate-500 dark:text-slate-400">
            Audible accounts are never included: the stored login is a device
            registration tied to this install's key, so it cannot be moved. Sign
            in to Audible again after a restore.
          </p>

          <button
            onClick={doDownload}
            disabled={downloading}
            className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 transition-colors disabled:opacity-50"
          >
            {downloading
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Download className="h-4 w-4" />}
            Download backup
          </button>
        </div>

        <hr className="border-slate-200 dark:border-slate-700" />

        {/* ── Restore ────────────────────────────────────────────────────── */}
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200">Restore from a backup</h4>

          <input
            ref={inputRef}
            type="file"
            accept="application/json,.json"
            onChange={(e) => onPick(e.target.files?.[0])}
            className="block w-full text-sm text-slate-600 dark:text-slate-400 file:mr-3 file:rounded-lg file:border file:border-slate-200 dark:file:border-slate-700 file:bg-white dark:file:bg-slate-700 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-slate-700 dark:file:text-slate-200 hover:file:bg-slate-50 dark:hover:file:bg-slate-600"
          />

          {file && (
            <div className="space-y-3 rounded-lg border border-slate-200 dark:border-slate-700 p-3">
              <div className="text-xs text-slate-500 dark:text-slate-400">
                <span className="font-mono text-slate-700 dark:text-slate-300">{file.name}</span>
                {file.doc.exported_at && <> · exported {new Date(file.doc.exported_at).toLocaleString()}</>}
                {file.doc.app_version && <> · v{file.doc.app_version}</>}
              </div>

              {missing.length > 0 && (
                <div className="flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40 px-3 py-2">
                  <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
                  <p className="text-xs text-amber-800 dark:text-amber-300">
                    This backup was saved without secrets. Whatever is already
                    stored here is kept, so you may still need to enter:{" "}
                    <span className="font-mono">{missing.join(", ")}</span>.
                  </p>
                </div>
              )}

              <fieldset className="space-y-1.5">
                <legend className="text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
                  Restore which sections?
                </legend>
                {available.map((s) => (
                  <label key={s} className="flex items-center gap-2.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={chosen.includes(s)}
                      onChange={(e) =>
                        setChosen((prev) =>
                          e.target.checked ? [...prev, s] : prev.filter((x) => x !== s)
                        )
                      }
                      className="h-4 w-4 rounded border-slate-300 dark:border-slate-600 text-brand-600 focus:ring-brand-500"
                    />
                    <span className="text-sm text-slate-700 dark:text-slate-300">{SECTION_LABELS[s]}</span>
                  </label>
                ))}
                {available.length === 0 && (
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    This file carries no settings sections.
                  </p>
                )}
              </fieldset>

              <p className="text-xs text-slate-500 dark:text-slate-400">
                The sections you tick are overwritten with what is in the file.
              </p>

              <div className="flex items-center gap-2">
                <button
                  onClick={doRestore}
                  disabled={restoring || chosen.length === 0}
                  className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 transition-colors disabled:opacity-50"
                >
                  {restoring
                    ? <Loader2 className="h-4 w-4 animate-spin" />
                    : <Upload className="h-4 w-4" />}
                  Restore
                </button>
                <button
                  onClick={reset}
                  className="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2 text-sm font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {error && (
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
          )}

          {report && (
            <div className="space-y-2 rounded-lg border border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/40 px-3 py-2.5">
              <p className="flex items-center gap-2 text-sm font-medium text-emerald-800 dark:text-emerald-300">
                <CheckCircle2 className="h-4 w-4" />
                {report.applied.length > 0
                  ? `Restored: ${report.applied.map((s) => SECTION_LABELS[s]).join(", ")}.`
                  : "Nothing was restored."}
              </p>
              {report.secrets_missing.length > 0 && (
                <p className="text-xs text-emerald-800 dark:text-emerald-300">
                  Still to enter by hand:{" "}
                  <span className="font-mono">{report.secrets_missing.join(", ")}</span>
                </p>
              )}
              {report.warnings.map((w, i) => (
                <p key={i} className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  {w}
                </p>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
