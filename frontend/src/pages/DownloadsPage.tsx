import { useState, useEffect, useCallback } from "react";
import {
  Download, CheckCircle, XCircle, Loader2, RefreshCw,
  Trash2, Headphones, AlertCircle, Library,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface DownloadItem {
  id: number;
  book_id: string;
  book_title: string | null;
  status: "queued" | "running" | "complete" | "error";
  progress: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

interface ScanStatus {
  id: number;
  status: "running" | "complete" | "error";
  books_added: number;
  output: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
}

function StatusIcon({ status }: { status: DownloadItem["status"] }) {
  if (status === "complete") return <CheckCircle className="h-4 w-4 text-green-500" />;
  if (status === "error") return <XCircle className="h-4 w-4 text-red-400" />;
  if (status === "running") return <Loader2 className="h-4 w-4 text-brand-500 animate-spin" />;
  return <Download className="h-4 w-4 text-slate-400" />;
}

function BookThumb({ bookId }: { bookId: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <div className="h-[5.625rem] w-[5.625rem] rounded shrink-0 bg-slate-100 flex items-center justify-center"><Headphones className="h-3.5 w-3.5 text-slate-300" /></div>;
  return (
    <img
      src={`/api/library/covers/${bookId}`}
      alt=""
      className="h-[5.625rem] w-[5.625rem] rounded shrink-0 object-cover"
      onError={() => setFailed(true)}
    />
  );
}

function ProgressBar({ progress, status }: { progress: number; status: DownloadItem["status"] }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-slate-100 overflow-hidden">
      <div
        className={cn(
          "h-full rounded-full transition-all duration-500",
          status === "complete" ? "bg-green-500" :
          status === "error" ? "bg-red-400" :
          "bg-brand-500"
        )}
        style={{ width: `${status === "complete" ? 100 : progress}%` }}
      />
    </div>
  );
}

export function DownloadsPage() {
  const [downloads, setDownloads] = useState<DownloadItem[]>([]);
  const [scan, setScan] = useState<ScanStatus | null>(null);
  const [scanVisible, setScanVisible] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");
  const [loadingDownloads, setLoadingDownloads] = useState(true);

  const fetchDownloads = useCallback(() => {
    api.get("/downloads")
      .then(r => setDownloads(r.data))
      .catch(() => {})
      .finally(() => setLoadingDownloads(false));
  }, []);

  const fetchScan = useCallback((show = false) => {
    api.get("/downloads/scan/latest")
      .then(r => {
        setScan(r.data);
        if (show) setScanVisible(true);
      })
      .catch(() => {});
  }, []);

  // Auto-dismiss completed/failed banners after 4 seconds
  useEffect(() => {
    if (!scanVisible || !scan || scan.status === "running") return;
    const t = setTimeout(() => setScanVisible(false), 4000);
    return () => clearTimeout(t);
  }, [scan, scanVisible]);

  useEffect(() => {
    fetchDownloads();
    // Don't show the on-load fetch — only show banners triggered by user action
    fetchScan(false);
  }, []);

  // Poll while there are active downloads or a running scan
  useEffect(() => {
    const hasActive =
      downloads.some(d => d.status === "queued" || d.status === "running") ||
      scan?.status === "running";

    if (!hasActive) return;

    const interval = setInterval(() => {
      fetchDownloads();
      fetchScan(true);
    }, 2000);
    return () => clearInterval(interval);
  }, [downloads, scan, fetchDownloads, fetchScan]);

  const handleScan = async () => {
    setScanning(true);
    setScanError("");
    setScanVisible(true);
    try {
      const { data } = await api.post("/downloads/scan");
      setScan(data);
    } catch (e: any) {
      setScanError(e.response?.data?.detail || "Scan failed to start");
      fetchScan(true);
    } finally {
      setScanning(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.delete(`/downloads/${id}`);
      setDownloads(prev => prev.filter(d => d.id !== id));
    } catch (e: any) {
      alert(e.response?.data?.detail || "Could not remove download");
    }
  };

  const active = downloads.filter(d => d.status === "queued" || d.status === "running");
  const completed = downloads.filter(d => d.status === "complete");
  const failed = downloads.filter(d => d.status === "error");

  return (
    <div className="max-w-[46rem] space-y-6">
      {/* Header + scan */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Downloads</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Manage your audiobook download queue.
          </p>
        </div>
        <button
          onClick={handleScan}
          disabled={scanning || scan?.status === "running"}
          className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shrink-0"
        >
          {scanning || scan?.status === "running"
            ? <Loader2 className="h-4 w-4 animate-spin" />
            : <RefreshCw className="h-4 w-4" />}
          {scanning || scan?.status === "running" ? "Scanning…" : "Scan Library"}
        </button>
      </div>

      {/* Scan status */}
      {scan && scanVisible && (
        <div className={cn(
          "rounded-xl border px-4 py-3 text-sm",
          scan.status === "running" ? "border-brand-200 bg-brand-50" :
          scan.status === "complete" ? "border-green-200 bg-green-50" :
          "border-red-200 bg-red-50"
        )}>
          <div className="flex items-center gap-2">
            {scan.status === "running" ? (
              <Loader2 className="h-4 w-4 text-brand-500 animate-spin shrink-0" />
            ) : scan.status === "complete" ? (
              <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />
            ) : (
              <AlertCircle className="h-4 w-4 text-red-500 shrink-0" />
            )}
            <span className={cn(
              "font-medium",
              scan.status === "running" ? "text-brand-700" :
              scan.status === "complete" ? "text-green-700" : "text-red-700"
            )}>
              {scan.status === "running" ? "Scanning library…" :
               scan.status === "complete"
                 ? `Scan complete — ${scan.books_added} new book${scan.books_added !== 1 ? "s" : ""} added`
                 : `Scan failed: ${scan.error_message || "Unknown error"}`}
            </span>
          </div>
        </div>
      )}
      {scanError && (
        <p className="text-sm text-red-600">{scanError}</p>
      )}

      {/* Active downloads */}
      {active.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-slate-700 mb-2">Active ({active.length})</h2>
          <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white overflow-hidden">
            {active.map(dl => (
              <div key={dl.id} className="px-4 py-[2.26875rem]">
                <div className="flex items-center gap-3 mb-2">
                  <StatusIcon status={dl.status} />
                  <BookThumb bookId={dl.book_id} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-900 truncate">
                      {dl.book_title || dl.book_id}
                    </p>
                    <p className="text-xs text-slate-400 capitalize">{dl.status}</p>
                  </div>
                  <span className="text-xs font-medium text-brand-600 tabular-nums shrink-0">
                    {dl.progress}%
                  </span>
                </div>
                <ProgressBar progress={dl.progress} status={dl.status} />
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Failed */}
      {failed.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-slate-700 mb-2">Failed ({failed.length})</h2>
          <div className="divide-y divide-slate-100 rounded-xl border border-red-200 bg-white overflow-hidden">
            {failed.map(dl => (
              <div key={dl.id} className="flex items-start gap-3 px-4 py-[2.26875rem]">
                <XCircle className="h-4 w-4 text-red-400 mt-0.5 shrink-0" />
                <BookThumb bookId={dl.book_id} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-900 truncate">
                    {dl.book_title || dl.book_id}
                  </p>
                  {dl.error_message && (
                    <p className="text-xs text-red-500 mt-0.5 line-clamp-2">{dl.error_message}</p>
                  )}
                </div>
                <button
                  onClick={() => handleDelete(dl.id)}
                  className="p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors shrink-0"
                  title="Remove"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Completed */}
      {completed.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-slate-700 mb-2">Completed ({completed.length})</h2>
          <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white overflow-hidden">
            {completed.map(dl => (
              <div key={dl.id} className="flex items-center gap-3 px-4 py-[2.26875rem]">
                <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />
                <BookThumb bookId={dl.book_id} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-900 truncate">
                    {dl.book_title || dl.book_id}
                  </p>
                  {dl.completed_at && (
                    <p className="text-xs text-slate-400">
                      {new Date(dl.completed_at).toLocaleDateString()}
                    </p>
                  )}
                </div>
                <button
                  onClick={() => handleDelete(dl.id)}
                  className="p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors shrink-0"
                  title="Remove"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Empty */}
      {!loadingDownloads && downloads.length === 0 && !scan && (
        <div className="flex flex-col items-center py-16 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-100 mb-4">
            <Headphones className="h-7 w-7 text-slate-300" />
          </div>
          <p className="text-sm font-medium text-slate-600">No downloads yet</p>
          <p className="text-xs text-slate-400 mt-1">
            Click a book in the Library to queue it for download.
          </p>
        </div>
      )}
    </div>
  );
}
