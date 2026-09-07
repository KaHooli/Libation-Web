import { useState, useEffect, useCallback, useRef } from "react";
import { ScrollText, RefreshCw, Download } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, downloadFile } from "@/lib/api";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";

const LOG_LEVELS = ["ALL", "INFO", "WARN", "ERROR", "DEBUG"] as const;
type LogLevel = typeof LOG_LEVELS[number];
const LOG_LINE_COUNTS = [100, 200, 500, 1000] as const;

function logLineColor(line: string): string {
  if (line.includes("[ERROR]")) return "text-red-400";
  if (line.includes("[WARN ]")) return "text-amber-400";
  if (line.includes("[DEBUG]")) return "text-slate-500";
  return "text-slate-300";
}

export function LogsSection() {
  const [lines, setLines] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [level, setLevel] = useState<LogLevel>("ALL");
  const [lineCount, setLineCount] = useState<number>(200);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchLogs = useCallback(async (scrollToBottom = false) => {
    setLoading(true);
    setFetchError(null);
    try {
      const { data } = await api.get("/logs", { params: { lines: lineCount, level: level.toLowerCase() } });
      setLines(data.lines);
      setTotal(data.total);
      setTruncated(data.truncated);
      if (scrollToBottom) setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    } catch { setFetchError("Failed to load logs."); }
    finally { setLoading(false); }
  }, [lineCount, level]);

  useEffect(() => { fetchLogs(false); }, [fetchLogs]);

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (autoRefresh) intervalRef.current = setInterval(() => fetchLogs(false), 5000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [autoRefresh, fetchLogs]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <ScrollText className="h-5 w-5 text-brand-600" />
              Server logs
            </CardTitle>
            <CardDescription>
              {truncated ? `Showing last ${lines.length} of ${total} lines` : `${total} line${total !== 1 ? "s" : ""}`}
              {" · "}/config/logs/libation-web.log
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            {/* Fetched, not linked: /api/logs/download is admin-only and the
                access token lives in memory, so a bare href arrives with no
                Authorization header and is refused. */}
            <button
              onClick={() => downloadFile("/logs/download", "libation-web.log")}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
            >
              <Download className="h-3.5 w-3.5" /> Download
            </button>
            <button
              onClick={() => fetchLogs(false)}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} /> Refresh
            </button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-0.5 rounded-lg border border-slate-200 dark:border-slate-700 p-1">
            {LOG_LEVELS.map((l) => (
              <button
                key={l}
                onClick={() => setLevel(l)}
                className={cn(
                  "px-2.5 py-0.5 text-xs font-semibold rounded-md transition-colors",
                  level === l ? "bg-brand-600 text-white" : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-white"
                )}
              >
                {l}
              </button>
            ))}
          </div>
          <select
            value={lineCount}
            onChange={(e) => setLineCount(Number(e.target.value))}
            className="text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-500"
          >
            {LOG_LINE_COUNTS.map((n) => <option key={n} value={n}>{n} lines</option>)}
          </select>
          <button
            onClick={() => setAutoRefresh((v) => !v)}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-lg border transition-colors",
              autoRefresh
                ? "border-brand-500 bg-brand-50 dark:bg-brand-900/20 text-brand-600 dark:text-brand-400"
                : "border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
            )}
          >
            <span className={cn("inline-block h-1.5 w-1.5 rounded-full", autoRefresh ? "bg-brand-500 animate-pulse" : "bg-slate-400")} />
            Auto-refresh
          </button>
        </div>

        {/* Log output */}
        <div className="rounded-lg bg-slate-950 border border-slate-800 overflow-hidden">
          <div className="h-96 overflow-y-auto p-3 font-mono text-xs leading-relaxed">
            {fetchError ? (
              <span className="text-red-400">{fetchError}</span>
            ) : lines.length === 0 && !loading ? (
              <span className="text-slate-500">No log entries found.</span>
            ) : (
              <>
                {truncated && (
                  <div className="text-slate-600 mb-2 select-none">— {total - lines.length} earlier lines not shown —</div>
                )}
                {lines.map((line, i) => (
                  <div key={i} className={cn("whitespace-pre-wrap break-all", logLineColor(line))}>{line}</div>
                ))}
                <div ref={bottomRef} />
              </>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
