import DashboardLayout from "@/components/DashboardLayout";
import React from "react";
import { SectionTitle, StatusPill } from "@/components/finance-ui";
import { IntakeBatchView } from "@/components/ForwardResultsInspector";
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

function HistoryCard({ record }: { record: { public_ref: string; asset: string | null; side: string | null; status: string; trust_tier: string; eligible_for_ranking: boolean; decision_timestamp: string | null } }) { return <article className="rounded-2xl border border-white/7 bg-white/[.025] p-4"><div className="flex items-start justify-between gap-3"><div><p className="font-mono text-[11px] text-violet-200">{record.public_ref}</p><p className="mt-1 text-sm font-medium">{record.asset ?? "—"} · {record.side ?? "—"}</p></div><StatusPill value={record.status}/></div><div className="mt-3 flex flex-wrap gap-2"><StatusPill value={record.trust_tier}/><span className="text-xs text-muted-foreground">{record.eligible_for_ranking ? "مؤهل للترتيب" : "غير مؤهل"}</span></div></article>; }
function Mini({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) { return <div className="rounded-2xl border border-white/8 bg-card/70 p-5"><span className="inline-flex rounded-xl bg-violet-400/10 p-2 text-violet-300">{icon}</span><h3 className="mt-4 font-semibold">{title}</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">{detail}</p></div>; }
