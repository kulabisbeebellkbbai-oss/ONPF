# ONPF 0.2.0: existing AWS installation update

This is an operator runbook for the Community Garden source update. Preparing this package does not upload, install, migrate, publish project content, or enable an AI provider. Use the exact `COMMIT`, `BUNDLE`, and `SHA256` in the delivery's `START-HERE.md`. The recorded target is `onpf-pilot`, `onpf.example.org`, `us-east-2`; confirm those values live before use.

The update contains CG001–CG016, additional program-media templates/uploads, and administrator-configured, user-triggered AI drafting. It upgrades application version 0.1.0 to 0.2.0 and applies migrations 010–017. Existing releases and approvals remain historical records. Publication is an explicit owner action. AI remains unavailable until the administrator configures a provider; the rest of the application works without it.

## 1. Confirm the target and retain recovery materials

In **AWS CloudShell**, use the intended account and region:

```bash
export AWS_PAGER=""
aws sts get-caller-identity --query Account --output text
aws cloudformation describe-stacks --stack-name onpf-pilot --region us-east-2 --query 'Stacks[0].{Status:StackStatus,Outputs:Outputs}' --output json
```

Record the account, hostname, EC2 instance, data volume, artifact bucket, and backup bucket. On that EC2 instance in **Session Manager** (not CloudShell):

```bash
sudo systemctl is-active onpf.service nginx onpf-backup.timer onpf-monitor.timer
sudo readlink -f /opt/onpf/current
sudo python3 -c "import json; print(json.load(open('/opt/onpf/current/release.json'))['commit'])"
sudo findmnt --target /var/lib/onpf --output TARGET,SOURCE,FSTYPE
sudo cat /var/lib/onpf/.onpf-volume-id
sudo python3 -c "import json; c=json.load(open('/etc/onpf/backup.json')); print({k:c[k] for k in ('public_host','region','stack','volume_id')})"
```

Require healthy services, the expected mount/volume, and the expected installation identity. Retain the exact old release directory, its supported bundle/checksum, and a private copy of the installation configuration and credentials under the existing retention policy. Do not print secrets. Compare the actual installed source/schema with the expected 0.1.0 baseline; a newer or divergent installation needs a compatibility review.

## 2. Transfer and verify the committed release

Extract the delivery ZIP locally. Upload only the tarball and its `.sha256.json` receipt through CloudShell's file upload. Set the exact release variables from `START-HERE.md`; set `ARTIFACT_BUCKET` from the confirmed stack output:

```bash
COMMIT=REPLACE_COMMIT
BUNDLE=REPLACE_BUNDLE
SHA256=REPLACE_SHA256
ARTIFACT_BUCKET=REPLACE_CONFIRMED_ARTIFACT_BUCKET
printf '%s  %s\n' "$SHA256" "$BUNDLE" | sha256sum --check
aws s3 cp "$BUNDLE" "s3://$ARTIFACT_BUCKET/releases/$COMMIT/$BUNDLE" --region us-east-2 --sse AES256
aws s3 cp "$BUNDLE.sha256.json" "s3://$ARTIFACT_BUCKET/releases/$COMMIT/$BUNDLE.sha256.json" --region us-east-2 --sse AES256
```

Stop if the checksum fails. SHA256 is only a trust check when its expected value comes from the independently retained delivery, not an untrusted replacement receipt. Retain earlier artifact objects.

In **Session Manager**, set the same four variables again and stage the verified bundle in a new directory:

```bash
COMMIT=REPLACE_COMMIT
BUNDLE=REPLACE_BUNDLE
SHA256=REPLACE_SHA256
ARTIFACT_BUCKET=REPLACE_CONFIRMED_ARTIFACT_BUCKET
STAGE=/root/onpf-community-garden-$COMMIT
sudo mkdir -m 0700 "$STAGE"
sudo /usr/local/bin/aws s3api get-object --region us-east-2 --bucket "$ARTIFACT_BUCKET" --key "releases/$COMMIT/$BUNDLE" "$STAGE/$BUNDLE"
printf '%s  %s\n' "$SHA256" "$STAGE/$BUNDLE" | sudo sha256sum --check
```

If the directory already exists, inspect the earlier attempt instead of overwriting it. Do not extract over the current release.

## 3. Rehearse on private real-data copies, then freeze traffic

