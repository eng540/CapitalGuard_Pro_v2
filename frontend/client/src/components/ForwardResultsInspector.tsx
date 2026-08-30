import React from "react";
import { useEffect, useState } from "react";
import { SectionTitle, StatusPill } from "@/components/finance-ui";
import { Button } from "@/components/ui/button";
import { RefreshCw, ExternalLink, Pencil, Save, X } from "lucide-react";
import { trpc } from "@/lib/trpc";
import { historicalSessionPath } from "@/lib/historical-session";
import { toast } from "sonner";
const toastSuccess = (message: string) => toast.success(message);
const toastError = (message: string) => toast.error(message);

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

type TimelinePoint = { event_type?: unknown; event_timestamp?: unknown; replay_status?: unknown; price?: unknown };
type IntakeReport = {
  counts: Record<string, number | string>;
  readiness: Record<string, boolean>;
  next_action: string;
  signals?: Array<Record<string, unknown>>;
};

type IntakeBatch = { id: number; ref: string; status: string; source_kind: string; total_records: number; accepted_records: number; rejected_records: number; processed_count: number; changed_count: number; result_status: string; created_at: string | null; items: ExtractedItem[] };

export function IntakeBatchView({ batch, report, onRefresh }: { batch: IntakeBatch; report?: IntakeReport; onRefresh: () => void }) {
  const retryReplay = trpc.capitalguard.admin.retryHistoricalReplay.useMutation({ onSuccess: () => { toastSuccess("تمت إعادة المحاكاة؛ يتم تحديث النتيجة الآن."); onRefresh(); }, onError: () => toastError("تعذرت إعادة المحاكاة من Core؛ لم تتغير النتيجة السابقة.") });
  const single = batch.items.length === 1;
  const processed = batch.processed_count;
  const changed = batch.changed_count;
  const needsReview = batch.items.filter(item => item.semantic_status === "INCOMPLETE" || item.semantic_status === "CONFLICT").length;
  return <div className="mt-5 rounded-2xl border border-white/10 bg-slate-950/40 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="font-mono text-xs text-cyan-200">{batch.ref}</p><p className="mt-1 text-sm text-muted-foreground">{single ? "رسالة واحدة" : `دفعة من ${batch.total_records} رسائل`} · عولج {processed} · تغيّر {changed} · يحتاج إكمال {needsReview}</p></div><div className="flex items-center gap-2"><StatusPill value={humanStatus(batch.status)}/><StatusPill value={humanResult(batch.result_status)}/><Button type="button" variant="outline" size="sm" onClick={onRefresh}><RefreshCw className="ml-2 h-3.5 w-3.5"/>تحديث النتائج</Button><a href={historicalSessionPath(batch.id)} className="inline-flex items-center rounded-md border border-white/10 px-2.5 py-1.5 text-xs text-cyan-100 transition hover:border-cyan-300/40 hover:bg-cyan-300/10"><ExternalLink className="ml-1.5 h-3.5 w-3.5"/>فتح الجلسة</a></div></div>
    <div className="mt-4 rounded-xl border border-cyan-400/15 bg-cyan-400/[.035] p-4"><p className="text-sm font-medium text-cyan-100">ماذا عمل النظام؟</p><p className="mt-2 text-xs leading-6 text-cyan-50/80">استلم النظام {single ? "الرسالة" : "الرسائل"}، حفظ زمن المصدر والهوية، استخرج القيم، ثم أوضح ما اكتمل وما يحتاج استكمالًا بسيطًا. لا يتم إنشاء توصية أو صفقة حية من هذه الشاشة.</p><div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-5"><span>المستلم: {batch.total_records}</span><span>عولج: {processed}</span><span>تغيّر: {changed}</span><span>يحتاج إكمال: {needsReview}</span><span>مكرر/مرفوض: {batch.rejected_records}</span></div></div>
    <div className="mt-4 space-y-3">{batch.items.map(item => <ExtractedItemCard key={item.id} batchId={batch.id} item={item} onSaved={onRefresh}/>)}</div>
    {report ? <div className="mt-4 rounded-xl border border-violet-400/15 bg-violet-400/[.035] p-4"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-medium text-violet-100">نتيجة المعالجة وربط الأحداث</p><span className="rounded-lg bg-violet-400/10 px-2 py-1 text-[11px] text-violet-100">{humanNextAction(report.next_action)}</span></div><div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-4"><span>Evidence: {report.counts.evidence_records ?? 0}</span><span>إشارات مستخرجة: {report.counts.historical_signals ?? 0}</span><span>أحداث Replay: {report.counts.replay_events ?? 0}</span><span>أحداث موثقة: {report.counts.verified_replay_events ?? 0}</span></div>{report.signals?.length ? <div className="mt-4 space-y-2">{report.signals.map(signal => { const view = normalizeSignal(signal); return <div key={view.public_ref} className="rounded-lg border border-white/8 bg-white/[.025] p-3 text-xs"><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-mono text-violet-200">{view.public_ref}</span><span>{view.asset ?? "أصل غير محدد"} · {view.side ?? "اتجاه غير محدد"} · {humanStatus(view.status)}</span></div><p className="mt-2 text-muted-foreground">دورة الحياة: {humanLifecycle(view.lifecycle_status)} · آخر حدث: {humanEvent(view.last_event)} · أحداث مرتبطة: {view.events} · موثقة: {view.verified_events} · الثقة: {view.confidence_score ?? "—"}</p>{view.replay_status === "REPLAY_PARTIAL" ? <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/[.05] p-3"><p className="font-medium text-amber-100">⚠️ المحاكاة وصلت إلى G6 لكن التغطية التاريخية جزئية</p><p className="mt-1 text-[11px] leading-5 text-amber-50/80">لن يتم احتساب إغلاق أو ربح كامل دون تغطية زمنية كافية.</p><div className="mt-2 flex flex-wrap items-center gap-2"><span className="text-[11px] text-muted-foreground">التغطية: {view.coverage_status ?? "PARTIAL_WINDOW"} · {view.coverage_ratio != null ? `${Math.round(view.coverage_ratio * 10000) / 100}%` : "—"}</span>{view.receipt_id ? <Button type="button" size="sm" variant="outline" disabled={retryReplay.isPending} onClick={() => retryReplay.mutate({ receiptId: view.receipt_id! })}><RefreshCw className={`ml-1.5 h-3.5 w-3.5 ${retryReplay.isPending ? "animate-spin" : ""}`}/>إعادة جلب البيانات والمحاكاة</Button> : null}</div></div> : null}{view.timeline.length ? <details className="mt-3 rounded-lg border border-violet-300/10 bg-violet-300/[.02] p-3"><summary className="cursor-pointer text-xs font-medium text-violet-100">عرض Timeline التاريخي</summary><div className="mt-2 space-y-2">{view.timeline.map((point, index) => <div key={`${view.public_ref}-${index}`} className="flex flex-wrap items-center justify-between gap-2 border-t border-white/6 pt-2 text-[11px]"><span>{humanEvent(point.event_type == null ? null : String(point.event_type))}</span><span className="text-muted-foreground">{point.event_timestamp ? new Date(String(point.event_timestamp)).toLocaleString("ar-SA") : "وقت غير متوفر"} · Replay {String(point.replay_status ?? "UNVERIFIED")}{point.price != null ? ` · السعر ${String(point.price)}` : ""}</span></div>)}</div></details> : null}</div>; })}</div> : null}</div> : null}
  </div>;
}

