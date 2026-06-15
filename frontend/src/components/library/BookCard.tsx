import { useState } from "react";
import { Headphones, Download, Loader2, CheckCircle } from "lucide-react";
import { formatDuration } from "@/lib/utils";
import { api } from "@/lib/api";

export interface Book {
  book_id: string;
  title: string;
  length_minutes: number | null;
  description: string | null;
  authors: string | null;
  narrators: string | null;
  series_name: string | null;
  series_index: string | null;
  date_added: string | null;
  date_published: string | null;
  is_abridged?: boolean | null;
}

interface Props {
  book: Book;
  onClick: () => void;
}

export function BookCard({ book, onClick }: Props) {
  const [imgFailed, setImgFailed] = useState(false);
  const [dlState, setDlState] = useState<"idle" | "queuing" | "queued">("idle");

  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (dlState !== "idle") return;
    setDlState("queuing");
    try {
      await api.post("/downloads", { book_id: book.book_id, book_title: book.title });
      setDlState("queued");
    } catch {
      setDlState("idle");
    }
  };

  return (
    <button
      onClick={onClick}
      className="group relative text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1 rounded-xl"
    >
      <div className="relative aspect-square rounded-xl overflow-hidden bg-slate-100 mb-2 shadow-sm group-hover:shadow-md transition-shadow">
        {!imgFailed ? (
          <img
            src={`/api/library/covers/${book.book_id}`}
            alt={book.title}
            className="absolute inset-0 w-full h-full object-cover"
            onError={() => setImgFailed(true)}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-slate-100 to-slate-200">
            <Headphones className="h-8 w-8 text-slate-400" />
          </div>
        )}

        {/* Download button overlay */}
        <button
          onClick={handleDownload}
          title="Download"
          className="absolute bottom-2 right-2 flex h-7 w-7 items-center justify-center rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 hover:bg-black/80 transition-all"
        >
          {dlState === "queuing" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : dlState === "queued" ? (
            <CheckCircle className="h-3.5 w-3.5 text-green-400" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
      <p className="text-xs font-semibold text-slate-800 dark:text-slate-200 leading-tight line-clamp-2">{book.title}</p>
      {book.authors && <p className="text-xs text-slate-500 dark:text-slate-400 truncate mt-0.5">{book.authors}</p>}
      {book.is_abridged && <p className="text-xs text-amber-600 mt-0.5">Abridged</p>}
      {book.length_minutes && <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">{formatDuration(book.length_minutes)}</p>}
    </button>
  );
}
