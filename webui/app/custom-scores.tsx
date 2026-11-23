"use client";

import {useEffect, useState, useSyncExternalStore} from "react";
import {
  chooseLocalResourceSource, customScoreDatasets, inspectCustomScores, installCustomScores, lookupCustomScores, removeCustomScores,
  type CustomDataset, type CustomMetric, type ResourceDownloadJob, type ScoreMapping, type ScorePreview,
} from "./local-service";
import type {VariantRow} from "./vcf";

const roleLabels = {
  chrom: "Chromosome", pos: "Position (1-based)", ref: "REF", alt: "ALT",
  strand: "Strand", gene: "Gene", transcript: "Transcript",
};
const roles = Object.keys(roleLabels) as (keyof ScoreMapping)[];
const required = roles.slice(0, 4);
const preference = "iei-hidden-custom-scores-v1";
const preferenceEvent = "iei-custom-scores-display";
const datasetsEvent = "iei-custom-scores-datasets-updated";
export function refreshCustomScoreDatasets() { window.dispatchEvent(new Event(datasetsEvent)); }
function subscribe(listener: () => void) {
  window.addEventListener("storage", listener);
  window.addEventListener(preferenceEvent, listener);
  return () => {
    window.removeEventListener("storage", listener);
    window.removeEventListener(preferenceEvent, listener);
  };
}
function useHiddenMetrics() {
  const raw = useSyncExternalStore(subscribe, () => localStorage.getItem(preference), () => null);
  try {
    const values: unknown = JSON.parse(raw ?? "[]");
    return new Set<string>(Array.isArray(values) ? values : []);
  } catch { return new Set<string>(); }
}
function saveHidden(hidden: Set<string>) {
  localStorage.setItem(preference, JSON.stringify([...hidden]));
  window.dispatchEvent(new Event(preferenceEvent));
}
export function resetCustomScoreDisplay() { saveHidden(new Set()); }

function useDatasets(refresh?: string) {
  const [result, setResult] = useState<{datasets: CustomDataset[]; error: string}>();
  useEffect(() => {
    let active = true;
    let request = 0;
    const reload = () => {
      const current = ++request;
      setResult(undefined);
      customScoreDatasets().then(datasets => { if (active && current === request) setResult({datasets, error: ""}); })
        .catch(e => { if (active && current === request) setResult({datasets: [], error: String(e)}); });
    };
    reload();
    window.addEventListener(datasetsEvent, reload);
    window.addEventListener("focus", reload);
    return () => { active = false; window.removeEventListener(datasetsEvent, reload); window.removeEventListener("focus", reload); };
  }, [refresh]);
  return {datasets: result?.datasets ?? [], error: result?.error ?? "", result};
}

export function CustomScoreDisplaySettings() {
  const {datasets, error} = useDatasets();
  const hidden = useHiddenMetrics();
  if (error) return <p role="alert">Custom scores unavailable · {error}</p>;
  if (!datasets.length) return null;
  return <><h3>Custom scores</h3><div className="custom-score-settings">{datasets.map(d =>
    <fieldset key={d.id}><legend>{d.name}</legend>{d.error && <p role="alert">{d.error}</p>}{d.scores.map(s => {
      const key = d.id + ":" + s.column;
      return <label key={key}><input type="checkbox" checked={!hidden.has(key)} onChange={e => {
        const next = new Set(hidden);
        if (e.target.checked) next.delete(key); else next.add(key);
        saveHidden(next);
      }}/>{s.label}</label>;
    })}</fieldset>)}</div></>;
}

