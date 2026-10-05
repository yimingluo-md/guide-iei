/** Schedule the next refresh only after the previous batch has settled. */
export function startSerialPolling(
  refresh: (initial: boolean) => Promise<void>,
  onError: (error: unknown) => void,
  intervalMs = 2500,
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  async function poll(initial: boolean) {
    try {
      await refresh(initial);
    } catch (error) {
      if (!stopped) onError(error);
    } finally {
      if (!stopped) timer = setTimeout(() => void poll(false), intervalMs);
    }
  }
  void poll(true);
  return () => { stopped = true; clearTimeout(timer); };
}
