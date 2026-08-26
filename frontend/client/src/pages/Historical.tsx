import DashboardLayout from "@/components/DashboardLayout";
import React from "react";
import { SectionTitle, StatusPill } from "@/components/finance-ui";
import { Button } from "@/components/ui/button";
import { trpc } from "@/lib/trpc";
import { CheckCheck, ClipboardPaste, FileClock, Layers3, ShieldAlert, Upload, RefreshCw } from "lucide-react";
import { toast } from "sonner";

type IntakeItem = {
  itemKey?: string;
  rawText?: string;
  sourceChatId?: number;
  sourceMessageId?: number;
  sourceMessageRevision?: number;
  sourceMessageTimestamp?: string;
  sourceReplyToMessageId?: number;
  sourceUri?: string;
  sourceOriginType?: string;
  relatedItemKey?: string;
};

type ExtractedItem = {
  id: number;
  order: number | null;
  item_key: string | null;
  status: string;
  semantic_status: string;
  parse_status: string | null;
  source_verification: string;
  source_timestamp: string | null;
  raw_text: string | null;
  missing_fields: string[];
  conflicting_fields: string[];
  canonical: Record<string, unknown>;
  rejection_reason: string | null;
  metadata?: { input_mode?: string | null; source_uri?: string | null; media?: Record<string, unknown> | null; related_item_key?: string | null };
};

type IntakeReport = {
  counts: Record<string, number>;
  readiness: Record<string, boolean>;
  next_action: string;
  signals?: Array<Record<string, unknown>>;
};

export type IntakeMode = "PASTE" | "UPLOAD" | "TELEGRAM_EXPORT";
type SourceKind = "TELEGRAM_EXPORT" | "MANUAL_ADMIN_IMPORT";

export function parseIntakeText(value: string, mode: IntakeMode): { sourceKind: SourceKind; items: IntakeItem[] } {
  const text = value.trim();
  if (!text) throw new Error("أدخل محتوى واحدًا على الأقل.");

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = null;
  }

  const exportMessages = (payload: Record<string, unknown>): IntakeItem[] => {
    const records = Array.isArray(payload.records) ? payload.records : Array.isArray(payload.messages) ? payload.messages : [];
    return records.filter(record => record && typeof record === "object").map((record, index) => {
      const row = record as Record<string, unknown>;
      const rawText = typeof row.raw_text === "string" ? row.raw_text : typeof row.text === "string" ? row.text : typeof row.caption === "string" ? row.caption : undefined;
      const sourceMessageId = Number(row.telegram_message_id ?? row.id);
      const sourceChatId = Number(row.telegram_channel_id ?? payload.id);
      const reply = Number(row.reply_to_message_id ?? row.reply_to);
      return {
        itemKey: String(row.item_key ?? row.id ?? `message-${index + 1}`),
        rawText,
        sourceChatId: Number.isSafeInteger(sourceChatId) ? sourceChatId : undefined,
        sourceMessageId: Number.isSafeInteger(sourceMessageId) ? sourceMessageId : undefined,
        sourceMessageTimestamp: typeof row.message_timestamp === "string" ? row.message_timestamp : typeof row.date === "string" ? row.date : undefined,
        sourceReplyToMessageId: Number.isSafeInteger(reply) ? reply : undefined,
        sourceUri: typeof row.source_uri === "string" ? row.source_uri : undefined,
        sourceOriginType: "TELEGRAM_EXPORT",
      };
    });
  };

  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    const items = exportMessages(parsed as Record<string, unknown>);
    if (items.length > 0) return { sourceKind: "TELEGRAM_EXPORT", items };
    const row = parsed as Record<string, unknown>;
    return { sourceKind: "MANUAL_ADMIN_IMPORT", items: [{ itemKey: "item-1", rawText: typeof row.raw_text === "string" ? row.raw_text : text, sourceOriginType: "WEB_PASTE" }] };
  }
  if (Array.isArray(parsed)) {
    const items = parsed.map((row, index) => {
      if (typeof row === "string") return { itemKey: `item-${index + 1}`, rawText: row, sourceOriginType: "WEB_PASTE" };
      if (row && typeof row === "object") {
        const item = row as Record<string, unknown>;
        return { itemKey: String(item.item_key ?? item.id ?? `item-${index + 1}`), rawText: typeof item.raw_text === "string" ? item.raw_text : typeof item.text === "string" ? item.text : undefined, sourceOriginType: "WEB_UPLOAD" };
      }
      return { itemKey: `item-${index + 1}`, rawText: String(row), sourceOriginType: "WEB_UPLOAD" };
    }).filter(item => Boolean(item.rawText));
    if (items.length > 0) return { sourceKind: mode === "TELEGRAM_EXPORT" ? "TELEGRAM_EXPORT" : "MANUAL_ADMIN_IMPORT", items };
  }

  const chunks = text.split(/\n\s*---\s*\n/g).map(item => item.trim()).filter(Boolean);
  return { sourceKind: "MANUAL_ADMIN_IMPORT", items: chunks.map((rawText, index) => ({ itemKey: `item-${index + 1}`, rawText, sourceOriginType: "WEB_PASTE" })) };
}