function ExtractedItemCard({ batchId, item, onSaved }: { batchId: number; item: ExtractedItem; onSaved: () => void }) {
  const canonical = item.canonical ?? {};
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState(() => ({
    asset: String(canonical.asset ?? ""),
    side: String(canonical.side ?? canonical.direction ?? "LONG").toUpperCase(),
    entry: String(canonical.entry ?? ""),
    stopLoss: String(canonical.stop_loss ?? ""),
    targets: formatTargets(canonical.targets),
  }));
  useEffect(() => {
    setDraft({ asset: String(canonical.asset ?? ""), side: String(canonical.side ?? canonical.direction ?? "LONG").toUpperCase(), entry: String(canonical.entry ?? ""), stopLoss: String(canonical.stop_loss ?? ""), targets: formatTargets(canonical.targets) });
  }, [item.id, JSON.stringify(canonical)]);
  const canCorrect = !["DUPLICATE", "REJECTED"].includes(item.status.toUpperCase());
  const correction = trpc.capitalguard.historicalCorrectItem.useMutation({
    onSuccess: () => { setEditing(false); setError(null); onSaved(); },
    onError: () => setError("تعذر حفظ التعديل من Core. لم تتغير البيانات السابقة."),
  });
  const update = (key: keyof typeof draft, value: string) => setDraft(current => ({ ...current, [key]: value }));
  const save = () => {
    const targets = parseTargets(draft.targets);
    if (!draft.asset.trim() || !draft.entry.trim() || !draft.stopLoss.trim() || !targets.length) {
      setError("أكمل الأصل والدخول والوقف وهدفًا واحدًا على الأقل.");
      return;
    }
    correction.mutate({ batchId, itemId: item.id, asset: draft.asset.trim().toUpperCase(), side: draft.side as "LONG" | "SHORT", entry: Number(draft.entry), stopLoss: Number(draft.stopLoss), targets });
  };
  return <div className="rounded-xl border border-white/8 bg-white/[.025] p-3"><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-mono text-xs text-violet-200">#{item.order ?? "—"} · {item.item_key ?? `item-${item.id}`}</span><div className="flex flex-wrap items-center gap-2"><StatusPill value={humanStatus(item.semantic_status)}/><StatusPill value={humanSource(item.source_verification)}/>{canCorrect ? <Button type="button" variant="outline" size="sm" onClick={() => { setEditing(value => !value); setError(null); }}><Pencil className="ml-1.5 h-3.5 w-3.5"/>{editing ? "إخفاء التعديل" : "تعديل سريع"}</Button> : null}</div></div><p className="mt-2 line-clamp-2 text-xs text-muted-foreground">{item.raw_text || "بدون نص؛ راجع بيانات الوسائط والمصدر."}</p><p className="mt-2 text-xs text-muted-foreground">زمن المصدر: {item.source_timestamp ? new Date(item.source_timestamp).toLocaleString("ar-SA") : "غير متوفر"}</p>{item.missing_fields.length > 0 ? <p className="mt-2 text-xs text-amber-200">حقول ناقصة: {item.missing_fields.join("، ")}</p> : null}{item.conflicting_fields.length > 0 ? <p className="mt-2 text-xs text-rose-200">تعارض يحتاج قرارًا: {item.conflicting_fields.join("، ")}</p> : null}{item.rejection_reason ? <p className="mt-2 text-xs text-rose-200">سبب الاستبعاد: {item.rejection_reason}</p> : null}{editing ? <div className="mt-3 rounded-lg border border-cyan-300/15 bg-cyan-300/[.03] p-3"><p className="text-xs font-medium text-cyan-100">عدّل القيم ثم احفظها</p><div className="mt-3 grid gap-2 sm:grid-cols-2"><label className="text-xs text-muted-foreground">الأصل<input value={draft.asset} onChange={event => update("asset", event.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-slate-950/70 px-2.5 py-2 text-sm text-foreground outline-none"/></label><label className="text-xs text-muted-foreground">الاتجاه<select value={draft.side} onChange={event => update("side", event.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-slate-950/70 px-2.5 py-2 text-sm text-foreground outline-none"><option value="LONG">LONG</option><option value="SHORT">SHORT</option></select></label><label className="text-xs text-muted-foreground">الدخول<input inputMode="decimal" value={draft.entry} onChange={event => update("entry", event.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-slate-950/70 px-2.5 py-2 text-sm text-foreground outline-none"/></label><label className="text-xs text-muted-foreground">وقف الخسارة<input inputMode="decimal" value={draft.stopLoss} onChange={event => update("stopLoss", event.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-slate-950/70 px-2.5 py-2 text-sm text-foreground outline-none"/></label><label className="text-xs text-muted-foreground sm:col-span-2">الأهداف <span className="text-[11px]">(مثال: 89000@20, 90000@80)</span><input inputMode="decimal" value={draft.targets} onChange={event => update("targets", event.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-slate-950/70 px-2.5 py-2 text-sm text-foreground outline-none"/></label></div>{error ? <p className="mt-2 text-xs text-rose-200">{error}</p> : null}<div className="mt-3 flex gap-2"><Button type="button" size="sm" onClick={save} disabled={correction.isPending}><Save className="ml-1.5 h-3.5 w-3.5"/>{correction.isPending ? "جارٍ الحفظ…" : "حفظ التعديل"}</Button><Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}><X className="ml-1.5 h-3.5 w-3.5"/>إلغاء</Button></div></div> : null}<details className="mt-3 rounded-lg border border-cyan-400/10 bg-cyan-400/[.02] p-3"><summary className="cursor-pointer text-xs font-medium text-cyan-100">عرض البيانات المستخرجة</summary><ExtractedFields canonical={canonical}/></details></div>;
}

function formatTargets(value: unknown): string { return Array.isArray(value) ? value.map(target => { const row: Record<string, unknown> = target && typeof target === "object" ? target as Record<string, unknown> : { price: target }; const price = row.price ?? row.value ?? ""; const percentage = row.percentage ?? row.close_percent; return percentage == null || percentage === "" ? String(price) : `${price}@${percentage}`; }).join(", ") : ""; }
function parseTargets(value: string): Array<{ price: number; percentage?: number }> { return value.split(/[,\s]+/).map(token => token.trim()).filter(Boolean).map(token => { const [rawPrice, rawPercentage] = token.split("@"); const price = Number(rawPrice); const percentage = rawPercentage === undefined ? undefined : Number(rawPercentage); return { price, ...(percentage !== undefined && Number.isFinite(percentage) ? { percentage } : {}) }; }).filter(target => Number.isFinite(target.price) && target.price > 0); }

function ExtractedFields({ canonical }: { canonical: Record<string, unknown> }) {
  const labels: Array<[string, string]> = [["asset", "الأصل"], ["symbol", "الرمز"], ["side", "الاتجاه"], ["market", "السوق"], ["order_type", "نوع الأمر"], ["entry", "الدخول"], ["stop_loss", "الوقف"], ["take_profit", "الهدف"], ["confidence", "الثقة"]];
  const fields = labels.map(([key, label]) => ({ key, label, value: canonical[key] })).filter(field => field.value !== undefined && field.value !== null && field.value !== "");
  const targets = Array.isArray(canonical.targets) ? canonical.targets : [];
  return <div className="mt-3 grid gap-2 sm:grid-cols-2">{fields.map(field => <div key={field.key} className="rounded-lg border border-white/8 bg-white/[.025] px-3 py-2 text-xs"><span className="text-muted-foreground">{field.label}</span><p className="mt-1 font-medium text-foreground">{formatExtractedValue(field.value)}</p></div>)}{targets.length > 0 ? <div className="rounded-lg border border-white/8 bg-white/[.025] px-3 py-2 text-xs sm:col-span-2"><span className="text-muted-foreground">الأهداف</span><p className="mt-1 font-medium text-foreground">{targets.map((target, index) => `${index + 1}: ${formatExtractedValue(target)}`).join(" · ")}</p></div> : null}{fields.length === 0 && targets.length === 0 ? <p className="text-xs text-muted-foreground">لم تُستخرج قيم منظمة بعد؛ راجع النص أو الوسائط.</p> : null}</div>;
}

function normalizeSignal(signal: Record<string, unknown>) { return { receipt_id: signal.receipt_id == null ? null : Number(signal.receipt_id), replay_status: signal.replay_status == null ? null : String(signal.replay_status), coverage_status: signal.coverage_status == null ? null : String(signal.coverage_status), coverage_ratio: signal.coverage_ratio == null ? null : Number(signal.coverage_ratio), public_ref: String(signal.public_ref ?? "signal"), asset: signal.asset == null ? null : String(signal.asset), side: signal.side == null ? null : String(signal.side), status: String(signal.status ?? "UNKNOWN"), confidence_score: signal.confidence_score == null ? null : String(signal.confidence_score), events: Number(signal.events ?? 0), verified_events: Number(signal.verified_events ?? 0), lifecycle_status: String(signal.lifecycle_status ?? "NOT_ACTIVATED"), last_event: signal.last_event == null ? null : String(signal.last_event), timeline: Array.isArray(signal.timeline) ? signal.timeline as TimelinePoint[] : [] }; }
function formatExtractedValue(value: unknown): string { if (Array.isArray(value)) return value.map(formatExtractedValue).join("، "); if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${formatExtractedValue(item)}`).join(" · "); return String(value); }
function humanStatus(value: string): string { return ({ SUCCESS: "استخراج مكتمل", INCOMPLETE: "يحتاج بيانات بسيطة", CONFLICT: "تعارض يحتاج اختيارك", STAGED: "تم الاستلام", DUPLICATE: "مكرر — لا تغيير", REJECTED: "لم يُسجل", REVIEW_REQUIRED: "تم التحليل — أكمل القيم الناقصة", EVIDENCE_INGESTED: "تم ربط الدليل", VALIDATED: "تم التحقق", REPLAYED: "اكتملت المحاكاة" } as Record<string, string>)[value] ?? value; }
function humanResult(value: string): string { return ({ CHANGED: "نتيجة: تغييرات مسجلة", PARTIAL_CHANGE: "نتيجة: تغييرات جزئية", NO_CHANGE: "نتيجة: لا تغيير" } as Record<string, string>)[value] ?? `نتيجة: ${value}`; }
function humanLifecycle(value: string): string { return ({ NOT_ACTIVATED: "لم يتفعّل تاريخيًا", ACTIVE: "مستمر تاريخيًا", AMBIGUOUS: "نتيجة غامضة", CLOSED_SL: "أُغلق عند الوقف", CLOSED_SOURCE: "أُغلق وفق المصدر", CLOSED_TARGETS: "اكتملت الأهداف" } as Record<string, string>)[value] ?? value; }
function humanEvent(value: string | null): string { return ({ ACTIVATED: "بدأت المحاكاة", TP1: "تحقق الهدف الأول", TP2: "تحقق الهدف الثاني", SL: "ضُرب وقف الخسارة", CLOSE: "حدث الإغلاق", AMBIGUOUS: "حدث غامض" } as Record<string, string>)[value ?? ""] ?? value ?? "لا أحداث"; }
function humanSource(value: string): string { return value === "VERIFIED_PROVENANCE" ? "مصدر موثق" : value === "UNVERIFIED" ? "مصدر غير موثق" : value; }
function humanNextAction(value: string): string { return ({ OWNER_REVIEW: "أكمل القيم الناقصة إن وجدت", EVIDENCE_INGESTION: "جارٍ تجهيز التتبع التاريخي", G5_DRAFT_REVIEW: "جارٍ ربط القيم بالتتبع", REPLAY_REVIEW: "المحاكاة جاهزة للعرض", REPORT_READY: "التقرير جاهز" } as Record<string, string>)[value] ?? "الحالة محدثة"; }
