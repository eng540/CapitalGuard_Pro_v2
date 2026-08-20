CREATE TABLE `historicalWallets` (
	`id` int AUTO_INCREMENT NOT NULL,
	`ownerId` int,
	`channelId` int,
	`ownerKind` enum('trader_follow','analyst','channel') NOT NULL,
	`publicRef` varchar(64) NOT NULL,
	`totalSignals` int NOT NULL DEFAULT 0,
	`replayedSignals` int NOT NULL DEFAULT 0,
	`verifiedPnlPct` decimal(12,4) NOT NULL DEFAULT '0',
	`maxDrawdownPct` decimal(12,4) NOT NULL DEFAULT '0',
	`updatedAt` timestamp NOT NULL DEFAULT (now()) ON UPDATE CURRENT_TIMESTAMP,
	CONSTRAINT `historicalWallets_id` PRIMARY KEY(`id`),
	CONSTRAINT `historical_wallets_public_ref_unique` UNIQUE(`publicRef`)
);
--> statement-breakpoint
CREATE INDEX `historical_wallets_owner_index` ON `historicalWallets` (`ownerId`,`ownerKind`);