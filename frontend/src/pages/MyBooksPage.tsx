import { useState, useEffect } from "react";
import {
  Headphones, LayoutGrid, List, Search, ChevronLeft, ChevronRight, X, Clock, Mic, Link2,
} from "lucide-react";
import { api, authApi } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { cn, formatDuration } from "@/lib/utils";
import { BookCard, type Book } from "@/components/library/BookCard";
import { Button } from "@/components/ui/button";

const PAGE_SIZE = 48;

interface Account {
  account_id: string;
  name: string;
  locale: string;
}

function BookDetail({ book, onClose }: { book: Book; onClose: () => void }) {
  const [imgFailed, setImgFailed] = useState(false);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="flex-1 bg-black/40" onClick={onClose} />
      <div className="w-full max-w-sm bg-white dark:bg-slate-800 shadow-2xl overflow-y-auto flex flex-col">
        <div className="flex items-center justify-end px-5 pt-4 pb-0 shrink-0">
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="px-5 pb-8">
          <div className="flex justify-center my-5">
            <div className="relative w-40 h-56 rounded-xl overflow-hidden bg-slate-100 dark:bg-slate-700 flex items-center justify-center shadow-lg">
              {!imgFailed
                ? <img src={`/api/library/covers/${book.book_id}`} alt={book.title} className="absolute inset-0 w-full h-full object-cover" onError={() => setImgFailed(true)} />
                : <Headphones className="h-14 w-14 text-slate-300" />}
            </div>
          </div>
          <h2 className="text-lg font-bold text-slate-900 dark:text-slate-100 text-center leading-snug mb-1">{book.title}</h2>
          {book.authors && <p className="text-sm text-slate-500 dark:text-slate-400 text-center">{book.authors}</p>}
          {book.series_name && (
            <p className="text-sm font-medium text-brand-600 text-center mt-1">
              {book.series_name}{book.series_index ? ` #${book.series_index}` : ""}
            </p>
          )}
          <div className="mt-5 pt-4 border-t border-slate-100 dark:border-slate-700 space-y-2.5">
            {book.length_minutes != null && (
              <div className="flex items-center gap-2.5 text-sm text-slate-600 dark:text-slate-300">
                <Clock className="h-4 w-4 text-slate-400 shrink-0" />{formatDuration(book.length_minutes)}
              </div>
            )}
            {book.narrators && (
              <div className="flex items-start gap-2.5 text-sm text-slate-600 dark:text-slate-300">
                <Mic className="h-4 w-4 text-slate-400 shrink-0 mt-0.5" />
                <span>Narrated by {book.narrators}</span>
              </div>
            )}
          </div>
          {book.description && (
            <div className="mt-4 pt-4 border-t border-slate-100 dark:border-slate-700">
              <p className="text-sm text-slate-600 dark:text-slate-300 leading-relaxed">{book.description}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function BookRow({ book, onClick }: { book: Book; onClick: () => void }) {
  const [imgFailed, setImgFailed] = useState(false);
  return (
    <button onClick={onClick} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors">
      <div className="relative h-14 w-10 shrink-0 rounded-lg overflow-hidden bg-slate-100 dark:bg-slate-700 flex items-center justify-center">
        {!imgFailed
          ? <img src={`/api/library/covers/${book.book_id}`} alt="" className="absolute inset-0 w-full h-full object-cover" onError={() => setImgFailed(true)} />
          : <Headphones className="h-4 w-4 text-slate-400" />}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-slate-900 dark:text-slate-100 truncate">{book.title}</p>
        {book.authors && <p className="text-xs text-slate-500 dark:text-slate-400 truncate">{book.authors}</p>}
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

export function MyBooksPage() {
  const { user, refreshUser } = useAuth();
  const [books, setBooks] = useState<Book[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [loading, setLoading] = useState(true);
  const [selectedBook, setSelectedBook] = useState<Book | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [linkingAccount, setLinkingAccount] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => { setDebouncedSearch(search); setPage(1); }, 350);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    api.get("/accounts").then(r => setAccounts(r.data)).catch(() => null);
  }, []);

  useEffect(() => {
    if (!user?.audible_account_id) { setLoading(false); return; }
    setLoading(true);
    api.get("/library/my-books", {
      params: { search: debouncedSearch, page, page_size: PAGE_SIZE },
    })
      .then(r => { setBooks(r.data.books ?? []); setTotal(r.data.total ?? 0); })
      .catch(() => { setBooks([]); setTotal(0); })
      .finally(() => setLoading(false));
  }, [user?.audible_account_id, debouncedSearch, page]);

  const linkAccount = async (accountId: string) => {
    setLinkingAccount(true);
    try {
      await authApi.updateMe({ audible_account_id: accountId });
      await refreshUser();
    } finally { setLinkingAccount(false); }
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);

  if (!user?.audible_account_id) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center max-w-sm mx-auto">
        <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-slate-100 dark:bg-slate-800 mb-5">
          <Link2 className="h-10 w-10 text-slate-300" />
        </div>
        <h2 className="text-base font-semibold text-slate-700 dark:text-slate-300 mb-1">Link your Audible account</h2>
        <p className="text-sm text-slate-400 mb-5">
          Select which connected Audible account is yours to see only your books here.
        </p>
        {accounts.length === 0 ? (
          <p className="text-sm text-slate-400">No accounts connected yet. Go to Accounts to add one.</p>
        ) : (
          <div className="w-full space-y-2">
            {accounts.map(a => (
              <button
                key={a.account_id}
                onClick={() => linkAccount(a.account_id)}
                disabled={linkingAccount}
                className="w-full rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-4 py-3 text-left hover:border-brand-500 hover:bg-brand-50 dark:hover:bg-brand-900/20 transition-colors"
              >
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{a.name || a.account_id}</p>
                <p className="text-xs text-slate-400">{a.account_id} · {a.locale}</p>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 pointer-events-none" />
          <input
            type="text"
            placeholder="Search your books…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 pl-9 pr-4 py-2 text-sm text-slate-800 dark:text-slate-100 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
          />
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <div className="flex rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
            <button onClick={() => setViewMode("grid")} className={cn("p-2 transition-colors", viewMode === "grid" ? "bg-brand-600 text-white" : "bg-white dark:bg-slate-800 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700")} aria-label="Grid view">
              <LayoutGrid className="h-4 w-4" />
            </button>
            <button onClick={() => setViewMode("list")} className={cn("p-2 border-l border-slate-200 dark:border-slate-700 transition-colors", viewMode === "list" ? "bg-brand-600 text-white" : "bg-white dark:bg-slate-800 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700")} aria-label="List view">
              <List className="h-4 w-4" />
            </button>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={async () => { await linkAccount(""); }}
            title="Unlink account"
            className="text-xs"
          >
            Change account
          </Button>
        </div>
      </div>

      {!loading && total > 0 && (
        <p className="text-xs text-slate-400">{total.toLocaleString()} book{total !== 1 ? "s" : ""}</p>
      )}

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
          <h2 className="text-base font-semibold text-slate-700 dark:text-slate-300 mb-1">
            {debouncedSearch ? `No results for "${debouncedSearch}"` : "No books found"}
          </h2>
          <p className="text-sm text-slate-400 max-w-xs">
            {debouncedSearch ? "Try a different search." : "Run a library scan to populate your books."}
          </p>
        </div>
      ) : viewMode === "grid" ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
          {books.map(book => (
            <BookCard key={book.book_id} book={book} onClick={() => setSelectedBook(book)} />
          ))}
        </div>
      ) : (
        <div className="divide-y divide-slate-100 dark:divide-slate-700 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 overflow-hidden">
          {books.map(book => <BookRow key={book.book_id} book={book} onClick={() => setSelectedBook(book)} />)}
        </div>
      )}

      {!loading && totalPages > 1 && (
        <div className="flex items-center justify-between pt-1">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total.toLocaleString()}
          </p>
          <div className="flex items-center gap-2">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 dark:border-slate-700 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-sm text-slate-600 dark:text-slate-400 tabular-nums">{page} / {totalPages}</span>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 dark:border-slate-700 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {selectedBook && <BookDetail book={selectedBook} onClose={() => setSelectedBook(null)} />}
    </div>
  );
}
