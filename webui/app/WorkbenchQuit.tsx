"use client";

import { useState } from "react";
import { getWorkbenchStatus, quitWorkbench } from "./local-service";

export function WorkbenchQuit({ onQuit }: { onQuit: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function quit() {
    setBusy(true);
    setError("");
    try {
      const status = await getWorkbenchStatus();
      if (status.blockers.length) {
        setError(`Wait for ${status.blockers.join(", ")} to finish before quitting.`);
        return;
      }
      if (!window.confirm(
        `Quit GUIDE-IEI?\n\nSave or export any Review once work in every browser tab first.\n\n`
        + `${status.annotations} annotation job(s) and ${status.downloads} dataset job(s) are active or queued. `
        + "Running jobs will be interrupted; queued annotation jobs can resume on the next launch. "
        + "Docker will remain running.\n\nClosing the browser alone does not stop GUIDE-IEI.",
      )) return;
      const result = await quitWorkbench(status.instance_id);
      onQuit(result.message);
    } catch (cause) {
      setError(`${cause instanceof Error ? cause.message : "Could not contact the workbench"}. If using an older app, quit from its Dock menu; for a Terminal launcher, press Control-C in that Terminal.`);
    } finally {
      setBusy(false);
    }
  }
  return <div className="workbench-quit">
    <button className="secondary-button" disabled={busy} onClick={() => void quit()}>{busy ? "Checking…" : "Quit GUIDE-IEI"}</button>
    {error && <div className="alert error" role="alert">{error}</div>}
  </div>;
}
