"""Read-only static export serving for the self-contained desktop edition."""
import mimetypes
import os
import shutil
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import unquote, urlsplit


def serve_static(handler, root: Path) -> None:
    root = root.resolve()
    name = unquote(urlsplit(handler.path).path).lstrip("/") or "index.html"
    with ExitStack() as files:
        try:
            target = (root / name).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                raise FileNotFoundError
            source = files.enter_context(target.open("rb"))
            size = os.fstat(source.fileno()).st_size
        except (OSError, ValueError):
            handler.send_error(404)
            return

        try:
            handler.send_response(200)
            handler.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            handler.send_header("Content-Length", str(size))
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.end_headers()
            shutil.copyfileobj(source, handler.wfile, length=1024 * 1024)
        except (OSError, ValueError):
            handler.close_connection = True
