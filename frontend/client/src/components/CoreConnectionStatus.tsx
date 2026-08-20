import { getTelegramInitData } from "@/lib/tma";
import { trpc } from "@/lib/trpc";
import { Cloud, ShieldCheck, WifiOff } from "lucide-react";

export default function CoreConnectionStatus() {
  const core = trpc.capitalguard.core.health.useQuery(undefined, { retry: false, refetchInterval: 30_000 });
  const inTma = Boolean(getTelegramInitData());
  const healthy = core.data?.status === "ok";
  return <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/8 bg-white/[.025] px-4 py-3 text-xs"><div className="flex items-center gap-2"><span className={healthy ? "text-emerald-300" : "text-amber-200"}>{healthy ? <Cloud className="h-4 w-4"/> : <WifiOff className="h-4 w-4"/>}</span><span>{healthy ? "CapitalGuard Core متصل" : "يتم التحقق من Core"}</span></div><span className={inTma ? "inline-flex items-center gap-1 text-cyan-200" : "text-muted-foreground"}>{inTma ? <><ShieldCheck className="h-3.5 w-3.5"/>هوية Telegram جاهزة</> : "افتح من Telegram لعرض المحفظة المرتبطة"}</span></div>;
}
