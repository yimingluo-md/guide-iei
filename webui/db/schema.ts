import { sql } from "drizzle-orm";
import { index, integer, real, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const imports = sqliteTable("imports", {
  id: text("id").primaryKey(),
  ownerEmail: text("owner_email"),
  filename: text("filename").notNull(),
  objectKey: text("object_key").notNull(),
  assembly: text("assembly").notNull().default("GRCh38"),
  vepVersion: text("vep_version"),
  status: text("status").notNull().default("pending"),
  passRecords: integer("pass_records").notNull().default(0),
  excludedNonPass: integer("excluded_non_pass").notNull().default(0),
  warningJson: text("warning_json").notNull().default("[]"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [index("imports_owner_created_idx").on(table.ownerEmail, table.createdAt)]);

export const samples = sqliteTable("samples", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  importId: text("import_id").notNull().references(() => imports.id, { onDelete: "cascade" }),
  name: text("name").notNull(),
}, (table) => [uniqueIndex("samples_import_name_uq").on(table.importId, table.name)]);

export const variants = sqliteTable("variants", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  importId: text("import_id").notNull().references(() => imports.id, { onDelete: "cascade" }),
  variantKey: text("variant_key").notNull(),
  chrom: text("chrom").notNull(),
  pos: integer("pos").notNull(),
  ref: text("ref").notNull(),
  alt: text("alt").notNull(),
  rsid: text("rsid"),
  filter: text("filter").notNull().default("PASS"),
  repeatMasker: integer("repeat_masker", { mode: "boolean" }).notNull().default(false),
  segdup: integer("segdup", { mode: "boolean" }).notNull().default(false),
  rawInfo: text("raw_info"),
}, (table) => [
  uniqueIndex("variants_import_key_uq").on(table.importId, table.variantKey),
  index("variants_locus_idx").on(table.chrom, table.pos),
]);

export const consequences = sqliteTable("consequences", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  variantId: integer("variant_id").notNull().references(() => variants.id, { onDelete: "cascade" }),
  gene: text("gene"),
  transcript: text("transcript"),
  hgvsC: text("hgvs_c"),
  hgvsP: text("hgvs_p"),
  consequence: text("consequence"),
  impact: text("impact"),
  mane: integer("mane", { mode: "boolean" }).notNull().default(false),
  gnomadPopmax: real("gnomad_popmax"),
  cadd: real("cadd"),
  alphaMissense: real("alpha_missense"),
  alphaPrediction: text("alpha_prediction"),
  loftee: text("loftee"),
  clinvar: text("clinvar"),
  spliceAi: real("splice_ai"),
  promoterAi: real("promoter_ai"),
  otherPredictorsJson: text("other_predictors_json").notNull().default("[]"),
}, (table) => [
  index("consequences_gene_idx").on(table.gene),
  index("consequences_priority_idx").on(table.impact, table.gnomadPopmax),
]);

export const genotypes = sqliteTable("genotypes", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  variantId: integer("variant_id").notNull().references(() => variants.id, { onDelete: "cascade" }),
  sampleId: integer("sample_id").notNull().references(() => samples.id, { onDelete: "cascade" }),
  gt: text("gt").notNull(),
  phased: integer("phased", { mode: "boolean" }).notNull().default(false),
  dp: integer("dp"),
  gq: integer("gq"),
  alleleBalance: real("allele_balance"),
}, (table) => [uniqueIndex("genotypes_variant_sample_uq").on(table.variantId, table.sampleId)]);

export const geneSets = sqliteTable("gene_sets", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  ownerEmail: text("owner_email"),
  name: text("name").notNull(),
  release: text("release"),
  source: text("source"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const geneSetMembers = sqliteTable("gene_set_members", {
  geneSetId: integer("gene_set_id").notNull().references(() => geneSets.id, { onDelete: "cascade" }),
  gene: text("gene").notNull(),
}, (table) => [uniqueIndex("gene_set_member_uq").on(table.geneSetId, table.gene)]);

export const savedCandidates = sqliteTable("saved_candidates", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  ownerEmail: text("owner_email"),
  variantId: integer("variant_id").notNull().references(() => variants.id, { onDelete: "cascade" }),
  sampleId: integer("sample_id").references(() => samples.id, { onDelete: "cascade" }),
  note: text("note").notNull().default(""),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [uniqueIndex("saved_owner_variant_sample_uq").on(table.ownerEmail, table.variantId, table.sampleId)]);
