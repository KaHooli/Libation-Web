import { useState, useEffect } from "react";
import { Sliders, Loader2 } from "lucide-react";
import { settingsApi } from "@/lib/api";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";

interface LibationSettingsData {
  decrypt_to_lossy: boolean | null;
  split_files_by_chapter: boolean | null;
  download_episodes: boolean | null;
  create_cue_sheet: boolean | null;
  save_cover_art_to_file: boolean | null;
  allow_audiobook_overwrite: boolean | null;
  strip_audible_brand_audio: boolean | null;
  strip_unabridged: boolean | null;
}

const LIBATION_TOGGLES: { key: keyof LibationSettingsData; label: string; desc: string }[] = [
  { key: "decrypt_to_lossy", label: "Download as MP3 (lossy)", desc: "Converts to MP3 instead of keeping lossless AAX/FLAC" },
  { key: "split_files_by_chapter", label: "Split by chapter", desc: "Creates one file per chapter instead of a single file" },
  { key: "download_episodes", label: "Download episodes", desc: "Also downloads podcast-style episodic content" },
  { key: "create_cue_sheet", label: "Create .cue sheet", desc: "Generate a cue sheet alongside the audio file" },
  { key: "save_cover_art_to_file", label: "Save cover art", desc: "Save cover art as a separate image file" },
  { key: "allow_audiobook_overwrite", label: "Allow overwrite", desc: "Re-download and overwrite existing files" },
  { key: "strip_audible_brand_audio", label: "Strip Audible branding", desc: "Remove Audible intro and outro audio" },
  { key: "strip_unabridged", label: 'Strip "Unabridged" from titles', desc: 'Remove the word "Unabridged" from file names' },
];

export function LibationSettingsSection() {
  const [data, setData] = useState<LibationSettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    settingsApi.getLibation()
      .then(r => setData(r.data))
      .catch(() => setError("Could not load settings. Connect an Audible account and scan first."))
      .finally(() => setLoading(false));
  }, []);

  const toggle = async (key: keyof LibationSettingsData) => {
    if (!data) return;
    const newVal = !data[key];
    const updated = { ...data, [key]: newVal };
    setData(updated);
    setSaving(true); setError(""); setSuccess("");
    try {
      await settingsApi.updateLibation(updated);
      setSuccess("Settings saved.");
      setTimeout(() => setSuccess(""), 3000);
    } catch { setError("Failed to save."); }
    finally { setSaving(false); }
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
          <Sliders className="h-5 w-5 text-brand-600" />
          Libation download settings
        </CardTitle>
        <CardDescription>
          Controls Libation's download behavior. Settings are read from and written to{" "}
          <code className="text-xs bg-slate-100 dark:bg-slate-700 px-1 rounded">/config/appsettings.json</code>.
          {saving && <span className="ml-2 text-xs text-brand-600"> Saving…</span>}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <Alert variant="error">{error}</Alert>}
        {success && <Alert variant="success">{success}</Alert>}
        {!data ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No appsettings.json found yet. Settings will appear after you connect an Audible account.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-700">
            {LIBATION_TOGGLES.map(({ key, label, desc }) => (
              <li key={key} className="flex items-center justify-between gap-4 py-3">
                <div>
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{label}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">{desc}</p>
                </div>
                <button
                  onClick={() => toggle(key)}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
                    data[key] ? "bg-brand-600" : "bg-slate-200 dark:bg-slate-600"
                  }`}
                  role="switch"
                  aria-checked={!!data[key]}
                >
                  <span
                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                      data[key] ? "translate-x-4" : "translate-x-0"
                    }`}
                  />
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
