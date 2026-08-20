type TelegramWebApp = {
  initData?: string;
  initDataUnsafe?: { user?: { id?: number; first_name?: string } };
  colorScheme?: "light" | "dark";
  expand?: () => void;
  ready?: () => void;
  close?: () => void;
};

declare global {
  interface Window { Telegram?: { WebApp?: TelegramWebApp }; }
}

export function getTelegramWebApp() { return typeof window === "undefined" ? undefined : window.Telegram?.WebApp; }
export function getTelegramInitData() { return getTelegramWebApp()?.initData ?? ""; }