export function useCustomReviewScores(row: VariantRow) {
  const {result} = useDatasets();
  const hgvsTranscript = row.hgvsC?.split(":")[0];
  const selectedTranscript = row.transcript;
  const versionedTranscript = hgvsTranscript && selectedTranscript && (hgvsTranscript === selectedTranscript
    || hgvsTranscript.split(".")[0] === selectedTranscript) ? hgvsTranscript : undefined;
  const query = JSON.stringify({chrom: row.chrom, pos: row.pos, ref: row.ref, alt: row.alt, gene: row.gene,
    geneId: row.geneId, strand: row.transcriptStrand, transcript: row.transcript,
    versionedTranscript});
  const [state, setState] = useState<{query: string; datasets: typeof result; data?: Awaited<ReturnType<typeof lookupCustomScores>>; error?: string}>();
  const hidden = useHiddenMetrics();
  useEffect(() => {
    let active = true;
    if (result?.datasets.length) {
      lookupCustomScores(JSON.parse(query)).then(data => { if (active) setState({query, datasets: result, data}); })
        .catch(e => { if (active) setState({query, datasets: result, error: String(e)}); });
    }
    return () => { active = false; };
  }, [query, result]);
  const current = state?.query === query && state.datasets === result ? state : undefined;
  const cards = (current?.data ?? []).flatMap(d => d.scores.flatMap((s, i) => {
    const key = d.id + ":" + s.column;
    if (hidden.has(key)) return [];
    const rows = d.matches.length ? d.matches : [{context: {}, unresolved: [], values: []}];
    return rows.map((m, j) => {
      const context = Object.entries(m.context).map(([role, value]) => role + " " + (value || "unknown"));
      if (m.unresolved.length) context.push("Unverified " + m.unresolved.join(", "));
      if (!d.matches.length) context.push("No matching row");
      else if (m.values[i] == null) context.push("Missing in source");
      return {key: key + ":" + j, label: s.label, value: null, displayText: m.values[i] ?? "—",
        note: [d.name, ...context].join(" · "), noteWhenMissing: true};
    });
  }));
  return {cards, error: result?.error || current?.error || current?.data?.flatMap(d => d.error ? [d.error] : []).join(" ")};
}

