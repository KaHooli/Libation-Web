import { useState, useEffect } from "react";
import {
  Headphones, LayoutGrid, List, Search,
  ChevronLeft, ChevronRight, X, Clock, Mic,
  BookOpen, Download, UserCheck, Users,
} from "lucide-react";
import { api, settingsApi } from "@/lib/api";
import { cn, formatDuration } from "@/lib/utils";
import { BookCard, type Book } from "@/components/library/BookCard";

interface LibraryData {
  books: Book[];
  total: number;
  page: number;
  page_size: number;
  empty_reason?: string;
}

interface Stats {
  total_books: number;
  total_downloads: number;
  accounts_count: number;
  downloads_per_user: { username: string; count: number }[];
}

function StatCards({ stats }: { stats: Stats }) {
  const topUser = stats.downloads_per_user.find(u => u.count > 0);
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 mb-2">
      {[
        { icon: BookOpen, label: "Books", value: stats.total_books.toLocaleString(), color: "text-brand-600" },
        { icon: Download, label: "Downloads", value: stats.total_downloads.toLocaleString(), color: "text-emerald-600" },
        { icon: UserCheck, label: "Accounts", value: stats.accounts_count.toLocaleString(), color: "text-amber-600" },
        {
          icon: Users,
          label: "Top Downloader",
          value: topUser ? `${topUser.username} (${topUser.count})` : "—",
          color: "text-violet-600",
        },
      ].map(({ icon: Icon, label, value, color }) => (
        <div key={label} className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-4 py-3 shadow-sm">
          <div className={cn("flex items-center gap-1.5 text-xs font-medium mb-1", color)}>
            <Icon className="h-3.5 w-3.5" />
            {label}
          </div>
          <p className="text-lg font-bold text-slate-900 dark:text-slate-100 truncate">{value}</p>
        </div>
      ))}
    </div>
  );
}

const PAGE_SIZE = 48;

const SORT_OPTIONS = [
  { value: "date_added:desc", label: "Recently Added" },
  { value: "date_added:asc", label: "Oldest First" },
  { value: "title:asc", label: "Title A–Z" },
  { value: "title:desc", label: "Title Z–A" },
  { value: "length:desc", label: "Longest First" },
  { value: "length:asc", label: "Shortest First" },
];

