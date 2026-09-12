"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { watchWorkbenchLifecycle } from "./local-service";
import { INITIAL_CONNECTION, monitorWorkbenchConnection, type WorkbenchConnectionState } from "./workbench-connection";

export function useWorkbenchConnection() {
  const [connection, setConnection] = useState(INITIAL_CONNECTION);
  const monitor = useRef<ReturnType<typeof monitorWorkbenchConnection> | null>(null);
  useEffect(() => {
    const current = monitorWorkbenchConnection(watchWorkbenchLifecycle, setConnection);
    monitor.current = current;
    return () => { monitor.current = null; current.dispose(); };
  }, []);
  const acknowledgeQuit = useCallback((instanceId: string) => monitor.current?.acknowledgeQuit(instanceId), []);
  return { connection, acknowledgeQuit };
}

export function WorkbenchConnectionNotice({ connection }: { connection: WorkbenchConnectionState }) {
  if (connection.phase === "checking" || connection.phase === "running") return null;
  const stopped = connection.phase === "stopped";
  const quitting = connection.phase === "quitting";
  return <section className="workbench-connection-notice" role="status" aria-live="polite" aria-atomic="true">
    <strong>{stopped ? "GUIDE-IEI is no longer running." : quitting ? "GUIDE-IEI is shutting down…" : connection.instanceId ? "Connection lost—reconnecting…" : "Connecting to GUIDE-IEI…"}</strong>
    <p>{stopped || quitting
      ? `You can close this tab. To resume, reopen GUIDE-IEI ${connection.desktopApp ? "from Applications" : "using your original launcher"}.`
      : "The local service cannot currently be reached or is restarting. If you closed GUIDE-IEI, reopen it. This page will reconnect automatically."}</p>
    <p>Your loaded review is preserved. You can still view and export loaded variants; actions that need the local service are unavailable until it reconnects.</p>
  </section>;
}
