"""Full Semantica knowledge graph over ALL of kern-main:
   - source structure (recursive .py) + cross-module links
   - all markdown docs (recursive, all dirs)
   - merge of knowledge/scm-books graph (24 books, graphify export)
   - theory->code bridge (book concepts -> modules that implement them)
"""
import json
import re
import sys
import traceback
from collections import Counter
from pathlib import Path

from semantica.context import ContextGraph
from semantica.semantic_extract.named_entity_recognizer import NamedEntityRecognizer

ROOT = Path(r"kern-main")
OUT_DIR = Path(r"kern-semantica-out")
OUT_DIR.mkdir(exist_ok=True)

SKIP_DIRS = {
    ".git", ".github", ".claude", ".cursor", ".serena",
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "__pycache__", "node_modules", "kern.egg-info",
    "graphify-out",  # existing code graph (graphify), not raw source
}


def walk(folder: Path, ext: str):
    """Yield files with given extension, skipping caches/hidden/graph artifacts."""
    for f in sorted(folder.rglob(f"*{ext}")):
        parts = set(f.relative_to(folder).parts)
        if parts & SKIP_DIRS:
            continue
        if any(p.startswith(".") for p in parts):
            continue
        yield f


def parse_module(text: str):
    """Return dict with classes (name, bases, methods, line), functions, imports."""
    mod = {
        "imports": {},      # {module_name: {symbols: set|None}}
        "classes": {},      # {name: {"bases": [], "methods": [], "line": int}}
        "functions": {},    # {name: {"line": int}}
    }
    mod["import_targets"] = []
    for m in re.finditer(r"^from\s+([\w.]+)\s+import\s+([^\n#]+)", text, re.M):
        target = m.group(1).strip()
        symbols = {s.strip() for s in m.group(2).split(",") if s.strip()}
        mod["imports"].setdefault(target, set()).update(symbols)
        mod["import_targets"].append((target, symbols))
    for m in re.finditer(r"^import\s+([^\n#]+)", text, re.M):
        for part in m.group(1).split(","):
            t = part.strip()
            if t:
                mod["imports"].setdefault(t, None)
                mod["import_targets"].append((t, None))

    current_class = None
    for i, line in enumerate(text.splitlines(), 1):
        cm = re.match(r"^class\s+(\w+)(\((.*?)\))?\s*:", line)
        if cm:
            name = cm.group(1)
            bases_s = cm.group(3) or ""
            bases = [b.strip().split(".")[-1] for b in bases_s.split(",") if b.strip()]
            mod["classes"][name] = {"bases": bases, "methods": [], "line": i}
            current_class = name
            continue
        fm = re.match(r"^\s*def\s+(\w+)\s*\(", line)
        if fm:
            fname = fm.group(1)
            if current_class is not None:
                mod["classes"][current_class]["methods"].append(fname)
            else:
                mod["functions"][fname] = {"line": i}
        elif not line.strip() or line.startswith(("#", " ")):
            pass
        else:
            current_class = None
    return mod


def parse_doc(text: str) -> list[str]:
    """Return H1/H2 headers as document section anchors."""
    return [m.group(1).strip() for m in re.finditer(r"^#{1,2}\s+(.+)$", text, re.M)]


