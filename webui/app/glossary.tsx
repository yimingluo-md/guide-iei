"use client";
// Tap-for-definition affordance over authored copy, plus the Glossary page.
// Definitions live in glossary-data.ts and are written once.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  GLOSSARY, GLOSSARY_CATEGORIES, segmentText,
  type GlossaryEntry,
} from "./glossary-data";

function TermPopover({ entry, onClose }: { entry: GlossaryEntry; onClose: () => void }) {
  const popoverRef = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    const onClick = (event: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [onClose]);
  return (
    <span className="glossary-popover" ref={popoverRef} role="dialog" aria-label={`Definition of ${entry.term}`}>
      <strong>{entry.term}</strong>
      <span>{entry.definition}</span>
    </span>
  );
}

/** Render authored copy with tap-for-definition on recognized genetics terms. */
export function GlossaryText({ text }: { text: string }) {
  const segments = useMemo(() => segmentText(text), [text]);
  const [openId, setOpenId] = useState<string | null>(null);
  if (segments.length === 1 && !segments[0].entry) return <>{text}</>;
  return (
    <>
      {segments.map((segment, index) => {
        const entry = segment.entry;
        if (!entry) return <span key={index}>{segment.text}</span>;
        return (
          <span className="glossary-anchor" key={`${entry.id}-${index}`}>
            <button
              type="button"
              className="glossary-term"
              aria-expanded={openId === entry.id}
              onClick={(event) => {
                event.stopPropagation();
                setOpenId(openId === entry.id ? null : entry.id);
              }}
            >
              {segment.text}
            </button>
            {openId === entry.id && (
              <TermPopover entry={entry} onClose={() => setOpenId(null)} />
            )}
          </span>
        );
      })}
    </>
  );
}

/** The Glossary page: searchable definitions grouped by category. */
export function GlossaryView() {
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const visible = GLOSSARY.filter((entry) =>
    !needle
    || entry.term.toLowerCase().includes(needle)
    || (entry.aliases ?? []).some((alias) => alias.toLowerCase().includes(needle))
    || entry.definition.toLowerCase().includes(needle));
  return (
    <div className="glossary-view">
      <div className="section-title">
        <div><p className="eyebrow">Reference</p><h2>Glossary</h2></div>
        <span>Plain-language definitions of the genetics terms used across this workbench</span>
      </div>
      <label className="form-field glossary-search">
        <span>Search</span>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Term, abbreviation, or words from a definition…"
          spellCheck={false}
        />
      </label>
      {GLOSSARY_CATEGORIES.map((category) => {
        const entries = visible.filter((entry) => entry.category === category);
        if (!entries.length) return null;
        return (
          <section className="glossary-section" key={category}>
            <h3>{category}</h3>
            <div className="glossary-entries">
              {entries.map((entry) => (
                <article key={entry.id}>
                  <strong>{entry.term}</strong>
                  {(entry.aliases?.length || entry.exactAliases?.length) ? (
                    <small>Also: {[...(entry.aliases ?? []), ...(entry.exactAliases ?? [])].join(" · ")}</small>
                  ) : null}
                  <p>{entry.definition}</p>
                </article>
              ))}
            </div>
          </section>
        );
      })}
      {!visible.length && <p className="glossary-empty">No terms match “{query}”.</p>}
    </div>
  );
}
