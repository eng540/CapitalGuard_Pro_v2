import { cn } from "@/lib/utils";
import { CheckCircle2, Clock3, ShieldAlert, Sparkles } from "lucide-react";
import React, { type ReactNode } from "react";

export function KpiCard({ label, value, change, icon, tone = "cyan" }: { label: string; value: string; change?: string; icon: ReactNode; tone?: "cyan" | "violet" | "emerald" | "amber" }) {
  const tones = {
    cyan: "from-cyan-400/20 to-cyan-400/0 text-cyan-300 border-cyan-400/15",
    violet: "from-violet-400/20 to-violet-400/0 text-violet-300 border-violet-400/15",
    emerald: "from-emerald-400/20 to-emerald-400/0 text-emerald-300 border-emerald-400/15",
    amber: "from-amber-400/20 to-amber-400/0 text-amber-300 border-amber-400/15",
  };
  return <div className={cn("relative overflow-hidden rounded-2xl border bg-gradient-to-br p-5", tones[tone])}>
    <div className="absolute -right-5 -top-5 h-20 w-20 rounded-full bg-current opacity-[.08] blur-2xl" />
    <div className="flex items-start justify-between gap-4"><p className="text-xs font-medium text-muted-foreground">{label}</p><span className="rounded-xl bg-background/60 p-2 backdrop-blur">{icon}</span></div>
    <p className="mt-5 text-2xl font-semibold tracking-tight text-foreground">{value}</p>
    {change ? <p className="mt-2 text-xs text-muted-foreground">{change}</p> : null}
  </div>;
}

export function SectionTitle({ eyebrow, title, action }: { eyebrow?: string; title: string; action?: ReactNode }) {
  return <div className="mb-4 flex items-end justify-between gap-4"><div>{eyebrow ? <p className="mb-1 text-[11px] font-semibold uppercase tracking-[.16em] text-cyan-300">{eyebrow}</p> : null}<h2 className="text-lg font-semibold tracking-tight text-foreground">{title}</h2></div>{action}</div>;
}

export function StatusPill({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const state = normalized.includes("closed") || normalized.includes("verified") || normalized.includes("consistent") || normalized.includes("replayed")
    ? { icon: <CheckCircle2 className="h-3.5 w-3.5" />, className: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20" }
    : normalized.includes("review") || normalized.includes("mismatch") || normalized.includes("stale")
      ? { icon: <ShieldAlert className="h-3.5 w-3.5" />, className: "bg-amber-400/10 text-amber-200 ring-amber-400/20" }
      : normalized.includes("active") || normalized.includes("live") || normalized.includes("canonical")
        ? { icon: <Sparkles className="h-3.5 w-3.5" />, className: "bg-cyan-400/10 text-cyan-200 ring-cyan-400/20" }
        : { icon: <Clock3 className="h-3.5 w-3.5" />, className: "bg-white/5 text-muted-foreground ring-white/10" };
  return <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ring-1", state.className)}>{state.icon}{value.replaceAll("_", " ")}</span>;
}

export function PreviewNotice() {
  return <div className="mb-6 flex items-center gap-3 rounded-2xl border border-cyan-400/15 bg-cyan-400/[.06] px-4 py-3 text-xs text-cyan-100"><Sparkles className="h-4 w-4 shrink-0 text-cyan-300" /><span>هذه معاينة واجهة. عند ربط CapitalGuard Core ستظهر بياناتك المعزولة فعليًا بدل بيانات العرض.</span></div>;
}
