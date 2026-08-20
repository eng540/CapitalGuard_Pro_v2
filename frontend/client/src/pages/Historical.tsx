import DashboardLayout from "@/components/DashboardLayout";
import { PreviewNotice, SectionTitle, StatusPill } from "@/components/finance-ui";
import { Button } from "@/components/ui/button";
import { previewBatches } from "@/lib/preview-data";
import { trpc } from "@/lib/trpc";
import { CheckCheck, FileClock, ShieldAlert, UploadCloud } from "lucide-react";

export default function Historical() {
  const batches = trpc.capitalguard.historicalBatches.useQuery();
  const rows = batches.data?.length ? batches.data : previewBatches;
  return <DashboardLayout><div dir="rtl" className="mx-auto max-w-[1380px]">
    <div className="mb-8 flex flex-col justify-between gap-4 lg:flex-row lg:items-end"><div><p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-violet-300">Historical Intelligence</p><h1 className="text-3xl font-semibold">الزمن ليس ملاحظة. إنه محرك القرار.</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">راجع المصدر، Temporal Decision، الاتساق المالي وبوابة إعادة اللعب قبل انتقال أي سجل إلى السمعة.</p></div><Button className="bg-violet-400 text-slate-950 hover:bg-violet-300"><UploadCloud className="ml-2 h-4 w-4"/>رفع Forward آمن</Button></div>
    <PreviewNotice />
    <div className="grid gap-4 md:grid-cols-3"><Mini icon={<FileClock/>} title="Temporal Router" detail="يفصل Live وHistorical وفق عمر المصدر وصلاحية السعر."/><Mini icon={<ShieldAlert/>} title="Financial Reconciliation" detail="يحجز تضارب PnL أو ترتيب الأحداث للمراجعة."/><Mini icon={<CheckCheck/>} title="Owner Review" detail="الاعتماد الصريح يسبق Evidence Ingestion دائمًا."/></div>
    <section className="mt-6 rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Review Queue" title="دفعات Forward التاريخية"/>
      <div className="grid gap-3 md:hidden">{rows.map((batch: any) => <BatchCard key={batch.ref ?? batch.publicRef} batch={batch}/>)}</div>
      <div className="hidden overflow-x-auto md:block"><table className="w-full min-w-[850px] text-right"><thead className="text-[11px] uppercase tracking-[.12em] text-muted-foreground"><tr><th className="pb-3">الدفعة</th><th className="pb-3">المصدر</th><th className="pb-3">السجلات</th><th className="pb-3">Temporal</th><th className="pb-3">Financial Outcome</th><th className="pb-3">Replay Gate</th><th className="pb-3">الإجراء</th></tr></thead><tbody>{rows.map((batch: any) => <tr key={batch.ref ?? batch.publicRef} className="border-t border-white/6 text-sm"><td className="py-4 font-mono text-xs text-violet-200">{batch.ref ?? batch.publicRef}</td><td className="py-4 font-medium">{batch.source ?? "Channel"}</td><td className="py-4 text-muted-foreground">{batch.accepted ?? batch.acceptedRecords} قبول · {batch.rejected ?? batch.rejectedRecords} رفض</td><td className="py-4"><StatusPill value={batch.temporal ?? batch.temporalMode ?? "PENDING"}/></td><td className="py-4"><StatusPill value={batch.outcome ?? batch.financialOutcome ?? "NOT_PARSED"}/></td><td className="py-4"><StatusPill value={batch.gate ?? batch.replayGate ?? batch.status}/></td><td className="py-4"><Button size="sm" variant="outline" className="border-white/10 bg-white/[.03]">فتح المراجعة</Button></td></tr>)}</tbody></table></div>
    </section>
  </div></DashboardLayout>;
}

function BatchCard({ batch }: { batch: any }) {
  const accepted = batch.accepted ?? batch.acceptedRecords;
  const rejected = batch.rejected ?? batch.rejectedRecords;
  return <article className="rounded-2xl border border-white/7 bg-white/[.025] p-4"><div className="flex items-start justify-between gap-3"><div><p className="font-mono text-[11px] text-violet-200">{batch.ref ?? batch.publicRef}</p><p className="mt-1 text-sm font-medium">{batch.source ?? "Channel"}</p></div><StatusPill value={batch.gate ?? batch.replayGate ?? batch.status}/></div><p className="mt-3 text-xs text-muted-foreground">{accepted} قبول · {rejected} رفض</p><div className="mt-3 flex flex-wrap gap-2"><StatusPill value={batch.temporal ?? batch.temporalMode ?? "PENDING"}/><StatusPill value={batch.outcome ?? batch.financialOutcome ?? "NOT_PARSED"}/></div><Button size="sm" variant="outline" className="mt-4 w-full border-white/10 bg-white/[.03]">فتح سجل المراجعة</Button></article>;
}
function Mini({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) { return <div className="rounded-2xl border border-white/8 bg-card/70 p-5"><span className="inline-flex rounded-xl bg-violet-400/10 p-2 text-violet-300">{icon}</span><h3 className="mt-4 font-semibold">{title}</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">{detail}</p></div>; }
