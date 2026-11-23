type Cell = string | number | boolean | null | undefined;

function tsvLine(cells: readonly Cell[]) {
  return cells.map(cell => {
    const value = String(cell ?? "");
    // Text can be interpreted as a spreadsheet formula, including after leading
    // whitespace. Numeric cells must stay numeric (negative scores are valid).
    const prefix = typeof cell === "string" && (/^\s*[=+\-@]/u.test(value) || /^[\t\r\n]/u.test(value)) ? "'" : "";
    return prefix + value.replaceAll("\\", "\\\\")
      .replaceAll("\t", "\\t").replaceAll("\r", "\\r").replaceAll("\n", "\\n");
  }).join("\t") + "\n";
}

export function reviewTsvBlob<T>(
  notice: string, headers: string[], rows: readonly T[], cells: (row: T) => Cell[],
) {
  const chunks: Blob[] = [];
  let chunk = tsvLine(["# " + notice]) + tsvLine(headers);
  for (const row of rows) {
    chunk += tsvLine(cells(row));
    if (chunk.length >= 64 * 1024) {
      chunks.push(new Blob([chunk]));
      chunk = "";
    }
  }
  return new Blob([...chunks, chunk], { type: "text/tab-separated-values;charset=utf-8" });
}
