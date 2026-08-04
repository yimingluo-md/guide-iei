"use client";

import { useEffect, useMemo, useState } from "react";
import type {
  ScreenContextCatalog,
  ScreenContextEvidence,
  ScreenImmuneContext,
  ScreenTissueContext,
} from "./local-service";

export type RegulatoryContextSet = {
  id: string;
  name: string;
  tissueIds: string[];
  immuneContextIds: string[];
  custom?: boolean;
};

export const REGULATORY_CONTEXT_SETS_KEY = "iei-regulatory-context-sets-v1";

export function readRegulatoryContextSets(): RegulatoryContextSet[] {
  try {
    const value = JSON.parse(localStorage.getItem(REGULATORY_CONTEXT_SETS_KEY) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item) => item && typeof item.id === "string" && typeof item.name === "string")
      .map((item) => ({
        id: item.id,
        name: item.name,
        tissueIds: Array.isArray(item.tissueIds) ? item.tissueIds.map(String) : [],
        immuneContextIds: Array.isArray(item.immuneContextIds) ? item.immuneContextIds.map(String) : [],
        custom: true,
      }));
  } catch {
    return [];
  }
}

export function storeRegulatoryContextSets(sets: RegulatoryContextSet[]) {
  localStorage.setItem(REGULATORY_CONTEXT_SETS_KEY, JSON.stringify(sets));
}

export function availableRegulatorySets(
  catalog: ScreenContextCatalog | null,
  custom: RegulatoryContextSet[],
) {
  const presets = (catalog?.presets ?? []).map((preset) => ({
    id: preset.id,
    name: preset.name,
    tissueIds: preset.tissue_ids,
    immuneContextIds: preset.immune_context_ids,
    custom: false,
  }));
  return [...presets, ...custom];
}

export function activeRegulatorySet(
  catalog: ScreenContextCatalog | null,
  custom: RegulatoryContextSet[],
  id: string,
) {
  const sets = availableRegulatorySets(catalog, custom);
  return sets.find((item) => item.id === id) ?? sets[0] ?? null;
}

function stateClass(state: string) {
  return `reg-state ${state.replaceAll("_", "-")}`;
}

function exactClasses(counts: Record<string, number>) {
  return Object.entries(counts)
    .filter(([label, count]) => label !== "inactive" && count > 0)
    .map(([label, count]) => `${label} ×${count}`);
}

export function RegulatorySummary({
  evidence, loading, error, codingConsequence, onOpen,
}: {
  evidence: ScreenContextEvidence | null;
  loading: boolean;
  error: string;
  codingConsequence: boolean;
  onOpen: () => void;
}) {
  const total = evidence?.overlaps.reduce((sum, item) => sum + item.summary.tissues_detected + item.summary.immune_contexts_detected, 0) ?? 0;
  const mixed = evidence?.overlaps.reduce((sum, item) => sum + item.summary.mixed_immune_contexts, 0) ?? 0;
  return <section className="regulatory-summary-card">
    <div><p className="eyebrow">Regulatory evidence</p><h2>SCREEN tissue &amp; immune context</h2><span>{loading ? "Checking prepared local context data…" : error ? "Context data unavailable" : evidence?.status === "overlap" ? `${evidence.overlaps.length} Registry cCRE overlap${evidence.overlaps.length === 1 ? "" : "s"} · ${total} positive context calls${mixed ? ` · ${mixed} mixed` : ""}` : evidence?.status === "no_overlap" ? "No SCREEN Registry V4 cCRE overlap" : "Prepared context data not installed"}</span>{codingConsequence && evidence?.status === "overlap" && <p className="regulatory-coding-explanation"><strong>Why are both coding and regulatory annotations shown?</strong><span>A genomic region can simultaneously have a transcript-specific coding consequence and be a regulatory element. The regulatory element may act on the same gene, another gene, or multiple genes.</span></p>}</div>
    <button className="secondary-button" onClick={onOpen}>Open regulatory evidence</button>
  </section>;
}

