import DashboardLayout from "@/components/DashboardLayout";
import { KpiCard, SectionTitle, StatusPill } from "@/components/finance-ui";
import { trpc } from "@/lib/trpc";
import { BarChart3, ShieldCheck, Target } from "lucide-react";
import { Link, useRoute } from "wouter";

export default function AnalystDossier() {
  const [, params] = useRoute("/analysts/:code");
  const analysts = trpc.capitalguard.discoverAnalysts.useQuery();
  const code = decodeURIComponent(params?.code ?? "");
  const analyst = (analysts.data?.items ?? []).find(row => (row.analyst_code ?? row.public_ref ?? "") === code);
  return <DashboardLayout><main dir="rtl" className="mx-auto max-w-[1100px]">
    <Link href="/analysts" className="text-xs text-cyan-200 hover:text-cyan-100">← العودة إلى لوحة المحللين</Link>
    {analysts.isLoading ? <p className="mt-8 rounded-2xl border border-white/8 bg-card/70 p-6 text-sm text-muted-foreground">جارٍ تحميل الملف العام من Core…</p> : analysts.isError ? <p className="mt-8 rounded-2xl border border-rose-400/20 bg-rose-400/5 p-6 text-sm text-rose-100">تعذر تحميل الملف العام.</p> : !analyst ? <p className="mt-8 rounded-2xl border border-white/8 bg-card/70 p-6 text-sm text-muted-foreground">لم يُعثر على محلل عام بهذا الرمز.</p> : <>
      <header className="mt-6 rounded-3xl border border-cyan-300/15 bg-cyan-300/[.04] p-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="font-mono text-xs text-cyan-300">{analyst.analyst_code ?? analyst.public_ref}</p><h1 className="mt-2 text-3xl font-semibold">{analyst.public_name}</h1><p className="mt-2 text-sm text-muted-foreground">سجل عام مصدره Core؛ الأهلية مرتبطة بالعينة والتحقق وليس بالترتيب الشكلي.</p></div><StatusPill value={analyst.eligible_for_ranking ? "verified" : "low_sample"}/></div></header>
      <div className="mt-6 grid gap-4 sm:grid-cols-3"><KpiCard label="Win Rate" value={`${analyst.win_rate_pct.toFixed(1)}%`} icon={<Target className="h-4 w-4"/>} tone="cyan"/><KpiCard label="Total PnL" value={`${analyst.total_pnl_pct.toFixed(2)}%`} icon={<BarChart3 className="h-4 w-4"/>} tone="emerald"/><KpiCard label="Sample Size" value={String(analyst.sample_size)} icon={<ShieldCheck className="h-4 w-4"/>}/></div>
      <section className="mt-6 rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Public Track Record" title="مؤشرات التحقق"/><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Metric label="Profit Factor" value={analyst.profit_factor_infinite ? "∞" : analyst.profit_factor?.toFixed(2) ?? "—"}/><Metric label="Max Drawdown" value={`${analyst.max_drawdown_pct.toFixed(2)}%`}/><Metric label="Active Signals" value={String(analyst.active_recommendations)}/><Metric label="Risk Exposure" value={`${analyst.risk_exposure_pct.toFixed(2)}%`}/></div><div className="mt-5 rounded-2xl border border-violet-300/15 bg-violet-300/[.04] p-4"><p className="text-xs font-semibold uppercase tracking-[.14em] text-violet-200">Signal Health</p><div className="mt-3 grid gap-3 sm:grid-cols-3"><Metric label="متوسط الوصول لأول هدف" value={analyst.signal_health?.avg_minutes_to_first_target == null ? "—" : `${analyst.signal_health.avg_minutes_to_first_target.toFixed(0)} دقيقة`}/><Metric label="انعكاس قبل الدخول" value={String(analyst.signal_health?.reversed_before_entry_count ?? 0)}/><Metric label="الأكثر ربحية" value={analyst.signal_health?.most_profitable_pairs?.[0] ? `${analyst.signal_health.most_profitable_pairs[0].asset} · ${analyst.signal_health.most_profitable_pairs[0].pnl_pct.toFixed(2)}%` : "—"}/></div></div><p className="mt-4 text-xs text-muted-foreground">آخر بيانات صالحة منذ {analyst.freshness_days ?? "—"} يوم. لا يعرض هذا الملف بيانات خاصة أو أوامر تنفيذ.</p></section>
    </>}
  </main></DashboardLayout>;
}
function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded-xl bg-white/[.04] p-3"><p className="text-[10px] text-muted-foreground">{label}</p><p className="mt-1 font-mono text-sm font-semibold text-cyan-100">{value}</p></div>; }
