import { LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

interface PlaceholderPageProps {
  icon: LucideIcon;
  title: string;
  description: string;
  phase: string;
}

export function PlaceholderPage({ icon: Icon, title, description, phase }: PlaceholderPageProps) {
  return (
    <Card>
      <CardContent className="py-16 flex flex-col items-center text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-100 mb-4">
          <Icon className="h-7 w-7 text-slate-400" />
        </div>
        <h2 className="text-lg font-semibold text-slate-700 mb-2">{title}</h2>
        <p className="text-sm text-slate-500 max-w-sm mb-3">{description}</p>
        <span className="inline-flex items-center rounded-full bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700">
          Coming in {phase}
        </span>
      </CardContent>
    </Card>
  );
}
