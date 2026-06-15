import { cn } from "@/lib/utils";
import React from "react";

export const Label = ({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) => (
  <label className={cn("block text-sm font-medium text-slate-700 mb-1.5", className)} {...props} />
);
