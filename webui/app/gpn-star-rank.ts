import reference from "../../config/gpn-star-ranks.json";
import { GPN_STAR_MODELS, normalizedVariantKey, type VariantRow } from "./vcf";

export type GpnModel = typeof GPN_STAR_MODELS[number]["id"];

// Empirical lower tail, including every tie. Counts are exact integers below 2^53.
export function gpnConstraintQ(row: VariantRow, model: GpnModel): number | null {
  const observation = row.predictions?.[model];
  if (observation?.sourceRelease !== reference.revision || observation.matchStatus !== "exact") return null;
  const llr = observation.values.llr;
  if (typeof llr !== "number" || !Number.isFinite(llr)) return null;
  const [chrom, , ref, alt] = normalizedVariantKey(row).split(":");
  if (!reference.chromosomes.includes(chrom) || !/^[ACGT]$/.test(ref) || !/^[ACGT]$/.test(alt) || ref === alt) return null;

  const table = model === "gpn_star_m" ? reference.models.m447 : reference.models.v100;
  const tick = Math.round(llr * reference.scale);
  if (Math.fround(tick / reference.scale) !== Math.fround(llr)) return null;
  const index = tick - table.first_tick;
  const count = table.cumulative[index];
  if (!count || count === table.cumulative[index - 1]) return null;
  return 10 * Math.log10(table.cumulative[table.cumulative.length - 1] / count);
}
