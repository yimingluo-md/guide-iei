"use client";

import { useEffect, useState } from "react";
import { elementGeneLinks, type ElementGeneLink, type RegulatoryVariant } from "./local-service";

export function ElementGeneLinks({variant, geneKnowledge}: {
  variant: RegulatoryVariant; geneKnowledge: (gene: string) => React.ReactNode;
}) {
  const {chrom, pos, ref, alt} = variant;
  const [result, setResult] = useState<Awaited<ReturnType<typeof elementGeneLinks>>>();
  const [error, setError] = useState("");
  const [context, setContext] = useState("");
  const [gene, setGene] = useState("");
  useEffect(() => {
    let active = true;
    elementGeneLinks({chrom,pos,ref,alt}).then(data => { if (active) setResult(data); })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [chrom,pos,ref,alt]);
  if (error) return <p role="alert">{error}</p>;
  if (!result) return <p role="status">Looking up regulatory elements…</p>;
  if (result.status === "not_installed") return <p>Install ENCODE-rE2G under Import &amp; QC → Annotation datasets.</p>;
  if (result.status === "unsupported") return <p>This lookup supports sequence-resolved GRCh38 alleles.</p>;
  if (!result.links.length) return <p>No published {result.label} link overlaps this allele in the installed release.</p>;
  const grouped = new Map<string, ElementGeneLink[]>();
  const contexts = [...new Set(result.links.map(row => row.context))].sort();
  const visible = result.links.filter(row => !context || (context === "@immune"
    ? row.systems.split("|").includes("immune system") : row.context === context));
  for (const row of visible) {
    const id = row.gene || row.symbol;
    const records = grouped.get(id);
    if (records) records.push(row); else grouped.set(id, [row]);
  }
  return <section className="regulatory-controls-card"><h2>Predicted target genes</h2>
    <p>{result.label} links overlapping elements to genes in reference biosamples. Scores describe the element–gene relationship.</p>
    <label>Reference context <select value={context} onChange={e => {setContext(e.target.value); setGene("");}}>
      <option value="">All overlapping contexts</option><option value="@immune">Immune system</option>
      {contexts.map(c => <option key={c}>{c}</option>)}</select></label>
    <p>{grouped.size} target genes · {visible.length} published links</p>
    {[...grouped.entries()].map(([id, rows]) => <details key={id}><summary><strong>{rows[0].symbol}</strong> · {new Set(rows.map(r => r.context)).size} contexts · {rows.length} links</summary>
      <button className="secondary-button" onClick={() => setGene(gene === id ? "" : id)}>Gene knowledge</button>
      {gene === id && geneKnowledge(rows[0].symbol)}
      <div className="ccre-gene-table-wrap"><table className="ccre-gene-table"><thead><tr><th>Context</th><th>{result.score_label}</th><th>Element (GRCh38)</th><th>Distance to TSS</th><th>Source</th></tr></thead>
      <tbody>{rows.map((r,i) => <tr key={i}><td>{r.context}</td><td>{r.score.toPrecision(3)}</td><td>{chrom}:{r.start+1}–{r.end}</td><td>{r.distance.toLocaleString()} bp</td>
        <td>{result.source_base ? <a href={`${result.source_base}${encodeURIComponent(r.accession)}/`} target="_blank" rel="noreferrer">{r.accession}</a> : r.accession}</td></tr>)}</tbody></table></div>
    </details>)}</section>;
}
