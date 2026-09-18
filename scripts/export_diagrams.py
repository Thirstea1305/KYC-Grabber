"""Export the mermaid diagrams from `docs/architecture.md` into standalone files.

`docs/architecture.md` stays the single source of truth. This script keeps
`docs/diagrams/*.mmd` (plus an index) in sync with it, and can optionally render
every diagram to SVG and PNG using mermaid-cli.

    python scripts/export_diagrams.py             # write / refresh the .mmd files
    python scripts/export_diagrams.py --check     # fail if they are out of date (CI friendly)
    python scripts/export_diagrams.py --render    # also render SVG + PNG (needs Node.js)

Rendered output goes to `docs/diagrams/rendered/`, which is git-ignored: the
`.mmd` sources are canonical and the images are generated on demand.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "architecture.md"
TARGET = ROOT / "docs" / "diagrams"
RENDERED = TARGET / "rendered"

HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$")
NUMBERING_RE = re.compile(r"^\d+(\.\d+)*[.)]?\s+")

DIAGRAM_TYPES = {
    "flowchart": "flowchart",
    "graph": "flowchart",
    "sequencediagram": "sequence",
    "statediagram": "state machine",
    "erdiagram": "entity relationship",
    "classdiagram": "class",
    "journey": "user journey",
    "gantt": "gantt",
    "pie": "pie",
    "mindmap": "mindmap",
    "timeline": "timeline",
    "quadrantchart": "quadrant",
}


@dataclass(frozen=True)
class Diagram:
    slug: str
    section: str
    kind: str
    body: str

    @property
    def filename(self) -> str:
        return f"{self.slug}.mmd"


def slugify(text: str) -> str:
    plain = NUMBERING_RE.sub("", text.strip())
    plain = re.sub(r"[^\w\s-]", "", plain.lower())
    return re.sub(r"[\s_]+", "-", plain).strip("-") or "diagram"


def detect_kind(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip().lower()
        if not stripped:
            continue
        for keyword, label in DIAGRAM_TYPES.items():
            if stripped.startswith(keyword):
                return label
        return "diagram"
    return "diagram"


def extract(markdown: str) -> list[Diagram]:
    """Return every ```mermaid block, named after the section it belongs to."""
    diagrams: list[Diagram] = []
    used: set[str] = set()
    section = "diagram"
    pending: list[str] | None = None
    in_other_fence = False

    for line in markdown.splitlines():
        stripped = line.strip()

        if pending is not None:  # collecting a mermaid block
            if stripped == "```":
                body = "\n".join(pending).rstrip()
                base = slugify(section)
                slug = base
                counter = 2
                while slug in used:
                    slug = f"{base}-{counter}"
                    counter += 1
                used.add(slug)
                diagrams.append(Diagram(slug=slug, section=section, kind=detect_kind(body), body=body))
                pending = None
            else:
                pending.append(line)
            continue

        if stripped.lower() == "```mermaid":
            pending = []
            continue

        if stripped.startswith("```"):  # some other fenced block: skip its contents
            in_other_fence = not in_other_fence
            continue

        if not in_other_fence:
            heading = HEADING_RE.match(line)
            if heading:
                section = NUMBERING_RE.sub("", heading.group("title").strip())

    if pending is not None:
        raise ValueError(f"Unclosed mermaid fence in {SOURCE}")

    return diagrams


def index_markdown(diagrams: list[Diagram]) -> str:
    rows = "\n".join(
        f"| [`{diagram.filename}`]({diagram.filename}) | {diagram.section} | {diagram.kind} |"
        for diagram in diagrams
    )
    gallery = "\n\n".join(
        f"### {diagram.section}\n\n"
        f"<sub>`{diagram.filename}` · {diagram.kind}</sub>\n\n"
        f"```mermaid\n{diagram.body}\n```"
        for diagram in diagrams
    )
    return f"""# Diagrams

Standalone copies of the mermaid diagrams in [`../architecture.md`](../architecture.md).

**Generated file — do not edit by hand.** Change the markdown, then regenerate:

```powershell
python scripts/export_diagrams.py            # refresh the .mmd files and this page
python scripts/export_diagrams.py --render   # also produce SVG + PNG in rendered/
```

## How to view them

* **Right here** — press <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>V</kbd> in VS Code (or
  <kbd>Ctrl</kbd>+<kbd>K</kbd> <kbd>V</kbd> for a side-by-side preview): every diagram below
  renders live, no extension required. GitHub renders this page the same way.
* **As images** — `python scripts/export_diagrams.py --render` writes SVG and PNG copies to
  `rendered/` (git-ignored); open those in any image viewer.
* **Editing** — paste any `.mmd` file into [mermaid.live](https://mermaid.live) for a live
  editor with export options.

## Index

| File | Section in architecture.md | Type |
| --- | --- | --- |
{rows}

## Rendered diagrams

{gallery}
"""


