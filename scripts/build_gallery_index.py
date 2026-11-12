"""Rebuild gallery/index.json from gallery/plugins/*.py.

Run after adding or changing a gallery plugin: python scripts/build_gallery_index.py
(tests/test_plugin_gallery.py fails if the index is out of date).
"""
import ast
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import plugin_permissions  # noqa: E402

GALLERY = os.path.join(ROOT, "gallery")


def build() -> dict:
    entries = []
    folder = os.path.join(GALLERY, "plugins")
    for filename in sorted(os.listdir(folder)):
        if not filename.endswith(".py"):
            continue
        path = os.path.join(folder, filename)
        with open(path, "rb") as f:
            raw = f.read()
        doc = ast.get_docstring(ast.parse(raw.decode("utf-8"))) or ""
        entries.append({
            "name": filename[:-3],
            "title": filename[:-3].replace("_", " ").title(),
            "description": " ".join(doc.split()),
            "permissions": plugin_permissions.declared(path) or [],
            "file": f"plugins/{filename}",
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    return {"version": 1, "plugins": entries}


if __name__ == "__main__":
    with open(os.path.join(GALLERY, "index.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(build(), f, indent=2)
        f.write("\n")
    print("gallery/index.json updated")
