# ONPF contact page: existing AWS instance update

This guide prepares an application release for an **existing** ONPF AWS instance. It does not create a stack or deploy anything by itself. Replace `RELEASE_COMMIT`, `BUNDLE_NAME`, and `BUNDLE_SHA256` with the exact values in the delivery package's `START-HERE.md`. Confirm the live target before any upload or downtime; the recorded pilot was `onpf-pilot` at `onpf.example.org` in `us-east-2`, but that record is not a live check.

The release adds public `/contact`, a footer link, Pillow for image validation, and migration `009_contact_log.sql`. The migration creates a table with only message type and send time. Message text, reply addresses and images are emailed without being saved in the database. The page reports that delivery is unavailable until the installation's SMTP relay is configured. No recipient address appears on the public page. This bundle also includes the earlier Idea Mosaic and license changes if the live pilot still runs the recorded `e2f19ef70e5de42e779a276121f057c168b475be` release. The separately prepared Mosaic update used `bbf13e503c04660b3a3fb6b6847211de295ddf36`. Verify the actual installed commit; a different or newer release needs a reviewed compatibility check before this update.

## 1. Confirm the target and current release

In **AWS CloudShell**, select the intended AWS account and region. Run the following read-only checks and confirm the account, stack, hostname, instance, artifact bucket, backup bucket and data volume before proceeding:

```bash
export AWS_PAGER=""
aws sts get-caller-identity --query Account --output text
aws cloudformation describe-stacks --stack-name onpf-pilot --region us-east-2 --query 'Stacks[0].{Status:StackStatus,Outputs:Outputs}' --output json
```

If this is a different stack, use its actual outputs and region throughout. In **EC2 Session Manager** on the confirmed instance, inspect without changing state:

```bash
sudo systemctl is-active onpf.service nginx onpf-backup.timer onpf-monitor.timer
sudo readlink -f /opt/onpf/current
sudo python3 -c "import json; print(json.load(open('/opt/onpf/current/release.json'))['commit'])"
sudo findmnt --target /var/lib/onpf --output TARGET,SOURCE,FSTYPE
sudo cat /var/lib/onpf/.onpf-volume-id
sudo python3 -c "import json; c=json.load(open('/etc/onpf/backup.json')); print({k:c[k] for k in ('public_host','region','stack','volume_id')})"
```

Require the expected site, healthy services and mounted data volume. Keep the previous release path and checksum for recovery planning. Do not print private SMTP settings or passwords.

## 2. Upload the verified release

Extract the delivery ZIP on Windows. Use **CloudShell Actions → Upload file** for `BUNDLE_NAME` and `BUNDLE_NAME.sha256.json` only; leave the tarball compressed. Verify the archive against the checksum in the trusted local package:

```bash
cd ~
printf '%s  %s\n' 'BUNDLE_SHA256' 'BUNDLE_NAME' | sha256sum --check
cat BUNDLE_NAME.sha256.json
```

Require `OK` and the exact `RELEASE_COMMIT`. Store both objects in the confirmed artifact bucket, retaining earlier releases:

```bash
REGION=us-east-2
STACK=onpf-pilot
COMMIT=RELEASE_COMMIT
BUNDLE=BUNDLE_NAME
ARTIFACT_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue | [0]" --output text)
printf 'Target artifact bucket: %s\n' "$ARTIFACT_BUCKET"
aws s3 cp "$BUNDLE" "s3://$ARTIFACT_BUCKET/releases/$COMMIT/$BUNDLE" --region "$REGION" --sse AES256
aws s3 cp "$BUNDLE.sha256.json" "s3://$ARTIFACT_BUCKET/releases/$COMMIT/$BUNDLE.sha256.json" --region "$REGION" --sse AES256
```

Compare the bucket to step 1 before uploading. In **Session Manager**, download the archive into a new private directory and verify it again:

```bash
REGION=us-east-2
ARTIFACT_BUCKET=REPLACE_WITH_CONFIRMED_ARTIFACT_BUCKET
COMMIT=RELEASE_COMMIT
BUNDLE=BUNDLE_NAME
STAGE=/root/onpf-contact-RELEASE_SHORT
sudo mkdir -m 0700 "$STAGE"
sudo /usr/local/bin/aws s3api get-object --region "$REGION" --bucket "$ARTIFACT_BUCKET" --key "releases/$COMMIT/$BUNDLE" "$STAGE/$BUNDLE"
printf '%s  %s\n' 'BUNDLE_SHA256' "$STAGE/$BUNDLE" | sudo sha256sum --check
```

If staging already exists, inspect the earlier attempt instead of overwriting it. Do not manually extract into `/opt/onpf/current`; the installed release tool validates the bundle and each manifest entry.

## 3. Prove a fresh off-host backup

Schedule a short maintenance window and have users finish work. In **Session Manager**, trigger and inspect a fresh backup:

```bash
sudo systemctl start onpf-backup.service
sudo systemctl show onpf-backup.service -p Result -p ExecMainStatus
sudo python3 - <<'PY'
import json, time
from datetime import datetime, timezone
r = json.load(open('/var/lib/onpf/backups/last-success.json'))
print('Uploaded UTC:', datetime.fromtimestamp(r['uploaded_at'], timezone.utc).isoformat())
print('Age seconds:', round(time.time() - r['uploaded_at']))
print('Key:', r['key'])
print('Version:', r.get('version_id'))
print('SHA256 base64:', r['checksum_sha256'])
print('Release commit:', r['release_commit'])
PY
```

