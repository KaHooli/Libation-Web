import { useState, useEffect, useCallback } from "react";
import {
  CheckCircle, XCircle, Loader2, Download, RefreshCw,
  Headphones, Filter, CheckCheck, RotateCcw, Layers, X, Search,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { cn, formatDuration } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

interface AccountOwner {
  account_id: string;
  name: string;
  owner_name: string | null;
  owner_username: string | null;
}

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

type FilterTab = "all" | "purchased" | "audible_plus" | "liberated" | "not_liberated" | "downloading";

const FILTER_TABS: { key: FilterTab; label: string }[] = [
  { key: "all", label: "All" },
  { key: "purchased", label: "Purchased" },
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
  multiSelectMode,
  onSelect,
  onDownload,
  onMark,
  downloadable,
}: {
  book: LiberateBook;
  selected: boolean;
  multiSelectMode: boolean;
  onSelect: () => void;
  onDownload: () => void;
  onMark: (liberated: boolean) => Promise<void>;
  downloadable: boolean;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const [queuing, setQueuing] = useState(false);
  const [marking, setMarking] = useState(false);

  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (queuing || !downloadable) return;
    setQueuing(true);
    await onDownload();
    setQueuing(false);
  };

  const handleMark = async (e: React.MouseEvent, liberated: boolean) => {
    e.stopPropagation();
    if (marking) return;
    setMarking(true);
    await onMark(liberated);
    setMarking(false);
  };

  const canMark = book.liberate_status !== "downloading";

  return (
    <button
      onClick={onSelect}
      className={cn(
        "group relative text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-xl",
        selected && "ring-2 ring-brand-500 ring-offset-2 dark:ring-offset-slate-900"
      )}
    >
      <div className="relative aspect-square rounded-xl overflow-hidden bg-slate-100 dark:bg-slate-700 mb-2 shadow-sm group-hover:shadow-md transition-shadow">
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

        {/* Checkbox overlay — visible in multi-select mode */}
        {multiSelectMode && (
          <div className={cn(
            "absolute top-1.5 left-1.5 flex h-5 w-5 items-center justify-center rounded-full border-2 transition-colors",
            selected
              ? "border-brand-600 bg-brand-600"
              : "border-white/80 bg-black/30 group-hover:border-white"
          )}>
            {selected && <CheckCircle className="h-3.5 w-3.5 text-white" />}
          </div>
        )}

        {/* Download button (not_liberated only, hidden in multi-select mode) */}
        {!multiSelectMode && book.liberate_status === "not_liberated" && downloadable && (
          <button
            onClick={handleDownload}
            title="Download"
            className="absolute bottom-1.5 right-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 hover:bg-black/80 transition-all"
          >
            {queuing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
          </button>
        )}

        {/* Mark button (hidden in multi-select mode — bulk actions handle that) */}
        {!multiSelectMode && canMark && (
          <button
            onClick={e => handleMark(e, book.liberate_status !== "liberated")}
            title={book.liberate_status === "liberated" ? "Mark as not downloaded" : "Mark as downloaded"}
            className="absolute top-1.5 right-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 hover:bg-black/80 transition-all"
          >
            {marking
              ? <Loader2 className="h-3 w-3 animate-spin" />
              : book.liberate_status === "liberated"
                ? <RotateCcw className="h-3 w-3" />
                : <CheckCheck className="h-3 w-3" />}
          </button>
        )}

        {selected && (
          <div className="absolute inset-0 bg-brand-600/20 rounded-xl" />
        )}
      </div>
      <p className="text-xs font-semibold text-slate-800 dark:text-slate-200 leading-tight line-clamp-2">{book.title}</p>
      {book.authors && <p className="text-xs text-slate-500 dark:text-slate-400 truncate mt-0.5">{book.authors}</p>}
      {book.is_abridged && <p className="text-xs text-amber-600 mt-0.5">Abridged</p>}
      {book.length_minutes && <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">{formatDuration(book.length_minutes)}</p>}
    </button>
  );
}

export function LiberatePage() {
  const { user } = useAuth();
  const [books, setBooks] = useState<LiberateBook[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<FilterTab>("all");
  const [ownerAccountId, setOwnerAccountId] = useState<string | null>(null);
  const [owners, setOwners] = useState<AccountOwner[]>([]);
  const [loading, setLoading] = useState(true);
  const [cap, setCap] = useState<CapStatus | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [inputValue, setInputValue] = useState("");
  const [search, setSearch] = useState("");
  const [multiSelectMode, setMultiSelectMode] = useState(false);
  const [bulkMarking, setBulkMarking] = useState(false);
  const [selectingAll, setSelectingAll] = useState(false);
  const [bulkStatus, setBulkStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [error, setError] = useState("");
  const [pageSize, setPageSize] = useState(48);
  const PAGE_SIZES = [24, 48, 96, 200];

  useEffect(() => {
    api.get("/accounts").then(r => {
      const withOwner = (r.data as AccountOwner[]).filter(a => a.owner_name || a.owner_username);
      setOwners(withOwner);
    }).catch(() => {});
  }, []);

  // Debounce search input by 300 ms
  useEffect(() => {
    const t = setTimeout(() => { setSearch(inputValue); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [inputValue]);

  const loadBooks = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const params: Record<string, unknown> = { filter_status: filter, page, page_size: pageSize };
      if (ownerAccountId) params.account_id = ownerAccountId;
      if (search) params.search = search;
      const [booksRes, capRes] = await Promise.all([
        api.get("/liberate/books", { params }),
        api.get("/liberate/cap"),
      ]);
      const incoming: LiberateBook[] = booksRes.data.books;
      if (silent) {
        // Only replace books whose status or progress actually changed to
        // avoid re-rendering unchanged tiles and resetting scroll position
        setBooks(prev => {
          const map = new Map(incoming.map(b => [b.book_id, b]));
          return prev.map(b => {
            const u = map.get(b.book_id);
            if (!u) return b;
            if (u.liberate_status === b.liberate_status && u.download_progress === b.download_progress) return b;
            return u;
          });
        });
      } else {
        setBooks(incoming);
        setTotal(booksRes.data.total);
      }
      setCap(capRes.data);
    } catch { if (!silent) setError("Failed to load books."); }
    finally { if (!silent) setLoading(false); }
  }, [filter, page, pageSize, ownerAccountId, search]);

  useEffect(() => { loadBooks(); }, [loadBooks]);

  // Poll while any book is downloading — silent so the grid never flickers
  useEffect(() => {
    const hasActive = books.some(b => b.liberate_status === "downloading");
    if (!hasActive) return;
    const t = setInterval(() => loadBooks(true), 2000);
    return () => clearInterval(t);
  }, [books, loadBooks]);

  const handleMark = async (book: LiberateBook, liberated: boolean) => {
    try {
      await api.patch(`/liberate/books/${book.book_id}`, { liberated });
      setBooks(bs => bs.map(b =>
        b.book_id === book.book_id
          ? { ...b, liberate_status: liberated ? "liberated" : "not_liberated", download_progress: null }
          : b
      ));
    } catch {
      setError("Failed to update book status.");
    }
  };

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

  const toggleMultiSelect = () => {
    setMultiSelectMode(m => !m);
    setSelected(new Set());
  };

  const selectAll = async () => {
    setSelectingAll(true);
    try {
      const params: Record<string, unknown> = { filter_status: filter };
      if (ownerAccountId) params.account_id = ownerAccountId;
      if (search) params.search = search;
      const { data } = await api.get("/liberate/book-ids", { params });
      setSelected(new Set(data.ids as string[]));
    } catch { setError("Failed to fetch all book IDs."); }
    finally { setSelectingAll(false); }
  };

  const bulkMark = async (liberated: boolean) => {
    if (selected.size === 0 || bulkMarking) return;
    setBulkMarking(true);
    const ids = Array.from(selected);
    for (const id of ids) {
      try { await api.patch(`/liberate/books/${id}`, { liberated }); } catch { /* continue */ }
    }
    setBooks(bs => bs.map(b =>
      selected.has(b.book_id)
        ? { ...b, liberate_status: liberated ? "liberated" : "not_liberated", download_progress: null }
        : b
    ));
    setSelected(new Set());
    setBulkMarking(false);
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

      {/* Owner tabs */}
      {owners.length > 0 && (
        <div className="flex gap-1 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-1 w-fit flex-wrap">
          <button
            onClick={() => { setOwnerAccountId(null); setPage(1); setSelected(new Set()); }}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
              ownerAccountId === null
                ? "bg-slate-800 dark:bg-slate-200 text-white dark:text-slate-900"
                : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
            )}
          >
            All
          </button>
          {owners.map(a => (
            <button
              key={a.account_id}
              onClick={() => { setOwnerAccountId(a.account_id); setPage(1); setSelected(new Set()); }}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                ownerAccountId === a.account_id
                  ? "bg-slate-800 dark:bg-slate-200 text-white dark:text-slate-900"
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
              )}
            >
              {a.owner_name || a.owner_username}
            </button>
          ))}
        </div>
      )}

      {/* Search bar */}
      <div className="flex justify-center">
        <div className="relative w-full max-w-lg">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 pointer-events-none" />
          <input
            type="text"
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            placeholder="Search by title…"
            className="w-full rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 pl-9 pr-9 py-2 text-sm text-slate-800 dark:text-slate-100 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-500 shadow-sm"
          />
          {inputValue && (
            <button
              onClick={() => setInputValue("")}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

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
        <div className="flex items-center gap-2 flex-wrap">
          <Button variant="outline" size="sm" onClick={() => loadBooks()}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>

          {/* Cap info (always visible) */}
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

          {multiSelectMode ? (
            /* ── Multi-select mode controls ── */
            <>
              <Button size="sm" variant="outline" onClick={selectAll} loading={selectingAll}>
                {selectingAll ? "Selecting…" : `Select All (${total.toLocaleString()})`}
              </Button>
              {selected.size > 0 && (
                <>
                  <Button size="sm" variant="outline" onClick={() => setSelected(new Set())}>
                    <X className="h-3.5 w-3.5" /> Clear ({selected.size})
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => bulkMark(true)}
                    loading={bulkMarking}
                    className="bg-emerald-600 hover:bg-emerald-700 text-white"
                  >
                    <CheckCheck className="h-3.5 w-3.5" /> Mark Downloaded
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => bulkMark(false)}
                    loading={bulkMarking}
                    className="bg-slate-600 hover:bg-slate-700 text-white"
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> Mark Not Downloaded
                  </Button>
                </>
              )}
              <Button size="sm" variant="outline" onClick={toggleMultiSelect}>
                <X className="h-3.5 w-3.5" /> Exit
              </Button>
            </>
          ) : (
            /* ── Normal mode controls ── */
            <>
              {/* Download-queueing selection controls */}
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

              <Button size="sm" variant="outline" onClick={toggleMultiSelect}>
                <Layers className="h-3.5 w-3.5" /> Multi Select
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Grid */}
      {loading ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="animate-pulse">
              <div className="aspect-square rounded-xl bg-slate-200 dark:bg-slate-700 mb-2" />
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
              : filter === "purchased" ? "No purchased titles found in your library."
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
              multiSelectMode={multiSelectMode}
              onSelect={() => {
                if (multiSelectMode) {
                  setSelected(s => { const n = new Set(s); n.has(book.book_id) ? n.delete(book.book_id) : n.add(book.book_id); return n; });
                } else {
                  if (book.liberate_status !== "not_liberated") return;
                  setSelected(s => { const n = new Set(s); n.has(book.book_id) ? n.delete(book.book_id) : n.add(book.book_id); return n; });
                }
              }}
              onDownload={() => handleDownloadOne(book)}
              onMark={(liberated) => handleMark(book, liberated)}
              downloadable={canDownload && !isCapExhausted && book.liberate_status === "not_liberated"}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {!loading && total > 0 && (
        <div className="flex items-center justify-between pt-1 flex-wrap gap-2">
          <div className="flex items-center gap-3">
            {total > pageSize && (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total.toLocaleString()}
              </p>
            )}
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-400 dark:text-slate-500">Per page:</span>
              <select
                value={pageSize}
                onChange={e => { setPageSize(Number(e.target.value)); setPage(1); }}
                className="rounded border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 px-1.5 py-0.5 text-xs text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-brand-500"
              >
                {PAGE_SIZES.map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
          </div>
          {total > pageSize && (
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>
                Prev
              </Button>
              <span className="text-sm text-slate-600 dark:text-slate-400 tabular-nums">{page} / {Math.ceil(total / pageSize)}</span>
              <Button variant="outline" size="sm" onClick={() => setPage(p => p + 1)} disabled={page >= Math.ceil(total / pageSize)}>
                Next
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