**Required before the live upgrade:** the fictional-data rehearsal shipped with this package cannot detect installation-specific data problems. Independently verify a recent real backup's S3 version/checksum/release identity, then perform the following in separate private rehearsal storage. Use a separate rehearsal host or an isolated environment that cannot serve production traffic; never point these checks at the live database or reuse its instance directory.

1. Restore that backup with the exact old release and matching migration sequence, using the [logical recovery procedure](aws-deployment.md#logical-restore-to-new-storage-after-data-loss). Keep the original archive and live storage intact. Do not copy the live signing secret; logical restore revokes sessions and external links.
2. Extract this new bundle with the trusted SHA256 and `extract_bundle` helper into a new rehearsal source directory. Install its locked dependencies and application into a separate virtual environment as an unprivileged user. Check mount options first: the private data volume may be `noexec`, which prevents native dependencies from loading. Place the rehearsal virtual environment on an executable code filesystem, such as a new dedicated directory under `/opt/onpf`, while keeping restored databases and archives in protected private storage. Do not run `install-release`, `configure`, or `start` on the production host for this rehearsal: those operate on the live release pointer and units.
3. Using the new environment, initialize the app factory against **only the restored rehearsal database and a fresh rehearsal instance directory**, with AI explicitly disabled. This migrates the copy without starting a listener. Run SQLite `quick_check` and `foreign_key_check`, require migration 017, and compare existing release IDs, content hashes, snapshots and approvals to the old restored copy. Confirm account/membership and project record counts and review representative current project content privately.
4. Create a 0.2.0 logical backup of the migrated copy, restore it into another new private database, and repeat integrity and retained-release checks. Require this to pass before continuing. Document errors without exposing project text or credentials; resolve any data-specific failure before scheduling the upgrade.

An offline migration invocation, run under the rehearsal service identity with its new 0.2.0 virtual environment, has this form. Replace both paths with verified **rehearsal-only** paths and keep the instance path new:

```python
from pathlib import Path
from onpf.app import create_app
database = Path('/PRIVATE-REHEARSAL/restored.sqlite3')
instance = Path('/PRIVATE-REHEARSAL/new-instance')
assert database.is_file() and not instance.exists()
app = create_app({'DATABASE': str(database), 'INSTANCE_PATH': str(instance),
                  'AI_BASE_URL': '', 'AI_MODEL': '', 'AI_API_KEY': '', 'AI_API_KEY_FILE': ''})
assert app.test_client().get('/health').status_code == 200
```

Protect these copies like production data and retain or remove them under the installation's existing privacy policy. Do not bundle them into the public-source delivery. Record the rehearsed old/new source commits and backup identity. A rehearsal of another source revision must be repeated for this exact delivery.

### Final backup after closing external writes

Arrange a maintenance window and have users finish work. Run commands individually and inspect each result before continuing. Blocking Nginx stops external writes while the old application remains available to the backup service:

```bash
sudo systemctl stop nginx onpf-backup.timer
sudo systemctl start onpf-backup.service
sudo systemctl show onpf-backup.service -p Result -p ExecMainStatus
sudo python3 -c "import json; r=json.load(open('/var/lib/onpf/backups/last-success.json')); print({k:r[k] for k in ('uploaded_at','key','version_id','checksum_sha256','release_commit')})"
```

Require success/exit 0, a fresh receipt, and the old installed commit. In **CloudShell**, independently verify that exact object/version in the confirmed backup bucket:

```bash
aws s3api head-object --region us-east-2 --bucket REPLACE_BACKUP_BUCKET --key REPLACE_BACKUP_KEY --version-id REPLACE_VERSION_ID --checksum-mode ENABLED --query '{SHA256:ChecksumSHA256,Release:Metadata,Modified:LastModified,VersionId:VersionId}' --output json
```

Match the receipt's version, base64 SHA256, and `onpf-release-commit` metadata. Preserve that receipt outside the instance. A missing or mismatched backup blocks installation. Backup hashes use base64; release hashes use hexadecimal.

## 4. Install and configure with traffic closed

In **Session Manager**:

```bash
sudo systemctl stop onpf.service onpf-backup.service onpf-monitor.timer onpf-monitor.service
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py install-release --bundle "$STAGE/$BUNDLE" --sha256 "$SHA256"
sudo python3 -c "import json; c=json.load(open('/opt/onpf/current/release.json'))['commit']; print(c); assert c == '$COMMIT'"
```

The installer validates the checksum and manifest, installs locked dependencies into a new release, and switches the code pointer. It does not migrate until the app starts. Never run `prepare-storage`, `initialize-db`, `demo`, or new-instance bootstrap against existing production storage. A failed installation can leave a partial new release: inspect it rather than deleting or overwriting it automatically.

Preserve the existing `/etc/onpf/backup.json`, SMTP settings, instance paths, and volume identity. Optional AI fields and secure credential provisioning are documented in [AI drafting](ai-drafting.md). Leave endpoint/model/key-file empty if the shared provider is not ready. No provider credential or account is included in the delivery. Do not enter credentials into chat, setup JSON, shell arguments, or session output/logs.

Regenerate the environment and units using the new code, then close Nginx again because `configure` starts it. At this point the application is still stopped, so configuration does not admit application writes:

```bash
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py configure --config /etc/onpf/backup.json --https
sudo systemctl stop nginx
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py start --config /etc/onpf/backup.json
sudo systemctl is-active onpf.service onpf-backup.timer onpf-monitor.timer
sudo /opt/onpf/current/.venv/bin/python -m pip check
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 2 --max-time 10 -H 'Host: onpf.example.org' -H 'X-Forwarded-Proto: https' http://127.0.0.1:8765/health
```

Use the confirmed canonical host in the health request. Production startup checks the mount, initialized storage and provider configuration under the service identity, then migrates the database. Inspect safe service errors if startup fails; do not reinitialize storage.

Read-only database checks:

```bash
sudo /opt/onpf/current/.venv/bin/python - <<'PY'
import sqlite3
db = sqlite3.connect('file:/var/lib/onpf/instance/onpf.sqlite3?mode=ro', uri=True)
assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
assert not db.execute('PRAGMA foreign_key_check').fetchall()
versions = [r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]
assert '017_drafting_attempts.sql' in versions
print('Database checks passed. Migrations:', versions)
db.close()
PY
```

If AI is configured, validate file access as the service user with the service environment. This command does not contact the provider:

```bash
sudo systemd-run --wait --pipe --collect -p User=onpf -p Group=onpf -p EnvironmentFile=/etc/onpf/production.env /opt/onpf/current/.venv/bin/python -m onpf.cli ai-check
```

Only after the administrator intentionally enables the shared account, repeat with `ai-check --connect` to send one fictional test prompt (potentially chargeable). Check Linux ownership/permissions and provider retention/spending settings. Skip both diagnostics if AI is deliberately disabled. A local test provider does not establish live account acceptance.

## 5. Reopen and accept the update

Once local checks pass, start Nginx and verify canonical HTTPS:

```bash
sudo systemctl start nginx
sudo systemctl is-active onpf.service nginx onpf-backup.timer onpf-monitor.timer
```

Verify login and an existing project; retained responses, approvals, and immutable Community Garden release 1; public tour/contact navigation; public directory without unintended publications; additional-document templates and viewer restrictions. Use a separate fictional workspace for write tests, never fabricated approvals or publications in the real garden project. For AI, check the provider label, per-use checkbox, editable output, and explicit Save behavior if enabled.

Trigger a fresh 0.2.0 backup and monitor run, require success, then repeat the independent S3 version/checksum/release-metadata check using the **new** commit. Review public HTTPS security headers and CloudWatch after normal evaluation intervals. Record target, old/new commit, backup receipts, database checks, service states, browser results and any deferred provider checks. A completed local package is not evidence of these live results.

## Recovery boundary

Before new-version startup, the old database has not migrated. If installation fails before startup, preserve the failed release and use the retained old release/configuration after confirming the schema is still the old one.

After startup/migration, **do not just point old code at the migrated database**. There is no down-migration. Keep the original failed/migrated data and separately recover the final pre-update logical backup with the exact old supported release and matching migration sequence, following [logical recovery](aws-deployment.md#logical-restore-to-new-storage-after-data-loss). Restore into a new database/instance; never overwrite the original. Validate it before redirecting traffic. Logical recovery rotates the installation secret and revokes sessions/invitation/review links; reissue them as needed.

After users resume work, rolling back to the pre-update backup loses later changes unless reconciled separately. Prefer repairing forward when appropriate. Retain old code, config, backup and original data until the new release and its off-host recovery rehearsal are accepted. No automatic rollback, resource replacement, or cleanup is included in this package.
