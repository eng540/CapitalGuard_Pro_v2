import { applyTelegramViewport, getTelegramWebApp } from "@/lib/tma";
import { trpc } from "@/lib/trpc";
import { useEffect, useState } from "react";

export default function TmaBridge() {
  const [insideTelegram, setInsideTelegram] = useState(false);
  const [authState, setAuthState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const utils = trpc.useUtils();
  const telegramLogin = trpc.auth.telegram.useMutation({
    onSuccess: user => {
      utils.auth.me.setData(undefined, user);
      void utils.auth.me.invalidate();
      setAuthState("ready");
    },
    onError: () => setAuthState("error"),
  });

  const retryVerification = () => {
    const app = getTelegramWebApp();
    if (!app?.initData) {
      setAuthState("error");
      return;
    }
    setAuthState("loading");
    telegramLogin.mutate({ initData: app.initData });
  };

  useEffect(() => {
    const app = getTelegramWebApp();
    if (!app) return;
    app.ready?.();
    app.expand?.();
    applyTelegramViewport(app);
    setInsideTelegram(true);
    if (!app.initData || telegramLogin.isPending) { setAuthState("error"); return; }
    retryVerification();
  // Telegram injects initData once for each Mini App launch; do not re-auth on renders.
  // eslint-disable-next-line react-hooks/exhaustive-deps
    const back = () => window.history.back();
    app.BackButton?.onClick?.(back);
    return () => app.BackButton?.offClick?.(back);
  }, []);
  if (!insideTelegram) return null;
  const label = authState === "ready" ? "جلسة Telegram مؤمنة" : authState === "error" ? "تعذر التحقق من جلسة Telegram" : "جارٍ تأمين جلسة Telegram…";
  return <div className="fixed left-3 z-50 rounded-full border border-cyan-300/15 bg-[#0c1728]/90 px-3 py-1.5 text-[10px] text-cyan-200 shadow-lg backdrop-blur" style={{ bottom: "calc(0.75rem + var(--tma-safe-bottom, 0px))" }}>{authState === "error" ? <button type="button" onClick={retryVerification} className="underline decoration-cyan-300/50 underline-offset-2">{label} · أعد المحاولة</button> : label}</div>;
}
