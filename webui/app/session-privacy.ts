/** Purge patient-derived bookmarks saved by earlier builds. Never read/copy them. */
export function clearLegacySavedCandidates(storage: Pick<Storage, "removeItem">): boolean {
  try {
    storage.removeItem("guideIeiSavedCandidates");
    return true;
  } catch {
    return false;
  }
}
