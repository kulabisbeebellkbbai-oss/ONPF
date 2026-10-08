# ONPF 0.3.0 AWS upgrade

The user authorized installation and confirmation on 2026-10-04, after the local
merge. The deployment targets the existing onpf-pilot instance in us-east-2;
installation, traffic closure and data migration follow the existing maintenance
backup/recovery procedure. Never initialize or prepare existing storage.

## Current compatibility evidence

Live preflight found installed commit 7d97f6240fb0786c80aa7afba78af623b58a9f04,
version 0.2.0 through migration 018, rather than the older handoff commit.
Its strict question-output schema and privacy-safe validation diagnostics are
merged into the combined 0.3.0 source before deployment. All 29 deployed-fix
checks failed against the earlier candidate, demonstrating the compatibility
gap. The integrated focused gate passed 62 checks. Its HTTP tests send the
reviewed evidence token required by the browser.

A new boundary regression demonstrated that strict-schema metadata exceeded the
prior fixed request overhead and escaped a scoped size limit. The reservation
now counts the exact serialized provider request; over-limit generation makes
zero provider calls. Supporting materials, historical references, review guards,
native question metadata and scoped accounting remain in the combined source.

The prior release artifact SHA256 is
4c5c3acbda3281bd4fab79d7d9628fbcc6f3855dc40c631daeccae59e691fa81.
The earlier fd34252 artifacts remain historical local verification artifacts;
the AWS deployment uses the newly integrated candidate and its separate receipt.

## Required deployment gates

- Exact integrated source regression, package manifest/hash and dependencies.
- Live target/account/volume validation and protected configuration retention.
- Independently verified old backup version/checksum/release identity; restore
  with the prior release, upgrade the protected copy and verify current restore.
- Close external writes; final old-release backup independently checked in S3.
- Stop writers, install/configure/start without database initialization, verify
  migrations through 026 and all original row values/frozen hashes.
- Verify installed health, manual/administration/AI policy paths and public
  retirement/cache behavior on isolated fictional data. Existing real projects
  are not retired or republished, and private evidence is not sent to a provider.
- New verified backup and independent restore; monitor/services/alarms and
  actual HTTPS proxy checks. Preserve old release and matching pre-upgrade backup
  for recovery; older code must not use the upgraded database.

The final deployment receipt records exact source/artifact identities, commands,
results and limits after these gates complete. This guide's preparation alone
does not claim successful installation.

Installation completed on 2026-10-04 using integrated source fead095. See the
[AWS deployment receipt](public-source-release.md) for confirmed
installed health, retained records, independent recovery and acceptance results.
