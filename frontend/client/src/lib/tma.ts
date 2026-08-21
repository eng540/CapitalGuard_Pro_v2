type TelegramWebApp = {
  initData?: string;
  initDataUnsafe?: { user?: { id?: number; first_name?: string } };
  colorScheme?: "light" | "dark";
  expand?: () => void;
  ready?: () => void;
  close?: () => void;
  themeParams?: { bg_color?: string; text_color?: string; button_color?: string; button_text_color?: string };
  safeAreaInset?: { top?: number; bottom?: number; left?: number; right?: number };
  contentSafeAreaInset?: { top?: number; bottom?: number; left?: number; right?: number };
  BackButton?: { isVisible?: boolean; show?: () => void; hide?: () => void; onClick?: (callback: () => void) => void; offClick?: (callback: () => void) => void };
  MainButton?: { setText?: (text: string) => void; show?: () => void; hide?: () => void };
};

declare global {
  interface Window { Telegram?: { WebApp?: TelegramWebApp }; }
}

export function getTelegramWebApp() { return typeof window === "undefined" ? undefined : window.Telegram?.WebApp; }
export function getTelegramInitData() { return getTelegramWebApp()?.initData ?? ""; }

export function applyTelegramViewport(app: TelegramWebApp) {
  const root = document.documentElement;
  const inset = app.contentSafeAreaInset ?? app.safeAreaInset ?? {};
  root.style.setProperty("--tma-safe-top", `${inset.top ?? 0}px`);
  root.style.setProperty("--tma-safe-bottom", `${inset.bottom ?? 0}px`);
  if (app.colorScheme) root.dataset.tmaTheme = app.colorScheme;
  if (app.themeParams?.bg_color) root.style.setProperty("--tma-bg", app.themeParams.bg_color);
  if (app.themeParams?.text_color) root.style.setProperty("--tma-text", app.themeParams.text_color);
}
