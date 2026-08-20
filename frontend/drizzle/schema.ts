import { boolean, index, integer, jsonb, pgEnum, pgTable, serial, text, timestamp, uniqueIndex, varchar } from "drizzle-orm/pg-core";

/**
 * A-PG boundary: these tables belong exclusively to the Web service.
 * Trading, recommendations, PnL, historical evidence, and replay data stay in Core.
 */
export const webUserRoles = ["user", "trader", "analyst", "admin"] as const;
export const webNotificationChannels = ["browser", "telegram", "email"] as const;

export const webUserRole = pgEnum("web_user_role", webUserRoles);
export const webNotificationChannel = pgEnum("web_notification_channel", webNotificationChannels);

export const users = pgTable("web_users", {
  id: serial("id").primaryKey(),
  openId: varchar("open_id", { length: 128 }).notNull().unique(),
  name: text("name"),
  email: varchar("email", { length: 320 }),
  loginMethod: varchar("login_method", { length: 64 }),
  role: webUserRole("role").notNull().default("trader"),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  lastSignedIn: timestamp("last_signed_in", { withTimezone: true }).defaultNow().notNull(),
});

export const webPreferences = pgTable("web_preferences", {
  id: serial("id").primaryKey(),
  userId: integer("user_id").notNull(),
  locale: varchar("locale", { length: 16 }).notNull().default("ar"),
  timezone: varchar("timezone", { length: 64 }).notNull().default("UTC"),
  theme: varchar("theme", { length: 24 }).notNull().default("dark"),
  dashboardLayout: jsonb("dashboard_layout").notNull().default({}),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
}, table => [uniqueIndex("web_preferences_user_unique").on(table.userId)]);

export const webSavedComparisons = pgTable("web_saved_comparisons", {
  id: serial("id").primaryKey(),
  userId: integer("user_id").notNull(),
  label: varchar("label", { length: 128 }).notNull(),
  analystCodes: jsonb("analyst_codes").notNull(),
  filters: jsonb("filters").notNull().default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
}, table => [index("web_saved_comparisons_user_index").on(table.userId)]);

export const webNotificationPreferences = pgTable("web_notification_preferences", {
  id: serial("id").primaryKey(),
  userId: integer("user_id").notNull(),
  channel: webNotificationChannel("channel").notNull(),
  enabled: boolean("enabled").notNull().default(true),
  topics: jsonb("topics").notNull().default({}),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
}, table => [uniqueIndex("web_notification_preferences_user_channel_unique").on(table.userId, table.channel)]);

export const webAuditEvents = pgTable("web_audit_events", {
  id: serial("id").primaryKey(),
  userId: integer("user_id"),
  action: varchar("action", { length: 96 }).notNull(),
  requestId: varchar("request_id", { length: 96 }),
  metadata: jsonb("metadata").notNull().default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
}, table => [index("web_audit_events_user_created_index").on(table.userId, table.createdAt)]);

export type User = typeof users.$inferSelect;
export type InsertUser = typeof users.$inferInsert;
