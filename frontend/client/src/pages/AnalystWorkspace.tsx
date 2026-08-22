import { useEffect, useMemo, useState } from "react";
import DashboardLayout from "@/components/DashboardLayout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { normalizeFinancialNumber, normalizeSymbol } from "@/lib/financial-input";
import { analystValidationMessage } from "@/lib/analyst-validation-message";
import { getAnalystTradeFlow, type AnalystMarket, type AnalystOrderType, type AnalystSide } from "@/lib/analyst-trade-flow";
import { trpc } from "@/lib/trpc";

type Target = { price: string; percent: string };
type PublicationState = "QUEUED" | "PUBLISHING" | "DELIVERED" | "RETRYING" | "FAILED" | "SAVED";

const publicationCopy: Record<PublicationState, { label: string; className: string }> = {
  SAVED: { label: "محفوظة من دون قنوات", className: "text-slate-200" },
  QUEUED: { label: "في انتظار Outbox", className: "text-cyan-200" },
  PUBLISHING: { label: "جارٍ التسليم", className: "text-cyan-200" },
  DELIVERED: { label: "تم التسليم", className: "text-emerald-200" },
  RETRYING: { label: "إعادة محاولة مجدولة", className: "text-amber-200" },
  FAILED: { label: "تعذر التسليم", className: "text-rose-200" },
};

function ChoiceCard<T extends string>({ value, selected, title, detail, onSelect }: { value: T; selected: boolean; title: string; detail: string; onSelect: (value: T) => void }) {
  return <button type="button" onClick={() => onSelect(value)} className={`rounded-2xl border p-4 text-right transition ${selected ? "border-cyan-300 bg-cyan-300/10 shadow-[0_0_0_1px_rgba(103,232,249,.15)]" : "border-white/10 bg-white/[.02] hover:border-white/25"}`}><p className="font-semibold">{title}</p><p className="mt-1 text-xs text-muted-foreground">{detail}</p></button>;
}

function ComposerError({ error }: { error: { message: string } | null | undefined }) {
  if (!error) return null;
  const validation = analystValidationMessage(error.message);
  return <div className="rounded-2xl border border-rose-400/20 bg-rose-500/10 p-4 text-sm text-rose-100"><p className="font-semibold">تحقق حقل: {validation.title}</p><p className="mt-1">{validation.message}</p></div>;
}

