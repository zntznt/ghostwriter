"""library/ <-> blueprint books.

Layout: a folder is a book. Its optional `_book.json` holds the book's own
fields ({"index": n, "blueprint_book": {...}} minus "blueprints"; index only
for child books). Every other `*.json` is one child ({"index": n,
"<kind>": {...}}); subfolders are child books.
Children sort by stored index, then name; children without one are numbered
after the highest index.

Assembly is plain dict plumbing over the decoded files, so the book holds
exactly what the library holds.
"""

import json
import re
from pathlib import Path

from tools.codec import dump_json, root_key

BOOK_META = "_book.json"


def load_book(folder: Path) -> dict:
    meta_path = folder / BOOK_META
    if meta_path.exists():
        book = json.loads(meta_path.read_text())["blueprint_book"]
    else:
        book = {"item": "blueprint-book", "label": folder.name}

    children = []
    for path in sorted(folder.iterdir()):
        if path.is_dir():
            sub_meta = path / BOOK_META
            index = json.loads(sub_meta.read_text()).get("index") if sub_meta.exists() else None
            children.append((index, path.name, load_book(path)))
        elif path.suffix == ".json" and path.name != BOOK_META:
            child = json.loads(path.read_text())
            root_key(child)
            children.append((child.pop("index", None), path.name, child))

    used = [i for i, _, _ in children if i is not None]
    if len(used) != len(set(used)):
        raise ValueError(f"{folder}: duplicate child index in {sorted(used)}")
    next_index = max(used, default=-1) + 1
    blueprints = []
    for index, _, child in sorted(children, key=lambda c: (c[0] is None, c[0] or 0, c[1])):
        if index is None:
            index, next_index = next_index, next_index + 1
        blueprints.append({"index": index, **child})
    book["blueprints"] = blueprints
    if "version" not in book:
        versions = [c[root_key(c)].get("version", 0) for c in blueprints]
        if versions:
            book["version"] = max(versions)
    return {"blueprint_book": book}


def save(data: dict, folder: Path, name: str | None = None, index: int | None = None) -> Path:
    """Write a decoded blueprintable into folder; books become subfolders."""
    kind = root_key(data)
    body = data[kind]
    name = name or slug(body.get("label") or kind)
    if kind != "blueprint_book":
        path = _unique(folder / f"{name}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_json(data))
        return path

    book_dir = _unique(folder / name)
    book_dir.mkdir(parents=True)
    meta = {"blueprint_book": {k: v for k, v in body.items() if k != "blueprints"}}
    if index is not None:
        meta = {"index": index, **meta}
    (book_dir / BOOK_META).write_text(dump_json(meta))
    for child in body.get("blueprints", []):
        child = dict(child)
        index = child.pop("index", None)
        child_kind = root_key(child)
        child_name = slug(child[child_kind].get("label") or child_kind)
        if index is not None:
            child_name = f"{index:02d}-{child_name}"
        if child_kind == "blueprint_book":
            save(child, book_dir, child_name, index)
        else:
            path = _unique(book_dir / f"{child_name}.json")
            path.write_text(dump_json({"index": index, **child}))
    return book_dir


def slug(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", "", text)  # rich-text icons like [item=iron-plate]
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "untitled"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while (candidate := path.with_name(f"{stem}-{n}{suffix}")).exists():
        n += 1
    return candidate
