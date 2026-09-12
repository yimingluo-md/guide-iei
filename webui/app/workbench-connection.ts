export type WorkbenchLifecycle = {
  service: string;
  instance_id: string;
  desktop_app?: boolean;
  quitting?: boolean;
  restarting?: boolean;
  watch_supported?: boolean;
};

export type WorkbenchConnectionState = {
  phase: "checking" | "running" | "reconnecting" | "quitting" | "stopped";
  instanceId: string;
  desktopApp: boolean;
};

export const INITIAL_CONNECTION: WorkbenchConnectionState = {
  phase: "checking", instanceId: "", desktopApp: false,
};

// Independent of React so races, connection failures and cancellation can be
// tested without touching a running workstation. Nothing is persisted.
export function monitorWorkbenchConnection(
  read: (instanceId: string, signal: AbortSignal) => Promise<WorkbenchLifecycle>,
  changed: (state: WorkbenchConnectionState) => void,
  { retryMs = 1500, timeoutMs = 20000 } = {},
) {
  let state = INITIAL_CONNECTION;
  let confirmedQuit = "";
  let disposed = false;
  let generation = 0;
  let request: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function publish(next: WorkbenchConnectionState) {
    if (state.phase !== next.phase || state.instanceId !== next.instanceId || state.desktopApp !== next.desktopApp) {
      state = next;
      changed(state);
    }
  }

  async function poll() {
    const current = ++generation;
    const controller = new AbortController();
    request = controller;
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    let delay = retryMs;
    try {
      // Reconnection probes return immediately instead of waiting another
      // long-poll interval before clearing a transient connection warning.
      const next = await read(state.phase === "running" ? state.instanceId : "", controller.signal);
      if (disposed || current !== generation) return;
      if (next.service !== "GUIDE-IEI" || !next.instance_id || typeof next.instance_id !== "string") {
        throw new Error("Not a GUIDE-IEI lifecycle response");
      }
      if (next.instance_id !== state.instanceId) confirmedQuit = "";
      if (next.quitting === true) confirmedQuit = next.instance_id;
      const phase = confirmedQuit === next.instance_id ? "quitting"
        : next.restarting === true ? "reconnecting" : "running";
      publish({ phase, instanceId: next.instance_id, desktopApp: next.desktop_app === true });
      if (phase === "running" && next.watch_supported !== false) delay = 0;
    } catch {
      if (disposed || current !== generation) return;
      // A failed request alone NEVER establishes that the user quit.
      publish({ ...state, phase: confirmedQuit && confirmedQuit === state.instanceId ? "stopped" : "reconnecting" });
    } finally {
      clearTimeout(timeout);
      if (!disposed && current === generation) {
        request = undefined;
        timer = setTimeout(() => void poll(), delay);
      }
    }
  }

  void poll();
  return {
    acknowledgeQuit(instanceId: string) {
      if (disposed) return;
      // Supersede any in-flight healthy response from before the accepted POST.
      generation++;
      clearTimeout(timer);
      request?.abort();
      confirmedQuit = instanceId;
      publish({ ...state, phase: "quitting", instanceId });
      timer = setTimeout(() => void poll(), 0);
    },
    dispose() {
      disposed = true;
      generation++;
      clearTimeout(timer);
      request?.abort();
    },
  };
}