export default function AnalystWorkspace() {
  const [market, setMarket] = useState<AnalystMarket>("Futures");
  const [side, setSide] = useState<AnalystSide>("LONG");
  const [orderType, setOrderType] = useState<AnalystOrderType>("LIMIT");
  const [asset, setAsset] = useState("BTCUSDT");
  const [entry, setEntry] = useState("");
  const [stopLoss, setStopLoss] = useState("");
  const [notes, setNotes] = useState("");
  const [targets, setTargets] = useState<Target[]>([{ price: "", percent: "100" }]);
  const [preview, setPreview] = useState<any>(null);
  const [selectedChannelIds, setSelectedChannelIds] = useState<number[]>([]);

  const symbol = normalizeSymbol(asset);
  const tradeFlow = getAnalystTradeFlow(market, orderType);
  const isMarket = !tradeFlow.manualEntryRequired;
  const assets = trpc.capitalguard.analyst.assets.useQuery({ market });
  const channels = trpc.capitalguard.analyst.publicationChannels.useQuery();
  const price = trpc.capitalguard.core.price.useQuery({ symbol }, { enabled: symbol.length >= 3, refetchInterval: 15_000 });
  const previewMutation = trpc.capitalguard.analyst.previewRecommendation.useMutation({ onSuccess: setPreview });
  const confirmMutation = trpc.capitalguard.analyst.confirmRecommendation.useMutation();
  const confirmedPublicRef = confirmMutation.data?.public_ref;
  const publicationStatus = trpc.capitalguard.analyst.recommendationPublication.useQuery(
    { publicRef: confirmedPublicRef ?? "pending" },
    {
      enabled: Boolean(confirmedPublicRef),
      refetchInterval: query => {
        const state = query.state.data?.publication.state;
        return state === "QUEUED" || state === "PUBLISHING" || state === "RETRYING" ? 5_000 : false;
      },
    },
  );

  const availableChannels = channels.data ?? [];
  const assetSuggestions = assets.data?.map(item => item.symbol) ?? [];
  const currentPrice = price.data && typeof price.data === "object" && "price" in price.data ? Number((price.data as { price: unknown }).price) : Number.NaN;
  const parsedTargets = useMemo(() => targets.map(target => ({ price: normalizeFinancialNumber(target.price), percent: normalizeFinancialNumber(target.percent) })), [targets]);
  const targetTotal = parsedTargets.reduce((sum, target) => sum + (Number.isFinite(target.percent) ? target.percent : 0), 0);
  const publication = publicationStatus.data?.publication;
  const publicationState = publication?.state ?? confirmMutation.data?.publication.state;
  const publicationTone = publicationState ? publicationCopy[publicationState as PublicationState] : null;

  useEffect(() => { if (!tradeFlow.allowedSides.includes(side)) setSide("LONG"); }, [side, tradeFlow.allowedSides]);
  useEffect(() => { if (channels.data) setSelectedChannelIds(channels.data.map(channel => channel.id)); }, [channels.data]);
  useEffect(() => { if (assets.data?.length && !assets.data.some(item => item.symbol === symbol)) setAsset(assets.data[0].symbol); }, [assets.data, symbol]);

  const updateTarget = (index: number, key: keyof Target, value: string) => setTargets(items => items.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item));
  const payload = () => ({ asset: symbol, side, market, orderType, entry: isMarket ? 0 : normalizeFinancialNumber(entry), stopLoss: normalizeFinancialNumber(stopLoss), targetsRaw: parsedTargets.map(target => `${target.price}@${target.percent}`).join(" "), notes, leverage: "20", channelIds: selectedChannelIds });
  const entryIsValid = isMarket || (Number.isFinite(normalizeFinancialNumber(entry)) && normalizeFinancialNumber(entry) > 0);
  const canPreview = symbol.length >= 3 && entryIsValid && Number.isFinite(normalizeFinancialNumber(stopLoss)) && normalizeFinancialNumber(stopLoss) > 0 && parsedTargets.length > 0 && parsedTargets.every(target => target.price > 0 && target.percent >= 0 && Number.isFinite(target.price) && Number.isFinite(target.percent)) && Math.abs(targetTotal - 100) < 0.001;

  return <DashboardLayout><main dir="rtl" className="mx-auto max-w-4xl space-y-6"><header><p className="text-xs font-semibold uppercase tracking-[.2em] text-cyan-300">Analyst Desk · Guided Flow</p><h1 className="mt-2 text-3xl font-semibold">إنشاء توصية بخطوات واضحة</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">اتبع التسلسل من السوق إلى الأمر؛ Core هو مصدر السعر والتحقق والنشر.</p></header>
    <section className="space-y-6 rounded-3xl border border-white/10 bg-card/70 p-5"><Step title="١. السوق"><div className="grid gap-3 sm:grid-cols-2"><ChoiceCard value="Spot" selected={market === "Spot"} onSelect={setMarket} title="Spot" detail="شراء Long فقط؛ لا يوجد Short."/><ChoiceCard value="Futures" selected={market === "Futures"} onSelect={setMarket} title="Futures" detail="يمكن اختيار Long أو Short."/></div></Step><Step title="٢. الاتجاه"><div className="grid gap-3 sm:grid-cols-2"><ChoiceCard value="LONG" selected={side === "LONG"} onSelect={setSide} title="LONG" detail="الاستفادة من صعود السعر."/>{market === "Futures" ? <ChoiceCard value="SHORT" selected={side === "SHORT"} onSelect={setSide} title="SHORT" detail="الاستفادة من هبوط السعر."/> : null}</div></Step><Step title="٣. الأصل"><div className="grid gap-3 sm:grid-cols-[1fr_200px]"><div><Input list="asset-suggestions" value={asset} onChange={event => setAsset(event.target.value)} onBlur={() => setAsset(symbol)} placeholder="مثال BTCUSDT"/><datalist id="asset-suggestions">{assetSuggestions.map(item => <option key={item} value={item}/>)}</datalist><p className="mt-2 text-xs text-muted-foreground">{assets.isLoading ? "جارٍ تحميل اقتراحات Core…" : assets.isError ? "تعذر تحميل الاقتراحات؛ أدخل رمزاً صحيحاً يدوياً." : `اقتراحات Core لـ${market}: ${assetSuggestions.join(" · ")}`}</p></div><Metric label="السعر الحالي من Core" value={Number.isFinite(currentPrice) ? currentPrice.toLocaleString() : price.isLoading ? "جارٍ التحميل…" : "غير متاح"}/></div></Step><Step title="٤. نوع الأمر"><div className="grid gap-3 sm:grid-cols-3"><ChoiceCard value="MARKET" selected={orderType === "MARKET"} onSelect={setOrderType} title="Market" detail="يدخل بسعر Core الحي؛ لا تدخل سعراً يدوياً."/><ChoiceCard value="LIMIT" selected={orderType === "LIMIT"} onSelect={setOrderType} title="Limit" detail="أدخل السعر المطلوب لتنفيذ الطلب."/><ChoiceCard value="STOP_MARKET" selected={orderType === "STOP_MARKET"} onSelect={setOrderType} title="Stop Market" detail="أدخل سعر التفعيل؛ ينفذ كسوق عند تحققه."/></div></Step></section>
    <section className="grid gap-4 rounded-3xl border border-white/10 bg-card/70 p-5 md:grid-cols-2">{isMarket ? <div className="rounded-2xl border border-dashed border-white/10 bg-white/[.02] p-4"><p className="font-medium">سعر الدخول تلقائي</p><p className="mt-1 text-xs text-muted-foreground">سيستخدم Core السعر الحي عند المعاينة والتأكيد.</p></div> : <label className="space-y-2 text-sm"><span>{orderType === "STOP_MARKET" ? "سعر التفعيل" : "سعر الدخول"}</span><Input inputMode="decimal" value={entry} onChange={event => setEntry(event.target.value)} placeholder="مثال 77K أو ٧٧٠٠٠"/></label>}<label className="space-y-2 text-sm"><span>وقف الخسارة</span><Input inputMode="decimal" value={stopLoss} onChange={event => setStopLoss(event.target.value)} placeholder="مثال 75K أو ٧٥٠٠٠"/></label></section>
    <section className="rounded-3xl border border-white/10 bg-card/70 p-5"><div className="flex items-center justify-between"><div><p className="text-xs font-semibold text-cyan-300">٥. إدارة الخروج</p><h2 className="mt-1 font-semibold">الأهداف ونسب الإغلاق</h2></div><Button size="sm" variant="outline" onClick={() => setTargets([...targets, { price: "", percent: "" }])}>إضافة هدف</Button></div><div className="mt-4 space-y-2">{targets.map((target, index) => <div key={index} className="grid grid-cols-[1fr_110px_38px] gap-2"><Input inputMode="decimal" value={target.price} placeholder={`هدف ${index + 1}`} onChange={event => updateTarget(index, "price", event.target.value)}/><Input inputMode="decimal" value={target.percent} placeholder="نسبة %" onChange={event => updateTarget(index, "percent", event.target.value)}/><Button size="icon" variant="ghost" disabled={targets.length === 1} onClick={() => setTargets(targets.filter((_, itemIndex) => itemIndex !== index))}>×</Button></div>)}</div><p className={Math.abs(targetTotal - 100) < 0.001 ? "mt-3 text-xs text-emerald-300" : "mt-3 text-xs text-amber-200"}>مجموع نسب الإغلاق: {Number.isFinite(targetTotal) ? targetTotal : 0}%</p></section>
    <section className="rounded-3xl border border-white/10 bg-card/70 p-5"><div className="flex flex-wrap justify-between gap-3"><div><p className="text-xs font-semibold text-cyan-300">٦. النشر</p><h2 className="mt-1 font-semibold">اختر القنوات</h2></div><div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => setSelectedChannelIds(availableChannels.map(channel => channel.id))}>تحديد الكل</Button><Button size="sm" variant="ghost" onClick={() => setSelectedChannelIds([])}>استبعاد الكل</Button></div></div><div className="mt-3 space-y-2">{availableChannels.map(channel => <label key={channel.id} className="flex cursor-pointer items-center justify-between rounded-2xl border border-white/8 bg-white/[.025] p-3 text-sm"><span>{channel.title}{channel.username ? <span className="mr-2 text-xs text-muted-foreground">@{channel.username}</span> : null}</span><input type="checkbox" checked={selectedChannelIds.includes(channel.id)} onChange={() => setSelectedChannelIds(current => current.includes(channel.id) ? current.filter(id => id !== channel.id) : [...current, channel.id])} className="h-4 w-4 accent-cyan-400" /></label>)}</div></section>
    <section className="rounded-3xl border border-white/10 bg-card/70 p-5"><label className="space-y-2 text-sm"><span>ملاحظة اختيارية</span><Textarea value={notes} onChange={event => setNotes(event.target.value)} /></label><div className="mt-5 flex flex-wrap gap-3"><Button onClick={() => previewMutation.mutate(payload())} disabled={!canPreview || previewMutation.isPending}>{previewMutation.isPending ? "جارٍ التحقق…" : "معاينة آمنة"}</Button><Button variant="outline" disabled={!preview || confirmMutation.isPending} onClick={() => confirmMutation.mutate(payload())}>{confirmMutation.isPending ? "جارٍ التأكيد…" : "تأكيد وإنشاء"}</Button></div>{!canPreview ? <p className="mt-3 text-xs text-amber-200">أكمل الحقول المطلوبة واجعل نسب الأهداف 100% قبل المعاينة.</p> : null}</section>
    <ComposerError error={previewMutation.error}/>{preview ? <section className="rounded-3xl border border-cyan-300/20 bg-cyan-300/[.04] p-5"><p className="font-semibold text-cyan-200">تمت المعاينة — لا توجد عملية كتابة</p><p className="mt-2 text-sm">الدخول الفعلي: {preview.entry} · السعر الحي: {preview.live_price ?? "غير متاح"} · القنوات المؤهلة: {preview.publication.eligible_channel_count}</p></section> : null}
    {confirmMutation.data ? <section className="rounded-3xl border border-emerald-300/20 bg-emerald-300/[.04] p-5 text-sm"><p className="font-semibold text-emerald-200">تم التأكيد: {confirmMutation.data.public_ref}</p><p className="mt-2 text-muted-foreground">حالة الإنشاء: {confirmMutation.data.publication.state} · تسليمات Outbox: {confirmMutation.data.publication.queued_delivery_count}</p><PublicationPanel publication={publication} state={publicationState} tone={publicationTone} isLoading={publicationStatus.isLoading} isError={publicationStatus.isError} onRetry={() => publicationStatus.refetch()}/></section> : null}
  </main></DashboardLayout>;
}

