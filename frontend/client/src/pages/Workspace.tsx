import React from "react";
import DashboardLayout from "@/components/DashboardLayout";
import CoreConnectionStatus from "@/components/CoreConnectionStatus";
import LiveSignalStatus from "@/components/LiveSignalStatus";
import { KpiCard, SectionTitle, StatusPill } from "@/components/finance-ui";
import { Button } from "@/components/ui/button";
import { trpc } from "@/lib/trpc";
import { Activity, ArrowUpRight, ChevronLeft, CircleDollarSign, WalletCards } from "lucide-react";
import { Link } from "wouter";

function displayMetric(value: unknown, suffix = "") {
  if (typeof value !== "number" && typeof value !== "string") return "غير متاح";
  return `${value}${suffix}`;
}

export default function Workspace() {
  const snapshot = trpc.capitalguard.core.traderSnapshot.useQuery(undefined, {
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
  });
  const model = snapshot.data?.ok === true ? snapshot.data : null;
  const positions = model?.portfolio.positions ?? [];
  const performance = model?.performance as Record<string, unknown> | undefined;
  const funnel = model?.funnel as Record<string, unknown> | undefined;

  return <DashboardLayout><main dir="rtl" className="mx-auto max-w-[1500px]">
    <header className="mb-8 flex flex-col justify-between gap-5 lg:flex-row lg:items-end"><div><p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-cyan-300">Core Live Read Model</p><h1 className="text-3xl font-semibold tracking-tight">مساحة المتداول الحية</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">تعرض هذه المساحة قراءة Core الحالية فقط. لا توجد بيانات مالية تجريبية أو نسخة مخزنة في Web.</p></div><div className="flex gap-3"><Button variant="outline" className="border-white/10 bg-white/[.03]" asChild><Link href="/risk">حاسبة المخاطر</Link></Button><Button className="bg-cyan-400 text-slate-950 hover:bg-cyan-300" asChild><Link href="/recommendations">استكشاف التوصيات <ChevronLeft className="mr-1 h-4 w-4" /></Link></Button></div></header>
    <CoreConnectionStatus />
    <LiveSignalStatus />
    {snapshot.isLoading ? <section className="mt-6 rounded-3xl border border-white/10 bg-card/70 p-6 text-sm text-muted-foreground">جارٍ تحميل قراءة المحفظة من Core…</section> : null}
    {snapshot.isError ? <section className="mt-6 rounded-3xl border border-amber-300/20 bg-amber-300/[.05] p-6"><h2 className="font-semibold text-amber-100">تعذر تحميل القراءة الحية</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">لم تُعرض أي بيانات بديلة. تحقق من اتصال Core ثم حدّث الصفحة.</p></section> : null}
    {model ? <>
      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><KpiCard label="مراكز مفتوحة" value={String(model.portfolio.open_position_count)} change="من Core الآن" icon={<WalletCards className="h-4 w-4" />} /><KpiCard label="PnL المحفظة المفعلة" value={displayMetric(performance?.total_pnl_pct, "%")} change="Activated Portfolio Only" icon={<ArrowUpRight className="h-4 w-4" />} tone="emerald" /><KpiCard label="Win Rate" value={displayMetric(performance?.win_rate_pct, "%")} change="صفقات مفعلة مغلقة" icon={<Activity className="h-4 w-4" />} tone="cyan" /><KpiCard label="صفقات مفعلة" value={displayMetric(funnel?.activated)} change="Funnel دورة الحياة" icon={<CircleDollarSign className="h-4 w-4" />} tone="violet" /></div>
      <section className="mt-6 rounded-3xl border border-cyan-300/15 bg-cyan-300/[.04] p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-[.16em] text-cyan-300">Read Model Status</p><h2 className="mt-1 font-semibold">مصدر البيانات ووقت القراءة</h2></div><StatusPill value="CORE LIVE" /></div><p className="mt-3 text-sm text-muted-foreground">آخر تحديث: {model.as_of ? new Date(model.as_of).toLocaleString("ar-SA") : "غير متاح"}. تُقرأ البيانات خادمياً من Core ولا تُكرر في قاعدة Web.</p></section>
      <section className="mt-6 rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Positions" title="المراكز الشخصية الحية" action={<Link href="/recommendations" className="text-xs text-cyan-300 hover:text-cyan-200">كل الصفقات</Link>} />{positions.length === 0 ? <p className="mt-5 rounded-2xl border border-dashed border-white/10 p-5 text-sm text-muted-foreground">لا توجد مراكز مفتوحة في قراءة Core الحالية.</p> : <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[760px] text-right"><thead className="text-[11px] uppercase tracking-[.12em] text-muted-foreground"><tr><th className="pb-3 font-medium">المرجع</th><th className="pb-3 font-medium">الأصل</th><th className="pb-3 font-medium">الدخول</th><th className="pb-3 font-medium">السعر الحي</th><th className="pb-3 font-medium">المصدر</th><th className="pb-3 font-medium">الحالة</th><th className="pb-3 text-left font-medium">PnL حي</th></tr></thead><tbody>{positions.map(position => <tr key={position.id} className="border-t border-white/6 text-sm"><td className="py-4 font-mono text-xs text-slate-300">UT-{position.id}</td><td className="py-4"><span className="font-medium">{position.asset}</span><span className={position.side.toUpperCase() === "LONG" ? "mr-2 text-xs text-emerald-300" : "mr-2 text-xs text-rose-300"}>{position.side.toUpperCase()}</span></td><td className="py-4 text-muted-foreground">{position.entry}</td><td className="py-4 text-muted-foreground">{position.live_price ?? "غير متاح"}</td><td className="py-4 text-xs text-muted-foreground">{position.source_type}</td><td className="py-4"><StatusPill value={position.status}/></td><td className={position.pnl_live_pct >= 0 ? "py-4 text-left font-medium text-emerald-300" : "py-4 text-left font-medium text-rose-300"}>{position.pnl_live_pct >= 0 ? "+" : ""}{position.pnl_live_pct.toFixed(2)}%</td></tr>)}</tbody></table></div>}</section>
      <section className="mt-6"><SectionTitle eyebrow="Live Recommendations" title="التوصيات المتصلة بمراكزك" />{positions.length === 0 ? <p className="mt-4 text-sm text-muted-foreground">لا توجد توصيات مفعلة لعرضها حالياً.</p> : <div className="mt-4 grid gap-4 lg:grid-cols-3">{positions.slice(0, 3).map(position => <article key={position.id} className="rounded-3xl border border-white/8 bg-card/70 p-5"><div className="flex items-start justify-between"><div><p className="font-mono text-[11px] text-cyan-300">UT-{position.id}</p><h3 className="mt-2 text-xl font-semibold">{position.asset}</h3></div><StatusPill value={position.status}/></div><p className={position.side.toUpperCase() === "LONG" ? "mt-4 text-sm text-emerald-300" : "mt-4 text-sm text-rose-300"}>{position.side.toUpperCase()} · {position.source_type}</p><div className="mt-5 grid grid-cols-2 gap-3 text-xs"><div className="rounded-xl bg-white/[.04] p-3"><p className="text-muted-foreground">Entry</p><p className="mt-1 font-medium">{position.entry}</p></div><div className="rounded-xl bg-white/[.04] p-3"><p className="text-muted-foreground">Stop</p><p className="mt-1 font-medium">{position.stop_loss}</p></div></div><p className="mt-4 text-xs text-muted-foreground">Targets: {position.targets.map(target => target.price).join(" · ") || "غير متاحة"}</p></article>)}</div>}</section>
    </> : null}
  </main></DashboardLayout>;
}
