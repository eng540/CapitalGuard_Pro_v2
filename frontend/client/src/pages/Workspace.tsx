import React from "react";
import DashboardLayout from "@/components/DashboardLayout";
import CoreConnectionStatus from "@/components/CoreConnectionStatus";
import LiveSignalStatus from "@/components/LiveSignalStatus";
import { KpiCard, SectionTitle, StatusPill } from "@/components/finance-ui";
import { Button } from "@/components/ui/button";
import { trpc } from "@/lib/trpc";
import { userTradeEventCopy } from "@/lib/user-trade-event-copy";
import { Activity, ArrowUpRight, ChevronLeft, CircleDollarSign, WalletCards } from "lucide-react";
import { Link } from "wouter";

type TraderRecommendation = {
  public_ref: string;
  display_ref: string;
  asset: string;
  side: string;
  status: string;
  timeline: Array<{ event_type: string; event_timestamp: string }>;
};

const lifecycleCopy: Record<string, { label: string; className: string; detail: string }> = {
  WATCHLIST: { label: "قيد المتابعة", className: "text-slate-200", detail: "لم تدخل الصفقة السوق بعد؛ يمكن إلغاء التتبع بلا سعر خروج أو PnL." },
  PENDING_ACTIVATION: { label: "بانتظار التفعيل", className: "text-amber-200", detail: "الأمر لم يتفعل بعد؛ لا يعامل كصفقة مغلقة." },
  ACTIVATED: { label: "مفعلة", className: "text-cyan-200", detail: "الصفقة دخلت السوق؛ الإغلاق اليدوي، إن طُلب، يعتمد على سعر Core الموثوق." },
  CLOSED: { label: "مغلقة", className: "text-emerald-200", detail: "سجل طرفي للقراءة فقط؛ لا يظهر إجراء إغلاق مكرر." },
  CANCELLED: { label: "أُلغي التتبع", className: "text-slate-300", detail: "أُلغي الأمر أو التتبع المعلق بلا سعر خروج أو PnL." },
};

function displayMetric(value: unknown, suffix = "") {
  if (typeof value !== "number" && typeof value !== "string") return "غير متاح";
  return `${value}${suffix}`;
}

