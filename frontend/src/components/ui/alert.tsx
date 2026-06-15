import { cn } from "@/lib/utils";
import { AlertCircle, CheckCircle2, Info } from "lucide-react";
import React from "react";

type AlertVariant = "error" | "success" | "info";

const variants: Record<AlertVariant, { container: string; icon: React.ReactNode }> = {
  error: {
    container: "bg-red-50 border-red-200 text-red-800",
    icon: <AlertCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />,
  },
  success: {
    container: "bg-green-50 border-green-200 text-green-800",
    icon: <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0 mt-0.5" />,
  },
  info: {
    container: "bg-blue-50 border-blue-200 text-blue-800",
    icon: <Info className="h-4 w-4 text-blue-500 shrink-0 mt-0.5" />,
  },
};

interface AlertProps {
  variant?: AlertVariant;
  children: React.ReactNode;
  className?: string;
}

export function Alert({ variant = "info", children, className }: AlertProps) {
  const v = variants[variant];
  return (
    <div className={cn("flex gap-2.5 rounded-lg border p-3 text-sm", v.container, className)}>
      {v.icon}
      <div>{children}</div>
    </div>
  );
}