export function CustomScoreSetup({job, onStarted}: {
  job?: ResourceDownloadJob; onStarted: (job: ResourceDownloadJob) => void;
}) {
  const [revision, setRevision] = useState(0);
  const {datasets, error: listError} = useDatasets(job?.status + (job?.id ?? "") + revision);
  const [paths, setPaths] = useState<string[]>([]);
  const [preview, setPreview] = useState<ScorePreview>();
  const [mapping, setMapping] = useState<ScoreMapping>();
  const [dashMissing, setDashMissing] = useState(false);
  const [scores, setScores] = useState<CustomMetric[]>([]);
  const [name, setName] = useState("");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const running = job?.status === "queued" || job?.status === "running";
  const jobId = job?.id;
  const jobStatus = job?.status;
  useEffect(() => {
    if (jobId && jobStatus !== "queued" && jobStatus !== "running") refreshCustomScoreDatasets();
  }, [jobId, jobStatus]);
  const [repairName, setRepairName] = useState<string | null>(null);
  const replacing = repairName === name || datasets.some(d => d.name.toLowerCase() === name.trim().toLowerCase());
  async function choose(append: boolean) {
    setBusy(true); setError("");
    try {
      const result = await chooseLocalResourceSource("custom_scores");
      if (!result.paths?.length) return;
      const next = [...new Set([...(append ? paths : []), ...result.paths])];
      const inspected = await inspectCustomScores(next);
      setPaths(next); setPreview(inspected);
      if (!append) {
        setMapping(inspected.mapping); setScores([]); setSearch("");
        setDashMissing(false);
        setName((result.names?.[0] ?? "").replace(/\.(gz|bgz|bgzf)$/i, "").replace(/\.(tsv|txt)$/i, ""));
      }
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  const columns = preview?.columns ?? [];
  const used = Object.values(mapping ?? {}).filter(Boolean);
  const valid = mapping && required.every(role => mapping[role]) && new Set(used).size === used.length
    && scores.length > 0 && name.trim() && scores.every(s => s.label.trim())
    && new Set(scores.map(s => s.label.trim().toLowerCase())).size === scores.length;
  async function install() {
    if (!mapping || !valid) return;
    setBusy(true); setError("");
    try {
      onStarted(await installCustomScores({paths, name, mapping, scores, replace: replacing,
        dash_is_missing_strand: Boolean(mapping.strand && dashMissing)}));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function remove(dataset: CustomDataset) {
    if (!window.confirm(`Remove the installed custom score index for ${dataset.name}? Your source files will be kept.`)) return;
    setBusy(true); setError("");
    try {
      await removeCustomScores(dataset.id);
      refreshCustomScoreDatasets();
      setRepairName(null);
      setRevision(value => value + 1);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <details className="custom-score-setup"><summary><strong>Custom score tables</strong></summary>
    <p>Import selected columns from local GRCh38 tables for Variant Review. Choose tab-separated files with a header and 1-based positions. Gzip and BGZF are supported.</p>
    {datasets.map(d => <p key={d.id}>{d.error ? <span role="alert">{d.error}</span> : <><b>{d.name}</b> · {d.scores.length} scores · {d.rows.toLocaleString()} rows{d.skipped_rows ? ` · ${d.skipped_rows.toLocaleString()} wild-type or unsupported allele rows skipped` : ""}</>} <button type="button" disabled={busy || running} className="danger-text-button" onClick={() => void remove(d)}>Remove index</button></p>)}
    <fieldset disabled={busy || running}><button type="button" className="secondary-button" onClick={() => void choose(false)}>{paths.length ? "Choose different tables" : "Choose tables"}</button>
    {paths.length > 0 && <> <button type="button" className="secondary-button" onClick={() => void choose(true)}>Add files</button>
      <details><summary>{paths.length} selected files</summary>{paths.map(p => <div key={p}>{p.split(/[\\/]/).pop()}</div>)}</details></>}
    {mapping && preview && <>
      <label>Dataset name<input maxLength={120} value={name} onChange={e => setName(e.target.value)}/></label>
      <h4>Confirm matching columns</h4><div className="custom-mapping">{roles.map(role => <label key={role}>{roleLabels[role]}
        <select value={mapping[role]} onChange={e => {
          const col = e.target.value; setMapping({...mapping, [role]: col}); setScores(scores.filter(s => s.column !== col));
        }}><option value="">{required.includes(role) ? "Choose column" : "Unused"}</option>
          {columns.map(c => <option key={c} disabled={used.includes(c) && mapping[role] !== c}>{c}</option>)}</select></label>)}</div>
      {mapping.strand && <label><input type="checkbox" checked={dashMissing} onChange={e => setDashMissing(e.target.checked)}/>
        Treat <code>-</code> as missing strand in this table. The default is minus strand.</label>}
      <h4>Choose score columns</h4><label>Search columns<input type="search" value={search} onChange={e => setSearch(e.target.value)}/></label>
      <div className="custom-column-list">{columns.filter(c => !used.includes(c) && c.toLowerCase().includes(search.toLowerCase())).map(c => {
        const item = scores.find(s => s.column === c);
        return <label key={c}><input type="checkbox" checked={!!item} onChange={e =>
          setScores(e.target.checked ? [...scores, {column: c, label: c}] : scores.filter(s => s.column !== c))}/>
          <span>{c}</span><small>{preview.examples.map(row => row[columns.indexOf(c)]).join(" · ")}</small></label>;
      })}</div>
      {scores.length > 0 && <><h4>Preview · {scores.length} selected</h4>
        {scores.map(s => <label key={s.column}>Display name for {s.column}<input value={s.label} maxLength={120}
          onChange={e => setScores(scores.map(item => item.column === s.column ? {...item, label: e.target.value} : item))}/></label>)}
        <div className="ccre-gene-table-wrap"><table className="ccre-gene-table"><thead><tr>{roles.filter(r => mapping[r]).map(r => <th key={r}>{roleLabels[r]}</th>)}{scores.map(s => <th key={s.column}>{s.label}</th>)}</tr></thead>
        <tbody>{preview.examples.map((row,i) => <tr key={i}>{[...roles.map(r => mapping[r]).filter(Boolean), ...scores.map(s => s.column)].map(c => <td key={c}>{row[columns.indexOf(c)] || "—"}</td>)}</tr>)}</tbody></table></div></>}
      {datasets.some(d => d.error) && name.trim() && <label><input type="checkbox" checked={repairName === name}
        onChange={e => setRepairName(e.target.checked ? name : null)}/>Replace the installed index with this dataset name</label>}
      {replacing && <p>Importing under this name replaces its entire installed table, including all files and selected scores.</p>}
      <button className="primary-button dark" type="button" disabled={!valid} onClick={() => void install()}>{replacing ? "Replace installed table" : "Import selected scores"}</button>
    </>}</fieldset>
    {busy && <p role="status">Reading selection…</p>}{job && <p role="status">{job.error || job.message}</p>}
    {(error || listError) && <p role="alert">{error || listError}</p>}
  </details>;
}
