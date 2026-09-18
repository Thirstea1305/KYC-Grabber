# Diagrams

Standalone copies of the mermaid diagrams in [`../architecture.md`](../architecture.md).

**Generated file — do not edit by hand.** Change the markdown, then regenerate:

```powershell
python scripts/export_diagrams.py            # refresh the .mmd files
python scripts/export_diagrams.py --render   # also produce SVG + PNG in rendered/
```

| File | Section in architecture.md | Type |
| --- | --- | --- |
| [`what-the-service-does.mmd`](what-the-service-does.mmd) | What the service does | flowchart |
| [`container-view.mmd`](container-view.mmd) | Container view | flowchart |
| [`happy-path-sequence.mmd`](happy-path-sequence.mmd) | Happy path (sequence) | sequence |
| [`job-state-machine.mmd`](job-state-machine.mmd) | Job state machine | state machine |
| [`data-model.mmd`](data-model.mmd) | Data model | entity relationship |
| [`always-on-operation.mmd`](always-on-operation.mmd) | Always-on operation | flowchart |

## Using these files

* GitHub, VS Code's Markdown preview and mermaid.live all render the fenced blocks in
  `architecture.md` directly — the `.mmd` files exist so a single diagram can be reused
  elsewhere (Confluence, a slide deck, a `.drawio`/`.vsdx` import, documentation tooling).
* `python scripts/export_diagrams.py --render` writes `rendered/<name>.svg` and
  `rendered/<name>.png` using [`@mermaid-js/mermaid-cli`](https://github.com/mermaid-js/mermaid-cli)
  via `npx` (Node.js required, nothing is installed into this repository).
  `rendered/` is git-ignored — the `.mmd` sources are the canonical artefacts.
