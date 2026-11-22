import { useId, useState } from "react";
import { compileRule, type Operator, type PredictionRule, type ReviewMetric } from "./prediction-filters";

const labels: Record<Operator, string> = {
  gte: "≥", lte: "≤", between: "between", absolute: "|score| ≥",
  oneOf: "is one of", present: "has a value", missing: "is missing",
};

export function PredictionScoreFilters({ metrics, rules, onChange, total }: {
  metrics: ReviewMetric[]; rules: PredictionRule[]; onChange: (next: PredictionRule[]) => void; total: number;
}) {
  const [picking, setPicking] = useState(false);
  const [query, setQuery] = useState("");
  const pickerId = useId();
  function update(id: string, patch: Partial<PredictionRule>) {
    onChange(rules.map(rule => rule.id === id ? { ...rule, ...patch } : rule));
  }
  const choices = metrics.filter(metric => metric.label.toLowerCase().includes(query.trim().toLowerCase()));
  return <div className="score-filters">
    {rules.length > 0 && <p className="microcopy" id="prediction-threshold-help">Rows must match every active rule. Comparisons exclude rows with a missing value.</p>}
    {rules.map(rule => {
      const metric = metrics.find(item => item.id === rule.metric);
      if (!metric) return null;
      const ops: Operator[] = metric.numeric ? ["gte", "lte", "between", "absolute"] : ["oneOf"];
      ops.push("present", "missing");
      const comparison = !["present", "missing", "oneOf"].includes(rule.operator);
      const inactive = !compileRule(rule, metric);
      return <fieldset className="score-rule" key={rule.id}>
        <legend>{metric.label}</legend>
        <small>{metric.count.toLocaleString()} of {total.toLocaleString()} review rows have a value</small>
        <div className="score-rule-inputs">
          <select aria-label={metric.label + " operator"} value={rule.operator}
            onChange={e => update(rule.id, { operator: e.target.value as Operator })}>
            {ops.map(op => <option key={op} value={op}>{labels[op]}</option>)}
          </select>
          {comparison && <input aria-label={metric.label + " value"} placeholder={rule.operator === "between" ? "From" : "Value"}
            value={rule.first} onChange={e => update(rule.id, { first: e.target.value })}/>}
          {rule.operator === "between" && <input aria-label={metric.label + " upper bound"} placeholder="To"
            value={rule.second} onChange={e => update(rule.id, { second: e.target.value })}/>}
        </div>
        {rule.operator === "oneOf" && <div className="score-category-list">
          {metric.categories.map(label => <label key={label}><input type="checkbox" checked={rule.choices.includes(label)}
            onChange={e => update(rule.id, { choices: e.target.checked ? [...rule.choices, label] : rule.choices.filter(value => value !== label) })}/>
            {label}</label>)}
        </div>}
        <div className="score-rule-footer">
          {inactive && <small>{rule.operator === "oneOf" ? "Select a value to apply." : rule.operator === "between"
            ? "Enter a lower and upper bound in order." : "Enter a number to apply."}</small>}
          <button type="button" aria-label={"Remove " + metric.label + " filter"} onClick={() => onChange(rules.filter(item => item.id !== rule.id))}>Remove</button>
        </div>
      </fieldset>;
    })}
    <button className="secondary-button" type="button" aria-expanded={picking} aria-controls={pickerId}
      disabled={!metrics.length} onClick={() => { setPicking(!picking); setQuery(""); }}>{picking ? "Close metric picker" : "Add predictor filter"}</button>
    {!metrics.length && <small>No predictor metrics in this review.</small>}
    {picking && <div id={pickerId} className="score-picker">
      <input type="search" autoFocus aria-label="Search predictor metrics" placeholder="Search metrics" value={query} onChange={e => setQuery(e.target.value)}
        onKeyDown={e => { if (e.key === "Escape") setPicking(false); }}/>
      <div className="score-metric-list">{choices.map(metric => <button key={metric.id} type="button" onClick={() => {
        onChange([...rules, { id: crypto.randomUUID(), metric: metric.id, operator: metric.numeric ? "gte" : "oneOf", first: "", second: "", choices: [] }]);
        setPicking(false);
      }}>{metric.label}</button>)}{!choices.length && <small>No matching metrics.</small>}</div>
    </div>}
  </div>;
}
