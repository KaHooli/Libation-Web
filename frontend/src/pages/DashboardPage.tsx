import { BookOpen, Download, Users, Headphones } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

const STATS = [
  { label: "Books in library", value: "—", icon: BookOpen, color: "text-brand-600 bg-brand-50" },
  { label: "Downloaded", value: "—", icon: Download, color: "text-green-600 bg-green-50" },
  { label: "Accounts", value: "—", icon: Users, color: "text-amber-600 bg-amber-50" },
  { label: "Listening hours", value: "—", icon: Headphones, color: "text-purple-600 bg-purple-50" },
];

export function DashboardPage() {
  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {STATS.map(({ label, value, icon: Icon, color }) => (
          <Card key={label}>
            <CardContent className="p-4">
              <div className={`inline-flex h-9 w-9 items-center justify-center rounded-lg ${color} mb-3`}>
                <Icon className="h-4 w-4" />
              </div>
              <p className="text-2xl font-bold text-slate-900">{value}</p>
              <p className="text-xs text-slate-500 mt-0.5">{label}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Coming soon */}
      <Card>
        <CardContent className="py-16 flex flex-col items-center text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-100 mb-4">
            <BookOpen className="h-7 w-7 text-slate-400" />
          </div>
          <h2 className="text-lg font-semibold text-slate-700 mb-2">Library coming in Phase 2</h2>
          <p className="text-sm text-slate-500 max-w-sm">
            Your Audible library will appear here once Libation accounts and
            scanning are wired up in the next phase.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
