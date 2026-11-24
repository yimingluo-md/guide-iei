"""Private local endpoint discovery for the native desktop launcher."""
from contextlib import ExitStack, contextmanager
import errno
import json
import os
from pathlib import Path
import socket
import sys
import tempfile


@contextmanager
def guard_loopback_aliases(port):
    """Hold non-listening sockets on overlapping addresses until IPv4 startup finishes."""
    with ExitStack() as stack:
        if sys.platform == "darwin":
            # With SO_REUSEADDR, Darwin can admit a specific IPv4 bind beside
            # a wildcard listener. Check the wildcard itself with no REUSEPORT.
            wildcard = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            wildcard.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            wildcard.bind(("0.0.0.0", port))
        if socket.has_ipv6:
            try:
                ipv6 = stack.enter_context(socket.socket(socket.AF_INET6, socket.SOCK_STREAM))
                # Without address reuse, this also checks IPv6 wildcard listeners.
                ipv6.bind(("::1", port))
            except OSError as exc:
                if exc.errno not in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL):
                    raise
        yield


def port_available(port):
    try:
        with guard_loopback_aliases(port), socket.socket() as probe:
            # Permit an immediate relaunch after a closed workbench connection.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        return False
    return True


def select_ui_port(preferred, service_port):
    """Choose a loopback port for the source launcher's separate browser UI."""
    if preferred != service_port and port_available(preferred):
        return preferred
    for candidate in range(3001, 65536):
        if candidate not in (preferred, service_port) and port_available(candidate):
            return candidate
    raise OSError("No available local port for the workbench interface")


def read_endpoint(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("service") != "GUIDE-IEI":
        return None
    port = data.get("port")
    instance = data.get("instance_id")
    if (type(port) is not int or not 1 <= port <= 65535
            or not isinstance(instance, str) or not instance or len(instance) > 128):
        return None
    # Never accept a URL/host from disk: discovery is always IPv4 loopback.
    return {"service": "GUIDE-IEI", "port": port, "instance_id": instance}


def write_endpoint(path, port, instance_id):
    path = Path(path)
    payload = {"service": "GUIDE-IEI", "port": port, "instance_id": instance_id}
    fd, name = tempfile.mkstemp(prefix=".endpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