function LoadingSkeleton({ mode }: { mode: "grid" | "list" }) {
  if (mode === "grid") {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
        {Array.from({ length: 12 }).map((_, i) => (
          <div key={i} className="animate-pulse">
            <div className="aspect-[2/3] rounded-xl bg-slate-200 mb-2" />
            <div className="h-3 rounded bg-slate-200 w-3/4 mb-1" />
            <div className="h-3 rounded bg-slate-200 w-1/2" />
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 overflow-hidden">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex gap-3 px-4 py-3 animate-pulse bg-white">
          <div className="h-14 w-10 rounded-lg bg-slate-200 shrink-0" />
          <div className="flex-1 space-y-2 py-1">
            <div className="h-3 rounded bg-slate-200 w-2/3" />
            <div className="h-3 rounded bg-slate-200 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

function EmptyState({ reason, search }: { reason?: string; search: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-slate-100 mb-5">
        <Headphones className="h-10 w-10 text-slate-300" />
      </div>
      {search ? (
        <>
          <h2 className="text-base font-semibold text-slate-700 mb-1">No results for "{search}"</h2>
          <p className="text-sm text-slate-400">Try a different search term.</p>
        </>
      ) : reason === "no_accounts" ? (
        <>
          <h2 className="text-base font-semibold text-slate-700 mb-1">No library yet</h2>
          <p className="text-sm text-slate-400 max-w-xs">
            Connect an Audible account and run a scan to populate your library.
          </p>
        </>
      ) : (
        <>
          <h2 className="text-base font-semibold text-slate-700 mb-1">Library is empty</h2>
          <p className="text-sm text-slate-400">Scan your Audible library to see books here.</p>
        </>
      )}
    </div>
  );
}

function BookRow({ book, onClick }: { book: Book; onClick: () => void }) {
  const [imgFailed, setImgFailed] = useState(false);
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 transition-colors"
    >
      <div className="relative h-14 w-10 shrink-0 rounded-lg overflow-hidden bg-slate-100 flex items-center justify-center">
        {!imgFailed ? (
          <img
            src={`/api/library/covers/${book.book_id}`}
            alt=""
            className="absolute inset-0 w-full h-full object-cover"
            onError={() => setImgFailed(true)}
          />
        ) : (
          <Headphones className="h-4 w-4 text-slate-400" />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-slate-900 truncate">{book.title}</p>
        {book.authors && <p className="text-xs text-slate-500 truncate">{book.authors}</p>}
        {book.series_name && (
          <p className="text-xs text-brand-600 truncate">
            {book.series_name}{book.series_index ? ` #${book.series_index}` : ""}
          </p>
        )}
      </div>
      {book.length_minutes != null && (
        <span className="text-xs text-slate-400 shrink-0">{formatDuration(book.length_minutes)}</span>
      )}
    </button>
  );
}

function BookDetail({ book, onClose }: { book: Book; onClose: () => void }) {
  const [imgFailed, setImgFailed] = useState(false);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="flex-1 bg-black/40" onClick={onClose} />
      <div className="w-full max-w-sm bg-white shadow-2xl overflow-y-auto flex flex-col">
        <div className="flex items-center justify-end px-5 pt-4 pb-0 shrink-0">
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="px-5 pb-8">
          {/* Cover */}
          <div className="flex justify-center my-5">
            <div className="relative w-40 h-56 rounded-xl overflow-hidden bg-slate-100 flex items-center justify-center shadow-lg">
              {!imgFailed ? (
                <img
                  src={`/api/library/covers/${book.book_id}`}
                  alt={book.title}
                  className="absolute inset-0 w-full h-full object-cover"
                  onError={() => setImgFailed(true)}
                />
              ) : (
                <Headphones className="h-14 w-14 text-slate-300" />
              )}
            </div>
          </div>

          {/* Title + author */}
          <h2 className="text-lg font-bold text-slate-900 text-center leading-snug mb-1">
            {book.title}
          </h2>
          {book.authors && (
            <p className="text-sm text-slate-500 text-center">{book.authors}</p>
          )}
          {book.series_name && (
            <p className="text-sm font-medium text-brand-600 text-center mt-1">
              {book.series_name}{book.series_index ? ` #${book.series_index}` : ""}
            </p>
          )}

          {/* Meta */}
          <div className="mt-5 pt-4 border-t border-slate-100 space-y-2.5">
            {book.length_minutes != null && (
              <div className="flex items-center gap-2.5 text-sm text-slate-600">
                <Clock className="h-4 w-4 text-slate-400 shrink-0" />
                {formatDuration(book.length_minutes)}
              </div>
            )}
            {book.narrators && (
              <div className="flex items-start gap-2.5 text-sm text-slate-600">
                <Mic className="h-4 w-4 text-slate-400 shrink-0 mt-0.5" />
                <span>Narrated by {book.narrators}</span>
              </div>
            )}
            {book.date_published && (
              <p className="text-xs text-slate-400">
                Published {new Date(book.date_published).toLocaleDateString(undefined, { year: "numeric", month: "long" })}
              </p>
            )}
          </div>

          {/* Description */}
          {book.description && (
            <div className="mt-4 pt-4 border-t border-slate-100">
              <p className="text-sm text-slate-600 leading-relaxed">{book.description}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function LibraryPage() {
  const [data, setData] = useState<LibraryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [sort, setSort] = useState("date_added:desc");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [page, setPage] = useState(1);
  const [selectedBook, setSelectedBook] = useState<Book | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    settingsApi.getStats().then(r => setStats(r.data)).catch(() => null);
  }, []);

  // Debounce search input
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [search]);

  // Fetch books whenever query params change
  useEffect(() => {
    const [sort_by, sort_dir] = sort.split(":");
    setLoading(true);
    api
      .get("/library/books", {
        params: { search: debouncedSearch, sort_by, sort_dir, page, page_size: PAGE_SIZE },
      })
      .then(r => setData(r.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [debouncedSearch, sort, page]);

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;

  return (
    <div className="space-y-4">
      {/* Stat cards */}
      {stats && <StatCards stats={stats} />}

      {/* Controls bar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 pointer-events-none" />
          <input
            type="text"
            placeholder="Search by title…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 pl-9 pr-4 py-2 text-sm text-slate-800 dark:text-slate-100 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
          />
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <select
            value={sort}
            onChange={e => { setSort(e.target.value); setPage(1); }}
            className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500"
          >
            {SORT_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <div className="flex rounded-lg border border-slate-200 overflow-hidden">
            <button
              onClick={() => setViewMode("grid")}
              className={cn(
                "p-2 transition-colors",
                viewMode === "grid" ? "bg-brand-600 text-white" : "bg-white text-slate-500 hover:bg-slate-50"
              )}
              aria-label="Grid view"
            >
              <LayoutGrid className="h-4 w-4" />
            </button>
            <button
              onClick={() => setViewMode("list")}
              className={cn(
                "p-2 border-l border-slate-200 transition-colors",
                viewMode === "list" ? "bg-brand-600 text-white" : "bg-white text-slate-500 hover:bg-slate-50"
              )}
              aria-label="List view"
            >
              <List className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Book count */}
      {!loading && data && data.total > 0 && (
        <p className="text-xs text-slate-400">
          {data.total.toLocaleString()} book{data.total !== 1 ? "s" : ""}
        </p>
      )}

      {/* Content area */}
      {loading ? (
        <LoadingSkeleton mode={viewMode} />
      ) : !data || data.books.length === 0 ? (
        <EmptyState reason={data?.empty_reason} search={debouncedSearch} />
      ) : viewMode === "grid" ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
          {data.books.map(book => (
            <BookCard key={book.book_id} book={book} onClick={() => setSelectedBook(book)} />
          ))}
        </div>
      ) : (
        <div className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white overflow-hidden">
          {data.books.map(book => (
            <BookRow key={book.book_id} book={book} onClick={() => setSelectedBook(book)} />
          ))}
        </div>
      )}

      {/* Pagination */}
      {!loading && data && totalPages > 1 && (
        <div className="flex items-center justify-between pt-1">
          <p className="text-sm text-slate-500">
            {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, data.total)} of {data.total.toLocaleString()}
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-sm text-slate-600 tabular-nums">{page} / {totalPages}</span>
            <button
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* Detail slide-over */}
      {selectedBook && (
        <BookDetail book={selectedBook} onClose={() => setSelectedBook(null)} />
      )}
    </div>
  );
}
