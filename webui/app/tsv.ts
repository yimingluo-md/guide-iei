type Cell = string | number | boolean | null | undefined;

function tsvLine(cells: readonly Cell[]) {
  return cells.map(cell => String(cell ?? "").replaceAll("\\", "\\\\")
    .replaceAll("\t", "\\t").replaceAll("\r", "\\r").replaceAll("\n", "\\n")).join("\t") + "\n";
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
