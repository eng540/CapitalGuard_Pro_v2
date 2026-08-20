import { z } from "zod";
import { coreVerifyTelegramInitData } from "./core-adapter";

const MAX_TELEGRAM_AUTH_AGE_SECONDS = 5 * 60;
const MAX_FUTURE_SKEW_SECONDS = 60;

const telegramUserSchema = z.object({
  id: z.number().int().positive(),
  first_name: z.string().trim().min(1).max(128),
  last_name: z.string().trim().max(128).optional(),
  username: z.string().trim().max(64).optional(),
});

export type TelegramWebIdentity = {
  openId: string;
  name: string;
  loginMethod: "telegram_mini_app";
};

function authError(code: string): Error {
  return new Error(code);
}

/** Parses only after Core has verified Telegram's HMAC signature. */
export function parseFreshTelegramInitData(initData: string, nowMs = Date.now()): TelegramWebIdentity {
  const params = new URLSearchParams(initData);
  const rawUser = params.get("user");
  const rawAuthDate = params.get("auth_date");
  if (!rawUser || !rawAuthDate) throw authError("TELEGRAM_AUTH_INVALID");

  const authDate = Number(rawAuthDate);
  if (!Number.isSafeInteger(authDate) || authDate <= 0) throw authError("TELEGRAM_AUTH_INVALID");

  const nowSeconds = Math.floor(nowMs / 1000);
  if (authDate > nowSeconds + MAX_FUTURE_SKEW_SECONDS) throw authError("TELEGRAM_AUTH_INVALID");
  if (nowSeconds - authDate > MAX_TELEGRAM_AUTH_AGE_SECONDS) throw authError("TELEGRAM_AUTH_EXPIRED");

  try {
    const user = telegramUserSchema.parse(JSON.parse(rawUser));
    const name = [user.first_name, user.last_name].filter(Boolean).join(" ").trim();
    return {
      openId: `telegram:${user.id}`,
      name: name || user.username || `Telegram ${user.id}`,
      loginMethod: "telegram_mini_app",
    };
  } catch {
    throw authError("TELEGRAM_AUTH_INVALID");
  }
}

export async function authenticateTelegramInitData(
  initData: string,
  verify: (value: string) => Promise<unknown> = coreVerifyTelegramInitData,
  nowMs = Date.now(),
): Promise<TelegramWebIdentity> {
  if (!initData.trim() || initData.length > 10_000) throw authError("TELEGRAM_AUTH_INVALID");
  await verify(initData);
  return parseFreshTelegramInitData(initData, nowMs);
}
