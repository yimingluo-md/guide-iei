CREATE TABLE `consequences` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`variant_id` integer NOT NULL,
	`gene` text,
	`transcript` text,
	`hgvs_c` text,
	`hgvs_p` text,
	`consequence` text,
	`impact` text,
	`mane` integer DEFAULT false NOT NULL,
	`gnomad_popmax` real,
	`cadd` real,
	`alpha_missense` real,
	`alpha_prediction` text,
	`loftee` text,
	`clinvar` text,
	`splice_ai` real,
	`promoter_ai` real,
	`other_predictors_json` text DEFAULT '[]' NOT NULL,
	FOREIGN KEY (`variant_id`) REFERENCES `variants`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `consequences_gene_idx` ON `consequences` (`gene`);--> statement-breakpoint
CREATE INDEX `consequences_priority_idx` ON `consequences` (`impact`,`gnomad_popmax`);--> statement-breakpoint
CREATE TABLE `gene_set_members` (
	`gene_set_id` integer NOT NULL,
	`gene` text NOT NULL,
	FOREIGN KEY (`gene_set_id`) REFERENCES `gene_sets`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `gene_set_member_uq` ON `gene_set_members` (`gene_set_id`,`gene`);--> statement-breakpoint
CREATE TABLE `gene_sets` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`owner_email` text,
	`name` text NOT NULL,
	`release` text,
	`source` text,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE TABLE `genotypes` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`variant_id` integer NOT NULL,
	`sample_id` integer NOT NULL,
	`gt` text NOT NULL,
	`phased` integer DEFAULT false NOT NULL,
	`dp` integer,
	`gq` integer,
	`allele_balance` real,
	FOREIGN KEY (`variant_id`) REFERENCES `variants`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`sample_id`) REFERENCES `samples`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `genotypes_variant_sample_uq` ON `genotypes` (`variant_id`,`sample_id`);--> statement-breakpoint
CREATE TABLE `imports` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_email` text,
	`filename` text NOT NULL,
	`object_key` text NOT NULL,
	`assembly` text DEFAULT 'GRCh38' NOT NULL,
	`vep_version` text,
	`status` text DEFAULT 'pending' NOT NULL,
	`pass_records` integer DEFAULT 0 NOT NULL,
	`excluded_non_pass` integer DEFAULT 0 NOT NULL,
	`warning_json` text DEFAULT '[]' NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE INDEX `imports_owner_created_idx` ON `imports` (`owner_email`,`created_at`);--> statement-breakpoint
CREATE TABLE `samples` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`import_id` text NOT NULL,
	`name` text NOT NULL,
	FOREIGN KEY (`import_id`) REFERENCES `imports`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `samples_import_name_uq` ON `samples` (`import_id`,`name`);--> statement-breakpoint
CREATE TABLE `saved_candidates` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`owner_email` text,
	`variant_id` integer NOT NULL,
	`sample_id` integer,
	`note` text DEFAULT '' NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`variant_id`) REFERENCES `variants`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`sample_id`) REFERENCES `samples`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `saved_owner_variant_sample_uq` ON `saved_candidates` (`owner_email`,`variant_id`,`sample_id`);--> statement-breakpoint
CREATE TABLE `variants` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`import_id` text NOT NULL,
	`variant_key` text NOT NULL,
	`chrom` text NOT NULL,
	`pos` integer NOT NULL,
	`ref` text NOT NULL,
	`alt` text NOT NULL,
	`rsid` text,
	`filter` text DEFAULT 'PASS' NOT NULL,
	`repeat_masker` integer DEFAULT false NOT NULL,
	`segdup` integer DEFAULT false NOT NULL,
	`raw_info` text,
	FOREIGN KEY (`import_id`) REFERENCES `imports`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `variants_import_key_uq` ON `variants` (`import_id`,`variant_key`);--> statement-breakpoint
CREATE INDEX `variants_locus_idx` ON `variants` (`chrom`,`pos`);