export function RegulatoryFilterControl({
  catalog, activeSetId, setActiveSetId, customSets, enabled, setEnabled,
  loading, progress, error,
}: {
  catalog: ScreenContextCatalog | null;
  activeSetId: string;
  setActiveSetId: (id: string) => void;
  customSets: RegulatoryContextSet[];
  enabled: boolean;
  setEnabled: (value: boolean) => void;
  loading: boolean;
  progress: string;
  error: string;
}) {
  const sets = availableRegulatorySets(catalog, customSets);
  const current = useMemo(
    () => activeRegulatorySet(catalog, customSets, activeSetId),
    [catalog, customSets, activeSetId],
  );
  const immuneAll = sets.find((set) => set.id === "immune-all") ?? null;
  const immuneContextActive = enabled && current?.id === "immune-all";
  if (!catalog?.available) return <p className="microcopy">Prepared SCREEN tissue/cell evidence is not installed. Registry overlap filtering remains available during WGS import.</p>;
  return <div className="regulatory-filter-control">
    <label className="check-row"><input type="checkbox" checked={immuneContextActive} disabled={!immuneAll} onChange={(event) => {
      if (event.target.checked && immuneAll) {
        setActiveSetId(immuneAll.id);
        setEnabled(true);
      } else if (immuneContextActive) {
        setEnabled(false);
      }
    }} /><span className="custom-check"/><span>Immune context<small>Keep variants with positive SCREEN evidence in any immune-related tissue aggregate or curated immune-cell context.</small></span></label>
    <details className="regulatory-filter-advanced" open={enabled && !immuneContextActive}><summary>Other context filters</summary><label className="field-label">Context set</label>
      <select value={current?.id ?? ""} onChange={(event) => setActiveSetId(event.target.value)}>{sets.map((set) => <option key={set.id} value={set.id}>{set.name}</option>)}</select>
      <label className="check-row"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span className="custom-check"/><span>Require positive SCREEN evidence<small>Only a positive call can include a variant; unavailable or not detected is never used automatically.</small></span></label>
      {enabled && <p className="microcopy">A variant qualifies when any selected tissue or cell context has positive evidence.</p>}
    </details>
    {loading && <p className="microcopy">{progress || "Screening local context matrices…"}</p>}
    {error && <p className="microcopy filter-error">{error}</p>}
  </div>;
}

export function RegulatoryEvidencePanel({
  catalog, evidence, loading, error, activeSetId, setActiveSetId,
  customSets, setCustomSets, children,
}: {
  catalog: ScreenContextCatalog | null;
  evidence: ScreenContextEvidence | null;
  loading: boolean;
  error: string;
  activeSetId: string;
  setActiveSetId: (id: string) => void;
  customSets: RegulatoryContextSet[];
  setCustomSets: (sets: RegulatoryContextSet[]) => void;
  children?: React.ReactNode;
}) {
  const [positiveOnly, setPositiveOnly] = useState(true);
  const [draftName, setDraftName] = useState("");
  const current = useMemo(
    () => activeRegulatorySet(catalog, customSets, activeSetId),
    [catalog, customSets, activeSetId],
  );
  const [draftTissues, setDraftTissues] = useState<Set<string>>(new Set());
  const [draftImmune, setDraftImmune] = useState<Set<string>>(new Set());
  const sets = availableRegulatorySets(catalog, customSets);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setDraftTissues(new Set(current?.tissueIds ?? []));
      setDraftImmune(new Set(current?.immuneContextIds ?? []));
      setDraftName(current?.custom ? current.name : `${current?.name ?? "Context"} copy`);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [current]);

  const allowedTissues = useMemo(() => new Set(current?.tissueIds ?? []), [current]);
  const allowedImmune = useMemo(() => new Set(current?.immuneContextIds ?? []), [current]);
  function toggle(setter: React.Dispatch<React.SetStateAction<Set<string>>>, id: string, checked: boolean) {
    setter((previous) => {
      const next = new Set(previous);
      if (checked) next.add(id); else next.delete(id);
      return next;
    });
  }
  function saveSet() {
    const name = draftName.trim();
    if (!name) return;
    const existing = current?.custom ? current.id : "";
    const id = existing || globalThis.crypto?.randomUUID?.() || `reg-${Date.now()}`;
    const next = { id, name, tissueIds: [...draftTissues], immuneContextIds: [...draftImmune], custom: true };
    const updated = existing ? customSets.map((item) => item.id === existing ? next : item) : [...customSets, next];
    setCustomSets(updated);
    setActiveSetId(id);
  }

  return <div className="regulatory-workspace">
    <header className="regulatory-workspace-head"><div><p className="eyebrow">Variant-scoped workspace</p><h1>Regulatory evidence</h1><p>Observed SCREEN evidence is shown separately from target-gene links and model predictions.</p></div><span className="local-only-badge">Local data</span></header>
    <div className="regulatory-module-tabs"><button className="active">SCREEN observed</button><button disabled title="Regulatory element–gene links are planned for future development">Element–gene links · to be developed</button><button disabled title="Gene-specific variant-effect prediction is planned for future development">Gene-specific variant-effect prediction · to be developed</button></div>
    {children}
    <section className="regulatory-controls-card">
      <div><label><span>Display context set</span><select value={current?.id ?? ""} onChange={(event) => setActiveSetId(event.target.value)}>{sets.map((set) => <option key={set.id} value={set.id}>{set.name}</option>)}</select></label><label className="inline-check"><input type="checkbox" checked={positiveOnly} onChange={(event) => setPositiveOnly(event.target.checked)}/> Show positive evidence only</label></div>
      <details><summary>Manage named context sets</summary><p>Context sets change what is displayed here. Variant filtering is a separate option in the results sidebar.</p><div className="context-set-editor"><label><span>Set name</span><input value={draftName} onChange={(event) => setDraftName(event.target.value)}/></label><div className="context-picker"><fieldset><legend>Tissue aggregates</legend>{catalog?.tissues.map((item) => <label key={item.id}><input type="checkbox" checked={draftTissues.has(item.id)} onChange={(event) => toggle(setDraftTissues, item.id, event.target.checked)}/>{item.name}</label>)}</fieldset><fieldset><legend>Immune contexts</legend>{catalog?.immune_contexts.map((item) => <label key={item.id}><input type="checkbox" checked={draftImmune.has(item.id)} onChange={(event) => toggle(setDraftImmune, item.id, event.target.checked)}/>{item.name}</label>)}</fieldset></div><div className="context-set-actions"><button className="primary-button dark" onClick={saveSet}>{current?.custom ? "Update set" : "Save as new set"}</button>{current?.custom && <button className="secondary-button" onClick={() => { const next = customSets.filter((item) => item.id !== current.id); setCustomSets(next); setActiveSetId(catalog?.presets[0]?.id ?? ""); }}>Delete set</button>}</div></div></details>
    </section>
    {loading && <div className="regulatory-loading">Reading the prepared Registry V4 tissue and immune matrices…</div>}
    {error && <div className="ccre-unavailable"><strong>Regulatory context unavailable</strong><span>{error}</span></div>}
    {!loading && !error && !catalog?.available && <div className="ccre-unavailable"><strong>Prepared tissue/cell context data are not installed</strong><span>The Registry overlap and nearby-gene context remain valid. Prepare the optional SCREEN context bundle to add tissue and immune-cell observations.</span></div>}
    {!loading && evidence?.status === "no_overlap" && <div className="ccre-no-overlap"><strong>No SCREEN Registry V4 cCRE overlap</strong><span>Tissue/cell context matrices are indexed by the Registry master catalog, so no per-context cCRE evidence is available at this locus.</span></div>}
    {evidence?.overlaps.map((overlap) => {
      const tissues = overlap.tissues.filter((item) => allowedTissues.has(item.id) && (!positiveOnly || item.activity_detected));
      const immune = overlap.immune_contexts.filter((item) => allowedImmune.has(item.id) && (!positiveOnly || item.activity_detected));
      return <section className="regulatory-ccre-card" key={overlap.accession}>
        <header><div><strong>{overlap.accession}</strong><span>{overlap.overall_class} · overall cell-type-agnostic class</span></div><code>{overlap.chrom}:{overlap.start.toLocaleString()}-{overlap.end.toLocaleString()}</code></header>
        <div className="regulatory-summary-stats"><span><strong>{overlap.summary.tissues_detected}</strong> / {overlap.summary.tissues_total} tissues detected</span><span><strong>{overlap.summary.immune_contexts_detected}</strong> / {overlap.summary.immune_contexts_total} immune contexts detected</span>{overlap.summary.mixed_immune_contexts > 0 && <span className="mixed"><strong>{overlap.summary.mixed_immune_contexts}</strong> mixed</span>}</div>
        <ContextTable title="Tissue aggregates" empty={positiveOnly ? "No positive calls in the selected tissue set." : "No tissues are selected."} rows={tissues.map((item) => <TissueRow key={item.id} item={item}/>)}/>
        <ContextTable title="Curated baseline immune contexts" empty={positiveOnly ? "No positive calls in the selected immune context set." : "No immune contexts are selected."} rows={immune.map((item) => <ImmuneRow key={item.id} item={item}/>)}/>
      </section>;
    })}
    <div className="regulatory-interpretation-note"><strong>Interpretation boundary</strong><span>A positive context call supports regulatory activity in that aggregate/context. It does not identify a causal cell, prove that the variant changes activity, or assign a target gene. Not detected and classification unavailable are not exclusionary evidence.</span></div>
  </div>;
}

