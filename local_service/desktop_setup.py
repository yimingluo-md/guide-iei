"""Native first-launch orchestration; the service owns setup and cleanup."""
import json
import os
import time
import urllib.error
import urllib.request


def api(base, route, body=None):
    request = urllib.request.Request(base + route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        with exc:
            detail = json.load(exc)
        raise RuntimeError(detail.get("error") or "Startup request failed") from exc


def write_status(path, phase, message, log_path=""):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"phase": phase, "message": message, "log_path": log_path}))
    os.replace(temporary, path)


def prepare_engine(base, support, status_path, control_path, alive, call=api, pause=time.sleep):
    """Return True to open review, False to quit; failed installs require Retry."""
    deferred = support / "annotation-setup-deferred.json"
    phase, job, log_path = "checking", None, ""
    skip = quit_requested = cancellation_sent = False
    attempted = False
    write_status(status_path, phase, "Checking installed annotation components…")
    while alive():
        try:
            action = json.loads(control_path.read_text()).get("action")
            control_path.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError):
            action = None
        if action in {"skip", "quit"}:
            skip, quit_requested = True, action == "quit"
        if action == "retry" and phase == "failed":
            phase, job, cancellation_sent = "checking", None, False
        try:
            if skip:
                # A timed-out POST may still have started a job. Discover it
                # before allowing review; never abandon an invisible installer.
                if attempted and job is None:
                    jobs = call(base, "/api/resource-downloads")["jobs"]
                    job = next((item for item in jobs if item.get("resource_id") == "annotation_engine"
                                and item["status"] in {"queued", "running"}), None)
                if job and not cancellation_sent:
                    call(base, "/api/annotation-engine/cancel", {"job_id": job["id"], "confirm": True})
                    cancellation_sent = True
                if job and call(base, "/api/annotation-engine/status").get("busy"):
                    write_status(status_path, "stopping", "Stopping annotation preparation safely…", log_path)
                else:
                    if not quit_requested:
                        deferred.write_text(json.dumps({"deferred": True}))
                    write_status(status_path, "skipped", "Opening without annotation. Engine setup remains available in Import & QC.", log_path)
                    return not quit_requested
            elif phase == "checking":
                write_status(status_path, phase, "Checking installed annotation components…")
                status = call(base, "/api/annotation-engine/status")
                if status.get("available"):
                    deferred.unlink(missing_ok=True)
                    write_status(status_path, "ready", "Annotation engine ready. Opening the workbench…")
                    return True
                if deferred.exists():
                    write_status(status_path, "skipped", "Opening without annotation, as previously selected.")
                    return True
                if status.get("state") == "runtime_starting":
                    write_status(status_path, phase, "Starting the installed container runtime…")
                else:
                    attempted = True
                    job = call(base, "/api/annotation-engine/setup", {"confirm": True})
                    log_path, phase = job.get("log_path", ""), "preparing"
                    write_status(status_path, phase, "Preparing the annotation engine…", log_path)
            elif phase == "preparing":
                jobs = call(base, "/api/resource-downloads")["jobs"]
                job = next(item for item in jobs if item["id"] == job["id"])
                if job["status"] in {"failed", "interrupted"}:
                    raise RuntimeError(job.get("error") or "Annotation preparation needs attention.")
                if job["status"] == "succeeded":
                    status = call(base, "/api/annotation-engine/status")
                    if not status.get("busy"):
                        if not status.get("available"):
                            raise RuntimeError(status.get("message") or "Engine validation failed")
                        deferred.unlink(missing_ok=True)
                        write_status(status_path, "ready", "Annotation engine ready. Opening the workbench…", log_path)
                        return True
                else:
                    write_status(status_path, phase, job.get("message") or "Preparing the annotation engine…", log_path)
        except (OSError, ValueError, RuntimeError, KeyError, StopIteration) as exc:
            phase = "failed"
            write_status(status_path, phase, str(exc) or "Preparation needs attention. Open Log, Retry, or open without annotation.", log_path)
        pause(.5)
    return False
