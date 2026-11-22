import type { GeneKnowledgeGene } from "./local-service";

export function GenccEvidence({ data }: { data: GeneKnowledgeGene["gencc"] }) {
  return <article className="gene-knowledge-card">
    <div className="section-title">
      <div><p className="eyebrow">Gene Curation Coalition</p><h3>GenCC gene–disease validity</h3></div>
      {data?.available && <span>Snapshot {data.release}</span>}
    </div>
    {!data?.available ? <p className="knowledge-empty">Update gene knowledge to add GenCC. Open Gene knowledge and choose Check and update now.</p>
      : !data.groups.length ? <p className="knowledge-empty">No published GenCC submission in this snapshot.</p>
      : data.groups.map(group => <details className="gencc-relationship" key={group.disease_id + "/" + group.inheritance_id}>
        <summary>
          <strong>{group.disease}</strong> · {group.inheritance}
          <span className="gencc-assessments">{group.classifications.join(" · ")}
            {group.conflict && <b>Conflicting submissions</b>}
          </span>
        </summary>
        <small>{group.disease_id} · {group.inheritance_id}</small>
        <div className="knowledge-table-wrap"><table className="knowledge-table">
          <thead><tr><th>Submitter</th><th>Classification</th><th>Evaluated</th><th>Evidence</th></tr></thead>
          <tbody>{group.submissions.map(row => <tr key={row.sgc_id}>
            <td><strong>{row.submitter_title}</strong>{row.submitter_title === "ClinGen" && <small>ClinGen curation via GenCC</small>}</td>
            <td>{row.classification_title}
              {row.disease_original_curie && row.disease_original_curie !== group.disease_id &&
                <small>{row.disease_original_title} ({row.disease_original_curie})</small>}
            </td>
            <td>{row.submitted_as_date.slice(0, 10) || "—"}</td>
            <td><a href={`https://thegencc.org/submissions/${row.sgc_id}.${row.version_number}`} target="_blank" rel="noreferrer">{row.sgc_id}.{row.version_number}</a>
              {/^https?:\/\//i.test(row.submitted_as_public_report_url) && <a href={row.submitted_as_public_report_url} target="_blank" rel="noreferrer">Submitter report</a>}
            </td>
          </tr>)}</tbody>
        </table></div>
      </details>)}
  </article>;
}