def load_books_graph(path: Path):
    """Load graphify export; return (nodes list, links list)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("nodes", []), data.get("links", [])


def main() -> None:
    ner = NamedEntityRecognizer(confidence_threshold=0.3)
    graph = ContextGraph(advanced_analytics=True)
    edge_stats: Counter = Counter()

    # ---------- SOURCE ----------
    py_files = list(walk(ROOT, ".py"))
    print(f"== Parsing {len(py_files)} python files (recursive, all kern-main) ==")

    modules: dict[str, dict] = {}
    for f in py_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = f.relative_to(ROOT)
        stem = str(rel).replace("\\", "/").replace("__init__", "").replace(".py", "").rstrip("/").replace("/", ".")
        if not stem:
            stem = "package_root"
        modules[stem] = {"path": str(f), "parse": parse_module(text), "text": text}

    print(f"== Adding {len(modules)} module nodes, classes, functions ==")
    for mod_name, m in modules.items():
        graph.add_node(mod_name, "Module", content=mod_name)
        for cname in m["parse"]["classes"]:
            graph.add_node(f"{mod_name}:{cname}", "Class", content=cname)
        for fname in m["parse"]["functions"]:
            graph.add_node(f"{mod_name}:{fname}", "Function", content=fname)

    def resolve_import(target: str) -> str | None:
        t = target.strip().lstrip(".")
        if t in modules:
            return t
        for mod in modules:
            if mod.endswith(t):
                return mod
        for mod in modules:
            if t.endswith(mod):
                return mod
        return None

    print("== Adding containment, inheritance, call, import edges ==")
    for mod_name, m in modules.items():
        p = m["parse"]
        for fname in p["functions"]:
            graph.add_edge(f"{mod_name}:{fname}", mod_name, edge_type="defined_in", weight=1.0)
            edge_stats["defined_in(function->module)"] += 1
        for cname in p["classes"]:
            graph.add_edge(f"{mod_name}:{cname}", mod_name, edge_type="defined_in", weight=1.0)
            edge_stats["defined_in(class->module)"] += 1
        for cname, cdata in p["classes"].items():
            for meth in cdata["methods"]:
                graph.add_edge(f"{mod_name}:{cname}", f"{mod_name}:{meth}", edge_type="has_method", weight=1.0)
                edge_stats["has_method"] += 1
        for cname, cdata in p["classes"].items():
            for base in cdata["bases"]:
                if base == cname:
                    continue
                target = None
                if base in p["classes"]:
                    target = f"{mod_name}:{base}"
                else:
                    for imp_target, syms in p["import_targets"]:
                        if syms is None or base in syms:
                            mod_hit = resolve_import(imp_target)
                            if mod_hit in modules and base in modules[mod_hit]["parse"]["classes"]:
                                target = f"{mod_hit}:{base}"
                                break
                if target:
                    graph.add_edge(f"{mod_name}:{cname}", target, edge_type="inherits_from", weight=1.0)
                    edge_stats["inherits_from"] += 1

        names_in_mod = set(p["functions"]) | set(p["classes"])
        for fname, fdata in p["functions"].items():
            lines = m["text"].splitlines()
            start = fdata["line"] - 1
            end = len(lines)
            for j in range(start, len(lines)):
                if j > start and re.match(r"^class\s+|^def\s+", lines[j]):
                    end = j
                    break
            body = "\n".join(lines[start:end])
            for called in re.findall(r"\b([a-z_][a-z0-9_]*)\s*\(", body):
                if called in names_in_mod and called != fname:
                    graph.add_edge(f"{mod_name}:{fname}", f"{mod_name}:{called}", edge_type="calls", weight=1.0)
                    edge_stats["calls"] += 1

        seen_mods: set[str] = set()
        for imp_target, _syms in p["import_targets"]:
            mod_hit = resolve_import(imp_target)
            if mod_hit and mod_hit in modules and mod_hit != mod_name and mod_hit not in seen_mods:
                seen_mods.add(mod_hit)
                graph.add_edge(mod_name, mod_hit, edge_type="imports", weight=1.0)
                edge_stats["imports"] += 1

    print(edge_stats)

    # ---------- DOCUMENTATION (ALL markdown) ----------
    md_files = list(walk(ROOT, ".md"))
    print(f"== Adding {len(md_files)} markdown docs ==")
    docs: dict[str, str] = {}
    doc_count = 0
    for f in md_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = f.relative_to(ROOT)
        stem = str(rel).replace("\\", "/").replace(".md", "")
        doc_name = f"doc:{stem}"
        docs[doc_name] = text
        graph.add_node(doc_name, "Documentation", content=str(rel))

        try:
            ents = ner.extract_entities(text)
        except Exception:
            ents = []
        concepts = set()
        for e in ents:
            label = e.text.strip()
            if 3 <= len(label) <= 40 and label[0].isupper():
                concepts.add(label)
        for c in concepts:
            cid = f"concept:{c}"
            graph.add_node(cid, "Concept", content=c)
            graph.add_edge(doc_name, cid, edge_type="mentions", weight=1.0)
            edge_stats["doc->concept"] += 1

        for h in parse_doc(text):
            hid = f"doc:{stem}:{h}"
            graph.add_node(hid, "Section", content=h)
            graph.add_edge(doc_name, hid, edge_type="has_section", weight=1.0)
            edge_stats["doc->section"] += 1

        for mod_name in modules:
            short = mod_name.split(".")[-1]
            if len(short) < 3:
                continue
            if re.search(rf"\b{short}\b", text):
                graph.add_edge(doc_name, mod_name, edge_type="documents", weight=1.0)
                edge_stats["doc->module"] += 1

        doc_count += 1

    print(f"  processed {doc_count} docs")

    # ---------- BOOKS (merge graphify export) ----------
    book_graph = Path(r"kern-main\knowledge\scm-books\graph.json")
    print(f"== Merging books graph: {book_graph} ==")
    bnodes, blinks = load_books_graph(book_graph)
    bmap = {}
    for n in bnodes:
        nid = n.get("id") or n.get("local_id")
        if not nid:
            continue
        bid = f"book:{nid}"
        bmap[nid] = bid
        btype = n.get("file_type", "concept")
        label = n.get("label") or nid
        # normalize type to avoid pollution
        if btype == "paper":
            btype = "Book"
        elif btype == "concept":
            btype = "BookConcept"
        elif btype == "document":
            btype = "BookDocument"
        else:
            btype = "BookRationale"
        graph.add_node(bid, btype, content=label)
        edge_stats["book->node"] += 1

    for blink in blinks:
        s = bmap.get(blink.get("source"))
        t = bmap.get(blink.get("target"))
        if not s or not t:
            continue
        rel = (blink.get("relation") or "related_to").replace(" ", "_")
        graph.add_edge(s, t, edge_type=rel, weight=float(blink.get("weight", 1.0)))
        edge_stats["book->book"] += 1

    # ---------- THEORY -> CODE BRIDGE ----------
    print("== Building theory->code bridge (book concepts -> modules) ==")
    mod_texts = {m: mm["text"].lower() for m, mm in modules.items()}
    # concept label -> module matches (word-boundary, min len 4)
    for n in bnodes:
        if n.get("file_type") != "concept":
            continue
        label = (n.get("label") or "").strip()
        if len(label) < 4:
            continue
        lw = label.lower()
        pattern = re.compile(r"\b" + re.escape(lw) + r"\b")
        for mod_name, mt in mod_texts.items():
            if pattern.search(mt):
                src = bmap.get(n.get("id"))
                if src:
                    graph.add_edge(src, mod_name, edge_type="theory_of", weight=1.0)
                    edge_stats["theory->code"] += 1

    print(edge_stats)

    # ---------- SAVE ----------
    print("== Saving ==")
    summary = graph.get_graph_summary()
    with open(OUT_DIR / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))

    gd = graph.to_dict()
    with open(OUT_DIR / "graph.json", "w", encoding="utf-8") as fh:
        json.dump(gd, fh, indent=2, default=str)
    print(f"  graph.json: nodes={len(gd.get('nodes', []))} edges={len(gd.get('edges', []))}")

    print("== Analytics ==")
    try:
        an = graph.analyze_connections()
        with open(OUT_DIR / "analytics.json", "w", encoding="utf-8") as fh:
            json.dump(an, fh, indent=2, default=str)
        print("  analytics.json saved")
    except Exception as exc:
        print(f"  analytics failed: {exc}")
        traceback.print_exc()

    print("== DONE ==")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
