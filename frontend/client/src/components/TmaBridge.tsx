import { getTelegramWebApp } from "@/lib/tma";
import { useEffect, useState } from "react";

export default function TmaBridge() {
  const [insideTelegram, setInsideTelegram] = useState(false);
  useEffect(() => {
    const app = getTelegramWebApp();
    if (!app) return;
    app.ready?.();
    app.expand?.();
    setInsideTelegram(true);
  }, []);
  if (!insideTelegram) return null;
  return <div className="fixed bottom-3 left-3 z-50 rounded-full border border-cyan-300/15 bg-[#0c1728]/90 px-3 py-1.5 text-[10px] text-cyan-200 shadow-lg backdrop-blur">Telegram Mini App</div>;
}