export default function Workspace() {
  const snapshot = trpc.capitalguard.core.traderSnapshot.useQuery(undefined, {
    refetchInterval: 20_000,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
  const recommendations = trpc.capitalguard.recommendations.useQuery(undefined, {
    refetchInterval: 20_000,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
  const model = snapshot.data?.ok === true ? snapshot.data : null;
  const positions = model?.portfolio.positions ?? [];
  const performance = model?.performance as Record<string, unknown> | undefined;
  const funnel = model?.funnel as Record<string, unknown> | undefined;
  const traderItems = (recommendations.data?.items ?? []) as TraderRecommendation[];
  const lifecycleCounts = traderItems.reduce<Record<string, number>>((counts, item) => ({ ...counts, [item.status]: (counts[item.status] ?? 0) + 1 }), {});
  const recentEvents = traderItems.flatMap(item => item.timeline.map(event => ({ ...event, publicRef: item.public_ref, displayRef: item.display_ref, asset: item.asset, status: item.status }))).sort((left, right) => right.event_timestamp.localeCompare(left.event_timestamp)).slice(0, 6);
  const attentionItems = traderItems.filter(item => ["ACTIVATED", "PENDING_ACTIVATION", "WATCHLIST"].includes(item.status));

  return <DashboardLayout><main dir="rtl" className="mx-auto max-w-[1500px]">
    <header className="mb-8 flex flex-col justify-between gap-5 lg:flex-row lg:items-end"><div><p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-cyan-300">Portfolio Hub</p><h1 className="text-3xl font-semibold tracking-tight">محفظتي الحية</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">ابدأ بما يحتاج قرارًا الآن، ثم راجع مراكزك وTimeline. كل رقم وحالة هنا قراءة مباشرة من Core.</p></div><div className="flex gap-3"><Button variant="outline" className="border-white/10 bg-white/[.03]" asChild><Link href="/risk">حاسبة المخاطر</Link></Button><Button className="bg-cyan-400 text-slate-950 hover:bg-cyan-300" asChild><Link href="/recommendations">استكشاف التوصيات <ChevronLeft className="mr-1 h-4 w-4" /></Link></Button></div></header>
    <CoreConnectionStatus />
    <LiveSignalStatus />
    {snapshot.isLoading ? <section className="mt-6 rounded-3xl border border-white/10 bg-card/70 p-6 text-sm text-muted-foreground">جارٍ تحميل قراءة المحفظة من Core…</section> : null}
    {snapshot.isError ? <section className="mt-6 rounded-3xl border border-amber-300/20 bg-amber-300/[.05] p-6"><h2 className="font-semibold text-amber-100">تعذر تحميل القراءة الحية</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">لم تُعرض أي بيانات بديلة. تحقق من اتصال Core ثم حدّث الصفحة.</p></section> : null}
    {model ? <>
      <section className="mt-6 rounded-3xl border border-amber-300/15 bg-amber-300/[.035] p-5"><SectionTitle eyebrow="الآن" title="ما يحتاج انتباهك" action={<Link href="/recommendations" className="text-xs text-cyan-300 hover:text-cyan-200">فتح المحفظة كاملة</Link>} />{attentionItems.length === 0 ? <p className="rounded-2xl border border-dashed border-white/10 p-4 text-sm text-muted-foreground">لا توجد توصيات مفتوحة أو معلقة تحتاج قرارًا الآن.</p> : <div className="grid gap-3 md:grid-cols-3">{attentionItems.slice(0, 3).map(item => { const copy = lifecycleCopy[item.status] ?? lifecycleCopy.WATCHLIST; return <Link key={item.public_ref} href={`/recommendations?ref=${encodeURIComponent(item.public_ref)}`} className="rounded-2xl border border-amber-300/15 bg-white/[.025] p-4 transition hover:border-cyan-300/35"><div className="flex items-center justify-between gap-3"><span className="font-medium">{item.asset}</span><StatusPill value={item.status} /></div><p className="mt-3 text-xs leading-5 text-muted-foreground">{copy.detail}</p><span className="mt-3 inline-flex items-center text-xs text-cyan-300">عرض Timeline <ChevronLeft className="mr-1 h-3.5 w-3.5" /></span></Link>; })}</div>}</section>
      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><KpiCard label="مراكز مفتوحة" value={String(model.portfolio.open_position_count)} change="من Core الآن" icon={<WalletCards className="h-4 w-4" />} /><KpiCard label="PnL المحفظة المفعلة" value={displayMetric(performance?.total_pnl_pct, "%")} change="Activated Portfolio Only" icon={<ArrowUpRight className="h-4 w-4" />} tone="emerald" /><KpiCard label="Win Rate" value={displayMetric(performance?.win_rate_pct, "%")} change="صفقات مفعلة مغلقة" icon={<Activity className="h-4 w-4" />} tone="cyan" /><KpiCard label="صفقات مفعلة" value={displayMetric(funnel?.activated)} change="Funnel دورة الحياة" icon={<CircleDollarSign className="h-4 w-4" />} tone="violet" /></div>
      <section className="mt-6 rounded-3xl border border-cyan-300/15 bg-cyan-300/[.04] p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-[.16em] text-cyan-300">Read Model Status</p><h2 className="mt-1 font-semibold">مصدر البيانات ووقت القراءة</h2></div><StatusPill value="CORE LIVE" /></div><p className="mt-3 text-sm text-muted-foreground">آخر تحديث: {model.as_of ? new Date(model.as_of).toLocaleString("ar-SA") : "غير متاح"}. تُقرأ البيانات خادمياً من Core ولا تُكرر في قاعدة Web.</p></section>
      <section className="mt-6 grid gap-4 lg:grid-cols-[1.15fr_.85fr]"><article className="rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Trader Lifecycle" title="حالات سجلاتك" action={<Link href="/recommendations" className="text-xs text-cyan-300 hover:text-cyan-200">عرض التفاصيل</Link>} />{recommendations.isLoading ? <p className="mt-4 text-sm text-muted-foreground">جارٍ تحميل حالات UserTrade من Core…</p> : recommendations.isError ? <p className="mt-4 rounded-2xl border border-amber-300/20 bg-amber-300/[.05] p-4 text-sm text-amber-100">تعذر تحميل حالات المتداول الآن. لم نعرض أي بيانات بديلة.</p> : <div className="mt-4 grid gap-2 sm:grid-cols-2">{Object.entries(lifecycleCopy).map(([status, copy]) => <div key={status} className="rounded-2xl border border-white/8 bg-white/[.025] p-3"><div className="flex items-center justify-between gap-3"><p className={`text-sm font-semibold ${copy.className}`}>{copy.label}</p><span className="font-mono text-sm">{lifecycleCounts[status] ?? 0}</span></div><p className="mt-2 text-xs leading-5 text-muted-foreground">{copy.detail}</p></div>)}</div>}</article><article className="rounded-3xl border border-white/8 bg-card/70 p-5"><p className="text-xs font-semibold uppercase tracking-[.16em] text-cyan-300">Read Alerts</p><h2 className="mt-1 font-semibold">تنبيهات القراءة الحالية</h2>{attentionItems.length === 0 ? <p className="mt-4 rounded-2xl border border-dashed border-white/10 p-4 text-sm text-muted-foreground">لا توجد حالياً سجلات مفتوحة أو معلقة تحتاج متابعة.</p> : <div className="mt-4 space-y-2">{attentionItems.slice(0, 3).map(item => { const copy = lifecycleCopy[item.status] ?? lifecycleCopy.WATCHLIST; return <Link key={item.public_ref} href={`/recommendations?ref=${encodeURIComponent(item.public_ref)}`} className="block rounded-2xl border border-white/8 bg-white/[.025] p-3 transition hover:border-cyan-300/30"><div className="flex items-center justify-between gap-3"><p className="font-medium">{item.asset}</p><span className={`text-xs font-semibold ${copy.className}`}>{copy.label}</span></div><p className="mt-1 text-xs text-muted-foreground">{copy.detail}</p></Link>; })}</div>}</article></section>
      <section className="mt-6 rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Timeline" title="أحدث أحداثك من Core" action={<Link href="/recommendations" className="text-xs text-cyan-300 hover:text-cyan-200">سجل التوصيات</Link>} />{recentEvents.length === 0 ? <p className="mt-4 rounded-2xl border border-dashed border-white/10 p-5 text-sm text-muted-foreground">لا توجد أحداث متاحة في قراءة Core الحالية.</p> : <ol className="mt-4 space-y-2">{recentEvents.map(event => { const copy = userTradeEventCopy(event.event_type); return <li key={`${event.publicRef}-${event.event_type}-${event.event_timestamp}`} className="flex items-start justify-between gap-4 rounded-2xl border border-white/8 bg-white/[.025] p-3"><div><p className="text-sm font-medium">{copy.label} · {event.asset}</p><p className="mt-1 text-xs text-muted-foreground">{copy.source} · {copy.detail}</p><p className="mt-1 font-mono text-[11px] text-cyan-300">{event.displayRef}</p></div><time className="shrink-0 text-xs text-muted-foreground">{new Date(event.event_timestamp).toLocaleString("ar-SA")}</time></li>; })}</ol>}</section>
      <section className="mt-6 rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Positions" title="المراكز الشخصية الحية" action={<Link href="/recommendations" className="text-xs text-cyan-300 hover:text-cyan-200">كل الصفقات</Link>} />{positions.length === 0 ? <p className="mt-5 rounded-2xl border border-dashed border-white/10 p-5 text-sm text-muted-foreground">لا توجد مراكز مفتوحة في قراءة Core الحالية.</p> : <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[760px] text-right"><thead className="text-[11px] uppercase tracking-[.12em] text-muted-foreground"><tr><th className="pb-3 font-medium">المرجع</th><th className="pb-3 font-medium">الأصل</th><th className="pb-3 font-medium">الدخول</th><th className="pb-3 font-medium">السعر الحي</th><th className="pb-3 font-medium">المصدر</th><th className="pb-3 font-medium">الحالة</th><th className="pb-3 font-medium">الحماية</th><th className="pb-3 text-left font-medium">PnL حي</th></tr></thead><tbody>{positions.map(position => <tr key={position.id} className="border-t border-white/6 text-sm"><td className="py-4 font-mono text-xs text-slate-300">UT-{position.id}</td><td className="py-4"><span className="font-medium">{position.asset}</span><span className={position.side.toUpperCase() === "LONG" ? "mr-2 text-xs text-emerald-300" : "mr-2 text-xs text-rose-300"}>{position.side.toUpperCase()}</span></td><td className="py-4 text-muted-foreground">{position.entry}</td><td className="py-4 text-muted-foreground">{position.live_price ?? "غير متاح"}</td><td className="py-4 text-xs text-muted-foreground">{position.source_type}</td><td className="py-4"><StatusPill value={position.status}/></td><td className="py-4 text-xs text-muted-foreground">{position.protection?.active ? position.protection.mode : "غير مفعلة"}</td><td className={position.pnl_live_pct >= 0 ? "py-4 text-left font-medium text-emerald-300" : "py-4 text-left font-medium text-rose-300"}>{position.pnl_live_pct >= 0 ? "+" : ""}{position.pnl_live_pct.toFixed(2)}%</td></tr>)}</tbody></table></div>}</section>
      <section className="mt-6"><SectionTitle eyebrow="Live Recommendations" title="التوصيات المتصلة بمراكزك" />{positions.length === 0 ? <p className="mt-4 text-sm text-muted-foreground">لا توجد توصيات مفعلة لعرضها حالياً.</p> : <div className="mt-4 grid gap-4 lg:grid-cols-3">{positions.slice(0, 3).map(position => <article key={position.id} className="rounded-3xl border border-white/8 bg-card/70 p-5"><div className="flex items-start justify-between"><div><p className="font-mono text-[11px] text-cyan-300">UT-{position.id}</p><h3 className="mt-2 text-xl font-semibold">{position.asset}</h3></div><StatusPill value={position.status}/></div><p className={position.side.toUpperCase() === "LONG" ? "mt-4 text-sm text-emerald-300" : "mt-4 text-sm text-rose-300"}>{position.side.toUpperCase()} · {position.source_type}</p><div className="mt-5 grid grid-cols-2 gap-3 text-xs"><div className="rounded-xl bg-white/[.04] p-3"><p className="text-muted-foreground">Entry</p><p className="mt-1 font-medium">{position.entry}</p></div><div className="rounded-xl bg-white/[.04] p-3"><p className="text-muted-foreground">Stop</p><p className="mt-1 font-medium">{position.stop_loss}</p></div></div><p className="mt-4 text-xs text-muted-foreground">Targets: {position.targets.map(target => target.price).join(" · ") || "غير متاحة"}</p></article>)}</div>}</section>
    </> : null}
  </main></DashboardLayout>;
}
