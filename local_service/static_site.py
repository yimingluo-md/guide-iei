"""Read-only static export serving for the self-contained desktop edition."""
import mimetypes
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit


def serve_static(handler, root: Path) -> None:
    root = root.resolve()
    name = unquote(urlsplit(handler.path).path).lstrip("/") or "index.html"
    try:
        target = (root / name).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            handler.send_error(404)
            return
        # No directory listings, source-tree fallback, or reads outside the export.
        with target.open("rb") as source:
            handler.send_response(200)
            handler.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            handler.send_header("Content-Length", str(target.stat().st_size))
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.end_headers()
            shutil.copyfileobj(source, handler.wfile, length=1024 * 1024)
    except (OSError, ValueError):
        handler.send_error(404)
