# Contributing to ONPF

Improvements are welcome and optional. The MIT No Attribution (MIT-0) license permits commercial use and proprietary adaptations. Attribution and retention of the project copyright/license notice are not required by MIT-0. Contributors offer original code/framework contributions under the same MIT-0 license; do not submit material you lack permission to share. Preserve original notices, licenses and source versions for third-party material. External dependency licenses remain their own licenses, described in THIRD_PARTY_NOTICES.md.

Start with the explicit README setup. Use fictional, clearly labeled input in tests and demonstrations. Never commit real participant information, credentials, tokens, databases, private archives, rendered scratch output, or the signing secret. Public exports need an allowlist; do not serialize private database records wholesale. Preserve original input through corrections, protect cross-program authorization, bind approvals to exact frozen candidates, and keep released material versions immutable.

For a behavioral change, write a meaningful regression, observe its failure, implement the change, then run the relevant tests. Before proposing integration, run the entire suite, dependency check and whitespace check:

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pip check
git diff --check
```

Use `.venv/bin/python` on Linux. Report actual platform/tool scope and any skipped checks. If document rendering changes, render every affected document type and a long-content example, inspect all generated pages, and distinguish ODT structural validity from visual pagination. Verify browser layout and real form behavior when changing a browser flow.

Describe the user-visible problem, resulting behavior, tests and limits in your proposed change. Update guides when behavior changes. Framework prompts and local drafts are editable; avoid inventing actual group input, agency endorsement or operational approval. A designated decision owner makes each program's final design choices; consensus is optional. Sharing a local improvement is an explicit choice, never a condition of using the framework.
