import DashboardLayout from "@/components/DashboardLayout";
import { KpiCard, PreviewNotice, SectionTitle, StatusPill } from "@/components/finance-ui";
import { Button } from "@/components/ui/button";
import { trpc } from "@/lib/trpc";
import { BadgeCheck, DatabaseZap, ShieldCheck, UsersRound } from "lucide-react";
import { toast } from "sonner";

type ReviewBatch = { id: number; ref: string; status: string; source_kind: string; total_records: number; accepted_records: number; rejected_records: number };

export default function Admin() {
  const overview = trpc.capitalguard.admin.overview.useQuery();
  const batches = trpc.capitalguard.admin.historicalReviewBatches.useQuery();
  const operations = trpc.capitalguard.admin.operationsFeed.useQuery(undefined, { refetchInterval: 15_000 });
  const review = trpc.capitalguard.admin.reviewHistoricalBatch.useMutation({
    onSuccess: result => { toast.success(`تم تسجيل القرار: ${String(result.status ?? "COMPLETED")}`); void batches.refetch(); },
    onError: () => toast.error("رفض Core الأمر أو تعذر تنفيذ المراجعة."),
  });
  const ingest = trpc.capitalguard.admin.ingestHistoricalEvidence.useMutation({
    onSuccess: result => { toast.success(`اكتمل إدخال Evidence: ${String(result.ingested ?? 0)} سجل.`); void batches.refetch(); },
    onError: () => toast.error("لم يكتمل إدخال Evidence. بقيت البيانات الحية دون تغيير."),
  });
  const values = overview.data ?? { users: 0, channels: 0, pendingReviews: 0, connection: "pending" };
  const liveQueue = batches.data ?? [];
  const queue: ReviewBatch[] = liveQueue;
  const hasLiveConnection = batches.isSuccess;
  const confirmReview = (batch: ReviewBatch, approved: boolean) => {
    const verb = approved ? "اعتماد" : "رفض";
    if (window.confirm(`${verb} الدفعة ${batch.ref}؟ القرار نهائي في Core ويسجل في سجل التدقيق.`)) review.mutate({ batchId: batch.id, approved, note: `Owner review from CapitalGuard Web (${verb})` });
  };
  const confirmIngestion = (batch: ReviewBatch) => {
    if (window.confirm(`بدء إدخال Evidence للدفعة ${batch.ref}؟ لن يتم إنشاء توصيات أو صفقات حية.`)) ingest.mutate({ batchId: batch.id });
  };
  return <DashboardLayout><div dir="rtl" className="mx-auto max-w-[1380px]">
    <div className="mb-8"><p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-amber-300">Operations Center</p><h1 className="text-3xl font-semibold">إدارة موثقة، لا تدخلات صامتة.</h1><p className="mt-2 text-sm text-muted-foreground">صلاحيات، مصادر، Owner Review وEvidence Ingestion ضمن مسار تدقيق واحد.</p></div>
    <PreviewNotice isLive={hasLiveConnection} />
    <div className="grid gap-4 md:grid-cols-4"><KpiCard label="المستخدمون" value={String(values.users)} icon={<UsersRound className="h-4 w-4"/>} tone="cyan"/><KpiCard label="القنوات" value={String(values.channels)} icon={<DatabaseZap className="h-4 w-4"/>} tone="violet"/><KpiCard label="مراجعات معلّقة" value={String(liveQueue.filter(batch => ["DRY_RUN", "REVIEW_REQUIRED"].includes(batch.status)).length)} icon={<ShieldCheck className="h-4 w-4"/>} tone="amber"/><KpiCard label="حالة الاتصال" value={batches.isSuccess ? "Ready" : batches.isError ? "Error" : "Pending"} icon={<BadgeCheck className="h-4 w-4"/>} tone="emerald"/></div>
    <section className="mt-6 rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Owner Review" title="طابور الاعتماد"/>{batches.isLoading ? <p className="py-8 text-sm text-muted-foreground">جارٍ تحميل طابور Core…</p> : batches.isError ? <p className="rounded-2xl border border-rose-400/20 bg-rose-400/5 p-4 text-sm text-rose-100">تعذر جلب طابور Core الحي. لم تُعرض أي بيانات بديلة؛ راجع اتصال API وصلاحية المالك ثم أعد المحاولة.</p> : queue.length === 0 ? <p className="rounded-2xl border border-white/8 bg-white/[.025] p-4 text-sm text-muted-foreground">لا توجد دفعات تاريخية مؤهلة للمراجعة حالياً.</p> : <div className="space-y-3">{queue.map(batch => <div key={batch.ref} className="grid gap-3 rounded-2xl border border-white/7 bg-white/[.025] p-4 lg:grid-cols-[1.1fr_.9fr_.9fr_1fr_auto] lg:items-center"><div><p className="font-mono text-xs text-amber-200">{batch.ref}</p><p className="mt-1 text-sm font-medium">{batch.source_kind}</p><p className="mt-1 text-xs text-muted-foreground">مقبول {batch.accepted_records} · مرفوض {batch.rejected_records} · إجمالي {batch.total_records}</p></div><StatusPill value={batch.status}/><span className="text-xs text-muted-foreground">Historical only</span><span className="text-xs text-cyan-200">لا توصية حية ولا UserTrade</span><div className="flex flex-wrap gap-2">{["DRY_RUN", "REVIEW_REQUIRED"].includes(batch.status) ? <><Button size="sm" className="bg-emerald-400 text-slate-950 hover:bg-emerald-300" disabled={review.isPending} onClick={() => confirmReview(batch, true)}>اعتماد</Button><Button size="sm" variant="outline" className="border-rose-400/25 text-rose-200" disabled={review.isPending} onClick={() => confirmReview(batch, false)}>رفض</Button></> : null}{batch.status === "VALIDATED" ? <Button size="sm" className="bg-cyan-400 text-slate-950 hover:bg-cyan-300" disabled={ingest.isPending} onClick={() => confirmIngestion(batch)}>إدخال Evidence</Button> : null}</div></div>)}</div>}</section>
    <section className="mt-6 rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Operations Feed" title="سجل الأحداث التشغيلي"/><div className="mb-4 grid gap-3 sm:grid-cols-3"><div className="rounded-xl bg-rose-400/10 p-3 text-sm text-rose-100">حرج: {operations.data?.summary.critical ?? 0}</div><div className="rounded-xl bg-amber-400/10 p-3 text-sm text-amber-100">تحذير: {operations.data?.summary.warning ?? 0}</div><div className="rounded-xl bg-cyan-400/10 p-3 text-sm text-cyan-100">آخر 100 حدث: {operations.data?.summary.total ?? 0}</div></div>{operations.isLoading ? <p className="py-4 text-sm text-muted-foreground">جارٍ تحميل أحداث Core…</p> : operations.isError ? <p className="rounded-2xl border border-rose-400/20 bg-rose-400/5 p-4 text-sm text-rose-100">تعذر جلب سجل الأحداث من Core. لا تُعرض بيانات بديلة.</p> : (operations.data?.events.length ?? 0) === 0 ? <p className="rounded-2xl border border-white/8 bg-white/[.025] p-4 text-sm text-muted-foreground">لا توجد أحداث تشغيلية حديثة. ستحدّث هذه المنطقة تلقائياً كل 15 ثانية أثناء فتح الصفحة.</p> : <div className="space-y-2">{operations.data?.events.map(event => <div key={event.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/8 bg-white/[.025] px-3 py-2 text-xs"><div className="flex items-center gap-2"><span className={event.severity === "critical" ? "text-rose-200" : event.severity === "warning" ? "text-amber-200" : "text-cyan-200"}>{event.severity.toUpperCase()}</span><span>{event.category} · {event.code}</span></div><div className="flex items-center gap-3 text-muted-foreground"><span className="font-mono">{event.record_ref}</span><span>{new Date(event.occurred_at).toLocaleString("ar-SA")}</span></div></div>)}</div>}</section>
    <section className="mt-6 grid gap-4 lg:grid-cols-3"><Rule title="دور المدير" detail="تعتمد الدفعات عبر جلسة Telegram للمالك، ثم يعيد Core فحص هوية المالك ومفتاح الخدمة."/><Rule title="Evidence Ingestion" detail="يبدأ بعد Owner Review؛ يستخدم مفتاح idempotency وسجل تدقيق ولا يحول السجل إلى توصية حية."/><Rule title="حماية السمعة" detail="Financial Mismatch وTimeline Conflict لا يصبحان ranking-eligible تلقائيًا."/></section>
  </div></DashboardLayout>;
}

function Rule({ title, detail }: { title: string; detail: string }) { return <div className="rounded-2xl border border-white/8 bg-white/[.025] p-5"><h3 className="font-semibold">{title}</h3><p className="mt-3 text-xs leading-5 text-muted-foreground">{detail}</p></div>; }
