import DashboardLayout from "@/components/DashboardLayout";
import { SectionTitle, StatusPill } from "@/components/finance-ui";
import { trpc } from "@/lib/trpc";
import { BellRing, CircleGauge, ListTree, LoaderCircle, X } from "lucide-react";
import React, { useState } from "react";

export default function Recommendations() {
  const query = trpc.capitalguard.recommendations.useQuery();
  const [selectedRef, setSelectedRef] = useState<string | null>(null);
  const [closeIdempotencyKey, setCloseIdempotencyKey] = useState<string | null>(null);
  const [partialCloseIdempotencyKey, setPartialCloseIdempotencyKey] = useState<string | null>(null);
  const [cancelIdempotencyKey, setCancelIdempotencyKey] = useState<string | null>(null);
  const [closeNotice, setCloseNotice] = useState<string | null>(null);
  const [partialClosePercent, setPartialClosePercent] = useState("");
  const detailQuery = trpc.capitalguard.recommendationDetail.useQuery(
    { publicRef: selectedRef ?? "pending" },
    { enabled: Boolean(selectedRef) },
  );

  const closeMutation = trpc.capitalguard.trader.closeUserTrade.useMutation({
    onSuccess: async (result) => {
      setCloseNotice(`تم إغلاق ${result.public_ref} بسعر Core ${result.close_price}${result.replayed ? " (إعادة آمنة)" : ""}.`);
      await Promise.all([query.refetch(), detailQuery.refetch()]);
    },
    onError: () => setCloseNotice("تعذر الإغلاق. لم يُنفذ أي بديل محلي؛ تحقق من حالة التوصية أو أعد المحاولة."),
  });
  const cancelMutation = trpc.capitalguard.trader.cancelPendingUserTrade.useMutation({
    onSuccess: async (result) => {
      setCloseNotice(`تم إلغاء التتبع المعلق لـ ${result.public_ref}. لم يُسجل سعر خروج أو PnL${result.replayed ? " (إعادة آمنة)" : ""}.`);
      await Promise.all([query.refetch(), detailQuery.refetch()]);
    },
    onError: () => setCloseNotice("تعذر إلغاء التتبع المعلق. لم يُنفذ إغلاق سعري؛ تحقق من حالة السجل أو أعد المحاولة."),
  });
  const partialCloseMutation = trpc.capitalguard.trader.partialCloseUserTrade.useMutation({
    onSuccess: async (result) => {
      setCloseNotice(`تم إغلاق ${result.closed_percent}% من ${result.public_ref} بسعر Core ${result.partial_close_price}. المتبقي ${result.remaining_open_size_percent}%${result.replayed ? " (إعادة آمنة)" : ""}.`);
      setPartialCloseIdempotencyKey(null);
      setPartialClosePercent("");
      await Promise.all([query.refetch(), detailQuery.refetch()]);
    },
    onError: () => setCloseNotice("تعذر الإغلاق الجزئي. لم يُرسل المتصفح سعراً ولم يُنفذ أي بديل محلي؛ تحقق من النسبة والحالة ثم أعد المحاولة."),
  });

  const selectRecommendation = (publicRef: string) => {
    setSelectedRef(publicRef);
    setCloseIdempotencyKey(null);
    setPartialCloseIdempotencyKey(null);
    setCancelIdempotencyKey(null);
    setPartialClosePercent("");
    setCloseNotice(null);
  };
  const requestClose = () => {
    if (!selectedRef) return;
    const idempotencyKey = closeIdempotencyKey ?? globalThis.crypto.randomUUID();
    setCloseIdempotencyKey(idempotencyKey);
    setPartialCloseIdempotencyKey(null);
    setCloseNotice("تأكيد مطلوب: سيطلب النظام سعراً موثوقاً من Core ثم يغلق صفقتك المتتبعة فقط.");
  };
  const confirmClose = () => {
    if (!selectedRef || !closeIdempotencyKey) return;
    closeMutation.mutate({ publicRef: selectedRef, idempotencyKey: closeIdempotencyKey });
  };
  const requestPartialClose = (remainingOpenSizePercent: number) => {
    const requestedPercent = Number(partialClosePercent);
    if (!Number.isFinite(requestedPercent) || requestedPercent <= 0 || requestedPercent >= remainingOpenSizePercent) {
      setCloseNotice(`أدخل نسبة موجبة أقل من المتبقي (${remainingOpenSizePercent}%). الإغلاق الكامل له إجراء منفصل.`);
      return;
    }
    const idempotencyKey = partialCloseIdempotencyKey ?? globalThis.crypto.randomUUID();
    setPartialCloseIdempotencyKey(idempotencyKey);
    setCloseIdempotencyKey(null);
    setCloseNotice(`تأكيد مطلوب: سيغلق Core نسبة ${requestedPercent}% فقط من الكمية الأصلية، باستخدام سعره الموثوق.`);
  };
  const confirmPartialClose = () => {
    if (!selectedRef || !partialCloseIdempotencyKey) return;
    partialCloseMutation.mutate({
      publicRef: selectedRef,
      closePercent: Number(partialClosePercent),
      idempotencyKey: partialCloseIdempotencyKey,
    });
  };
  const requestCancelPending = () => {
    if (!selectedRef) return;
    const idempotencyKey = cancelIdempotencyKey ?? globalThis.crypto.randomUUID();
    setCancelIdempotencyKey(idempotencyKey);
    setCloseNotice("تأكيد مطلوب: سيُلغي Core التتبع المعلق فقط من دون سعر خروج أو PnL.");
  };
  const confirmCancelPending = () => {
    if (!selectedRef || !cancelIdempotencyKey) return;
    cancelMutation.mutate({ publicRef: selectedRef, idempotencyKey: cancelIdempotencyKey });
  };

  const rows = query.data?.items ?? [];
  return (
    <DashboardLayout>
      <div dir="rtl" className="mx-auto max-w-[1380px]">
        <div className="mb-8 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-cyan-300">Live Signals</p>
            <h1 className="text-3xl font-semibold">توصيات قابلة للتتبع، وليست رسائل عابرة.</h1>
            <p className="mt-2 text-sm text-muted-foreground">قراءة حية من Core مرتبطة بجلسة Telegram، مع IDs وحالة الأهداف وسجل أحداث.</p>
          </div>
          <div className="flex gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1"><BellRing className="h-4 w-4 text-cyan-300" />تنبيهات مرئية آمنة</span>
            <span className="inline-flex items-center gap-1"><CircleGauge className="h-4 w-4 text-violet-300" />لا تنفيذ تلقائي</span>
          </div>
        </div>
        <SectionTitle eyebrow="Core Read Model" title="توصياتك المتتبعة" action={query.data?.as_of ? <span className="text-xs text-cyan-200">تحديث {new Date(query.data.as_of).toLocaleString("ar-SA")}</span> : undefined} />
        {query.isLoading ? <p className="mt-5 rounded-2xl border border-white/8 bg-card/70 p-5 text-sm text-muted-foreground">جارٍ تحميل توصيات Core…</p> : null}
        {query.isError ? <p className="mt-5 rounded-2xl border border-rose-400/20 bg-rose-400/5 p-5 text-sm text-rose-100">تعذر جلب توصيات Core الحية. لم تُعرض بيانات بديلة.</p> : null}
        {!query.isLoading && !query.isError && rows.length === 0 ? <p className="mt-5 rounded-2xl border border-white/8 bg-card/70 p-5 text-sm text-muted-foreground">لا توجد توصيات أو صفقات متتبعة لهذا الحساب حالياً.</p> : null}
        {!query.isLoading && !query.isError && rows.length > 0 ? (
          <div className="mt-5 grid gap-4 lg:grid-cols-3">
            {rows.map((row) => (
              <article key={row.public_ref} className="rounded-3xl border border-white/8 bg-card/70 p-5">
                <div className="flex items-start justify-between"><div><p className="font-mono text-[11px] text-cyan-300">{row.display_ref}</p><h2 className="mt-2 text-2xl font-semibold">{row.asset}</h2></div><StatusPill value={row.status} /></div>
                <p className={row.side.toUpperCase() === "LONG" ? "mt-3 text-sm text-emerald-300" : "mt-3 text-sm text-rose-300"}>{row.side.toUpperCase()} · {row.source_type}</p>
                {row.source?.public_ref ? <p className="mt-2 text-xs text-muted-foreground">المصدر: <span className="font-mono text-cyan-200">{row.source.public_ref}</span></p> : null}
                <div className="mt-5 grid grid-cols-2 gap-3"><Cell label="Entry" value={row.entry} /><Cell label="Stop" value={row.stop_loss} /></div>
                <div className="mt-3 rounded-2xl bg-white/[.035] p-4"><p className="text-[11px] text-muted-foreground">الأهداف</p><p className="mt-1 text-sm font-medium">{row.targets.length} targets</p></div>
                <button type="button" onClick={() => selectRecommendation(row.public_ref)} className="mt-4 rounded-xl border border-cyan-300/25 px-3 py-2 text-xs font-medium text-cyan-100 transition hover:bg-cyan-300/10">عرض التفاصيل</button>
                <div className="mt-5 border-t border-white/7 pt-4"><p className="mb-3 flex items-center gap-2 text-xs font-medium"><ListTree className="h-4 w-4 text-violet-300" />Timeline</p><div className="space-y-2 text-xs text-muted-foreground">{row.timeline.length ? row.timeline.map((event) => <p key={`${event.event_type}-${event.event_timestamp}`}>• {event.event_type} <span className="text-cyan-200">{new Date(event.event_timestamp).toLocaleString("ar-SA")}</span></p>) : <p>لا توجد أحداث مسجلة بعد.</p>}</div></div>
              </article>
            ))}
          </div>
        ) : null}
        {selectedRef ? <DetailSheet /> : null}
      </div>
    </DashboardLayout>
  );

  function DetailSheet() {
    if (detailQuery.isLoading) return <CommandSheet><p className="mt-4 text-sm text-muted-foreground">جارٍ التحقق من الملكية وتحميل التفاصيل…</p></CommandSheet>;
    if (detailQuery.isError || !detailQuery.data) return <CommandSheet><p className="mt-4 text-sm text-rose-200">التفاصيل غير متاحة لهذا الحساب أو تعذر الاتصال بـCore.</p></CommandSheet>;
    const item = detailQuery.data.item;
    const remainingOpenSizePercent = Number(item.open_size_percent);
    const partialAvailable = Number.isFinite(remainingOpenSizePercent) && remainingOpenSizePercent > 0;
    return <CommandSheet>
      <div className="mt-4 grid grid-cols-2 gap-3"><Cell label="الحالة" value={item.status} /><Cell label="الحجم المتبقي" value={`${item.open_size_percent}%`} /><Cell label="تحديث العقد" value={detailQuery.data.schema_version} /><Cell label="وقت القراءة" value={new Date(detailQuery.data.as_of).toLocaleString("ar-SA")} /><Cell label="المصدر" value={item.source?.public_ref ?? "سجل متداول مباشر"} /></div>
      {["WATCHLIST", "PENDING_ACTIVATION"].includes(item.status) ? <div className="mt-4 rounded-2xl border border-amber-300/20 bg-amber-300/5 p-4"><p className="text-sm font-medium text-amber-100">إلغاء تتبع معلق آمن</p><p className="mt-1 text-xs text-muted-foreground">لم تدخل هذه الصفقة السوق بعد. لن يرسل المتصفح سعراً، ولن يسجل Core سعر خروج أو PnL.</p>{closeNotice ? <p className="mt-3 text-xs text-cyan-100">{closeNotice}</p> : null}{!cancelIdempotencyKey ? <button type="button" onClick={requestCancelPending} className="mt-3 rounded-xl border border-amber-300/35 px-3 py-2 text-xs font-medium text-amber-100 hover:bg-amber-300/10">طلب إلغاء التتبع</button> : <div className="mt-3 flex flex-wrap gap-2"><button type="button" disabled={cancelMutation.isPending} onClick={confirmCancelPending} className="inline-flex items-center gap-2 rounded-xl bg-amber-300 px-3 py-2 text-xs font-semibold text-slate-950 disabled:opacity-60">{cancelMutation.isPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : null}تأكيد الإلغاء</button><button type="button" disabled={cancelMutation.isPending} onClick={() => { setCancelIdempotencyKey(null); setCloseNotice(null); }} className="rounded-xl border border-white/15 px-3 py-2 text-xs text-muted-foreground">رجوع</button></div>}</div> : null}
      {item.status === "ACTIVATED" ? <div className="mt-4 space-y-3 rounded-2xl border border-amber-300/20 bg-amber-300/5 p-4"><div><p className="text-sm font-medium text-amber-100">إدارة صفقة مفعّلة بأمان</p><p className="mt-1 text-xs text-muted-foreground">لا يرسل المتصفح سعراً. يطلب Core سعراً موثوقاً ويفرض الملكية والحالة والحجم المتبقي.</p></div>{closeNotice ? <p className="text-xs text-cyan-100">{closeNotice}</p> : null}{partialAvailable ? <div className="rounded-xl border border-white/10 bg-black/10 p-3"><p className="text-xs font-medium text-cyan-100">إغلاق جزئي</p><p className="mt-1 text-[11px] text-muted-foreground">أدخل نسبة من الكمية الأصلية، موجبة وأقل من المتبقي ({item.open_size_percent}%). الإغلاق الكامل له إجراء مستقل.</p><label className="mt-3 block text-xs text-muted-foreground" htmlFor="partial-close-percent">نسبة الإغلاق الجزئي</label><input id="partial-close-percent" aria-label="نسبة الإغلاق الجزئي" inputMode="decimal" type="number" min="0.01" max={Math.max(0, remainingOpenSizePercent - 0.01)} step="0.01" value={partialClosePercent} onChange={(event) => { setPartialClosePercent(event.target.value); setPartialCloseIdempotencyKey(null); }} className="mt-1 w-full rounded-xl border border-white/15 bg-slate-950 px-3 py-2 text-sm outline-none ring-cyan-300/50 focus:ring-2" placeholder="مثال: 25" />{!partialCloseIdempotencyKey ? <button type="button" onClick={() => requestPartialClose(remainingOpenSizePercent)} className="mt-3 rounded-xl border border-cyan-300/35 px-3 py-2 text-xs font-medium text-cyan-100 hover:bg-cyan-300/10">طلب إغلاق جزئي</button> : <div className="mt-3 flex flex-wrap gap-2"><button type="button" disabled={partialCloseMutation.isPending} onClick={confirmPartialClose} className="inline-flex items-center gap-2 rounded-xl bg-cyan-300 px-3 py-2 text-xs font-semibold text-slate-950 disabled:opacity-60">{partialCloseMutation.isPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : null}تأكيد الإغلاق الجزئي</button><button type="button" disabled={partialCloseMutation.isPending} onClick={() => { setPartialCloseIdempotencyKey(null); setCloseNotice(null); }} className="rounded-xl border border-white/15 px-3 py-2 text-xs text-muted-foreground">إلغاء</button></div>}</div> : <p className="rounded-xl border border-amber-300/20 p-3 text-xs text-amber-100">لا تتوفر قيمة الحجم المتبقي من Core حالياً؛ أعد تحديث القراءة قبل طلب إغلاق جزئي.</p>}{!closeIdempotencyKey ? <button type="button" onClick={requestClose} className="rounded-xl border border-amber-300/35 px-3 py-2 text-xs font-medium text-amber-100 hover:bg-amber-300/10">طلب إغلاق يدوي كامل</button> : <div className="flex flex-wrap gap-2"><button type="button" disabled={closeMutation.isPending} onClick={confirmClose} className="inline-flex items-center gap-2 rounded-xl bg-amber-300 px-3 py-2 text-xs font-semibold text-slate-950 disabled:opacity-60">{closeMutation.isPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : null}تأكيد الإغلاق الكامل</button><button type="button" disabled={closeMutation.isPending} onClick={() => { setCloseIdempotencyKey(null); setCloseNotice(null); }} className="rounded-xl border border-white/15 px-3 py-2 text-xs text-muted-foreground">إلغاء</button></div>}</div> : null}
      {!["WATCHLIST", "PENDING_ACTIVATION", "ACTIVATED"].includes(item.status) ? <p className="mt-4 text-xs text-emerald-200">هذا السجل طرفي بالفعل؛ لا يظهر أمر إغلاق أو إلغاء مكرر.</p> : null}
      <p className="mt-4 text-xs text-muted-foreground">الواجهة تفرّق بين إلغاء سجل لم يدخل السوق وإغلاق جزئي أو كامل لسجل مفعّل فقط؛ لا يوجد تنفيذ سوقي أو تعديل تلقائي.</p>
    </CommandSheet>;
  }

  function CommandSheet({ children }: { children: React.ReactNode }) {
    return <section className="fixed inset-x-4 bottom-4 z-50 mx-auto max-w-xl rounded-3xl border border-cyan-300/20 bg-slate-950/95 p-5 shadow-2xl backdrop-blur" aria-live="polite"><div className="flex items-start justify-between gap-4"><div><p className="font-mono text-xs text-cyan-300">{selectedRef}</p><h2 className="mt-1 text-lg font-semibold">تفاصيل قراءة من Core</h2></div><button type="button" onClick={() => selectRecommendation("")} aria-label="إغلاق التفاصيل" className="rounded-lg p-2 text-muted-foreground hover:bg-white/10"><X className="h-4 w-4" /></button></div>{children}</section>;
  }
}

function Cell({ label, value }: { label: string; value: unknown }) {
  return <div className="rounded-xl bg-white/[.04] p-3"><p className="text-[10px] text-muted-foreground">{label}</p><p className="mt-1 text-sm font-medium">{String(value ?? "—")}</p></div>;
}
