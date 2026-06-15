import { useState, useEffect, useCallback } from "react";
import {
  CheckCircle, XCircle, Loader2, Download, RefreshCw,
  Headphones, Filter,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

interface LiberateBook {
  book_id: string;
  title: string;
  authors: string | null;
  series_name: string | null;
  series_index: string | null;
  length_minutes: number | null;
  liberate_status: "liberated" | "not_liberated" | "error" | "downloading";
  download_progress: number | null;
  content_type: string | null;
  is_abridged: boolean | null;
  is_audible_plus: boolean | null;
}

interface CapStatus {
  cap: number | null;
  used: number;
  remaining: number | null;
  resets_at: string | null;
}

type FilterTab = "all" | "audible_plus" | "liberated" | "not_liberated" | "downloading";

const FILTER_TABS: { key: FilterTab; label: string }[] = [
  { key: "all", label: "All" },
  { key: "audible_plus", label: "Audible Plus" },
  { key: "liberated", label: "Downloaded" },
  { key: "not_liberated", label: "Not Downloaded" },
  { key: "downloading", label: "In Progress" },
];

function StatusBadge({ status, progress }: { status: LiberateBook["liberate_status"]; progress: number | null }) {
  if (status === "downloading") {
    return (
      <div className="absolute bottom-1.5 left-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-blue-600 shadow">
        <Loader2 className="h-3.5 w-3.5 text-white animate-spin" />
        {progress != null && (
          <span className="absolute -top-4 left-1/2 -translate-x-1/2 text-[10px] font-bold text-white bg-blue-600 rounded px-1">
            {progress}%
          </span>
        )}
      </div>
    );
  }
  if (status === "liberated") {
    return (
      <div className="absolute bottom-1.5 left-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-emerald-500 shadow">
        <CheckCircle className="h-3.5 w-3.5 text-white" />
      </div>
    );
  }
  return (
    <div className="absolute bottom-1.5 left-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-red-500 shadow">
      <XCircle className="h-3.5 w-3.5 text-white" />
    </div>
  );
}

function BookTile({
  book,
  selected,
  onSelect,
  onDownload,
  downloadable,
}: {
  book: LiberateBook;
  selected: boolean;
  onSelect: () => void;
  onDownload: () => void;
  downloadable: boolean;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const [queuing, setQueuing] = useState(false);

  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (queuing || !downloadable) return;
    setQueuing(true);
    await onDownload();
    setQueuing(false);
  };

  return (
    <button
      onClick={onSelect}
      className={cn(
        "group relative text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-xl",
        selected && "ring-2 ring-brand-500 ring-offset-2 dark:ring-offset-slate-900"
      )}
    >
      <div className="relative aspect-[2/3] rounded-xl overflow-hidden bg-slate-100 dark:bg-slate-700 mb-2 shadow-sm group-hover:shadow-md transition-shadow">
        {!imgFailed ? (
          <img
            src={`/api/library/covers/${book.book_id}`}
            alt={book.title}
            className="absolute inset-0 w-full h-full object-cover"
            onError={() => setImgFailed(true)}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            <Headphones className="h-8 w-8 text-slate-400" />
          </div>
        )}

        <StatusBadge status={book.liberate_status} progress={book.download_progress} />

        {book.liberate_status === "not_liberated" && downloadable && (
          <button
            onClick={handleDownload}
            title="Download"
            className="absolute bottom-1.5 right-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 hover:bg-black/80 transition-all"
          >
            {queuing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
          </button>
        )}

        {selected && (
          <div className="absolute inset-0 bg-brand-600/20 rounded-xl" />
        )}
      </div>
      <p className="text-xs font-semibold text-slate-800 dark:text-slate-200 leading-tight line-clamp-2">{book.title}</p>
      {book.authors && <p className="text-xs text-slate-500 dark:text-slate-400 truncate mt-0.5">{book.authors}</p>}
      {book.is_abridged && <p className="text-xs text-amber-600 mt-0.5">Abridged</p>}
    </button>
  );
}

export function LiberatePage() {
  const { user } = useAuth();
  const [books, setBooks] = useState<LiberateBook[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<FilterTab>("all");
  const [loading, setLoading] = useState(true);
  const [cap, setCap] = useState<CapStatus | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkStatus, setBulkStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [error, setError] = useState("");
  const PAGE_SIZE = 48;

  const loadBooks = useCallback(async () => {
    setLoading(true);
    try {
      const [booksRes, capRes] = await Promise.all([
        api.get("/liberate/books", { params: { filter_status: filter, page, page_size: PAGE_SIZE } }),
        api.get("/liberate/cap"),
      ]);
      setBooks(booksRes.data.books);
      setTotal(booksRes.data.total);
      setCap(capRes.data);
    } catch { setError("Failed to load books."); }
    finally { setLoading(false); }
  }, [filter, page]);

  useEffect(() => { loadBooks(); }, [loadBooks]);

  // Poll while any book is downloading
  useEffect(() => {
    const hasActive = books.some(b => b.liberate_status === "downloading");
    if (!hasActive) return;
    const t = setInterval(loadBooks, 2000);
    return () => clearInterval(t);
  }, [books, loadBooks]);

  const handleDownloadOne = async (book: LiberateBook) => {
    try {
      await api.post("/downloads", { book_id: book.book_id, book_title: book.title });
      await loadBooks();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      if (typeof detail === "object" && detail && "resets_at" in detail) {
        setError(`Download cap reached. Resets at ${new Date((detail as { resets_at: string }).resets_at).toLocaleTimeString()}`);
      } else {
        setError(typeof detail === "string" ? detail : "Failed to queue download.");
      }
    }
  };

  const autoSelectNext = () => {
    const remaining = cap?.remaining ?? 0;
    if (remaining <= 0) return;
    const candidates = books
      .filter(b => b.liberate_status === "not_liberated")
      .slice(0, remaining)
      .map(b => b.book_id);
    setSelected(new Set(candidates));
  };

  const confirmSelected = async () => {
    if (selected.size === 0) return;
    const toQueue = books.filter(b => selected.has(b.book_id));
    for (const book of toQueue) {
      try {
        await api.post("/downloads", { book_id: book.book_id, book_title: book.title });
      } catch { /* individual failures are silent; cap 429 stops the loop */ break; }
    }
    setSelected(new Set());
    await loadBooks();
  };

  const downloadAll = async () => {
    setBulkStatus("running");
    setError("");
    try {
      await api.post("/liberate/download-all");
      setBulkStatus("done");
    } catch {
      setBulkStatus("error");
      setError("Failed to start bulk download.");
    }
  };

  const canDownload = user?.is_admin || (user?.permissions?.can_download ?? true);
  const isCapExhausted = cap && cap.cap !== null && cap.remaining === 0;
  const hasNoCapAndAdmin = user?.is_admin || cap?.cap === null;

  return (
    <div className="space-y-4">
      {error && <Alert variant="error" className="mb-2">{error}<button className="ml-2 underline text-xs" onClick={() => setError("")}>dismiss</button></Alert>}

      {/* Bulk liberate banner */}
      {bulkStatus === "running" && (
        <Alert variant="info">
          <Loader2 className="inline h-3.5 w-3.5 animate-spin mr-1" />
          Bulk download in progress — LibationCli is liberating all unliberated books. Check the Downloads page for progress.
        </Alert>
      )}

      {/* Controls row */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        {/* Filter tabs */}
        <div className="flex gap-1 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-1 w-fit flex-wrap">
          {FILTER_TABS.map(t => (
            <button
              key={t.key}
              onClick={() => { setFilter(t.key); setPage(1); }}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                filter === t.key
                  ? "bg-brand-600 text-white"
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={loadBooks}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>

          {/* Cap info */}
          {cap && cap.cap !== null && (
            <span className={cn(
              "text-xs font-medium px-2 py-1 rounded-full",
              isCapExhausted
                ? "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400"
                : "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400"
            )}>
              {cap.remaining}/{cap.cap} downloads left
            </span>
          )}

          {/* Selection controls */}
          {selected.size > 0 && (
            <>
              <Button size="sm" variant="outline" onClick={() => setSelected(new Set())}>
                Clear ({selected.size})
              </Button>
              <Button size="sm" onClick={confirmSelected}>
                Queue {selected.size} book{selected.size !== 1 ? "s" : ""}
              </Button>
            </>
          )}

          {/* Primary action */}
          {canDownload && !isCapExhausted && selected.size === 0 && (
            hasNoCapAndAdmin ? (
              <Button size="sm" onClick={downloadAll} loading={bulkStatus === "running"}>
                <Download className="h-3.5 w-3.5" /> Download All
              </Button>
            ) : cap && cap.remaining! > 0 ? (
              <Button size="sm" onClick={autoSelectNext}>
                <Filter className="h-3.5 w-3.5" /> Select Next {cap.remaining}
              </Button>
            ) : null
          )}

          {isCapExhausted && cap?.resets_at && (
            <span className="text-xs text-slate-500 dark:text-slate-400">
              Resets {new Date(cap.resets_at).toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>

      {/* Count */}
      {!loading && total > 0 && (
        <p className="text-xs text-slate-400">{total.toLocaleString()} book{total !== 1 ? "s" : ""}</p>
      )}

      {/* Grid */}
      {loading ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="animate-pulse">
              <div className="aspect-[2/3] rounded-xl bg-slate-200 dark:bg-slate-700 mb-2" />
              <div className="h-3 rounded bg-slate-200 dark:bg-slate-700 w-3/4 mb-1" />
            </div>
          ))}
        </div>
      ) : books.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-slate-100 dark:bg-slate-800 mb-5">
            <Headphones className="h-10 w-10 text-slate-300" />
          </div>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {filter === "all" ? "No books in library yet. Connect an account and scan."
              : filter === "audible_plus" ? "No Audible Plus titles found in your library."
              : `No books with status "${filter}".`}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
          {books.map(book => (
            <BookTile
              key={book.book_id}
              book={book}
              selected={selected.has(book.book_id)}
              onSelect={() => {
                if (book.liberate_status !== "not_liberated") return;
                setSelected(s => {
                  const n = new Set(s);
                  n.has(book.book_id) ? n.delete(book.book_id) : n.add(book.book_id);
                  return n;
                });
              }}
              onDownload={() => handleDownloadOne(book)}
              downloadable={canDownload && !isCapExhausted && book.liberate_status === "not_liberated"}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {!loading && total > PAGE_SIZE && (
        <div className="flex items-center justify-between pt-1">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total.toLocaleString()}
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>
              Prev
            </Button>
            <span className="text-sm text-slate-600 dark:text-slate-400 tabular-nums">{page} / {Math.ceil(total / PAGE_SIZE)}</span>
            <Button variant="outline" size="sm" onClick={() => setPage(p => p + 1)} disabled={page >= Math.ceil(total / PAGE_SIZE)}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