export default function Historical() {
  const historical = trpc.capitalguard.historicalWallet.useQuery();
  const [inputMode, setInputMode] = React.useState<IntakeMode>("PASTE");
  const [rawInput, setRawInput] = React.useState("");
  const [batchLabel, setBatchLabel] = React.useState("");
  const [isPartial, setIsPartial] = React.useState(false);
  const [batchId, setBatchId] = React.useState<number | null>(null);
  const intakeList = trpc.capitalguard.historicalIntakeList.useQuery(undefined, { refetchInterval: 10_000 });
  const createIntake = trpc.capitalguard.historicalIntake.useMutation({
    onSuccess: result => { setBatchId(result.batch.id); void intakeList.refetch(); toast.success(`تم استقبال الدفعة ${result.batch.ref}: ${result.batch.accepted_records} عنصر قابل للمراجعة.`); },
    onError: error => toast.error(error.message || "تعذر استقبال المحتوى التاريخي."),
  });
  const intake = trpc.capitalguard.historicalIntakeDetail.useQuery({ batchId: batchId ?? 1 }, { enabled: batchId !== null, refetchInterval: batchId !== null ? 5_000 : false });
  const intakeReport = trpc.capitalguard.historicalIntakeReport.useQuery({ batchId: batchId ?? 1 }, { enabled: batchId !== null, refetchInterval: batchId !== null ? 5_000 : false });
  const rows = historical.data?.items ?? [];
  const intakeBatch = intake.data?.batch;

  const handleFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setRawInput(await file.text());
    setInputMode(file.name.toLowerCase().endsWith(".json") ? "TELEGRAM_EXPORT" : "UPLOAD");
  };

  const submitIntake = (event: React.FormEvent) => {
    event.preventDefault();
    try {
      const { sourceKind, items } = parseIntakeText(rawInput, inputMode);
      createIntake.mutate({ sourceKind, inputMode, items, isPartial, batchLabel: batchLabel.trim() || undefined });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "صيغة الإدخال غير صالحة.");
    }
  };

  return <DashboardLayout><div dir="rtl" className="mx-auto max-w-[1380px]">
    <div className="mb-8"><p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-violet-300">Historical Intelligence</p><h1 className="text-3xl font-semibold">الزمن ليس ملاحظة. إنه محرك القرار.</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">أدخل رسالة واحدة أو رسائل متعددة أو دفعة كاملة. يحتفظ النظام بمصدر كل عنصر وترتيبه وحالته، ثم يمرره إلى التحليل والمراجعة التاريخية دون إنشاء توصية أو صفقة حية.</p></div>
    <div className="grid gap-4 md:grid-cols-3"><Mini icon={<FileClock/>} title="Temporal Router" detail="يفصل Live وHistorical وفق عمر المصدر وصلاحية السعر."/><Mini icon={<Layers3/>} title="Batch 1..N" detail="يحافظ على هوية كل رسالة وترتيبها وحالتها داخل الدفعة."/><Mini icon={<ShieldAlert/>} title="Financial Reconciliation" detail="يحجز تضارب PnL أو ترتيب الأحداث للمراجعة."/><Mini icon={<CheckCheck/>} title="Owner Review" detail="الاعتماد الصريح يسبق Evidence Ingestion دائمًا."/></div>

    <section className="mt-6 rounded-3xl border border-cyan-400/20 bg-cyan-400/[.035] p-5">
      <SectionTitle eyebrow="Historical Intake · 1..N" title="استقبال وتحليل المحتوى" action={<span className="text-xs text-cyan-200">لا توصية حية · لا UserTrade</span>}/>
      <form onSubmit={submitIntake} className="mt-4 space-y-4">
        <div className="grid gap-3 md:grid-cols-[180px_1fr_180px]">
          <label className="text-sm text-muted-foreground">نوع الإدخال<select value={inputMode} onChange={event => setInputMode(event.target.value as IntakeMode)} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-950/70 px-3 py-2 text-sm text-foreground"><option value="PASTE">لصق نص</option><option value="UPLOAD">رفع ملف نصي/JSON</option><option value="TELEGRAM_EXPORT">Telegram Export</option></select></label>
          <label className="text-sm text-muted-foreground">اسم الدفعة (اختياري)<input value={batchLabel} onChange={event => setBatchLabel(event.target.value)} placeholder="مثال: قناة يناير 2025" className="mt-2 w-full rounded-xl border border-white/10 bg-slate-950/70 px-3 py-2 text-sm text-foreground"/></label>
          <label className="flex items-end gap-2 pb-2 text-sm text-muted-foreground"><input type="checkbox" checked={isPartial} onChange={event => setIsPartial(event.target.checked)} className="h-4 w-4"/> دفعة جزئية</label>
        </div>
        <div className="flex flex-wrap gap-2"><label className="inline-flex cursor-pointer items-center gap-2 rounded-xl border border-white/10 px-3 py-2 text-xs text-muted-foreground hover:border-cyan-300/40"><Upload className="h-4 w-4"/> اختيار ملف<input type="file" accept=".txt,.json,.csv" onChange={handleFile} className="hidden"/></label><span className="inline-flex items-center gap-2 rounded-xl border border-white/10 px-3 py-2 text-xs text-muted-foreground"><ClipboardPaste className="h-4 w-4"/> للرسائل المتعددة استخدم فاصلًا مستقلًا: ---</span></div>
        <textarea value={rawInput} onChange={event => setRawInput(event.target.value)} rows={9} placeholder={'ألصق رسالة واحدة، أو افصل الرسائل المتعددة بسطر --- مستقل.\n\nمثال:\n#BTCUSDT LONG\nEntry: 100\nSL: 95\nTP1: 105\n---\nUpdate: move stop to entry 100'} className="w-full rounded-2xl border border-white/10 bg-slate-950/80 p-4 font-mono text-sm leading-6 text-foreground outline-none ring-cyan-300/40 focus:ring-2"/>
        <div className="flex flex-wrap items-center gap-3"><Button type="submit" disabled={createIntake.isPending || !rawInput.trim()} className="bg-cyan-400 text-slate-950 hover:bg-cyan-300">{createIntake.isPending ? "جارٍ استقبال الدفعة…" : "استقبال وتحليل المحتوى"}</Button><span className="text-xs text-muted-foreground">يمكن أن تحتوي الدفعة على عنصر واحد أو حتى 5000 عنصر.</span></div>
      </form>
      {intakeBatch ? <IntakeBatchView batch={intakeBatch} report={intakeReport.data?.report} onRefresh={() => { void intake.refetch(); void intakeReport.refetch(); }} /> : null}
      <div className="mt-5 rounded-2xl border border-white/8 bg-white/[.02] p-4"><div className="flex items-center justify-between gap-3"><p className="text-sm font-medium">دفعاتك الأخيرة</p><span className="text-xs text-muted-foreground">تُحفظ في Core</span></div>{intakeList.isLoading ? <p className="py-3 text-xs text-muted-foreground">جارٍ تحميل الدفعات…</p> : intakeList.isError ? <p className="py-3 text-xs text-rose-200">تعذر جلب الدفعات السابقة من Core.</p> : (intakeList.data?.batches.length ?? 0) === 0 ? <p className="py-3 text-xs text-muted-foreground">لا توجد دفعات مدخلة بعد.</p> : <div className="mt-3 space-y-2">{intakeList.data?.batches.map(batch => <button type="button" key={batch.id} onClick={() => setBatchId(batch.id)} className="flex w-full flex-wrap items-center justify-between gap-2 rounded-xl border border-white/8 bg-white/[.025] px-3 py-2 text-right text-xs hover:border-cyan-300/35"><span className="font-mono text-cyan-200">{batch.ref}</span><span>{batch.total_records} عنصر</span><StatusPill value={batch.status}/><span className="text-muted-foreground">{batch.metadata?.input_mode ? String(batch.metadata.input_mode) : batch.source_kind}</span></button>)}</div>}</div>
    </section>

    <section className="mt-6 rounded-3xl border border-white/8 bg-card/70 p-5"><SectionTitle eyebrow="Core Historical Read Model" title="سجل المتابعة التاريخي" action={historical.data?.as_of ? <span className="text-xs text-violet-200">تحديث {new Date(historical.data.as_of).toLocaleString("ar-SA")}</span> : undefined}/>{historical.isLoading ? <p className="py-8 text-sm text-muted-foreground">جارٍ تحميل السجل التاريخي من Core…</p> : historical.isError ? <p className="rounded-2xl border border-rose-400/20 bg-rose-400/5 p-4 text-sm text-rose-100">تعذر جلب السجل التاريخي الحي. لم تُعرض دفعات عرض بديلة.</p> : rows.length === 0 ? <p className="rounded-2xl border border-white/8 bg-white/[.025] p-4 text-sm text-muted-foreground">لا توجد إشارات تاريخية منسوبة إلى هذا الحساب حالياً.</p> : <><div className="grid gap-3 md:hidden">{rows.map(record => <HistoryCard key={record.public_ref} record={record}/>)}</div><div className="hidden overflow-x-auto md:block"><table className="w-full min-w-[760px] text-right"><thead className="text-[11px] uppercase tracking-[.12em] text-muted-foreground"><tr><th className="pb-3">السجل</th><th className="pb-3">الأصل</th><th className="pb-3">الاتجاه</th><th className="pb-3">الحالة</th><th className="pb-3">الثقة</th><th className="pb-3">الترتيب</th><th className="pb-3">وقت القرار</th></tr></thead><tbody>{rows.map(record => <tr key={record.public_ref} className="border-t border-white/6 text-sm"><td className="py-4 font-mono text-xs text-violet-200">{record.public_ref}</td><td className="py-4 font-medium">{record.asset ?? "—"}</td><td className="py-4">{record.side ?? "—"}</td><td className="py-4"><StatusPill value={record.status}/></td><td className="py-4"><StatusPill value={record.trust_tier}/></td><td className="py-4">{record.eligible_for_ranking ? "مؤهل" : "غير مؤهل"}</td><td className="py-4 text-xs text-muted-foreground">{record.decision_timestamp ? new Date(record.decision_timestamp).toLocaleString("ar-SA") : "—"}</td></tr>)}</tbody></table></div></>}</section>
  </div></DashboardLayout>;
}

