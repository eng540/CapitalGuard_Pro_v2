import { getTelegramInitData } from "@/lib/tma";
import { trpc } from "@/lib/trpc";
import { Cloud, ShieldCheck, WifiOff } from "lucide-react";
import React from "react";

export default function CoreConnectionStatus() {
  const core = trpc.capitalguard.core.health.useQuery(undefined, { retry: false, refetchInterval: 30_000 });
  const auth = trpc.auth.me.useQuery(undefined, { retry: false });
  const hasTelegramInitData = Boolean(getTelegramInitData());
  const verifiedTelegramSession = Boolean(auth.data?.openId?.startsWith("telegram:"));
  const healthy = core.data?.status === "ok";
  const telegramLabel = verifiedTelegramSession ? "جلسة Telegram مؤمنة" : hasTelegramInitData && auth.isLoading ? "جارٍ تأمين جلسة Telegram" : hasTelegramInitData ? "جلسة Telegram غير موثقة" : "افتح من Telegram لعرض المحفظة المرتبطة";
  return <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/8 bg-white/[.025] px-4 py-3 text-xs"><div className="flex items-center gap-2"><span className={healthy ? "text-emerald-300" : "text-amber-200"}>{healthy ? <Cloud className="h-4 w-4"/> : <WifiOff className="h-4 w-4"/>}</span><span>{healthy ? "CapitalGuard Core متصل" : "يتم التحقق من Core"}</span></div><span className={verifiedTelegramSession ? "inline-flex items-center gap-1 text-cyan-200" : "text-amber-200"}>{verifiedTelegramSession ? <><ShieldCheck className="h-3.5 w-3.5"/>{telegramLabel}</> : telegramLabel}</span></div>;
}
