# Existing AWS instance: optional AI drafting update

Use the existing AWS release update, backup and recovery procedures from
[the contact update guide](aws-contact-update.md) and
[AWS deployment](aws-deployment.md). Keep the independently verified off-host
backup and current trusted release artifact/checksum. This guide prepares a local
0.2.0 update; it does not claim a production update or paid OpenAI acceptance.

Before installation, record the intended instance, currently installed trusted
release/checksum and an independently verified **pre-upgrade** off-host backup.
Stop conflicting app/backup writers as the existing runbook requires and retain
the exact old release for recovery. The integrated application preserves the
deployed Community Garden migrations 010 through 017 and adds migration
018_ai_drafting.sql. Its native clarification records use a separate table;
legacy clarification records and frozen publications remain intact. A standalone
AI archive built from ce56d64 must not be installed against the deployed schema.
Use the newly verified combined release and its deployment receipt;
restoration must follow [the upgrade and rollback procedure](backup-and-retention.md#upgrade-to-020-and-rollback).
The exact reviewed source SHA, unique archive names/checksums, safe extraction,
dependency checks and installed fixture recovery result must be supplied in the
final delivery receipt. They are pending independent review at this source
verification checkpoint. Do not substitute an earlier development archive.

1. Verify the intended EC2 instance in Session Manager. CloudShell is not the
   instance's systemd shell. Perform the normal reviewed-source app release
   install/configure/start workflow with the app/backup stopped only where its
   existing runbook requires it. The included `onpf.service` adds the optional
   `/etc/onpf/ai-drafting.env` EnvironmentFile after `production.env`. A disabled
   installation needs no AI file, runtime or provider account.
2. For first AI setup, follow [the private gateway setup guide](ai-drafting-setup.md).
   It provisions only a separate pinned gateway service and runtime when the
   operator explicitly runs its install command. It refuses existing gateway
   targets and AI settings. Do not put OpenAI keys in application setup JSON,
   production.env, release artifacts or CLI arguments. No Nginx/security-group
   change is required; keep 4000 private.
3. Review generated nonsecret config privately, start the gateway explicitly,
   verify its restricted identity, exact loopback listener and service state,
   then restart ONPF. Record Linux wheel/service/permission acceptance separately.
4. After explicit operator authorization, perform the guide's fictional paid
   acceptance call. Verify a complete JSON result and record only a fixed outcome,
   date and model. Local fake upstream verification is separate evidence.
5. On later ordinary app updates, preserve `/etc/onpf/ai-drafting.env`,
   `/etc/onpf/ai-gateway-key`, `/etc/onpf-ai-gateway` and `/opt/onpf-ai-gateway`.
   The app installer regenerates production.env without erasing the optional
   AI settings. The gateway launcher is copied into its persistent runtime; it
   does not point at `/opt/onpf/current` or a release that can be replaced.
   Its ASGI shim is also copied into that persistent runtime and exposes only
   the authenticated completion route; upstream UI/assets/admin/health routes
   remain unavailable. Gateway/app restarts are independent. Runtime upgrades and provider/model
   changes require a separately reviewed lock/config and fictional acceptance.

For a rollback to 0.1.0, disable AI and restore the trusted old release **with the
pre-upgrade database backup into fresh storage**, using a new instance secret
and the documented session/access-link invalidation. Never point older code at
the migrated 0.2.0 database; switching code alone is not a schema rollback.
Keep the protected gateway files private for deliberate recovery. Gateway
installation never initializes the ONPF database. Disabling AI does not remove
saved draft provenance. Record post-update HTTPS/normal forms/drafting, service
identity/listener/permission, persistent restart and independent new backup/
monitor checks separately. No production deployment, organizational approval
or provider zero-retention guarantee follows from this local update package.
