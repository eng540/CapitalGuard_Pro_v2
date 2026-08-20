import { COOKIE_NAME } from "@shared/const";
import { z } from "zod";
import { getSessionCookieOptions } from "./_core/cookies";
import { sdk } from "./_core/sdk";
import { systemRouter } from "./_core/systemRouter";
import { publicProcedure, router } from "./_core/trpc";
import { capitalguardRouter } from "./capitalguard";
import * as db from "./db";
import { authenticateTelegramInitData } from "./telegram-auth";

export const appRouter = router({
    // if you need to use socket.io, read and register route in server/_core/index.ts, all api should start with '/api/' so that the gateway can route correctly
  system: systemRouter,
  auth: router({
    me: publicProcedure.query(opts => opts.ctx.user),
    telegram: publicProcedure
      .input(z.object({ initData: z.string().trim().min(20).max(10_000) }))
      .mutation(async ({ ctx, input }) => {
        const identity = await authenticateTelegramInitData(input.initData);
        await db.upsertUser({
          openId: identity.openId,
          name: identity.name,
          loginMethod: identity.loginMethod,
          lastSignedIn: new Date(),
        });
        const user = await db.getUserByOpenId(identity.openId);
        if (!user) throw new Error("TELEGRAM_WEB_USER_NOT_PERSISTED");

        const sessionToken = await sdk.createSessionToken(user.openId, {
          name: user.name || identity.name,
        });
        ctx.res.cookie(COOKIE_NAME, sessionToken, {
          ...getSessionCookieOptions(ctx.req),
          maxAge: 365 * 24 * 60 * 60 * 1000,
        });
        return user;
      }),
    logout: publicProcedure.mutation(({ ctx }) => {
      const cookieOptions = getSessionCookieOptions(ctx.req);
      ctx.res.clearCookie(COOKIE_NAME, { ...cookieOptions, maxAge: -1 });
      return {
        success: true,
      } as const;
    }),
  }),
  capitalguard: capitalguardRouter,

  // TODO: add feature routers here, e.g.
  // todo: router({
  //   list: protectedProcedure.query(({ ctx }) =>
  //     db.getUserTodos(ctx.user.id)
  //   ),
  // }),
});

export type AppRouter = typeof appRouter;
