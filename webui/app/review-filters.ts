export function passesMinimumScore(score: number | null | undefined, minimum: number | null): boolean {
  return minimum === null || (typeof score === "number" && Number.isFinite(score) && score >= minimum);
}

type ScreenVariant = { key: string; chrom: string; pos: number; ref: string; alt: string };

/** Publish only confirmed matches, after each completed batch; ignore cancelled runs. */
export async function screenVariantBatches(
  variants: ScreenVariant[],
  request: (batch: ScreenVariant[]) => Promise<{ available: boolean; matching_keys: string[] }>,
  onProgress: (matches: Set<string>, tested: number, total: number) => void,
  isActive: () => boolean,
  batchSize = 1000,
): Promise<void> {
  if (!Number.isInteger(batchSize) || batchSize < 1) throw new Error("Invalid SCREEN batch size");
  const matches = new Set<string>();
  if (!variants.length && isActive()) onProgress(matches, 0, 0);
  for (let index = 0; index < variants.length; index += batchSize) {
    if (!isActive()) return;
    const batch = variants.slice(index, index + batchSize);
    const result = await request(batch);
    if (!isActive()) return;
    if (!result.available) throw new Error("Prepared SCREEN context data are unavailable.");
    const requested = new Set(batch.map((variant) => variant.key));
    result.matching_keys.forEach((key) => { if (requested.has(key)) matches.add(key); });
    onProgress(new Set(matches), Math.min(index + batchSize, variants.length), variants.length);
  }
}
