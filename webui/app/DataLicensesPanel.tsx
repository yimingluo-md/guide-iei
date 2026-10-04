import starterLicenses from "../../config/starter-licenses.json";
import registry from "../../config/predictor-registry.json";

/** Informational, offline-readable notices. Never records or gates consent. */
export default function DataLicensesPanel() {
  const texts: Record<string, { url: string; text: string }> = starterLicenses.license_texts;
  return <div className="gene-knowledge-settings data-licenses-panel">
    <div className="content-header"><div><p className="eyebrow">About the data</p>
      <h1>Data sources &amp; licenses</h1>
      <p className="subtitle">GUIDE-IEI software is MIT-licensed. Annotation datasets retain their own licenses; some are restricted to noncommercial use.</p>
    </div></div>
    <section className="gene-resource-section">
      <h2>Software and data have separate terms</h2>
      <p>A free application does not remove a dataset&apos;s use restrictions. Review the applicable upstream terms for your intended use. No acknowledgment is required on this page.</p>
      <p>Notices are available here offline. Upstream links open external websites only when selected. Listing a resource does not mean it is installed or endorsed by its authors.</p>
      <a href="https://github.com/yimingluo-md/guide-iei/blob/main/LICENSE" target="_blank" rel="noreferrer">GUIDE-IEI MIT license ↗</a>
    </section>
    <section className="gene-resource-section">
      <h2>Compact starter annotations</h2>
      <p>The starter package is in development and is not yet part of the released installer. The following notices accompany newly prepared subsets.</p>
      {starterLicenses.resources.map((resource) => <article className="software-update-card" key={resource.id}>
        <h3>{resource.name}</h3>
        <p><strong>{resource.license_id}</strong> · <a href={resource.source_url} target="_blank" rel="noreferrer">Official source ↗</a> · <a href={texts[resource.license_id].url} target="_blank" rel="noreferrer">Upstream terms ↗</a></p>
        <p>{resource.attribution}</p>
        <details><summary>Subset changes and retained source notices</summary>
          <p>{resource.changes}</p><pre className="data-license-text">{resource.source_notice}</pre>
        </details>
        <details><summary>Read the full license — available offline</summary>
          <pre className="data-license-text">{texts[resource.license_id].text}</pre>
        </details>
      </article>)}
    </section>
    <section className="gene-resource-section">
      <h2>Other annotation sources</h2>
      <p>These links identify the registry&apos;s upstream terms and sources. Resource-specific installation requirements remain unchanged. Engine and runtime components have separate notices in the application package.</p>
      <ul>{registry.resources.filter((resource) => !resource.id.startsWith("starter_")).map((resource) => <li key={resource.id}>
        <strong>{resource.label}</strong> — {resource.license_name}
        {resource.source_url && <> · <a href={resource.source_url} target="_blank" rel="noreferrer">Source ↗</a></>}
      </li>)}</ul>
    </section>
  </div>;
}
