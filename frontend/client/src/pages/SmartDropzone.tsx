import DashboardLayout from "@/components/DashboardLayout";
import { SectionTitle, StatusPill } from "@/components/finance-ui";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { trpc } from "@/lib/trpc";
import { BrainCircuit, ClipboardPaste, FileStack, Link2, ShieldCheck, Sparkles, UploadCloud } from "lucide-react";
import React, { useRef, useState } from "react";
import { IntakeBatchView, parseIntakeText, type IntakeMode } from "./Historical";

const example = "BTCUSDT LONG\nEntry: 69,158\nStop: 69,000\nTP1: 70,000\nTP2: 72,000\nMarket order filled";

export default function SmartDropzone() {
  const [text, setText] = useState("");
  const [sourceUri, setSourceUri] = useState("");
  const [batchLabel, setBatchLabel] = useState("");
  const [inputMode, setInputMode] = useState<IntakeMode>("PASTE");
  const [batchNotice, setBatchNotice] = useState<string | null>(null);
  const [batchId, setBatchId] = useState<number | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const analyzer = trpc.capitalguard.smartAnalyze.useMutation();
  const historicalIntake = trpc.capitalguard.historicalIntake.useMutation({ onSuccess: result => { setBatchId(result.batch.id); setBatchNotice(`تم استقبال الدفعة ${result.batch.ref} — جارٍ عرض ما استخرجه Core.`); } });
  const result = analyzer.data;
  const intake = trpc.capitalguard.historicalIntakeDetail.useQuery({ batchId: batchId ?? 1 }, { enabled: batchId !== null, refetchInterval: batchId !== null ? 5_000 : false });
  const intakeReport = trpc.capitalguard.historicalIntakeReport.useQuery({ batchId: batchId ?? 1 }, { enabled: batchId !== null, refetchInterval: batchId !== null ? 5_000 : false });
  const run = () => analyzer.mutate({ text });
  const loadFiles = async (files: FileList | File[]) => {
    const contents = await Promise.all(Array.from(files).map(file => file.text()));
    setText(contents.filter(Boolean).join("\n---\n"));
    setInputMode(contents.length > 1 ? "UPLOAD" : files[0]?.name.toLowerCase().endsWith(".json") ? "TELEGRAM_EXPORT" : "UPLOAD");
    setBatchNotice(`${contents.length} ملف جاهز للاستقبال التاريخي.`);
  };
  const submitBatch = () => {
    try {
      const parsed = parseIntakeText(text, inputMode);
      const items = parsed.items.map(item => ({ ...item, sourceUri: sourceUri.trim() || item.sourceUri, sourceOriginType: sourceUri.trim() ? "WEB_LINK" : item.sourceOriginType }));
      historicalIntake.mutate({ sourceKind: parsed.sourceKind, inputMode, items, isPartial: false, batchLabel: batchLabel.trim() || undefined });
    } catch (error) {
      setBatchNotice(error instanceof Error ? error.message : "صيغة الدفعة غير صالحة.");
    }
  };

  return <DashboardLayout><div dir="rtl" className="mx-auto max-w-[1240px]"><div className="mb-8"><p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-violet-300">Signal Radar</p><h1 className="text-3xl font-semibold">رادار تدقيق الإشارات</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">ألصق رسالة، اسحب صورة أو ارفع ملفًا. سيعرض النظام ما استلمه وما استخرجه وما يحتاج مراجعة في نفس المكان.</p></div>
    <div className="grid gap-6 lg:grid-cols-[1.05fr_.95fr]"><section className="rounded-3xl border border-white/8 bg-card/70 p-6"><SectionTitle eyebrow="1 · أدخل المصدر" title="رسالة أو ملف أو دفعة"/><div onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); void loadFiles(event.dataTransfer.files); }} className="group rounded-2xl border border-dashed border-cyan-400/25 bg-cyan-400/[.035] p-5 transition hover:border-cyan-400/45"><div className="mb-4 flex items-center gap-3 text-cyan-200"><span className="rounded-xl bg-cyan-400/10 p-2"><UploadCloud className="h-5 w-5"/></span><div><p className="text-sm font-medium">اسحب ملفات نصية أو JSON إلى هنا</p><p className="mt-1 text-xs text-muted-foreground">يمكنك أيضًا لصق رسالة واحدة أو عدة رسائل مفصولة بـ ---</p></div></div><Textarea value={text} onChange={event => { setText(event.target.value); setInputMode("PASTE"); }} placeholder="الصق الرسالة هنا…" className="min-h-[220px] resize-y border-white/10 bg-background/50 leading-7"/><input ref={fileInput} type="file" multiple accept=".txt,.json,.csv" className="hidden" onChange={event => { if (event.target.files) void loadFiles(event.target.files); }}/><div className="mt-4 flex flex-wrap gap-3"><Button onClick={run} disabled={text.trim().length < 16 || analyzer.isPending} className="bg-violet-400 text-slate-950 hover:bg-violet-300"><BrainCircuit className="ml-2 h-4 w-4"/>{analyzer.isPending ? "جاري التحليل" : "حلّل الرسالة"}</Button><Button variant="outline" className="border-white/10 bg-white/[.03]" onClick={() => setText(example)}><ClipboardPaste className="ml-2 h-4 w-4"/>حمّل مثالًا</Button><Button variant="outline" className="border-white/10 bg-white/[.03]" onClick={() => fileInput.current?.click()}><FileStack className="ml-2 h-4 w-4"/>اختيار ملفات</Button></div></div><div className="mt-4 grid gap-3 md:grid-cols-2"><label className="space-y-2 text-xs text-muted-foreground"><span className="flex items-center gap-2"><Link2 className="h-3.5 w-3.5"/>رابط المصدر (بيانات provenance فقط)</span><input value={sourceUri} onChange={event => setSourceUri(event.target.value)} placeholder="https://t.me/..." className="w-full rounded-xl border border-white/10 bg-slate-950/70 px-3 py-2 text-sm text-foreground outline-none"/></label><label className="space-y-2 text-xs text-muted-foreground"><span>اسم الدفعة</span><input value={batchLabel} onChange={event => setBatchLabel(event.target.value)} placeholder="مثال: أرشيف قناة يناير" className="w-full rounded-xl border border-white/10 bg-slate-950/70 px-3 py-2 text-sm text-foreground outline-none"/></label></div><Button onClick={submitBatch} disabled={historicalIntake.isPending || text.trim().length < 1} variant="outline" className="mt-4 w-full border-cyan-300/25 text-cyan-100">{historicalIntake.isPending ? "جارٍ استقبال الدفعة…" : "حفظ كدفعة واستعراض النتائج"}</Button>{batchNotice ? <p className="mt-3 rounded-xl border border-cyan-300/15 bg-cyan-300/[.04] p-3 text-xs text-cyan-100">{batchNotice}</p> : null}<p className="mt-4 rounded-xl border border-amber-400/15 bg-amber-400/[.05] p-3 text-xs leading-5 text-amber-100">هذا التحليل مساعد استخراج وتفسير. لا يُعد نصيحة مالية ولا يصدر أوامر تنفيذ أو يغير أي سجل حي.</p></section>
      <section className="rounded-3xl border border-white/8 bg-card/70 p-6"><SectionTitle eyebrow="2 · النتيجة" title="ماذا استخرج النظام؟"/>{analyzer.error ? <div className="rounded-2xl border border-rose-400/20 bg-rose-400/[.08] p-4 text-sm text-rose-200">تعذر التحليل الآن. تحقق من النص وأعد المحاولة.</div> : !result ? <div className="grid min-h-[390px] place-items-center rounded-2xl border border-white/7 bg-white/[.02] p-8 text-center"><div><span className="mx-auto inline-flex rounded-2xl bg-violet-400/10 p-3 text-violet-300"><Sparkles className="h-6 w-6"/></span><p className="mt-4 font-medium">لا توجد نتيجة بعد</p><p className="mt-2 max-w-xs text-xs leading-5 text-muted-foreground">ستظهر هنا الإشارة المستخرجة، مستوى الثقة، والتوجيه الزمني المقترح.</p></div></div> : <div className="space-y-4"><div className="flex items-center justify-between rounded-2xl border border-white/7 bg-white/[.025] p-4"><div><p className="text-xs text-muted-foreground">التصنيف</p><p className="mt-1 font-semibold">{result.classification.replaceAll("_", " ")}</p></div><StatusPill value={result.temporalHint}/></div><div className="grid grid-cols-2 gap-3"><Cell label="الأصل" value={result.asset ?? "غير محدد"}/><Cell label="الاتجاه" value={result.side}/><Cell label="الدخول" value={result.entry ?? "—"}/><Cell label="وقف الخسارة" value={result.stopLoss ?? "—"}/></div><div className="rounded-2xl bg-white/[.035] p-4"><p className="text-xs text-muted-foreground">الأهداف المستخرجة</p><p className="mt-2 font-mono text-sm text-cyan-200">{result.targets.length ? result.targets.join(" · ") : "لم تُستخرج أهداف مؤكدة"}</p></div><div className="rounded-2xl border border-violet-400/15 bg-violet-400/[.06] p-4"><p className="text-xs text-violet-200">التفسير · ثقة {(result.confidence * 100).toFixed(0)}%</p><p className="mt-2 text-sm leading-6 text-slate-200">{result.explanation}</p><div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/8"><div className="h-full rounded-full bg-gradient-to-l from-violet-400 to-cyan-300" style={{ width: `${result.confidence * 100}%` }}/></div></div><div className="flex gap-3 rounded-2xl border border-amber-400/15 bg-amber-400/[.05] p-4 text-xs leading-5 text-amber-100"><ShieldCheck className="h-4 w-4 shrink-0"/>{result.safetyNotice}</div></div>}{intake.data?.batch ? <IntakeBatchView batch={intake.data.batch} report={intakeReport.data?.report} onRefresh={() => { void intake.refetch(); void intakeReport.refetch(); }} /> : null}</section></div></div></DashboardLayout>;
}
function Cell({ label, value }: { label: string; value: string | number }) { return <div className="rounded-xl bg-white/[.04] p-3"><p className="text-[10px] text-muted-foreground">{label}</p><p className="mt-1 text-sm font-medium">{value}</p></div>; }
