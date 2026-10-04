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


def write_status(path, phase, message, log_path="", **details):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"phase": phase, "message": message, "log_path": log_path, **details}))
    os.replace(temporary, path)


def prepare_engine(base, support, status_path, control_path, alive, call=api, pause=time.sleep, *, essential=False):
    """Return True to open review, False to quit; failed installs require Retry."""
    deferred = support / "annotation-setup-deferred.json"
    phase, job, log_path = "checking", None, ""
    quit_requested = cancellation_sent = False
    attempted = False
    authorized = not essential
    status_route = "/api/essential-setup/status" if essential else "/api/annotation-engine/status"
    setup_route = "/api/essential-setup/setup" if essential else "/api/annotation-engine/setup"
    resource_id = "essential_setup" if essential else "annotation_engine"
    location = {}

    def update_location(status):
        for key in ("annotation_path", "free_bytes", "estimated_required_bytes"):
            if key in status:
                location[key] = status[key]

    def publish(phase, message, log_path="", **details):
        # Every snapshot is self-contained: the native poll may miss the brief
        # waiting state, or start with an already prepared installation.
        write_status(status_path, phase, message, log_path, **{**location, **details})

    publish(phase, "Checking installed annotation components…")
    while alive():
        try:
            action = json.loads(control_path.read_text()).get("action")
            control_path.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError):
            action = None
        if action == "quit":
            quit_requested = True
        if action == "prepare" and phase in {"waiting", "failed"}:
            authorized = True
            phase, job, cancellation_sent = "checking", None, False
        if action == "retry" and phase == "failed":
            authorized = True
            phase, job, cancellation_sent = "checking", None, False
        try:
            if quit_requested:
                # A timed-out POST may still have started a job. Discover it
                # before allowing review; never abandon an invisible installer.
                if attempted and job is None:
                    jobs = call(base, "/api/resource-downloads")["jobs"]
                    job = next((item for item in jobs if item.get("resource_id") in {resource_id, "annotation_engine"}
                                and item["status"] in {"queued", "running"}), None)
                if job and not cancellation_sent:
                    call(base, "/api/annotation-engine/cancel", {"job_id": job["id"], "confirm": True})
                    cancellation_sent = True
                if job and call(base, status_route).get("busy"):
                    publish("stopping", "Stopping annotation preparation safely…", log_path)
                else:
                    publish("stopped", "Annotation preparation stopped.", log_path)
                    return False
            elif phase in {"checking", "waiting"}:
                publish(phase, "Checking installed annotation components…")
                status = call(base, status_route)
                update_location(status)
                if status.get("available") and not status.get("busy") and not status.get("restart_required"):
                    deferred.unlink(missing_ok=True)
                    publish("ready", "GUIDE-IEI is ready. Opening the workbench…")
                    return True
                if essential and status.get("restart_required"):
                    phase = "waiting"
                    publish(phase, "Restart to activate the selected storage location.",
                                 can_prepare=False, **{k: status[k] for k in ("annotation_path", "free_bytes", "estimated_required_bytes")})
                    pause(.5)
                    continue
                if essential and not authorized:
                    phase = "waiting"
                    installable = status.get("package", {}).get("installable", False) or status.get("legacy_ready", False)
                    publish(phase, status.get("message") or
                                 "Choose your data location, then prepare the environment and essential annotations together.",
                                 can_prepare=installable,
                                 **{k: status[k] for k in ("annotation_path", "free_bytes", "estimated_required_bytes")})
                    pause(.5)
                    continue
                if essential and status.get("busy"):
                    jobs = call(base, "/api/resource-downloads")["jobs"]
                    job = next((item for item in jobs if item.get("resource_id") in {resource_id, "annotation_engine"}
                                and item["status"] in {"queued", "running"}), None)
                    if job:
                        log_path, phase = job.get("log_path", ""), "preparing"
                    pause(.5)
                    continue
                if status.get("state") == "runtime_starting":
                    publish(phase, "Starting the installed container runtime…")
                else:
                    attempted = True
                    route = "/api/annotation-engine/setup" if essential and status.get("legacy_ready") else setup_route
                    job = call(base, route, {"confirm": True})
                    log_path, phase = job.get("log_path", ""), "preparing"
                    publish(phase, "Preparing GUIDE-IEI…", log_path)
            elif phase == "preparing":
                jobs = call(base, "/api/resource-downloads")["jobs"]
                job = next(item for item in jobs if item["id"] == job["id"])
                if job["status"] in {"failed", "interrupted"}:
                    raise RuntimeError(job.get("error") or "Annotation preparation needs attention.")
                if job["status"] == "succeeded":
                    status = call(base, status_route)
                    update_location(status)
                    if not status.get("busy"):
                        if not status.get("available"):
                            raise RuntimeError(status.get("message") or "Engine validation failed")
                        deferred.unlink(missing_ok=True)
                        publish("ready", "GUIDE-IEI is ready. Opening the workbench…", log_path)
                        return True
                else:
                    publish(phase, job.get("message") or "Preparing the annotation engine…", log_path)
        except (OSError, ValueError, RuntimeError, KeyError, StopIteration) as exc:
            phase = "failed"
            publish(phase, str(exc) or "Preparation needs attention. Open Log, Retry, or Quit GUIDE-IEI.", log_path)
        pause(.5)
    return False
