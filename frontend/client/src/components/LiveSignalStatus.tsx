import { Bell, Radio } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { trpc } from "@/lib/trpc";

export default function LiveSignalStatus() {
  const signals = trpc.capitalguard.recommendations.useQuery(undefined, { refetchInterval: 15_000, refetchOnWindowFocus: true });
  const known = useRef<Set<string>>(new Set());
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">(() => typeof Notification === "undefined" ? "unsupported" : Notification.permission);
  useEffect(() => {
    const rows = signals.data?.items ?? [];
    const refs: string[] = rows.map(row => row.public_ref).filter(Boolean);
    if (!known.current.size) { refs.forEach(ref => known.current.add(ref)); return; }
    const incoming = refs.filter(ref => !known.current.has(ref));
    incoming.forEach(ref => known.current.add(ref));
    if (incoming.length && permission === "granted") new Notification("CapitalGuard · إشارة جديدة", { body: `${incoming.length} إشارة جديدة متاحة للمراجعة.` });
  }, [signals.data, permission]);
  const requestPermission = async () => { if (typeof Notification === "undefined") return; setPermission(await Notification.requestPermission()); };
  return <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/8 bg-white/[.025] px-4 py-3"><div className="flex items-center gap-2 text-xs"><Radio className="h-4 w-4 text-emerald-300"/><span className="font-medium">تحديث أثناء فتح الصفحة</span><span className="text-muted-foreground">· مراجعة كل 15 ثانية</span></div>{permission === "default" ? <button onClick={requestPermission} className="inline-flex items-center gap-2 rounded-lg bg-white/5 px-3 py-1.5 text-xs text-cyan-200 hover:bg-white/10"><Bell className="h-3.5 w-3.5"/>فعّل التنبيهات</button> : <span className="text-[11px] text-muted-foreground">{permission === "granted" ? "تنبيهات المتصفح مفعلة" : "تنبيهات المتصفح غير مفعلة"}</span>}</div>;
}