function ContextTable({ title, rows, empty }: { title: string; rows: React.ReactNode[]; empty: string }) {
  return <section className="reg-context-section"><h2>{title}<span>{rows.length} shown</span></h2>{rows.length ? <div className="reg-context-list">{rows}</div> : <p className="reg-context-empty">{empty}</p>}</section>;
}

function TissueRow({ item }: { item: ScreenTissueContext }) {
  return <article className="reg-context-row"><div><strong>{item.name}</strong><small>Tissue aggregate</small></div><span className={stateClass(item.state)}>{item.state_label}</span><div className="reg-context-classes"><span>{item.class}</span></div></article>;
}

function ImmuneRow({ item }: { item: ScreenImmuneContext }) {
  const exact = exactClasses(item.exact_class_counts);
  return <article className="reg-context-row immune"><div><strong>{item.name}</strong><small>{item.ontology_id} · {item.lineages.join(", ")}</small></div><span className={stateClass(item.state)}>{item.state_label}</span><div className="reg-context-classes">{exact.length ? exact.map((value) => <span key={value}>{value}</span>) : <span>No positive class</span>}</div><div className="reg-context-capability"><strong>{item.donors_detected} / {item.donor_count}</strong><span>donors detected</span><em className={item.classification_capable_donors === 0 ? "unavailable" : ""}>Classifier assays {item.classification_capable_donors} of {item.donor_count} donors</em></div>{item.is_nested_context && <small className="nested-context">Nested Cell Ontology context; donors may also appear in parent contexts.</small>}</article>;
}
