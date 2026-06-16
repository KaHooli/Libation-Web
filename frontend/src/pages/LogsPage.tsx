import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "@/lib/api";
import { RefreshCw, Download, Circle } from "lucide-react";
import { cn } from "@/lib/utils";

const LEVELS = ["ALL", "INFO", "WARN", "ERROR", "DEBUG"] as const;
type Level = typeof LEVELS[number];

const LINE_COUNTS = [100, 200, 500, 1000] as const;

function levelColor(line: string): string {
  if (line.includes("[ERROR]")) return "text-red-400";
  if (line.includes("[WARN ]")) return "text-amber-400";
  if (line.includes("[DEBUG]")) return "text-slate-500";
  return "text-slate-300";
}

export function LogsPage() {
  const [lines, setLines] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [level, setLevel] = useState<Level>("ALL");
  const [lineCount, setLineCount] = useState<number>(200);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchLogs = useCallback(async (scrollToBottom = false) => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get("/api/logs", {
        params: { lines: lineCount, level: level.toLowerCase() },
      });
      setLines(data.lines);
      setTotal(data.total);
      setTruncated(data.truncated);
      if (scrollToBottom) {
        setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
      }
    } catch {
      setError("Failed to load logs.");
    } finally {
      setLoading(false);
    }
  }, [lineCount, level]);

  // Initial load + when filters change
  useEffect(() => {
    fetchLogs(true);
  }, [fetchLogs]);

  // Auto-refresh
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (autoRefresh) {
      intervalRef.current = setInterval(() => fetchLogs(false), 5000);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [autoRefresh, fetchLogs]);

  const handleDownload = () => {
    window.open("/api/logs/download", "_blank");
  };

  return (
    <div className="h-full flex flex-col gap-4 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Server Logs</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            {truncated
              ? `Showing last ${lines.length} of ${total} lines`
              : `${total} line${total !== 1 ? "s" : ""}`}
            {" · "}/config/logs/libation-web.log
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleDownload}
            className="flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
          >
            <Download className="h-4 w-4" />
            Download
          </button>
          <button
            onClick={() => fetchLogs(false)}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            Refresh
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Level tabs */}
        <div className="flex items-center gap-1 rounded-lg border border-slate-200 dark:border-slate-700 p-1">
          {LEVELS.map((l) => (
            <button
              key={l}
              onClick={() => setLevel(l)}
              className={cn(
                "px-3 py-1 text-xs font-semibold rounded-md transition-colors",
                level === l
                  ? "bg-brand-600 text-white"
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-white"
              )}
            >
              {l}
            </button>
          ))}
        </div>

        {/* Line count */}
        <select
          value={lineCount}
          onChange={(e) => setLineCount(Number(e.target.value))}
          className="text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          {LINE_COUNTS.map((n) => (
            <option key={n} value={n}>{n} lines</option>
          ))}
        </select>

        {/* Auto-refresh toggle */}
        <button
          onClick={() => setAutoRefresh((v) => !v)}
          className={cn(
            "flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-lg border transition-colors",
            autoRefresh
              ? "border-brand-500 bg-brand-50 dark:bg-brand-900/20 text-brand-600 dark:text-brand-400"
              : "border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
          )}
        >
          <Circle className={cn("h-2 w-2 fill-current", autoRefresh ? "text-brand-500 animate-pulse" : "text-slate-400")} />
          Auto-refresh
        </button>
      </div>

      {/* Log output */}
      <div className="flex-1 min-h-0 rounded-xl bg-slate-950 border border-slate-800 overflow-hidden flex flex-col">
        {error ? (
          <div className="flex items-center justify-center h-full text-red-400 text-sm">{error}</div>
        ) : lines.length === 0 && !loading ? (
          <div className="flex items-center justify-center h-full text-slate-500 text-sm">
            No log entries found.
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed scrollbar-thin scrollbar-thumb-slate-700 scrollbar-track-transparent">
            {truncated && (
              <div className="text-slate-600 mb-2 select-none">
                — {total - lines.length} earlier lines not shown —
              </div>
            )}
            {lines.map((line, i) => (
              <div key={i} className={cn("whitespace-pre-wrap break-all", levelColor(line))}>
                {line}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>
    </div>
  );
}
