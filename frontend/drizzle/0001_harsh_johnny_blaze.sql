CREATE TABLE `analystProfiles` (
	`id` int AUTO_INCREMENT NOT NULL,
	`userId` int NOT NULL,
	`analystCode` varchar(32) NOT NULL,
	`headline` varchar(160),
	`winRate` decimal(7,4) NOT NULL DEFAULT '0',
	`totalPnlPct` decimal(12,4) NOT NULL DEFAULT '0',
	`maxDrawdownPct` decimal(12,4) NOT NULL DEFAULT '0',
	`sampleSize` int NOT NULL DEFAULT 0,
	`verifiedAt` timestamp,
	`updatedAt` timestamp NOT NULL DEFAULT (now()) ON UPDATE CURRENT_TIMESTAMP,
	CONSTRAINT `analystProfiles_id` PRIMARY KEY(`id`),
	CONSTRAINT `analystProfiles_userId_unique` UNIQUE(`userId`),
	CONSTRAINT `analyst_profiles_code_unique` UNIQUE(`analystCode`)
);
--> statement-breakpoint
CREATE TABLE `channels` (
	`id` int AUTO_INCREMENT NOT NULL,
	`channelCode` varchar(32) NOT NULL,
	`displayName` varchar(128) NOT NULL,
	`telegramChannelId` varchar(64),
	`trust` enum('canonical','unclaimed','claimed','verified') NOT NULL DEFAULT 'unclaimed',
	`ownerId` int,
	`createdAt` timestamp NOT NULL DEFAULT (now()),
	`updatedAt` timestamp NOT NULL DEFAULT (now()) ON UPDATE CURRENT_TIMESTAMP,
	CONSTRAINT `channels_id` PRIMARY KEY(`id`),
	CONSTRAINT `channels_code_unique` UNIQUE(`channelCode`)
);
--> statement-breakpoint
CREATE TABLE `historicalBatches` (
	`id` int AUTO_INCREMENT NOT NULL,
	`publicRef` varchar(64) NOT NULL,
	`requestedByUserId` int NOT NULL,
	`channelId` int,
	`status` enum('staged','review_required','validated','evidence_ingested','replay_pending','replayed','rejected') NOT NULL DEFAULT 'staged',
	`acceptedRecords` int NOT NULL DEFAULT 0,
	`rejectedRecords` int NOT NULL DEFAULT 0,
	`temporalMode` varchar(64),
	`financialOutcome` varchar(64),
	`replayGate` varchar(64),
	`ownerReview` varchar(64),
	`createdAt` timestamp NOT NULL DEFAULT (now()),
	`reviewedAt` timestamp,
	CONSTRAINT `historicalBatches_id` PRIMARY KEY(`id`),
	CONSTRAINT `historical_batches_public_ref_unique` UNIQUE(`publicRef`)
);
--> statement-breakpoint
CREATE TABLE `portfolios` (
	`id` int AUTO_INCREMENT NOT NULL,
	`userId` int NOT NULL,
	`currency` varchar(8) NOT NULL DEFAULT 'USDT',
	`totalEquity` decimal(20,8) NOT NULL DEFAULT '0',
	`availableBalance` decimal(20,8) NOT NULL DEFAULT '0',
	`realizedPnl` decimal(20,8) NOT NULL DEFAULT '0',
	`unrealizedPnl` decimal(20,8) NOT NULL DEFAULT '0',
	`updatedAt` timestamp NOT NULL DEFAULT (now()) ON UPDATE CURRENT_TIMESTAMP,
	CONSTRAINT `portfolios_id` PRIMARY KEY(`id`),
	CONSTRAINT `portfolios_userId_unique` UNIQUE(`userId`)
);
--> statement-breakpoint
CREATE TABLE `recommendations` (
	`id` int AUTO_INCREMENT NOT NULL,
	`publicRef` varchar(64) NOT NULL,
	`analystId` int,
	`channelId` int,
	`asset` varchar(32) NOT NULL,
	`side` enum('long','short') NOT NULL,
	`status` enum('pending','active','partial','closed','cancelled') NOT NULL DEFAULT 'pending',
	`entry` decimal(24,10),
	`stopLoss` decimal(24,10),
	`targets` json,
	`finalPnlPct` decimal(12,4),
	`temporalDecision` varchar(64),
	`createdAt` timestamp NOT NULL DEFAULT (now()),
	`activatedAt` timestamp,
	`closedAt` timestamp,
	CONSTRAINT `recommendations_id` PRIMARY KEY(`id`),
	CONSTRAINT `recommendations_public_ref_unique` UNIQUE(`publicRef`)
);
--> statement-breakpoint
CREATE TABLE `temporalDecisions` (
	`id` int AUTO_INCREMENT NOT NULL,
	`batchId` int,
	`sourceRef` varchar(128) NOT NULL,
	`mode` varchar(64) NOT NULL,
	`route` varchar(64) NOT NULL,
	`reasons` json,
	`ageSeconds` int,
	`marketAsOf` timestamp,
	`createdAt` timestamp NOT NULL DEFAULT (now()),
	CONSTRAINT `temporalDecisions_id` PRIMARY KEY(`id`)
);
--> statement-breakpoint
CREATE TABLE `trades` (
	`id` int AUTO_INCREMENT NOT NULL,
	`publicRef` varchar(64) NOT NULL,
	`userId` int NOT NULL,
	`recommendationId` int,
	`asset` varchar(32) NOT NULL,
	`side` enum('long','short') NOT NULL,
	`status` enum('pending','active','partial','closed','cancelled') NOT NULL DEFAULT 'pending',
	`sourceType` varchar(24) NOT NULL DEFAULT 'manual',
	`entry` decimal(24,10),
	`stopLoss` decimal(24,10),
	`size` decimal(24,10),
	`realizedPnl` decimal(20,8) NOT NULL DEFAULT '0',
	`createdAt` timestamp NOT NULL DEFAULT (now()),
	`closedAt` timestamp,
	CONSTRAINT `trades_id` PRIMARY KEY(`id`),
	CONSTRAINT `trades_public_ref_unique` UNIQUE(`publicRef`)
);
--> statement-breakpoint
ALTER TABLE `users` MODIFY COLUMN `role` enum('user','trader','analyst','admin') NOT NULL DEFAULT 'trader';--> statement-breakpoint
CREATE INDEX `historical_batches_status_index` ON `historicalBatches` (`status`);--> statement-breakpoint
CREATE INDEX `recommendations_analyst_index` ON `recommendations` (`analystId`);--> statement-breakpoint
CREATE INDEX `temporal_decisions_batch_index` ON `temporalDecisions` (`batchId`);--> statement-breakpoint
CREATE INDEX `trades_user_status_index` ON `trades` (`userId`,`status`);