export function IntakeBatchView({ batch, report, onRefresh }: { batch: { id: number; ref: string; status: string; source_kind: string; total_records: number; accepted_records: number; rejected_records: number; created_at: string | null; items: ExtractedItem[] }; report?: IntakeReport; onRefresh: () => void }) {
  const single = batch.items.length === 1;
  const processed = batch.items.filter(item => item.semantic_status === "SUCCESS").length;
  const needsReview = batch.items.filter(item => item.semantic_status === "INCOMPLETE" || item.semantic_status === "CONFLICT").length;
  return <div className="mt-5 rounded-2xl border border-white/10 bg-slate-950/40 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="font-mono text-xs text-cyan-200">{batch.ref}</p><p className="mt-1 text-sm text-muted-foreground">{single ? "رسالة واحدة" : `دفعة من ${batch.total_records} رسائل`} · عولج {processed} · يحتاج مراجعة {needsReview}</p></div><div className="flex items-center gap-2"><StatusPill value={humanStatus(batch.status)}/><Button type="button" variant="outline" size="sm" onClick={onRefresh}><RefreshCw className="ml-2 h-3.5 w-3.5"/>تحديث النتائج</Button></div></div>
    <div className="mt-4 rounded-xl border border-cyan-400/15 bg-cyan-400/[.035] p-4"><p className="text-sm font-medium text-cyan-100">ماذا عمل النظام؟</p><p className="mt-2 text-xs leading-6 text-cyan-50/80">استلم النظام {single ? "الرسالة" : "الرسائل"}، حفظ زمن المصدر والهوية، حاول استخراج القيم، ثم صنّف كل عنصر وأبقى الناقص أو المتعارض للمراجعة. لا يتم إنشاء توصية أو صفقة حية من هذه الشاشة.</p><div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-4"><span>المستلم: {batch.total_records}</span><span>استخراج مكتمل: {processed}</span><span>يحتاج مراجعة: {needsReview}</span><span>مكرر/مرفوض: {batch.rejected_records}</span></div></div>
    <div className="mt-4 space-y-3">{batch.items.map(item => <ExtractedItemCard key={item.id} item={item}/>)}</div>
    {report ? <div className="mt-4 rounded-xl border border-violet-400/15 bg-violet-400/[.035] p-4"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-medium text-violet-100">نتيجة المعالجة وربط الأحداث</p><span className="rounded-lg bg-violet-400/10 px-2 py-1 text-[11px] text-violet-100">{humanNextAction(report.next_action)}</span></div><div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-4"><span>Evidence: {report.counts.evidence_records ?? 0}</span><span>إشارات مستخرجة: {report.counts.historical_signals ?? 0}</span><span>أحداث Replay: {report.counts.replay_events ?? 0}</span><span>أحداث موثقة: {report.counts.verified_replay_events ?? 0}</span></div>{report.signals?.length ? <div className="mt-4 space-y-2">{report.signals.map(signal => { const view = normalizeSignal(signal); return <div key={view.public_ref} className="rounded-lg border border-white/8 bg-white/[.025] p-3 text-xs"><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-mono text-violet-200">{view.public_ref}</span><span>{view.asset ?? "أصل غير محدد"} · {view.side ?? "اتجاه غير محدد"} · {humanStatus(view.status)}</span></div><p className="mt-2 text-muted-foreground">أحداث مرتبطة: {view.events} · موثقة: {view.verified_events} · الثقة: {view.confidence_score ?? "—"}</p></div>; })}</div> : null}</div> : null}
  </div>;
}

function ExtractedItemCard({ item }: { item: ExtractedItem }) {
  return <div className="rounded-xl border border-white/8 bg-white/[.025] p-3"><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-mono text-xs text-violet-200">#{item.order ?? "—"} · {item.item_key ?? `item-${item.id}`}</span><div className="flex flex-wrap gap-2"><StatusPill value={humanStatus(item.semantic_status)}/><StatusPill value={humanSource(item.source_verification)}/></div></div><p className="mt-2 line-clamp-2 text-xs text-muted-foreground">{item.raw_text || "بدون نص؛ راجع بيانات الوسائط والمصدر."}</p><p className="mt-2 text-xs text-muted-foreground">زمن المصدر: {item.source_timestamp ? new Date(item.source_timestamp).toLocaleString("ar-SA") : "غير متوفر"}</p>{item.missing_fields.length > 0 ? <p className="mt-2 text-xs text-amber-200">حقول ناقصة: {item.missing_fields.join("، ")}</p> : null}{item.conflicting_fields.length > 0 ? <p className="mt-2 text-xs text-rose-200">تعارض يحتاج قرارًا: {item.conflicting_fields.join("، ")}</p> : null}{item.rejection_reason ? <p className="mt-2 text-xs text-rose-200">سبب الاستبعاد: {item.rejection_reason}</p> : null}<details className="mt-3 rounded-lg border border-cyan-400/10 bg-cyan-400/[.02] p-3"><summary className="cursor-pointer text-xs font-medium text-cyan-100">عرض البيانات المستخرجة</summary><ExtractedFields canonical={item.canonical}/></details></div>;
}

function ExtractedFields({ canonical }: { canonical: Record<string, unknown> }) {
  const labels: Array<[string, string]> = [["asset", "الأصل"], ["symbol", "الرمز"], ["side", "الاتجاه"], ["market", "السوق"], ["order_type", "نوع الأمر"], ["entry", "الدخول"], ["stop_loss", "الوقف"], ["take_profit", "الهدف"], ["confidence", "الثقة"]];
  const fields = labels.map(([key, label]) => ({ key, label, value: canonical[key] })).filter(field => field.value !== undefined && field.value !== null && field.value !== "");
  const targets = Array.isArray(canonical.targets) ? canonical.targets : [];
  return <div className="mt-3 grid gap-2 sm:grid-cols-2">{fields.map(field => <div key={field.key} className="rounded-lg border border-white/8 bg-white/[.025] px-3 py-2 text-xs"><span className="text-muted-foreground">{field.label}</span><p className="mt-1 font-medium text-foreground">{formatExtractedValue(field.value)}</p></div>)}{targets.length > 0 ? <div className="rounded-lg border border-white/8 bg-white/[.025] px-3 py-2 text-xs sm:col-span-2"><span className="text-muted-foreground">الأهداف</span><p className="mt-1 font-medium text-foreground">{targets.map((target, index) => `${index + 1}: ${formatExtractedValue(target)}`).join(" · ")}</p></div> : null}{fields.length === 0 && targets.length === 0 ? <p className="text-xs text-muted-foreground">لم تُستخرج قيم منظمة بعد؛ راجع النص أو الوسائط.</p> : null}</div>;
}

function normalizeSignal(signal: Record<string, unknown>) { return { public_ref: String(signal.public_ref ?? "signal"), asset: signal.asset == null ? null : String(signal.asset), side: signal.side == null ? null : String(signal.side), status: String(signal.status ?? "UNKNOWN"), confidence_score: signal.confidence_score == null ? null : String(signal.confidence_score), events: Number(signal.events ?? 0), verified_events: Number(signal.verified_events ?? 0) }; }
function formatExtractedValue(value: unknown): string { if (Array.isArray(value)) return value.map(formatExtractedValue).join("، "); if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${formatExtractedValue(item)}`).join(" · "); return String(value); }
function humanStatus(value: string): string { return ({ SUCCESS: "استخراج مكتمل", INCOMPLETE: "يحتاج بيانات", CONFLICT: "تعارض يحتاج مراجعة", STAGED: "تم الاستلام", DUPLICATE: "مكرر", REJECTED: "مستبعد", REVIEW_REQUIRED: "بانتظار المراجعة", EVIDENCE_INGESTED: "تم إدخال الدليل", VALIDATED: "تم التحقق", REPLAYED: "اكتمل Replay" } as Record<string, string>)[value] ?? value; }
function humanSource(value: string): string { return value === "VERIFIED_PROVENANCE" ? "مصدر موثق" : value === "UNVERIFIED" ? "مصدر غير موثق" : value; }
function humanNextAction(value: string): string { return ({ OWNER_REVIEW: "اعتماد المصدر", EVIDENCE_INGESTION: "إدخال الدليل", G5_DRAFT_REVIEW: "مراجعة الإشارة", REPLAY_REVIEW: "مراجعة Replay", REPORT_READY: "التقرير جاهز" } as Record<string, string>)[value] ?? "مراجعة الحالة"; }

function HistoryCard({ record }: { record: { public_ref: string; asset: string | null; side: string | null; status: string; trust_tier: string; eligible_for_ranking: boolean; decision_timestamp: string | null } }) { return <article className="rounded-2xl border border-white/7 bg-white/[.025] p-4"><div className="flex items-start justify-between gap-3"><div><p className="font-mono text-[11px] text-violet-200">{record.public_ref}</p><p className="mt-1 text-sm font-medium">{record.asset ?? "—"} · {record.side ?? "—"}</p></div><StatusPill value={record.status}/></div><div className="mt-3 flex flex-wrap gap-2"><StatusPill value={record.trust_tier}/><span className="text-xs text-muted-foreground">{record.eligible_for_ranking ? "مؤهل للترتيب" : "غير مؤهل"}</span></div></article>; }
function Mini({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) { return <div className="rounded-2xl border border-white/8 bg-card/70 p-5"><span className="inline-flex rounded-xl bg-violet-400/10 p-2 text-violet-300">{icon}</span><h3 className="mt-4 font-semibold">{title}</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">{detail}</p></div>; }