Require `Result=success`, `ExecMainStatus=0`, a fresh timestamp and the **currently installed old commit**. In CloudShell, independently inspect that exact S3 backup object using the backup bucket and key/version from the receipt:

```bash
aws s3api head-object --region us-east-2 --bucket REPLACE_BACKUP_BUCKET --key REPLACE_NEW_BACKUP_KEY --version-id REPLACE_VERSION_ID --checksum-mode ENABLED --query '{SHA256:ChecksumSHA256,Release:Metadata,Modified:LastModified,VersionId:VersionId}' --output json
```

Match the version ID, base64 checksum and `onpf-release-commit` metadata. Stop if the backup is missing or unverified. Keep the old release and this pre-update backup.

## 4. Install during the maintenance window

Run each command separately in Session Manager, checking its result before the next. Assign variables again if the shell changed:

```bash
sudo systemctl stop nginx onpf-backup.timer onpf-backup.service onpf.service
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py install-release --bundle /root/onpf-contact-RELEASE_SHORT/BUNDLE_NAME --sha256 BUNDLE_SHA256
sudo python3 -c "import json; c=json.load(open('/opt/onpf/current/release.json'))['commit']; print(c); assert c == 'RELEASE_COMMIT'"
```

The installer creates a new release directory and installs the locked dependencies, including Pillow. It does not migrate the database until the application starts. Do not run `prepare-storage`, `initialize-db`, `demo`, or a new-server bootstrap on this existing data volume.

### Configure mail delivery privately

If a STARTTLS SMTP relay and authorized sender are ready, edit the existing private `/etc/onpf/backup.json` in place with `sudoedit`. Add or update `smtp_host`, `smtp_port` (usually `587`), `smtp_from`, `smtp_user`, and `smtp_password_file` (`/etc/onpf/smtp-password`). Leave `contact_to` empty for the built-in current recipient or set it privately for this installation. Do not place the SMTP password in JSON, the release bundle, CloudShell, chat, or shell history. If no relay is ready, leave the optional SMTP values empty; `/contact` will remain unavailable for sending after installation.

For authenticated SMTP, create the password file only if it does not already exist, then edit and verify permissions without printing its contents:

```bash
sudo test -e /etc/onpf/smtp-password || sudo install -m 0640 -o root -g onpf /dev/null /etc/onpf/smtp-password
sudoedit /etc/onpf/smtp-password
sudo stat -c '%a %U:%G %n' /etc/onpf/smtp-password
sudo -u onpf test -r /etc/onpf/smtp-password
```

Require `640 root:onpf` and successful readability for the application user. Follow [contact setup](contact-setup.md) for the complete settings. Then regenerate the private service environment from the **existing** installation config and start the app:

```bash
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py configure --config /etc/onpf/backup.json --https
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py start --config /etc/onpf/backup.json
sudo systemctl is-active onpf.service nginx onpf-backup.timer onpf-monitor.timer
```

The `configure` command can start Nginx before the app is ready, so a brief gateway error can occur. Require all four units active. Startup applies migration `009_contact_log.sql` to the existing database. **Switching only the code pointer back to an older release is not a safe rollback after this migration.** If startup fails, preserve the old release, data and backup and diagnose before choosing a recovery path; do not overwrite or reinitialize the database.

## 5. Verify the contact feature and retained data

In a normal browser, open the confirmed HTTPS hostname. Check that Contact appears in the footer on sign-in, workspace, and license pages; `/contact` has the message types, optional reply and workspace fields, 2000-character limit, and image-only attachment input; and no recipient address is displayed. If the earlier Mosaic update was not installed, verify its theme and license link as well. Check sign-in and an existing program without submitting fictional data into real workspaces.

If SMTP is configured, send one small nonpersonal test message with an image. Confirm it actually arrives, its image opens, and a supplied reply address works. If SMTP is not configured, require the page to state that sending is unavailable and keep it that way until the relay is ready. Check only type and time in the new log, without printing message bodies:

```bash
sudo python3 - <<'PY'
import sqlite3
db = sqlite3.connect('file:/var/lib/onpf/instance/onpf.sqlite3?mode=ro', uri=True)
print('Migration:', db.execute("SELECT version FROM schema_migrations WHERE version='009_contact_log.sql'").fetchone())
print('Columns:', [row[1] for row in db.execute('PRAGMA table_info(contact_sent_log)')])
print('Recent types:', db.execute('SELECT message_type,sent_at FROM contact_sent_log ORDER BY sent_at DESC LIMIT 5').fetchall())
db.close()
PY
```

Require exactly `message_type` and `sent_at` columns. Check the database, instance directory and service logs for unexpected retained uploads or message text only with safe, nonpersonal test markers; do not print real submissions.

Finally trigger a fresh backup under the **new** release and confirm its off-host object as in step 3:

```bash
sudo systemctl start onpf-backup.service
sudo systemctl start onpf-monitor.service
sudo systemctl show onpf-backup.service onpf-monitor.service -p Id -p Result -p ExecMainStatus
sudo python3 -c "import json; r=json.load(open('/var/lib/onpf/backups/last-success.json')); print({k:r[k] for k in ('uploaded_at','key','version_id','checksum_sha256','release_commit')}); assert r['release_commit']=='RELEASE_COMMIT'"
```

Repeat the S3 checksum/version check for this new receipt and review alarms after their normal evaluation interval. This package does not change CloudFormation or Nginx. No live AWS status, backup, mail delivery or browser result is implied by local packaging.
