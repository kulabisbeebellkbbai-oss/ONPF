# Editable public ONPF packages

Only a current decision owner of the source program can download a public package. The package is built from an immutable approved release, its frozen framework labels, and its explicitly selected immutable material versions. Editing a live draft or publishing a later material version does not alter an old release export. Downloading does not publish a material version or change the release.

Review the actual authored document text before distributing it: these editable fields are designated public. Program title, purpose, local context, operating status, and selected module keys are also public. Account identities, owner lists, approval actors, intake responses, response history, review evidence, decisions and rationale, session/invitation tokens, editor change notes, and material operator metadata are excluded by explicit allowlists. Giving permission for a quotation does not automatically include it. This format has no intake-to-quotation publishing feature.

## Files and source format

The outer ZIP declares `format: "onpf-public-package"` and `schema_version: 1` in `manifest.json` and `source/program.json`. It contains:

- `documents/{overview,delivery,budget,volunteers,session-plan,feedback,adoption}.{md,odt}`: seven editable document types, frozen section ordering/labels/text, and labeled budget values.
- `templates/session-plan.{md,odt}`: a blank session worksheet with writing space.
- `tables/budget.csv`: literal budget cells, with risky spreadsheet formula prefixes neutralized by a leading apostrophe.
- `source/program.json`: editable program fields, ordered document sections, frozen document templates/guidance, material content and source terms, and release provenance. It is usable without an ONPF installation. It contains no authority or approval objects.
- `materials/<version-uuid>.json`: exact public selected material snapshots with literal text or document-bundle content, license, ownership/permission basis, notices, content-only hash, version identifiers, source version and inherited original terms.
- `LICENSE`, `NOTICES.md`, `RELEASE-NOTES.md`, and `README.md`.

Paths use fixed document keys and canonical UUIDs, never user-authored titles. Unsupported document/section keys produce an explicit error rather than silently disappearing. Blank document sections are marked as unresolved local requirements and provide writing space. The source JSON keeps them blank for editing. Cost status remains estimated or confirmed; unknown costs remain unknown. Design approval and permission to operate are distinct; the recorded operating status is visible in exported documents.

Markdown escapes authored markup; ODT uses ordinary escaped text, headings and editable tables without macros, script elements or hyperlinks. Newlines, tabs, repeated spaces and Unicode are preserved in ODT's logical text. CSV is an interchange table, not an executable formula interface. A formula apostrophe is an export-only defense; it is not inserted in the JSON source or original database values.

## License and checksum meaning

`LICENSE` contains the MIT No Attribution (MIT-0) license with Copyright (c) 2026 Christopher Kula. Commercial use, sale, sublicensing and proprietary adaptations are permitted. Contributing improvements back is encouraged, not required. Selected third-party and locally adapted materials retain their own recorded license, permission basis and notices; they are not relabeled MIT-0. Original source terms remain alongside local adaptation terms. Recorded permission is source evidence, not an independent ownership finding.

The manifest maps every file except the manifest itself to its SHA-256 checksum. `public_source_sha256` is the checksum of the actual `source/program.json` bytes. `provenance.source_snapshot_hash` identifies the original approved **private** snapshot, which includes audit data absent from this public package. It is not a checksum of the public projection. Release/program UUIDs, release number, timestamp and frozen framework version are provenance. Checksums detect corruption; they are not signatures, authentication, licensing proof, or imported approval.

Exports are written to a temporary archive in the configured directory and atomically renamed on success. Failed generation leaves the immutable release unchanged; a retry regenerates it. Browser downloads use `EXPORT_DIRECTORY`, defaulting to the instance's `exports` directory. Browser paths never choose a destination filesystem path. Keep this directory private and separate from source control.

## Import and bounded parsing

An authenticated account can create a **new** program from a package, using the same authority as ordinary program creation. The caller becomes its sole decision owner. Facilitator status in another program neither grants nor removes that creation authority. No import writes to an existing program. New program/material identifiers are generated locally. Approval rule resets to all local owners and operating status resets to pending. No people, submissions, decisions, roles, approvals, candidates or released designs are imported. Materials become editable unapproved local drafts; review and explicitly release/adopt locally before using them in a new approved design.

Validation completes before any database mutation. Import caps the uploaded file at **25 MiB** and total expanded outer-ZIP members at **100 MiB**. It rejects absolute/drive paths, parent traversal, backslashes, noncanonical paths, directories, symlinks, duplicate names, encrypted members, unsupported compression, unsupported formats, unknown fields and mismatched hashes. No archive member is extracted to the filesystem. Every declared member's actual streamed bytes are hashed. The package must contain exactly the members required by its declared source schema.

Additional parser bounds are 512 outer ZIP members, 1 MiB manifest JSON, 8 MiB editable source JSON, and JSON nesting depth 32. Duplicate JSON object keys and nonfinite numeric constants are rejected. Only the declared bounded JSON source and matching material JSON copies are parsed; embedded ODT/XML, Markdown and CSV are never parsed or executed during import. Source format v1 supports the locally installed framework version, the seven current document keys and their section keys, at most 100 material versions, and at most 100 ancestry entries per material. The material content schema retains its existing two-megabyte bound, 50 documents, 100 sections per document, and 500 budget rows per document. Exports refuse a source beyond these supported JSON/material bounds.

Normal browser forms retain their existing 2 MiB request limit. The public import endpoint allows 26 MiB multipart requests to accommodate a 25 MiB file and form overhead; the actual upload is independently capped at 25 MiB. Temporary uploaded files are removed after success and failure. A failed write rolls back the entire new program and all its provenance/material records.

## Import provenance and controlled withdrawal

`imported_packages` preserves the validated public source, package checksum, local importer and import timestamp for the newly created program. `imported_material_sources` ties each new material draft to its complete original public material snapshot. These source identifiers and terms remain references only. Original notices must remain verbatim when editing imported adaptations; original license/permission terms remain in lineage when the material is released, derived and exported again. Local added-work terms may still describe a proprietary adaptation permitted by the original source.

Public imports are not private backups. Private backup/restore must preserve and validate the two import provenance tables and their program/material ownership graph. Controlled removal must account for authored personal text copied into these public source records as well as normal documents, material versions and approved snapshots. The exporter requires an available `state == "released"` record and regenerates a package for each download; the print view uses the same availability projection. A withdrawal implementation must make affected releases unavailable and remove any stored generated archive copies in the configured export directory. Files already downloaded or distributed cannot be recalled by a local deletion. Historical source hashes must remain clearly identified as original provenance rather than rewritten approval evidence.

Routes: `POST /exports/releases/<release_id>/package`, `GET /exports/releases/<release_id>/print`, and `GET|POST /exports/import`. Export/import mutations retain CSRF protection. Authenticated print views contain only the public projection and use no-store/no-referrer headers. The release page exposes package and print actions, and the new-program page links to import.
