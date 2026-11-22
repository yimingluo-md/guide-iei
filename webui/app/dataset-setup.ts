import type { ServiceCapabilities } from "./local-service";

type Source = ServiceCapabilities["annotation_profile"]["sources"][number];

export function datasetForScope(source: Source, scope: "exome" | "whole_genome"): Source {
  if (scope !== "exome" || !source.exome_setup) return source;
  return { ...source, ...source.exome_setup, download_id: source.exome_setup.download_id ?? undefined };
}

export function groupDatasetSources(sources: Source[]) {
  const wgsIds = new Set(["spliceai", "screen_context", "alphagenome_avi", "encode_re2g"]);
  const userIds = new Set(["promoterai", "genia"]);
  return {
    wgs: sources.filter((source) => wgsIds.has(source.id)),
    userProvided: sources.filter((source) => userIds.has(source.id)),
    optional: sources.filter((source) => !wgsIds.has(source.id) && !userIds.has(source.id)
      && (source.id === "dbnsfp" || source.recommendation === "optional")),
    essential: sources.filter((source) => !wgsIds.has(source.id) && !userIds.has(source.id)
      && source.id !== "dbnsfp" && source.recommendation !== "optional"),
  };
}