def sync(diagrams: list[Diagram], *, check: bool) -> list[Path]:
    """Write the .mmd files and the index. In check mode, report drift instead."""
    TARGET.mkdir(parents=True, exist_ok=True)
    stale: list[Path] = []
    expected: dict[Path, str] = {
        TARGET / diagram.filename: diagram.body + "\n" for diagram in diagrams
    }
    expected[TARGET / "README.md"] = index_markdown(diagrams)

    for path, content in expected.items():
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing == content:
            continue
        if check:
            stale.append(path)
            continue
        path.write_text(content, encoding="utf-8")
        print(f"{'created' if existing is None else 'updated'} {path.relative_to(ROOT)}")

    if not check:
        known = {path.name for path in expected}
        for orphan in sorted(TARGET.glob("*.mmd")):
            if orphan.name not in known:
                orphan.unlink()
                print(f"removed {orphan.relative_to(ROOT)} (no longer in architecture.md)")

    return stale


def _npx_command(npx: str) -> list[str]:
    # npx resolves to npx.cmd on Windows, which CreateProcess cannot run directly.
    if os.name == "nt" and npx.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", npx]
    return [npx]


def render(diagrams: list[Diagram], formats: list[str]) -> int:
    """Render each diagram with mermaid-cli. Returns a process exit code."""
    npx = shutil.which("npx")
    if npx is None:
        print("npx not found - install Node.js to render images, or use mermaid.live.", file=sys.stderr)
        return 2

    RENDERED.mkdir(parents=True, exist_ok=True)
    failures = 0
    for diagram in diagrams:
        source = TARGET / diagram.filename
        for fmt in formats:
            output = RENDERED / f"{diagram.slug}.{fmt}"
            command = _npx_command(npx) + [
                "-y", "@mermaid-js/mermaid-cli",
                "-i", str(source),
                "-o", str(output),
                "-b", "white",
                "-q",
            ]
            print(f"rendering {diagram.filename} -> rendered/{output.name}")
            try:
                subprocess.run(command, check=True, cwd=ROOT)
            except (subprocess.CalledProcessError, OSError) as exc:
                failures += 1
                print(f"  failed: {exc}", file=sys.stderr)

    if failures:
        print(f"{failures} render(s) failed.", file=sys.stderr)
        return 1
    print(f"Rendered {len(diagrams) * len(formats)} file(s) into {RENDERED.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export mermaid diagrams from docs/architecture.md")
    parser.add_argument("--check", action="store_true", help="exit non-zero if the exports are stale")
    parser.add_argument("--render", action="store_true", help="also render SVG/PNG with mermaid-cli")
    parser.add_argument("--formats", default="svg,png", help="comma separated formats for --render")
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"error: {args.source} not found", file=sys.stderr)
        return 2

    diagrams = extract(args.source.read_text(encoding="utf-8"))
    if not diagrams:
        print(f"error: no mermaid blocks found in {args.source}", file=sys.stderr)
        return 2

    stale = sync(diagrams, check=args.check)
    if args.check:
        if stale:
            print("out of date:", ", ".join(str(path.relative_to(ROOT)) for path in stale), file=sys.stderr)
            print("run: python scripts/export_diagrams.py", file=sys.stderr)
            return 1
        print(f"{len(diagrams)} diagram(s) in sync with {args.source.relative_to(ROOT)}")
        return 0

    print(f"{len(diagrams)} diagram(s) exported to {TARGET.relative_to(ROOT)}")

    if args.render:
        formats = [fmt.strip().lower() for fmt in args.formats.split(",") if fmt.strip()]
        return render(diagrams, formats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
