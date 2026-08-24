import DashboardLayout from "@/components/DashboardLayout";
import { KpiCard, SectionTitle, StatusPill } from "@/components/finance-ui";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { trpc } from "@/lib/trpc";
import { Calculator, Gauge, ShieldCheck, Target, Wallet } from "lucide-react";
import { useState } from "react";

export default function RiskStudio() {
  const [capital, setCapital] = useState("10000");
  const [risk, setRisk] = useState("1");
  const [riskAmount, setRiskAmount] = useState("");
  const [entry, setEntry] = useState("70000");
  const [stop, setStop] = useState("69500");
  const [leverage, setLeverage] = useState("1");
  const [side, setSide] = useState<"long" | "short">("long");
  const planner = trpc.capitalguard.riskPlan.useMutation();
  const snapshot = trpc.capitalguard.core.traderSnapshot.useQuery(undefined, { refetchInterval: 15_000, refetchOnWindowFocus: true });
  const plan = planner.data;
  const positions = snapshot.data?.ok === true ? snapshot.data.portfolio.positions : [];
  const totalExposure = positions.reduce((sum, position) => sum + Math.abs(position.entry * (position.open_size_percent ?? 100) / 100), 0);
  const run = () => planner.mutate({ capital: Number(capital), riskPercent: Number(risk), riskAmount: riskAmount.trim() ? Number(riskAmount) : undefined, entry: Number(entry), stop: Number(stop), side, leverage: Number(leverage) });

  return <DashboardLayout><div dir="rtl" className="mx-auto max-w-[1280px]">
    <div className="mb-8"><p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-cyan-300">Risk Studio</p><h1 className="text-3xl font-semibold">احسب المخاطرة قبل أن تفتح المركز.</h1><p className="mt-2 max-w-3xl text-sm text-muted-foreground">محرك حساب مستقل؛ لا ينفذ صفقة ولا يرسل أوامر لأي وسيط. بيانات المراكز الحالية، إن توفرت، تأتي من Core فقط.</p></div>
    <div className="grid gap-6 lg:grid-cols-[.9fr_1.1fr]">
      <section className="rounded-3xl border border-white/8 bg-card/70 p-6"><SectionTitle title="مدخلات الخطة"/><div className="grid gap-4 sm:grid-cols-2"><Field label="رأس المال" value={capital} onChange={setCapital}/><Field label="المخاطرة % (fallback)" value={risk} onChange={setRisk}/><Field label="مبلغ المخاطرة المباشر" value={riskAmount} onChange={setRiskAmount}/><Field label="سعر الدخول" value={entry} onChange={setEntry}/><Field label="وقف الخسارة" value={stop} onChange={setStop}/><Field label="الرافعة" value={leverage} onChange={setLeverage}/><div className="space-y-2"><Label>الاتجاه</Label><Select value={side} onValueChange={(value: "long" | "short") => setSide(value)}><SelectTrigger className="border-white/10 bg-white/[.03]"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="long">LONG</SelectItem><SelectItem value="short">SHORT</SelectItem></SelectContent></Select></div></div><Button onClick={run} disabled={planner.isPending} className="mt-6 w-full bg-cyan-400 text-slate-950 hover:bg-cyan-300"><Calculator className="ml-2 h-4 w-4"/>{planner.isPending ? "جاري الحساب" : "احسب الخطة"}</Button></section>
      <section className="rounded-3xl border border-white/8 bg-card/70 p-6"><SectionTitle eyebrow="What-If" title="خطة حجم المركز"/><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><KpiCard label="المخاطرة القصوى" value={`${plan?.riskAmount ?? "—"} USDT`} icon={<ShieldCheck className="h-4 w-4"/>} tone="amber"/><KpiCard label="حجم المركز" value={String(plan?.quantity ?? "—")} icon={<Target className="h-4 w-4"/>}/><KpiCard label="القيمة الاسمية" value={`${plan?.notional ?? "—"} USDT`} icon={<Wallet className="h-4 w-4"/>} tone="violet"/><KpiCard label="الهامش التقريبي" value={`${plan?.marginRequired ?? "—"} USDT`} icon={<Gauge className="h-4 w-4"/>} tone="cyan"/></div>{plan && !plan.valid ? <p className="mt-5 rounded-xl border border-rose-400/20 bg-rose-400/10 p-4 text-sm text-rose-200">اتجاه وقف الخسارة غير صالح لهذه الجهة. لا يمكن إنشاء خطة مخاطرة.</p> : <p className="mt-6 rounded-xl border border-white/8 bg-white/[.03] p-4 text-sm leading-6 text-muted-foreground">{plan?.liquidationBufferNote === "HIGH_LEVERAGE_REVIEW_REQUIRED" ? "الرافعة مرتفعة؛ راجع هامش التصفية والرسوم يدويًا قبل أي قرار." : "تُعرض النتائج كمساعدة قرار فقط. تحقق من السيولة والرسوم والرافعة قبل أي تنفيذ يدوي."}</p>}</section>
    </div>
    <section className="mt-6 rounded-3xl border border-white/8 bg-card/70 p-6"><SectionTitle eyebrow="Core Exposure Heatmap" title="التعرّض الحالي للمراكز" action={<span className="text-xs text-muted-foreground">إجمالي تقريبي: {totalExposure.toLocaleString()} USDT</span>}/>{snapshot.isLoading ? <p className="mt-4 text-sm text-muted-foreground">جارٍ تحميل التعرّض من Core…</p> : snapshot.isError ? <p className="mt-4 rounded-2xl border border-amber-300/20 bg-amber-300/5 p-4 text-sm text-amber-100">تعذر تحميل heatmap الحالية. لم تُعرض بيانات بديلة.</p> : positions.length === 0 ? <p className="mt-4 rounded-2xl border border-dashed border-white/10 p-4 text-sm text-muted-foreground">لا توجد مراكز مفتوحة في قراءة Core الحالية.</p> : <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">{positions.map(position => { const exposure = Math.abs(position.entry * (position.open_size_percent ?? 100) / 100); const share = totalExposure > 0 ? exposure / totalExposure * 100 : 0; return <article key={position.id} className="rounded-2xl border border-white/8 bg-white/[.025] p-4"><div className="flex items-center justify-between gap-3"><div><p className="font-semibold">{position.asset}</p><p className="mt-1 text-xs text-muted-foreground">{position.side.toUpperCase()} · {position.market}</p></div><StatusPill value={position.status}/></div><div className="mt-4 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-cyan-300" style={{ width: `${Math.min(100, share)}%` }}/></div><div className="mt-2 flex justify-between text-xs text-muted-foreground"><span>{exposure.toLocaleString()} USDT</span><span>{share.toFixed(1)}%</span></div><p className="mt-3 text-xs text-muted-foreground">الحماية: {position.protection?.active ? `${position.protection.mode} مفعلة` : "غير مفعلة"} · PnL {position.pnl_live_pct.toFixed(2)}%</p></article>; })}</div>}</section>
  </div></DashboardLayout>;
}
function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) { return <div className="space-y-2"><Label>{label}</Label><Input value={value} onChange={event => onChange(event.target.value)} inputMode="decimal" className="border-white/10 bg-white/[.03]" /></div>; }
