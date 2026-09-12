#!/usr/bin/env python3
"""Check the built guide allowlist, local links/images/anchors and search scope."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASE = "/guide-iei"


class Page(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        for attribute in ("href", "src"):
            if attrs.get(attribute):
                self.links.append(attrs[attribute])


def verify(folder: Path, root: Path = ROOT) -> None:
    folder = folder.resolve()
    expected = {Path("index.html")} | {
        Path("guide") / p.with_suffix(".html").name for p in (root / "docs/guide").glob("*.md")
    }
    actual = {p.relative_to(folder) for p in folder.rglob("*.html")}
    if not expected <= actual or actual - expected - {Path("404.html")}:
        raise ValueError(f"Unexpected published pages: missing={expected - actual}, extra={actual - expected}")
    pages = {p: Page((folder / p).read_text(encoding="utf-8")) for p in actual}

    def local_target(page: Path, link: str):
        url = urlsplit(link)
        if url.scheme or url.netloc:
            if url.scheme == "https" and url.netloc == "yimingluo-md.github.io":
                pass
            else:
                return None, url
        path = unquote(url.path)
        if path.startswith("/"):
            if path != BASE and not path.startswith(BASE + "/"):
                raise ValueError(f"Site link escapes its base URL: {page}: {link}")
            target = folder / path[len(BASE):].lstrip("/")
        else:
            target = folder / page.parent / path if path else folder / page
        target = target.resolve()
        if not target.is_relative_to(folder):
            raise ValueError(f"Link escapes site: {page}: {link}")
        if target.is_dir():
            target = target / "index.html"
        return target, url

    for page, parsed in pages.items():
        for link in parsed.links:
            target, url = local_target(page, link)
            if target is None:
                continue
            if not target.is_file():
                raise ValueError(f"Broken local link/image: {page}: {link}")
            relative = target.relative_to(folder)
            if url.fragment and relative in pages and unquote(url.fragment) not in pages[relative].ids:
                raise ValueError(f"Missing HTML anchor: {page}: {link}")
    search = folder / "assets/js/search-data.json"
    data = json.loads(search.read_text(encoding="utf-8"))
    entries = list(data.values()) if isinstance(data, dict) else data
    if not entries:
        raise ValueError("Empty guide search index")
    for entry in entries:
        target, _ = local_target(Path("index.html"), entry["url"])
        if target is None or target.relative_to(folder) not in expected:
            raise ValueError("Non-guide document in search index")
    print(f"Verified {len(expected)} guide pages, local links/images/anchors, and guide-only search.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    verify(parser.parse_args().folder)