function Step({ title, children }: { title: string; children: React.ReactNode }) { return <div><p className="mb-3 text-xs font-semibold text-cyan-300">{title}</p>{children}</div>; }
function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded-2xl border border-cyan-300/15 bg-cyan-300/[.04] p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 font-mono text-lg text-cyan-200">{value}</p></div>; }
function PublicationPanel({ publication, state, tone, isLoading, isError, onRetry }: { publication: any; state: string | undefined; tone: { label: string; className: string } | null; isLoading: boolean; isError: boolean; onRetry: () => void }) {
  if (!state) return null;
  if (isLoading && !publication) return <p className="mt-4 text-xs text-cyan-100">جارٍ جلب حالة التسليم من Outbox…</p>;
  if (isError) return <div className="mt-4 rounded-2xl border border-amber-300/20 bg-amber-300/5 p-3"><p className="text-xs text-amber-100">تعذر تحديث حالة التسليم الآن. لم نعتبر النشر ناجحاً.</p><Button className="mt-2" size="sm" variant="outline" onClick={onRetry}>تحديث الحالة</Button></div>;
  return <div className="mt-4 rounded-2xl border border-white/10 bg-slate-950/30 p-4"><div className="flex items-center justify-between gap-4"><p className="font-medium">متابعة التسليم لكل قناة</p><p className={`text-xs font-semibold ${tone?.className ?? "text-muted-foreground"}`}>{tone?.label ?? state}</p></div><p className="mt-1 text-xs text-muted-foreground">تتحدث الحالة تلقائياً كل 5 ثوانٍ فقط أثناء وجود تسليم قيد التنفيذ أو إعادة محاولة.</p><div className="mt-3 space-y-2">{publication?.channels?.map((channel: any) => { const channelTone = publicationCopy[channel.state as PublicationState]; const retryScheduled = channel.state === "RETRYING" && Boolean(channel.next_attempt_at); return <div key={channel.channel_id} className="flex items-center justify-between rounded-xl border border-white/8 bg-white/[.025] px-3 py-2"><div><p className="text-sm">{channel.channel_title}</p><p className="text-[11px] text-muted-foreground">محاولات: {channel.attempts}{retryScheduled ? " · إعادة محاولة مجدولة" : ""}</p></div><span className={`text-xs font-medium ${channelTone?.className ?? "text-muted-foreground"}`}>{channelTone?.label ?? channel.state}</span></div>; })}</div>{publication?.failed_count ? <p className="mt-3 text-xs text-rose-100">فشل تسليم قناة واحدة أو أكثر. تحقق من عضوية البوت وصلاحية النشر في القناة، ثم راقب حالة إعادة المحاولة؛ لا تعِد إنشاء التوصية.</p> : null}<Button className="mt-3" size="sm" variant="outline" onClick={onRetry}>تحديث الحالة</Button></div>;
}
