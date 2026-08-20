import DashboardLayout from "@/components/DashboardLayout";
import { KpiCard, PreviewNotice, SectionTitle, StatusPill } from "@/components/finance-ui";
import { Button } from "@/components/ui/button";
import { previewPortfolio, previewRecommendations, previewTrades, pnlSeries } from "@/lib/preview-data";
import { trpc } from "@/lib/trpc";
import LiveSignalStatus from "@/components/LiveSignalStatus";
import CoreConnectionStatus from "@/components/CoreConnectionStatus";
import { Activity, ArrowDownLeft, ArrowUpRight, BarChart3, ChevronLeft, CircleDollarSign, WalletCards } from "lucide-react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Link } from "wouter";

export default function Workspace() {
  const liveSnapshot = trpc.capitalguard.core.traderSnapshot.useQuery(undefined, {
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
  });
  const hasCoreReadModel = liveSnapshot.data?.ok === true;
  const positions = liveSnapshot.data?.portfolio.positions ?? [];
  const performance = liveSnapshot.data?.performance as Record<string, unknown> | undefined;
  const funnel = liveSnapshot.data?.funnel as Record<string, unknown> | undefined;
  const rows = hasCoreReadModel
    ? positions.map(position => ({
      id: `REC-${position.id}`,
      asset: position.asset,
      side: position.side.toLowerCase(),
      entry: position.entry,
      source: position.source_type,
      status: position.status,
      pnl: `${position.pnl_live_pct >= 0 ? "+" : ""}${position.pnl_live_pct.toFixed(2)}%`,
    }))
    : previewTrades;
  const cards = hasCoreReadModel
    ? positions.map(position => ({
      ref: `REC-${position.id}`,
      asset: position.asset,
      side: position.side.toLowerCase(),
      source: position.source_type,
      status: position.status,
      entry: position.entry,
      stop: position.stop_loss,
      targets: position.targets.map(target => target.price).join(" · "),
    }))
    : previewRecommendations;
  const kpis = hasCoreReadModel
    ? {
      openPositions: String(liveSnapshot.data?.portfolio.open_position_count ?? 0),
      totalPnl: String(performance?.total_pnl_pct ?? "—"),
      winRate: String(performance?.win_rate_pct ?? "—"),
      activated: String(funnel?.activated ?? "—"),
    }
    : {
      openPositions: String(previewPortfolio.totalEquity),
      totalPnl: `+${previewPortfolio.realizedPnl}`,
      winRate: `+${previewPortfolio.unrealizedPnl}`,
      activated: String(previewPortfolio.availableBalance),
    };
  return <DashboardLayout>
    <div dir="rtl" className="mx-auto max-w-[1500px]">
      <div className="mb-8 flex flex-col justify-between gap-5 lg:flex-row lg:items-end"><div><p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-cyan-300">Control Center</p><h1 className="text-3xl font-semibold tracking-tight">صباح التداول، راقب الصورة كاملة.</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">المحفظة، المخاطر، التوصيات ودورة الأحداث في مساحة واحدة مفهومة من أول نظرة.</p></div><div className="flex gap-3"><Button variant="outline" className="border-white/10 bg-white/[.03]" asChild><Link href="/risk">حاسبة المخاطر</Link></Button><Button className="bg-cyan-400 text-slate-950 hover:bg-cyan-300" asChild><Link href="/recommendations">استكشاف التوصيات <ChevronLeft className="mr-1 h-4 w-4" /></Link></Button></div></div>
      <PreviewNotice isLive={hasCoreReadModel} />
      <CoreConnectionStatus />
      <LiveSignalStatus />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><KpiCard label={hasCoreReadModel ? "مراكز مفتوحة" : "إجمالي القيمة"} value={hasCoreReadModel ? kpis.openPositions : `${Number(kpis.openPositions).toLocaleString()} ${previewPortfolio.currency}`} change={hasCoreReadModel ? "من Core في هذه اللحظة" : "صافي المحفظة بعد المراكز المفتوحة"} icon={<WalletCards className="h-4 w-4" />} /><KpiCard label={hasCoreReadModel ? "PnL المحفظة المفعلة" : "PnL المحقق"} value={kpis.totalPnl} change={hasCoreReadModel ? "Activated Portfolio Only" : "آخر 30 يومًا"} icon={<ArrowUpRight className="h-4 w-4" />} tone="emerald" /><KpiCard label={hasCoreReadModel ? "Win Rate" : "PnL غير المحقق"} value={kpis.winRate} change={hasCoreReadModel ? "حسب الصفقات المفعلة المغلقة" : "يُحدّث عند ربط مصدر السوق"} icon={<Activity className="h-4 w-4" />} tone="cyan" /><KpiCard label={hasCoreReadModel ? "صفقات مفعلة" : "الرصيد المتاح"} value={kpis.activated} change={hasCoreReadModel ? "من Funnel دورة الحياة" : "جاهز لإدارة المخاطر"} icon={<CircleDollarSign className="h-4 w-4" />} tone="violet" /></div>
      {hasCoreReadModel ? <p className="mt-3 text-xs text-cyan-200">تم التحديث من Core عند {liveSnapshot.data?.as_of ? new Date(liveSnapshot.data.as_of).toLocaleTimeString("ar-SA") : "الآن"}؛ لا تحفظ المنصة هذه البيانات المالية محلياً.</p> : liveSnapshot.isError ? <p className="mt-3 text-xs text-amber-200">تعذر جلب قراءة Core الحية؛ بقيت بيانات العرض ظاهرة بوضوح حتى تتعافى الخدمة.</p> : null}
      <div className="mt-6 grid gap-6 xl:grid-cols-[1.55fr_.9fr]"><section className="panel-glow rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Performance" title="منحنى PnL الأسبوعي" action={<span className="text-xs text-emerald-300">+1.84% هذا الأسبوع</span>} /><div className="h-[290px]" dir="ltr"><ResponsiveContainer width="100%" height="100%"><AreaChart data={pnlSeries}><defs><linearGradient id="pnl" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#22d3ee" stopOpacity={0.35}/><stop offset="100%" stopColor="#22d3ee" stopOpacity={0}/></linearGradient></defs><CartesianGrid vertical={false} stroke="#ffffff10"/><XAxis dataKey="day" tickLine={false} axisLine={false} tick={{ fill: "#8190ad", fontSize: 11 }}/><YAxis hide domain={[-100, 400]}/><Tooltip contentStyle={{ background: "#121a2d", border: "1px solid #ffffff18", borderRadius: 14 }} labelStyle={{ color: "#dce8ff" }}/><Area type="monotone" dataKey="pnl" stroke="#22d3ee" strokeWidth={2.4} fill="url(#pnl)" /></AreaChart></ResponsiveContainer></div></section>
      <section className="rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Signal Health" title="نبض السوق"/><div className="space-y-4"><div className="rounded-2xl border border-white/8 bg-white/[.025] p-4"><div className="flex items-center justify-between"><p className="text-sm font-medium">حالة التدفق</p><StatusPill value="LIVE REVIEW SAFE" /></div><p className="mt-3 text-xs leading-5 text-muted-foreground">لا إنشاء تلقائي لتوصية أو صفقة. كل إشارة حديثة تمر بمراجعة صريحة.</p></div><div className="rounded-2xl border border-white/8 bg-white/[.025] p-4"><div className="flex items-center justify-between"><p className="text-sm font-medium">Temporal Decisioning</p><span className="font-mono text-xs text-cyan-300">0 LEAKS</span></div><div className="mt-3 h-2 overflow-hidden rounded-full bg-white/5"><div className="h-full w-[76%] rounded-full bg-gradient-to-r from-cyan-400 to-violet-400"/></div><p className="mt-2 text-xs text-muted-foreground">المسارات الحية والتاريخية معزولة حسب الزمن والسعر.</p></div><Link href="/historical" className="flex items-center justify-between rounded-2xl bg-gradient-to-l from-violet-400/15 to-cyan-400/10 p-4 transition hover:brightness-110"><span className="text-sm font-medium">مراجعة الدفعات التاريخية</span><ChevronLeft className="h-4 w-4 text-cyan-300"/></Link></div></section></div>
      <section className="mt-6 rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Positions" title="الصفقات الشخصية" action={<Link href="/recommendations" className="text-xs text-cyan-300 hover:text-cyan-200">كل الصفقات</Link>} /><div className="overflow-x-auto"><table className="w-full min-w-[680px] text-right"><thead className="text-[11px] uppercase tracking-[.12em] text-muted-foreground"><tr><th className="pb-3 font-medium">الصفقة</th><th className="pb-3 font-medium">الأصل</th><th className="pb-3 font-medium">الدخول</th><th className="pb-3 font-medium">المصدر</th><th className="pb-3 font-medium">الحالة</th><th className="pb-3 text-left font-medium">PnL</th></tr></thead><tbody>{rows.map((trade: any) => <tr key={trade.id ?? trade.publicRef} className="border-t border-white/6 text-sm"><td className="py-4 font-mono text-xs text-slate-300">{trade.id ?? trade.publicRef}</td><td className="py-4"><span className="font-medium">{trade.asset}</span><span className={trade.side === "long" ? "mr-2 text-xs text-emerald-300" : "mr-2 text-xs text-rose-300"}>{trade.side?.toUpperCase()}</span></td><td className="py-4 text-muted-foreground">{trade.entry}</td><td className="py-4 text-xs text-muted-foreground">{trade.source ?? trade.sourceType}</td><td className="py-4"><StatusPill value={trade.status}/></td><td className="py-4 text-left font-medium text-emerald-300">{trade.pnl ?? trade.realizedPnl}</td></tr>)}</tbody></table></div></section>
      <section className="mt-6"><SectionTitle eyebrow="Live Recommendations" title="التوصيات التي تتابعها"/><div className="grid gap-4 lg:grid-cols-3">{cards.slice(0,3).map((rec: any) => <article key={rec.ref ?? rec.publicRef} className="rounded-3xl border border-white/8 bg-card/70 p-5 transition hover:-translate-y-1 hover:border-cyan-400/25"><div className="flex items-start justify-between"><div><p className="font-mono text-[11px] text-cyan-300">{rec.ref ?? rec.publicRef}</p><h3 className="mt-2 text-xl font-semibold">{rec.asset}</h3></div><StatusPill value={rec.status}/></div><p className={rec.side === "long" ? "mt-4 text-sm text-emerald-300" : "mt-4 text-sm text-rose-300"}>{rec.side?.toUpperCase()} · {rec.source ?? "Core"}</p><div className="mt-5 grid grid-cols-2 gap-3 text-xs"><div className="rounded-xl bg-white/[.04] p-3"><p className="text-muted-foreground">Entry</p><p className="mt-1 font-medium">{rec.entry}</p></div><div className="rounded-xl bg-white/[.04] p-3"><p className="text-muted-foreground">Stop</p><p className="mt-1 font-medium">{rec.stop ?? rec.stopLoss}</p></div></div><p className="mt-4 text-xs text-muted-foreground">Targets: {rec.targets}</p></article>)}</div></section>
    </div>
  </DashboardLayout>;
}
