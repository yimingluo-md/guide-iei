"use client";
// Tap-for-definition affordance over authored copy, plus a browsable panel.
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

/** Searchable full glossary, opened from the topbar. */
export function GlossaryPanel({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const needle = query.trim().toLowerCase();
  const visible = GLOSSARY.filter((entry) =>
    !needle
    || entry.term.toLowerCase().includes(needle)
    || (entry.aliases ?? []).some((alias) => alias.toLowerCase().includes(needle))
    || entry.definition.toLowerCase().includes(needle));
  return (
    <div className="glossary-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="glossary-panel" role="dialog" aria-label="Glossary">
        <header>
          <div><p className="eyebrow">Reference</p><h2>Glossary</h2></div>
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search terms…"
            spellCheck={false}
          />
          <button type="button" className="secondary-button" onClick={onClose}>Close</button>
        </header>
        <div className="glossary-panel-body">
          {GLOSSARY_CATEGORIES.map((category) => {
            const entries = visible.filter((entry) => entry.category === category);
            if (!entries.length) return null;
            return (
              <section key={category}>
                <h3>{category}</h3>
                {entries.map((entry) => (
                  <article key={entry.id}>
                    <strong>{entry.term}</strong>
                    {(entry.aliases?.length || entry.exactAliases?.length) ? (
                      <small>{[...(entry.aliases ?? []), ...(entry.exactAliases ?? [])].join(" · ")}</small>
                    ) : null}
                    <p>{entry.definition}</p>
                  </article>
                ))}
              </section>
            );
          })}
          {!visible.length && <p className="glossary-empty">No terms match “{query}”.</p>}
        </div>
      </div>
    </div>
  );
}
