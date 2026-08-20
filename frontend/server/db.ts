import { eq } from "drizzle-orm";
import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";
import { InsertUser, users } from "../drizzle/schema";
import { ENV } from "./_core/env";

let pool: Pool | null = null;
let db: ReturnType<typeof drizzle> | null = null;

/** Web-owned PostgreSQL only. Never point DATABASE_URL to the Core PostgreSQL service. */
export async function getDb() {
  if (!db && process.env.DATABASE_URL) {
    try {
      pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 5, idleTimeoutMillis: 30_000 });
      db = drizzle(pool);
    } catch (error) {
      console.warn("[Web Database] Failed to create PostgreSQL pool:", error);
      pool = null;
      db = null;
    }
  }
  return db;
}

function normalizedRole(role: InsertUser["role"] | "user" | undefined): "trader" | "analyst" | "admin" {
  return role === "analyst" || role === "admin" ? role : "trader";
}

export async function upsertUser(user: InsertUser): Promise<void> {
  if (!user.openId) throw new Error("User openId is required for upsert");
  const database = await getDb();
  if (!database) {
    console.warn("[Web Database] Cannot upsert user: database not available");
    return;
  }
  const role = user.openId === ENV.ownerOpenId ? "admin" : normalizedRole(user.role);
  const now = new Date();
  await database.insert(users).values({
    openId: user.openId,
    name: user.name ?? null,
    email: user.email ?? null,
    loginMethod: user.loginMethod ?? null,
    role,
    lastSignedIn: user.lastSignedIn ?? now,
    updatedAt: now,
  }).onConflictDoUpdate({
    target: users.openId,
    set: {
      name: user.name ?? null,
      email: user.email ?? null,
      loginMethod: user.loginMethod ?? null,
      role,
      lastSignedIn: now,
      updatedAt: now,
    },
  });
}

export async function getUserByOpenId(openId: string) {
  const database = await getDb();
  if (!database) return undefined;
  const result = await database.select().from(users).where(eq(users.openId, openId)).limit(1);
  return result[0];
}

export async function getWebUserCount() {
  const database = await getDb();
  if (!database) return 0;
  return (await database.select().from(users)).length;
}
