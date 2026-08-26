import { cn } from "@/lib/utils";
import { CheckCircle2, Clock3, ShieldAlert, Sparkles, CircleAlert, LoaderCircle } from "lucide-react";
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

export type CoreStatusKey = "SUCCESS" | "INCOMPLETE" | "CONFLICT" | "STAGED" | "REVIEW_REQUIRED" | "EVIDENCE_INGESTED" | "HISTORICAL" | "HISTORICAL_REPLAY_NOT_READY" | "PENDING_ACTIVATION" | "ACTIVATED" | "CLOSED" | "CANCELLED" | "FAILED" | "RETRYING" | "VERIFIED" | "LOW_SAMPLE" | "LIVE" | "CANONICAL" | "REPLAYED" | "UNKNOWN";

const statusViews: Record<CoreStatusKey, { label: string; icon: ReactNode; className: string }> = {
  SUCCESS: { label: "اكتمل", icon: <CheckCircle2 className="h-3.5 w-3.5" />, className: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20" },
  INCOMPLETE: { label: "بيانات ناقصة", icon: <CircleAlert className="h-3.5 w-3.5" />, className: "bg-amber-400/10 text-amber-200 ring-amber-400/20" },
  CONFLICT: { label: "يحتاج قرارًا", icon: <ShieldAlert className="h-3.5 w-3.5" />, className: "bg-amber-400/10 text-amber-200 ring-amber-400/20" },
  STAGED: { label: "تم الاستلام", icon: <Clock3 className="h-3.5 w-3.5" />, className: "bg-white/5 text-muted-foreground ring-white/10" },
  REVIEW_REQUIRED: { label: "بانتظار المراجعة", icon: <ShieldAlert className="h-3.5 w-3.5" />, className: "bg-amber-400/10 text-amber-200 ring-amber-400/20" },
  EVIDENCE_INGESTED: { label: "تم إدخال الدليل", icon: <CheckCircle2 className="h-3.5 w-3.5" />, className: "bg-cyan-400/10 text-cyan-200 ring-cyan-400/20" },
  HISTORICAL: { label: "تاريخية", icon: <Clock3 className="h-3.5 w-3.5" />, className: "bg-violet-400/10 text-violet-200 ring-violet-400/20" },
  HISTORICAL_REPLAY_NOT_READY: { label: "تحتاج تجهيزًا", icon: <ShieldAlert className="h-3.5 w-3.5" />, className: "bg-amber-400/10 text-amber-200 ring-amber-400/20" },
  PENDING_ACTIVATION: { label: "بانتظار التفعيل", icon: <Clock3 className="h-3.5 w-3.5" />, className: "bg-amber-400/10 text-amber-200 ring-amber-400/20" },
  ACTIVATED: { label: "مفعلة", icon: <Sparkles className="h-3.5 w-3.5" />, className: "bg-cyan-400/10 text-cyan-200 ring-cyan-400/20" },
  CLOSED: { label: "مغلقة", icon: <CheckCircle2 className="h-3.5 w-3.5" />, className: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20" },
  CANCELLED: { label: "أُلغي التتبع", icon: <CircleAlert className="h-3.5 w-3.5" />, className: "bg-white/5 text-muted-foreground ring-white/10" },
  FAILED: { label: "فشل", icon: <CircleAlert className="h-3.5 w-3.5" />, className: "bg-rose-400/10 text-rose-200 ring-rose-400/20" },
  RETRYING: { label: "إعادة المحاولة", icon: <LoaderCircle className="h-3.5 w-3.5" />, className: "bg-amber-400/10 text-amber-200 ring-amber-400/20" },
  VERIFIED: { label: "موثق", icon: <CheckCircle2 className="h-3.5 w-3.5" />, className: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20" },
  LOW_SAMPLE: { label: "عينة محدودة", icon: <ShieldAlert className="h-3.5 w-3.5" />, className: "bg-amber-400/10 text-amber-200 ring-amber-400/20" },
  LIVE: { label: "حي", icon: <Sparkles className="h-3.5 w-3.5" />, className: "bg-cyan-400/10 text-cyan-200 ring-cyan-400/20" },
  CANONICAL: { label: "معتمد", icon: <CheckCircle2 className="h-3.5 w-3.5" />, className: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20" },
  REPLAYED: { label: "اكتمل Replay", icon: <CheckCircle2 className="h-3.5 w-3.5" />, className: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20" },
  UNKNOWN: { label: "غير محددة", icon: <Clock3 className="h-3.5 w-3.5" />, className: "bg-white/5 text-muted-foreground ring-white/10" },
};

export function StatusPill({ value }: { value: string }) {
  const normalized = value.toUpperCase().replaceAll(" ", "_");
  const aliases: Record<string, CoreStatusKey> = { VERIFIED: "VERIFIED", LOW_SAMPLE: "LOW_SAMPLE", LIVE: "LIVE", CANONICAL: "CANONICAL", REPLAYED: "REPLAYED", CONSISTENT: "SUCCESS", CLOSED: "CLOSED", ACTIVE: "ACTIVATED", STALE: "REVIEW_REQUIRED", REVIEW: "REVIEW_REQUIRED", OWNER_REVIEW_REQUIRED: "REVIEW_REQUIRED" };
  const key = (aliases[normalized] ?? normalized) as CoreStatusKey;
  const state = statusViews[key] ?? statusViews.UNKNOWN;
  return <span title={value} className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ring-1", state.className)}>{state.icon}{state.label}</span>;
}

export function PreviewNotice({ isLive = false }: { isLive?: boolean }) {
  const message = isLive
    ? "هذه قراءة حية من CapitalGuard Core. لا تحفظ المنصة أي توصيات أو PnL محلياً."
    : "هذه معاينة واجهة. عند ربط CapitalGuard Core ستظهر بياناتك المعزولة فعليًا بدل بيانات العرض.";
  return <div className="mb-6 flex items-center gap-3 rounded-2xl border border-cyan-400/15 bg-cyan-400/[.06] px-4 py-3 text-xs text-cyan-100"><Sparkles className="h-4 w-4 shrink-0 text-cyan-300" /><span>{message}</span></div>;
}
