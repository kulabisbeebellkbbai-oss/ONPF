# ONPF public-page designs: round 1

Five independent, responsive design previews for selection before refinement:

1. Common Ground: warm editorial layout with serif typography and a process illustration.
2. Open / Possible: yellow poster composition, large type and geometric shapes.
3. Civic Blueprint: dark technical layout with a labelled workflow diagram.
4. Idea Mosaic: centred introduction and colourful modular cards.
5. Field Guide: minimal sidebar and a vertical workflow index.

Run from the repository root in PowerShell:

```powershell
.venv/Scripts/python.exe scripts/preview-designs.py
```

Open <http://127.0.0.1:8879/designs/index.html>. The server binds to loopback and uses a separate empty instance under `output/design-round-1/instance`. Preview links open the real license and login routes on this isolated instance. The isolated instance may contain explicitly fictional local review accounts and programs. Stop a foreground preview with Ctrl+C.

Idea Mosaic (04) was selected and has been applied to the application landing page and shared styles. The other concepts remain as design history; none of these preview routes are registered in the production application. The shared stylesheet is `base.css`; rebuild the HTML after copy changes with `.venv/Scripts/python.exe scripts/build-design-previews.py`.

The application source includes the MIT-0 license, readable `/license` page, linked footer and system overview on `/login`. The shared footer now reads `2026 - ONPF`; Christopher Kula appears in the license text, not the page footer. The Mosaic palette, cards, forms and tables apply across the application, including standalone inquiry and receipt pages. Print styles retain plain pages and questionnaire writing space. The packaged license is included in installed wheels and new exports. Existing recorded third-party and inherited material terms are preserved. Historical verification documents describe the license tested at their original revision.

License source: <https://opensource.org/license/mit-0>, with Copyright (c) 2026 Christopher Kula.
