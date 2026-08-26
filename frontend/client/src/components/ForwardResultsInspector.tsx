import React from "react";
import { SectionTitle, StatusPill } from "@/components/finance-ui";
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";

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

type IntakeBatch = { id: number; ref: string; status: string; source_kind: string; total_records: number; accepted_records: number; rejected_records: number; created_at: string | null; items: ExtractedItem[] };

export function IntakeBatchView({ batch, report, onRefresh }: { batch: IntakeBatch; report?: IntakeReport; onRefresh: () => void }) {
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
