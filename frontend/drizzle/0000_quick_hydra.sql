CREATE TYPE "public"."web_notification_channel" AS ENUM('browser', 'telegram', 'email');--> statement-breakpoint
CREATE TYPE "public"."web_user_role" AS ENUM('user', 'trader', 'analyst', 'admin');--> statement-breakpoint
CREATE TABLE "web_users" (
	"id" serial PRIMARY KEY NOT NULL,
	"open_id" varchar(128) NOT NULL,
	"name" text,
	"email" varchar(320),
	"login_method" varchar(64),
	"role" "web_user_role" DEFAULT 'trader' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"last_signed_in" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "web_users_open_id_unique" UNIQUE("open_id")
);
--> statement-breakpoint
CREATE TABLE "web_audit_events" (
	"id" serial PRIMARY KEY NOT NULL,
	"user_id" integer,
	"action" varchar(96) NOT NULL,
	"request_id" varchar(96),
	"metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "web_notification_preferences" (
	"id" serial PRIMARY KEY NOT NULL,
	"user_id" integer NOT NULL,
	"channel" "web_notification_channel" NOT NULL,
	"enabled" boolean DEFAULT true NOT NULL,
	"topics" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "web_preferences" (
	"id" serial PRIMARY KEY NOT NULL,
	"user_id" integer NOT NULL,
	"locale" varchar(16) DEFAULT 'ar' NOT NULL,
	"timezone" varchar(64) DEFAULT 'UTC' NOT NULL,
	"theme" varchar(24) DEFAULT 'dark' NOT NULL,
	"dashboard_layout" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "web_saved_comparisons" (
	"id" serial PRIMARY KEY NOT NULL,
	"user_id" integer NOT NULL,
	"label" varchar(128) NOT NULL,
	"analyst_codes" jsonb NOT NULL,
	"filters" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE INDEX "web_audit_events_user_created_index" ON "web_audit_events" USING btree ("user_id","created_at");--> statement-breakpoint
CREATE UNIQUE INDEX "web_notification_preferences_user_channel_unique" ON "web_notification_preferences" USING btree ("user_id","channel");--> statement-breakpoint
CREATE UNIQUE INDEX "web_preferences_user_unique" ON "web_preferences" USING btree ("user_id");--> statement-breakpoint
CREATE INDEX "web_saved_comparisons_user_index" ON "web_saved_comparisons" USING btree ("user_id");