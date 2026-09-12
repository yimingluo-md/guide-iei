#!/usr/bin/env python3
"""Stage only the curated User Guide for Pages; never copy the whole docs tree.

Source Markdown remains readable in GitHub. Only the staged copies have links
rewritten: guide links stay on the website, other documents go to GitHub.
The output must not exist, so stale/unreviewed files cannot leak into a build.
"""
from __future__ import annotations

import argparse
import posixpath
import re
import shutil
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from check_documentation import FENCES, LINKS

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/yimingluo-md/guide-iei/blob/main/"


def stage(root: Path, output: Path) -> list[Path]:
    root = root.resolve()
    docs = root / "docs"
    chapters = sorted((docs / "guide").glob("*.md"))
    if not chapters or any(not re.fullmatch(r"\d{2}-[a-z0-9-]+\.md", p.name) for p in chapters):
        raise ValueError("Expected numbered guide chapters")
    sources = {docs / "guide.md": Path("index.md")}
    sources.update({p: Path("guide") / p.name for p in chapters})
    assets: set[Path] = set()
    rendered = {}
    for source, destination in sources.items():
        if source.is_symlink() or not source.resolve().is_relative_to(docs):
            raise ValueError("Guide sources must be regular files inside docs")
        content = source.read_text(encoding="utf-8")
        if not content.startswith("---\n"):
            raise ValueError(f"Missing guide front matter: {source.name}")
        fences = [(m.start(), m.end()) for m in FENCES.finditer(content)]

        def rewrite(match: re.Match) -> str:
            if any(start <= match.start() < end for start, end in fences):
                return match.group()
            url = urlsplit(match.group(1).strip("<>"))
            if url.scheme or url.netloc or not url.path:
                return match.group()
            target = (source.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError(f"Missing or out-of-repository guide link in {source.name}")
            if target in sources:
                path = posixpath.relpath(sources[target].with_suffix(".html").as_posix(), destination.parent.as_posix())
            elif target.is_relative_to(docs / "assets" / "img"):
                if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".svg", ".webp"}:
                    raise ValueError("Only referenced guide images may be copied")
                assets.add(target)
                path = posixpath.relpath(target.relative_to(docs).as_posix(), destination.parent.as_posix())
            else:
                path = REPOSITORY + quote(target.relative_to(root).as_posix(), safe="/")
            link = urlunsplit(("", "", path, url.query, url.fragment))
            start, end = match.start(1) - match.start(), match.end(1) - match.start()
            return match.group()[:start] + link + match.group()[end:]

        content = LINKS.sub(rewrite, content)
        permalink = "/" if destination.name == "index.md" else "/" + destination.with_suffix(".html").as_posix()
        rendered[destination] = "---\npermalink: " + permalink + "\n" + content[4:]
    config = docs / "_config.yml"
    if config.is_symlink() or not config.is_file():
        raise ValueError("Missing regular Pages configuration")
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(config, output / "_config.yml")
    for destination, content in rendered.items():
        path = output / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for asset in assets:
        path = output / asset.relative_to(docs)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(asset, path)
    return sorted(rendered)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    pages = stage(ROOT, args.output)
    print(f"Staged {len(pages)} guide pages and their referenced images; other documentation excluded.")
