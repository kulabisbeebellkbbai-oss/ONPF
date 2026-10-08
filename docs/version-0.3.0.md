# ONPF 0.3.0 — combined Bridge Cafe update

BC001–BC011 ship together. The original requirements and acceptance checks remain
in [the walkthrough requests](public-source-release.md);
[the approved plan](public-source-release.md)
records the design decisions.

Deployment preflight also reconciles the already installed question-schema and
privacy-safe validation fixes from 7d97f62. These fixes are retained in the
deployment candidate. Scoped request limits count the complete provider request,
including strict-schema metadata, before reserving usage or contacting AI.

## Workflows

- Plain-language AI sharing disclosure, readable references, clean titles,
  compact previews, search and accessible form actions.
- Proposal evidence is prepared for decision drafting, including exact inherited
  response revisions. Deliberate exclusions remain excluded.
- Question wording, rationale, grouping, stage, related document and support
  survive the drafting handoff.
- Organizer clarifications retain author, time and context. Corrections append
  revisions; reviewed selections control disclosure.
- Initial creation preserves populated sections. New sections contain the
  approved embedded review statement through Save and exports. Verification
  rejects outstanding statements. Manual deletion does not approve a release
  or grant operating permission.
- A separate administration application has its own /admin/ entry point. The
  old /manage entry redirects there. Access removal revokes sessions while
  preserving attribution and project records.
- System, project and user AI switches must all permit drafting. Owners control
  their projects; administrators control installation and user settings.
  Changes are audited without private draft text.
- General supporting documents use headings, paragraphs, lists, tables,
  response spaces, checkboxes and validated local images. Letter, A4, card and
  poster layouts export to PDF, editable ODT, HTML and Markdown. Existing recipe,
  pantry and other legacy documents retain their identities and data.
- Reviewed reusable templates accept bounded item-specific values. Derived
  documents retain the source revision and stay private by default. Population
  and rendering make no AI request.
- Owners can retire projects after confirmation. Public listings and all ONPF
  publication URLs are withdrawn; editing and drafting stop. Private history
  remains available. Reactivation is private and requires a fresh publication.
  External downloaded copies cannot be recalled.

## Resources and accounting

Transport defaults remain 45 seconds, 96,000 evidence bytes and 4,096 output
tokens. Additional defaults are 131,072 request bytes, 65,536 output bytes,
one concurrent reservation, 60 attempts per UTC day at each applicable scope,
20 items per batch, 20 canonical PDF pages and 50 MiB of supporting-document
storage including revisions. System limits have server ceilings; project and
user limits can tighten them. Editable ODT may repaginate in an office app.

Storage is aggregated over the entire installation at system scope, the
selected project at project scope, and materials originally created by the
selected user at user scope. That user's allowance includes later revisions
by collaborators. Legacy materials without creator snapshots still count in
system and project totals. All comparisons occur in the write transaction.
Frozen public PDF exports use the supported absolute ceiling of 100 pages;
private saving and exporting enforce the current scoped allowance.

Drafting preserves signed source fingerprints from the reviewed catalog.
Changed versions require an explicit source refresh before any AI request.
Material rights and template settings remain destination metadata, separate
from AI suggestions. Internal brief, purpose and audience fields do not enter
public material projections.

Prices and allowances use micro USD; prices are per 1,000 tokens and must match
the installed model alias. Pricing is operator configuration; no provider rate
is invented. Unknown pricing with a monetary cap denies paid drafting. Each
explicit attempt reserves a conservative maximum atomically at all scopes
before contact. Reported usage reconciles it; uncertain attempts retain their
reservation. There is no automatic retry or paid fallback. Usage accounting is
the sole generation-time write; authored records, issuance, approval and
publication require separate actions. Provider diagnostics outside the app
require separate operator accounting.

## Upgrade and recovery

Source baseline: 07ca3e3. Recorded installation: 58d1c4bbab4cee1d64dc6bd124115dc739e2fe06,
version 0.2.0 through migration 018. Public health also reported 0.2.0 during
preparation. Check production source/configuration again before deployment.

Migrations 001–018 retain their bytes. Additive migrations 019–026 introduce
clarification revisions, administration/audit, usage reservations, retirement,
structured materials and history, supporting origins and retired write gates.
Frozen releases/publications are not rewritten. Exact historical 0.1.0,
Community Garden 0.2.0 and AI-enabled 0.2.0 archives remain recoverable.
Restored pending usage becomes uncertain accounted attempts.

Use the existing AWS maintenance backup and independently verified restore
procedure before installation. Keep prior source and the matching pre-upgrade
data backup. Rollback after migrations requires that matching backup; older
code must not use the upgraded database. Preserve protected environment and
gateway files. After an authorized deployment, independently verify installed
version, migrations, health, backups, alarms, proxy headers, AI policy and public
withdrawal.

## Scope

Printable surveys and inventory/session/engagement forms remain documentation.
There are no new hosted answer endpoints, completed operational records, live
inventory, operational tracking or HIFIS replacement. Approval, operating
permission, public selection and publication stay separate. The Bridge Cafe
walkthrough data is not retired or deleted.

See [verification evidence](public-source-release.md) for checks and
their limits.

## Installed AWS delivery

The existing pilot was upgraded on 2026-10-04 from live source 7d97f62 to
integrated source fead095, retaining its deployed strict question schema and
privacy-safe diagnostics. Health reports 0.3.0, migrations through 026, and
the unchanged installation identity. The integrated suite passed 987 checks
with 9 skipped. See the [deployment receipt](public-source-release.md)
for exact artifact identity, preserved data, installed acceptance and verified
pre/post-upgrade backup recovery. Earlier preparation baselines above are
historical; the installed artifact is the fead095 delivery recorded there.
