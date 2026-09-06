import { useState, useEffect } from "react";
import { Info, BookOpen, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";

export function AboutSection() {
  const [cliVersion, setCliVersion] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get("/updates/version")
      .then(({ data }) => setCliVersion(data.cli_version))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Info className="h-5 w-5 text-brand-600" />
          About
        </CardTitle>
        <CardDescription>LibationCLI version information.</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="text-sm">
          <div className="flex items-center justify-between py-2.5">
            <dt className="text-slate-500 dark:text-slate-400">Installed CLI version</dt>
            <dd className="font-mono font-semibold text-slate-800 dark:text-slate-200">
              {loading
                ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" />
                : cliVersion ? `v${cliVersion}` : "Unknown"}
            </dd>
          </div>
        </dl>
        <p className="text-xs text-slate-400 dark:text-slate-500 mt-3">
          To update LibationCLI, rebuild the Docker image with a newer{" "}
          <code className="font-mono">LIBATION_VERSION</code>.
        </p>
      </CardContent>
    </Card>
  );
}

export function ApiDocsSection() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookOpen className="h-5 w-5 text-brand-600" />
          API documentation
        </CardTitle>
        <CardDescription>
          Interactive API docs are built into Libation Web UI via FastAPI.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex gap-3 flex-wrap">
        <a
          href="/docs"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-700 px-4 py-2 text-sm font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-600 transition-colors shadow-sm"
        >
          Swagger UI
        </a>
        <a
          href="/redoc"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-700 px-4 py-2 text-sm font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-600 transition-colors shadow-sm"
        >
          ReDoc
        </a>
      </CardContent>
    </Card>
  );
}
