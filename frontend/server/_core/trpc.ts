import { NOT_ADMIN_ERR_MSG, UNAUTHED_ERR_MSG } from '@shared/const';
import { initTRPC, TRPCError } from "@trpc/server";
import superjson from "superjson";
import type { TrpcContext } from "./context";

const t = initTRPC.context<TrpcContext>().create({
  transformer: superjson,
});

export const router = t.router;
export const publicProcedure = t.procedure;

const requireUser = t.middleware(async opts => {
  const { ctx, next } = opts;

  if (!ctx.user) {
    throw new TRPCError({ code: "UNAUTHORIZED", message: UNAUTHED_ERR_MSG });
  }

  return next({
    ctx: {
      ...ctx,
      user: ctx.user,
    },
  });
});

export const protectedProcedure = t.procedure.use(requireUser);

export function canAccessCapitalGuardRole(userRole: string, allowed: Array<"trader" | "analyst" | "admin">) {
  return userRole === "admin" || allowed.includes(userRole as "trader" | "analyst" | "admin");
}

function roleProcedure(roles: Array<"trader" | "analyst" | "admin">) {
  return protectedProcedure.use(
    t.middleware(async opts => {
      const user = opts.ctx.user;
      if (!user) {
        throw new TRPCError({ code: "UNAUTHORIZED", message: UNAUTHED_ERR_MSG });
      }
      if (!canAccessCapitalGuardRole(user.role, roles)) {
        throw new TRPCError({ code: "FORBIDDEN", message: "Your role cannot access this CapitalGuard workspace." });
      }
      return opts.next({ ctx: { ...opts.ctx, user } });
    })
  );
}

export const traderProcedure = roleProcedure(["trader"]);
export const analystProcedure = roleProcedure(["analyst"]);

export const adminProcedure = t.procedure.use(
  t.middleware(async opts => {
    const { ctx, next } = opts;

    if (!ctx.user || ctx.user.role !== 'admin') {
      throw new TRPCError({ code: "FORBIDDEN", message: NOT_ADMIN_ERR_MSG });
    }

    return next({
      ctx: {
        ...ctx,
        user: ctx.user,
      },
    });
  }),
);
