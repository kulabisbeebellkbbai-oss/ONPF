"""Load portable prompts and document structures from packaged files."""
import json
from pathlib import Path

from onpf.db import Record

FRAMEWORK_PATH = Path(__file__).resolve().parent.parent / "framework"


def load_framework() -> Record:
    modules = {}
    for source in sorted(FRAMEWORK_PATH.glob("*.json")):
        module = json.loads(source.read_text(encoding="utf-8"))
        modules[module["key"]] = module
    documents = {}
    for source in sorted((FRAMEWORK_PATH / "documents").glob("*.md")):
        title = source.stem.replace("-", " ").title()
        sections = []
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                title = line[2:]
            elif line.startswith("## "):
                key, label = line[3:].split(" | ", 1)
                sections.append({"key": key, "label": label, "guidance": ""})
            elif sections and line.strip():
                sections[-1]["guidance"] += (" " if sections[-1]["guidance"] else "") + line.strip()
        documents[source.stem] = {"key": source.stem, "title": title, "sections": sections}
    return {"version": modules["core"]["version"], "stages": modules["core"]["stages"], "modules": modules, "documents": documents}
