import React from "react";
import DashboardLayout from "@/components/DashboardLayout";
import { SectionTitle } from "@/components/finance-ui";
import { ArrowUpRight, BarChart3, ClipboardCheck, FileClock, Gauge, Megaphone, Settings2, ShieldCheck } from "lucide-react";
import { Link } from "wouter";

const destinations = [
  { href: "/analyst/workspace", icon: Megaphone, title: "إنشاء ونشر توصية", detail: "ابنِ الإشارة، اعرض معاينة Core، ثم راقب حالة التسليم." },
  { href: "/admin", icon: ClipboardCheck, title: "مركز المراجعة", detail: "راجع المصادر والاستثناءات وقرارات Evidence وReplay." },
  { href: "/historical", icon: FileClock, title: "التدقيق التاريخي", detail: "استعرض جلسات الاستقبال، الاستخراج، ونتائج Replay الموثقة." },
  { href: "/analysts", icon: BarChart3, title: "اكتشاف المحللين", detail: "قارن الأداء العام وفق بيانات Core وأهلية العينة." },
  { href: "/signals", icon: Gauge, title: "اكتشاف الإشارات", detail: "ابحث في الإشارات العامة دون إنشاء حالة محلية." },
  { href: "/risk", icon: ShieldCheck, title: "استوديو المخاطر", detail: "احسب الخطة والتعرض قبل أي قرار يدوي." },
];

export default function StudioHub() {
  return <DashboardLayout><main dir="rtl" className="mx-auto max-w-[1200px]">
    <header className="mb-8">
      <p className="mb-2 text-xs font-semibold uppercase tracking-[.2em] text-violet-300">Pro Studio & Operations</p>
      <h1 className="text-3xl font-semibold tracking-tight">الاستوديو والعمليات</h1>
      <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">مساحة المحلل والمالك. اختر مهمة واحدة؛ تبقى القرارات والبيانات داخل Core ولا تتداخل مع رادار المتداول.</p>
    </header>
    <section className="rounded-3xl border border-violet-300/15 bg-violet-300/[.04] p-5">
      <SectionTitle eyebrow="Choose a task" title="ما المهمة التي تريد تنفيذها؟" action={<span className="text-xs text-violet-200">6 مساحات متقدمة</span>} />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {destinations.map(({ href, icon: Icon, title, detail }) => <Link key={href} href={href} className="group rounded-2xl border border-white/10 bg-white/[.025] p-4 transition hover:-translate-y-0.5 hover:border-violet-300/40 hover:bg-violet-300/[.06]">
          <div className="flex items-start justify-between gap-3"><span className="rounded-xl bg-violet-300/10 p-2 text-violet-200"><Icon className="h-5 w-5" /></span><ArrowUpRight className="h-4 w-4 text-muted-foreground transition group-hover:text-violet-200" /></div>
          <h2 className="mt-4 font-semibold">{title}</h2>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">{detail}</p>
        </Link>)}
      </div>
    </section>
    <section className="mt-5 rounded-2xl border border-white/8 bg-card/60 p-4 text-xs leading-6 text-muted-foreground"><Settings2 className="ml-2 inline h-4 w-4 text-violet-200" /> تظهر هنا الأدوات المتقدمة فقط. لا تستخدم هذه المساحة لاستقبال رسالة أو متابعة صفقة؛ انتقل إلى الرادار أو المحفظة لهذه المهام.</section>
  </main></DashboardLayout>;
}
