import React from "react";
import DashboardLayout from "@/components/DashboardLayout";
import { SectionTitle } from "@/components/finance-ui";
import { ArrowUpRight, BarChart3, ClipboardCheck, FileClock, Gauge, Megaphone, Settings2, ShieldCheck } from "lucide-react";
import { Link } from "wouter";

type Destination = { href: string; icon: React.ComponentType<{ className?: string }>; title: string; detail: string };

const groups: Array<{ eyebrow: string; title: string; detail: string; destinations: Destination[] }> = [
  { eyebrow: "01 · بناء", title: "إنشاء القرار", detail: "أنشئ إشارة أو احسب المخاطرة قبل النشر أو التنفيذ اليدوي.", destinations: [
    { href: "/analyst/workspace", icon: Megaphone, title: "إنشاء ونشر توصية", detail: "ابنِ الإشارة، اعرض معاينة Core، ثم راقب حالة التسليم." },
    { href: "/risk", icon: ShieldCheck, title: "استوديو المخاطر", detail: "احسب الخطة والتعرض قبل أي قرار يدوي." },
  ] },
  { eyebrow: "02 · حوكمة", title: "المراجعة والتشغيل", detail: "افحص المصدر والاستثناءات والأدلة وقرارات Replay دون خلطها بالمحفظة اليومية.", destinations: [
    { href: "/admin", icon: ClipboardCheck, title: "مركز المراجعة", detail: "راجع المصادر والاستثناءات وقرارات Evidence وReplay." },
    { href: "/historical", icon: FileClock, title: "التدقيق التاريخي", detail: "استعرض جلسات الاستقبال والاستخراج ونتائج Replay الموثقة." },
  ] },
  { eyebrow: "03 · بحث", title: "الاستكشاف والتحليل", detail: "استكشف الإشارات والمحللين كقراءة مستقلة من Core.", destinations: [
    { href: "/analysts", icon: BarChart3, title: "اكتشاف المحللين", detail: "قارن الأداء العام وفق بيانات Core وأهلية العينة." },
    { href: "/signals", icon: Gauge, title: "اكتشاف الإشارات", detail: "ابحث في الإشارات العامة دون إنشاء حالة محلية." },
  ] },
];

function DestinationCard({ destination }: { destination: Destination }) {
  const Icon = destination.icon;
  return <Link href={destination.href} className="group rounded-2xl border border-white/10 bg-white/[.025] p-4 transition hover:-translate-y-0.5 hover:border-violet-300/40 hover:bg-violet-300/[.06]">
    <div className="flex items-start justify-between gap-3"><span className="rounded-xl bg-violet-300/10 p-2 text-violet-200"><Icon className="h-5 w-5" /></span><ArrowUpRight className="h-4 w-4 text-muted-foreground transition group-hover:text-violet-200" /></div>
    <h3 className="mt-4 font-semibold">{destination.title}</h3>
    <p className="mt-2 text-xs leading-5 text-muted-foreground">{destination.detail}</p>
  </Link>;
}

export default function StudioHub() {
  return <DashboardLayout><main dir="rtl" className="mx-auto max-w-[1200px]">
    <header className="mb-8"><p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-violet-300">Pro Studio & Operations</p><h1 className="text-3xl font-semibold tracking-tight">الاستوديو والعمليات</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">مساحة متقدمة مقسمة حسب المهمة: بناء القرار، الحوكمة، ثم البحث. لا تستخدمها لاستقبال رسالة أو متابعة صفقة؛ انتقل إلى الرادار أو المحفظة.</p></header>
    <div className="space-y-5">{groups.map(group => <section key={group.title} className="rounded-3xl border border-violet-300/15 bg-violet-300/[.035] p-5"><SectionTitle eyebrow={group.eyebrow} title={group.title} action={<span className="text-xs text-violet-200">{group.destinations.length} مساحات</span>} /><p className="mt-2 text-xs leading-5 text-muted-foreground">{group.detail}</p><div className="mt-4 grid gap-3 sm:grid-cols-2">{group.destinations.map(destination => <DestinationCard key={destination.href} destination={destination} />)}</div></section>)}</div>
    <section className="mt-5 rounded-2xl border border-white/8 bg-card/60 p-4 text-xs leading-6 text-muted-foreground"><Settings2 className="ml-2 inline h-4 w-4 text-violet-200" /> الأدوات المتقدمة تقرأ من Core ولا تنشئ حالة محلية بديلة. كل أمر حساس يعرض سببه وحالته قبل التنفيذ.</section>
  </main></DashboardLayout>;
}
