# Set up another independent ONPF AWS instance

Use this guide for a **new installation with a new blank data volume**, its own stack, hostname, accounts and backups. Preparation runs on Windows; AWS commands run in browser-based CloudShell or the server's Session Manager terminal. You do not need to run the application locally. For an existing instance, use [upgrades and recovery](aws-deployment.md#11-upgrades-and-recovery) instead.

The initial AWS deployment and an isolated logical recovery have succeeded. That does not complete every acceptance check or certify another installation. The recovery fixture contained **zero frozen releases**, so frozen-release preservation remains unproven. Absent/wrong mount, missing monitor, failed-upload/stale-backup alarms and load capacity also need separate evidence. Record this installation's results in the [acceptance checklist](public-source-release.md).

## 1. Choose the instance's inputs

Keep a private deployment record outside the server. Never copy another instance's resource IDs, database, secret or private backup into a new independent installation.

| Input | What to record |
| --- | --- |
| AWS account and region | Authorized account; one commercial region for server, volume and buckets. `us-east-2` worked for the first deployment; verify availability and policy for your choice. |
| Stack | A unique CloudFormation stack name, beginning with a letter and using letters, numbers and hyphens. |
| Public hostname | Your lowercase canonical DNS name, without scheme, port or path; control of its authoritative DNS. |
| Contact email | Organizational address for certificate notices and confirmed SNS alarm delivery. |
| Size and retention | Reviewed instance type, data capacity, backup-retention days and nonpersonal agency tag. Defaults are `t3.small`, 20 GiB and 30 days. |
| Release | Reviewed full source commit, bundle HEX SHA256, bootstrap checksums and unique delivery ID. |
| Operational ownership | Who administers accounts, checks alarms, restores data, approves costs and disposes of retained resources. |

Review [account, billing and domain guidance](aws-deployment.md#1-account-region-and-billing) and current regional prices before deployment. Include EC2, both EBS disks, public IPv4, S3 versions/requests, CloudWatch/SNS, DNS, transfer and domain renewal. Budgets notify; they are **not hard spending caps**. This deployment is not cost-free. Preserve account guardrails; changing region or activating advanced account features is not a routine prerequisite.

## 2. Prepare one uniquely named delivery on Windows

Use a reviewed committed release that includes the same-origin Referrer-Policy fix (first present in `e2f19ef70e5de42e779a276121f057c168b475be`) and later reviewed fixes. The builder packages **committed HEAD**, not working edits. If using an already prepared delivery, retain its trusted checksums and adapt the filenames below; do not mix bootstrap files from different releases.

**Local PowerShell, repository root:** use the existing Python environment, without starting ONPF. If `.venv/Scripts/python.exe` is absent, install Python 3.11+ and Git, obtain a Git checkout, and create the environment with `py -3 -m venv .venv`; see [Windows prerequisites](../README.md#first-time-setup-on-windows). Only Python's standard library and Git are needed to build this archive; skip the README's local database/account/launch steps. The timestamp plus random suffix gives every attempt separate filenames and an S3 prefix.

```powershell
$releaseId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + ([guid]::NewGuid().ToString('N').Substring(0,8))
$delivery = "output/ONPF-AWS-$releaseId"
New-Item -ItemType Directory -Path $delivery -ErrorAction Stop | Out-Null
.venv/Scripts/python.exe deploy/aws/build_bundle.py --source . --output "$delivery/onpf-$releaseId.tar.gz"
if ($LASTEXITCODE -ne 0) { throw 'Bundle build failed' }
$receipt = Get-Content -LiteralPath "$delivery/onpf-$releaseId.tar.gz.sha256.json" -Raw | ConvertFrom-Json
if ((Get-FileHash "$delivery/onpf-$releaseId.tar.gz" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $receipt.sha256) { throw 'Bundle checksum mismatch' }
@'
import json, subprocess, sys
from pathlib import Path
out, ident = Path(sys.argv[1]), sys.argv[2]
commit = json.loads((out/f'onpf-{ident}.tar.gz.sha256.json').read_text())['commit']
for source, target in (
    ('install.py', f'install-{ident}.py'),
    ('build_bundle.py', f'build_bundle-{ident}.py'),
    ('infrastructure.json', f'infrastructure-{ident}.json'),
    ('parameters.example.json', f'parameters-{ident}.json'),
):
    data = subprocess.run(['git', 'show', f'{commit}:deploy/aws/{source}'], check=True, capture_output=True).stdout
    (out/target).write_bytes(data)
'@ | .venv/Scripts/python.exe - $delivery $releaseId
if ($LASTEXITCODE -ne 0) { throw 'Release file export failed' }
Get-FileHash "$delivery/onpf-$releaseId.tar.gz","$delivery/install-$releaseId.py","$delivery/build_bundle-$releaseId.py","$delivery/infrastructure-$releaseId.json" -Algorithm SHA256
Write-Output "Delivery ID: $releaseId; source commit: $($receipt.commit)"
```

Retain these checksums independently in the private release record. Hashes uploaded beside code do not independently authenticate the publisher. Edit `parameters-DELIVERY_ID.json` only after resolving the AMI below. Do not reuse an earlier delivery filename or overwrite its artifacts when retrying.

## 3. Open CloudShell, pin Ubuntu and upload

Sign into the intended AWS account, select its intended region and open **CloudShell** from console search. Wait for the Bash prompt; enter `bash` if necessary. CloudShell uses your console identity, without copying access keys. Use a standard CloudShell environment; its upload control is unavailable in CloudShell VPC environments. A new shell does not retain these assignments.

**AWS CloudShell (Bash), read-only:** fill every `REPLACE_...` input before proceeding. Compare the printed identity and session region to your private record.

```bash
REGION='REPLACE_REGION'
STACK='REPLACE_UNIQUE_STACK_NAME'
DELIVERY='REPLACE_DELIVERY_ID'
printf 'Session region: %s\n' "${AWS_REGION:-${AWS_DEFAULT_REGION:-NOT_SET}}"
aws sts get-caller-identity
AMI=$(aws ssm get-parameter --region "$REGION" --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
aws ec2 describe-images --region "$REGION" --image-ids "$AMI" --query 'Images[].{Id:ImageId,Owner:OwnerId,Name:Name,Architecture:Architecture,State:State}'
aws ec2 describe-instance-type-offerings --region "$REGION" --location-type availability-zone --filters Name=instance-type,Values=t3.small --query 'InstanceTypeOfferings[].{Type:InstanceType,Zone:Location}' --output table
```

Require Canonical owner **`099720109477`**, Ubuntu Server 24.04, x86_64/amd64 and state `available`. Pin the exact returned ID in `UbuntuImageId`; do not use the example ID. Check availability for your selected permitted instance type if different. Offerings do not guarantee capacity or quota. In the local parameter file set `PublicHost`, `AlarmEmail`, `AgencyTag`, `InstanceType`, `DataVolumeSizeGiB` and `BackupRetentionDays` for this new installation. Preserve valid JSON strings and save as `.json`, not `.json.txt`.

In CloudShell run `cd ~`. Choose **Actions → Upload file → Browse → Open → Upload**, wait for completion and repeat for these six uniquely named files from this delivery:

- `infrastructure-DELIVERY_ID.json` and your edited `parameters-DELIVERY_ID.json`.
- `onpf-DELIVERY_ID.tar.gz` and `onpf-DELIVERY_ID.tar.gz.sha256.json`.
- `install-DELIVERY_ID.py` and `build_bundle-DELIVERY_ID.py`.

Use **Open in new browser tab** if the CloudShell panel is cramped. Upload copies files only; it does not create resources. Keep the archive compressed.

**AWS CloudShell (Bash), same session:**

```bash
cd ~
ls -l "infrastructure-$DELIVERY.json" "parameters-$DELIVERY.json" "onpf-$DELIVERY.tar.gz" "onpf-$DELIVERY.tar.gz.sha256.json" "install-$DELIVERY.py" "build_bundle-$DELIVERY.py"
python3 -m json.tool "parameters-$DELIVERY.json" > /dev/null
sha256sum "infrastructure-$DELIVERY.json" "onpf-$DELIVERY.tar.gz" "install-$DELIVERY.py" "build_bundle-$DELIVERY.py"
```

Compare all four hashes with the trusted Windows record. Stop on a missing file, invalid JSON or mismatch.

## 4. Review and execute the new stack

Read the exported [template](../deploy/aws/infrastructure.json) and parameter values. **AWS CloudShell (Bash):** create a reviewable change set, then inspect it. A failed command is a stop point; do not paste the next block after a failure.

```bash
aws cloudformation validate-template --region "$REGION" --template-body "file://infrastructure-$DELIVERY.json"
aws cloudformation create-change-set --region "$REGION" --stack-name "$STACK" --change-set-name "initial-$DELIVERY" --change-set-type CREATE --template-body "file://infrastructure-$DELIVERY.json" --parameters "file://parameters-$DELIVERY.json" --capabilities CAPABILITY_IAM
aws cloudformation wait change-set-create-complete --region "$REGION" --stack-name "$STACK" --change-set-name "initial-$DELIVERY"
aws cloudformation describe-change-set --region "$REGION" --stack-name "$STACK" --change-set-name "initial-$DELIVERY"
```

Review account/region, hostname/AMI, costs, only 80/443 ingress, no SSH, scoped IAM, encrypted disks and private buckets. **The next block creates paid resources**; run it after your organization's deployment review.

```bash
aws cloudformation execute-change-set --region "$REGION" --stack-name "$STACK" --change-set-name "initial-$DELIVERY"
aws cloudformation wait stack-create-complete --region "$REGION" --stack-name "$STACK"
umask 077
aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" --query 'Stacks[0].Outputs' --output json > "stack-outputs-$DELIVERY.json"
cat "stack-outputs-$DELIVERY.json"
```

If creation fails, inspect CloudFormation events before retrying. Securely retain **all outputs** independently of EC2, particularly region, instance ID, volume ID/AZ, Elastic IP, bucket names/prefixes, pinned AMI, hostname, metric stack name and SNS topic ARN. Confirm the SNS subscription email now. `CREATE_COMPLETE` does not prove cloud-init or ONPF installation succeeded.

**AWS CloudShell (Bash), upload verified release:** copy the exact commit from the receipt and bucket from this stack's outputs. A unique delivery suffix prevents a second build of the same commit overwriting an earlier key. Keep this full prefix in the release record.

```bash
ARTIFACT_BUCKET='REPLACE_ARTIFACT_BUCKET_OUTPUT'
COMMIT='REPLACE_FULL_RECEIPT_COMMIT'
RELEASE_PREFIX="releases/$COMMIT/$DELIVERY"
aws s3 cp "onpf-$DELIVERY.tar.gz" "s3://$ARTIFACT_BUCKET/$RELEASE_PREFIX/onpf-aws.tar.gz" --region "$REGION" --sse AES256
aws s3 cp "onpf-$DELIVERY.tar.gz.sha256.json" "s3://$ARTIFACT_BUCKET/$RELEASE_PREFIX/onpf-aws.tar.gz.sha256.json" --region "$REGION" --sse AES256
aws s3 cp "install-$DELIVERY.py" "s3://$ARTIFACT_BUCKET/$RELEASE_PREFIX/bootstrap/install.py" --region "$REGION" --sse AES256
aws s3 cp "build_bundle-$DELIVERY.py" "s3://$ARTIFACT_BUCKET/$RELEASE_PREFIX/bootstrap/build_bundle.py" --region "$REGION" --sse AES256
```

## 5. Connect through Session Manager and verify the release

In **EC2 → Instances**, select the output `InstanceId`, then **Connect → Session Manager → Connect**. Enter `bash` if needed. This is the Ubuntu server terminal, separate from CloudShell. If unavailable, inspect EC2/SSM/bootstrap status; do not add public SSH automatically.

**EC2 Session Manager (Ubuntu Bash):**

```bash
sudo cloud-init status --wait
sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service --no-pager
/usr/local/bin/aws --version
sudo systemctl is-active nginx
```

Require completed cloud-init without errors, active SSM and the native AWS CLI version pinned in your reviewed template (currently 2.37.4). Nginx should be inactive at this stage; `is-active` returning nonzero for `inactive` is expected. See [bootstrap details](aws-deployment.md#5-connect-and-verify-bootstrap) if diagnosis is needed.

**EC2 Session Manager (Ubuntu Bash):** re-enter the recorded values; the server does not inherit CloudShell variables. Use a new private staging path per delivery.

```bash
REGION='REPLACE_REGION'
ARTIFACT_BUCKET='REPLACE_ARTIFACT_BUCKET_OUTPUT'
COMMIT='REPLACE_FULL_RECEIPT_COMMIT'
DELIVERY='REPLACE_DELIVERY_ID'
RELEASE_PREFIX="releases/$COMMIT/$DELIVERY"
STAGE="/root/onpf-stage-$DELIVERY"
sudo mkdir -m 0700 "$STAGE"
sudo /usr/local/bin/aws s3api get-object --region "$REGION" --bucket "$ARTIFACT_BUCKET" --key "$RELEASE_PREFIX/onpf-aws.tar.gz" "$STAGE/onpf-aws.tar.gz"
sudo /usr/local/bin/aws s3api get-object --region "$REGION" --bucket "$ARTIFACT_BUCKET" --key "$RELEASE_PREFIX/bootstrap/install.py" "$STAGE/install.py"
sudo /usr/local/bin/aws s3api get-object --region "$REGION" --bucket "$ARTIFACT_BUCKET" --key "$RELEASE_PREFIX/bootstrap/build_bundle.py" "$STAGE/build_bundle.py"
sudo sha256sum "$STAGE/onpf-aws.tar.gz" "$STAGE/install.py" "$STAGE/build_bundle.py"
```

If the stage already exists, inspect the earlier attempt rather than overwrite it. Compare all three hashes to the independently retained release record **before executing either Python file**. The sibling filenames `install.py` and `build_bundle.py` are deliberate. The safe installer validates archive members and the internal release manifest; do not manually extract the bundle to bootstrap.

## 6. Identify the blank data disk, install and initialize

**Read-only inspection, EC2 Session Manager (Ubuntu Bash):**

```bash
sudo lsblk --json --paths --output PATH,TYPE,SERIAL,FSTYPE,MOUNTPOINTS
sudo nvme list
```

Match the **unique whole-disk serial** to this stack's `DataVolumeId`, removing its hyphen for the serial comparison. Confirm the attachment to this instance and recorded AZ in EC2. Do not guess `/dev/sdf` or an NVMe number, and never select the root disk. Require the intended data disk to be blank: no filesystem, signature, child partition or mount. The guarded installer checks these conditions. If identity or blankness is uncertain, stop; existing state uses recovery, not formatting.

**First installation only, EC2 Session Manager (Ubuntu Bash):** `prepare-storage` formats that positively identified blank disk.

```bash
VOLUME_ID='REPLACE_DATA_VOLUME_ID_OUTPUT'
DEVICE='/dev/REPLACE_IDENTIFIED_BLANK_DEVICE'
BUNDLE_SHA256='REPLACE_TRUSTED_BUNDLE_HEX_SHA256'
sudo /usr/bin/python3 "$STAGE/install.py" prepare-storage --volume-id "$VOLUME_ID" --device "$DEVICE"
sudo findmnt --json --target /var/lib/onpf --output TARGET,UUID,FSTYPE
sudo cat /var/lib/onpf/.onpf-volume-id
sudo /usr/bin/python3 "$STAGE/install.py" install-release --bundle "$STAGE/onpf-aws.tar.gz" --sha256 "$BUNDLE_SHA256"
```

Require exact mount target `/var/lib/onpf`, ext4 and marker matching the volume ID; retain its filesystem UUID. Create `"$STAGE/backup.json"` with `sudoedit`, using [the config template](../deploy/aws/templates/backup.example.json). Set literal values for `public_host`, `volume_id`, `bucket`, `region` and `stack` from **this** stack (`stack` is output `MetricStackName`). JSON does not expand shell variables. Keep the standard `/var/lib/onpf` paths and `backups/` prefix. Review `local_keep` and request limits; the example hostname and IDs must be replaced.

The Contact page uses the private SMTP settings in the same setup JSON. Complete [contact form setup](contact-setup.md) before inviting users to send messages; until SMTP is configured, the page reports that sending is unavailable.

**EC2 Session Manager (Ubuntu Bash), first initialization only:**

```bash
sudo test ! -e "$STAGE/backup.json" && sudo install -m 0600 /opt/onpf/current/deploy/aws/templates/backup.example.json "$STAGE/backup.json"
sudoedit "$STAGE/backup.json"
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py configure --config "$STAGE/backup.json"
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py initialize-db --config /etc/onpf/backup.json
sudo stat -c '%a %U:%G %n' /var/lib/onpf /var/lib/onpf/.onpf-volume-id /etc/onpf /etc/onpf/backup.json /var/www /var/www/onpf-acme
sudo -u onpf test -x /etc/onpf
sudo -u onpf test -r /etc/onpf/backup.json
```

Expected: data root and `/etc/onpf` root:onpf 0750; marker onpf:onpf 0400; config root:onpf 0640; both web directories root:root 0755. Initialization creates an empty database without users and refuses an existing one. Never rerun it to resolve a startup or recovery error. Initial Nginx configuration serves ACME only, returning 503 for other canonical requests.

## 7. Point DNS, prove ACME access and activate HTTPS

In your authoritative public DNS zone, set the chosen hostname's **A record** to this stack's `ElasticIp`. Verify registrar delegation, preserve email/other records and avoid duplicate zones. Do not add AAAA for this IPv4-only profile. A short initial TTL such as 300 seconds helps planned changes.

**Local PowerShell, external check:**

```powershell
$publicHost = 'REPLACE_CANONICAL_HOST'
Resolve-DnsName $publicHost -Type A
```

Require the intended public IP. **EC2 Session Manager (Ubuntu Bash):** inspect `nginx -T` locally and confirm the actual worker account, normally `www-data`. Use that account below.

```bash
sudo /usr/sbin/nginx -T
sudo install -d -m 0755 /var/www/onpf-acme/.well-known
sudo install -d -m 0755 /var/www/onpf-acme/.well-known/acme-challenge
printf 'onpf-acme-acceptance\n' | sudo tee /var/www/onpf-acme/.well-known/acme-challenge/onpf-check >/dev/null
sudo chmod 0644 /var/www/onpf-acme/.well-known/acme-challenge/onpf-check
sudo namei -l /var/www/onpf-acme/.well-known/acme-challenge/onpf-check
sudo -u www-data test -r /var/www/onpf-acme/.well-known/acme-challenge/onpf-check
```

Every public webroot ancestor must be traversable by the Nginx worker. Do not make private ONPF directories public to fix ACME. **Local PowerShell:** require HTTP 200 and exact sentinel text, ideally also from another network.

```powershell
curl.exe --fail "http://$publicHost/.well-known/acme-challenge/onpf-check"
```

**EC2 Session Manager (Ubuntu Bash), after sentinel success:** enter this installation's hostname/email. `--agree-tos` accepts the certificate issuer's terms; review them first.

```bash
PUBLIC_HOST='REPLACE_CANONICAL_HOST'
CONTACT_EMAIL='REPLACE_ORGANIZATIONAL_EMAIL'
sudo rm /var/www/onpf-acme/.well-known/acme-challenge/onpf-check
sudo /usr/bin/certbot certonly --webroot --webroot-path /var/www/onpf-acme --domain "$PUBLIC_HOST" --email "$CONTACT_EMAIL" --agree-tos
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py configure --config /etc/onpf/backup.json --https
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py start --config /etc/onpf/backup.json
sudo /usr/bin/certbot renew --dry-run --run-deploy-hooks
sudo systemctl status certbot.timer --no-pager
sudo /usr/sbin/nginx -t
```

Stop on certificate failure; HTTPS configure requires the certificate first. Keep `--https` on subsequent configuration updates. Use the apt Certbot timer already installed. Require successful simulated renewal and deploy-hook validation/reload; Nginx's successful syntax-test messages can appear on stderr and do not by themselves indicate failure. Verify external HTTP redirects to the canonical HTTPS origin and the browser trusts the certificate. The public proxy emits one `Referrer-Policy: same-origin` header so Flask-WTF's HTTPS CSRF checks work; do not disable CSRF/referrer checks to make login succeed.

## 8. Create owner accounts and prove backup/notification delivery

**EC2 Session Manager, interactive terminal:** choose a separate authorized username for each facilitator/owner.

```bash
sudo /usr/sbin/runuser -u onpf -- /opt/onpf/current/.venv/bin/python -m onpf.cli --instance /var/lib/onpf/instance create-user --database /var/lib/onpf/instance/onpf.sqlite3 --username REPLACE_CHOSEN_USERNAME
```

Enter and confirm a strong password of at least 12 characters at the private prompt. Never put it in arguments, screenshots, chat or the deployment record. There is no default login. In a fresh browser page, verify login and logout at the canonical HTTPS hostname before sharing participant links.

**EC2 Session Manager (Ubuntu Bash):** exercise the actual hardened units, not just root's AWS CLI.

```bash
sudo systemctl start onpf-backup.service
sudo systemctl show onpf-backup.service -p Result -p ExecMainStatus
sudo systemctl start onpf-monitor.service
sudo systemctl show onpf-monitor.service -p Result -p ExecMainStatus
sudo systemctl list-timers onpf-backup.timer onpf-monitor.timer
sudo cat /var/lib/onpf/backups/last-success.json
```

Require both units to report `Result=success`, `ExecMainStatus=0`, a completed per-object receipt and fresh `last-success.json`. Retain the receipt privately: it contains object key, version, **base64** checksum, release commit and upload time. Do not display the private backup archive: it contains password hashes and contributions. Release-bundle hashes are **HEX**, a different representation.

**AWS CloudShell (Bash), operator identity:** copy exact values from the receipt and this stack. If S3 returned a version ID, verify that version with `--version-id`, as below; omit the option only if the receipt genuinely has no version.

```bash
REGION='REPLACE_REGION'
STACK='REPLACE_METRIC_STACK_NAME_OUTPUT'
BACKUP_BUCKET='REPLACE_BACKUP_BUCKET_OUTPUT'
KEY='REPLACE_RECEIPT_KEY'
VERSION='REPLACE_RECEIPT_VERSION_ID'
TOPIC_ARN='REPLACE_ALARM_TOPIC_ARN_OUTPUT'
aws s3api head-object --region "$REGION" --bucket "$BACKUP_BUCKET" --key "$KEY" --version-id "$VERSION" --checksum-mode ENABLED
aws sns list-subscriptions-by-topic --region "$REGION" --topic-arn "$TOPIC_ARN"
aws cloudformation describe-stack-resources --region "$REGION" --stack-name "$STACK" --query "StackResources[?ResourceType=='AWS::CloudWatch::Alarm'].{Logical:LogicalResourceId,Physical:PhysicalResourceId}"
```

Compare checksum, version and `onpf-release-commit` metadata to the receipt. The runtime role intentionally cannot read historical backups; use the operator identity for this check. Require a confirmed SNS subscription, not `PendingConfirmation`. Save the physical alarm names from stack resources; CloudFormation-generated names need not start with the stack name.

In **CloudWatch → Metrics → ONPF/Operations**, select the sole dimension `StackName` matching the output. Inspect actual fresh datapoints: `ApplicationHealthy` Count=1, `DataDiskUsedPercent` Percent, `BackupAgeHours` None below 30 and `BackupSuccess` Count=1. A listed metric name alone is insufficient. Check all stack alarms and real ALARM/OK email delivery in the next step. Daily backup runs at 08:00 UTC with up to ten minutes' jitter; monitoring runs every five minutes. See [backup behavior and retries](aws-deployment.md#9-backups-hardened-services-and-alerts) for retention and pending-upload handling.

## 9. Reboot, then perform a controlled 25-minute alarm test

Do this before participant use, after a fresh verified backup. **EC2 Session Manager (Ubuntu Bash):**

```bash
sudo reboot
```

The connection will close. Wait for EC2 status checks and reconnect through Session Manager. **In the new Ubuntu Bash session:**

```bash
sudo findmnt --json --target /var/lib/onpf --output TARGET,UUID,FSTYPE
sudo cat /var/lib/onpf/.onpf-volume-id
sudo systemctl is-active onpf.service nginx
sudo systemctl list-timers onpf-backup.timer onpf-monitor.timer certbot.timer
sudo ss -ltnp
sudo systemctl start onpf-backup.service onpf-monitor.service
sudo systemctl show onpf-backup.service onpf-monitor.service -p Result -p ExecMainStatus
```

Require the same recorded mount UUID/volume marker, active application/proxy, backend bound only to `127.0.0.1:8765`, successful services and external browser login. Recheck fresh S3 completion as above. Wait for normal fresh metrics and the application alarm to become OK before starting the outage test.

The next test deliberately stops the app for **25 minutes**. Keep Nginx and the monitor running. CloudWatch needs three 300-second breaching periods; aggregation/delivery can add delay. Restarting immediately can prevent an ALARM and makes the test inconclusive. The temporary timer is an operator test safeguard, not infrastructure auto-recovery.

**EC2 Session Manager (Ubuntu Bash), arm restoration first:**

```bash
TEST_UNIT="onpf-alarm-test-restart-$(date -u +%Y%m%d%H%M%S)"
sudo systemd-run --unit="$TEST_UNIT" --on-active=25m --timer-property=AccuracySec=1s /usr/bin/systemctl start onpf.service
sudo systemctl list-timers "$TEST_UNIT.timer" --no-pager
sudo systemctl status "$TEST_UNIT.timer" --no-pager
```

**Require an active waiting timer and a next-run time about 25 minutes ahead before continuing.** Keep its name and UTC start time in the record. If arming fails, including an already-existing unit name, do not stop the app. Do not repeat a successfully completed alarm rehearsal merely because you reopen this guide.

**EC2 Session Manager (Ubuntu Bash), begin the planned outage:**

```bash
date -u
sudo systemctl stop onpf.service
sudo systemctl start onpf-monitor.service
sudo systemctl is-active onpf.service
sudo systemctl is-active onpf-monitor.timer
```

The application should be `inactive` (nonzero exit is expected), while the monitor timer remains active. An intentional `systemctl stop` does not trigger the app's `Restart=on-failure` policy.

**WAIT now. Do not run a start/restart command during the planned 25-minute window.** In CloudWatch, observe zero `ApplicationHealthy` samples, the actual application alarm's transition to ALARM and the confirmed recipient's email. Record timestamps. A brief observation every five minutes is sufficient; leave the restoration timer armed even after the ALARM email arrives.

After the timer's recorded due time, **EC2 Session Manager (Ubuntu Bash), read-only check:**

```bash
date -u
sudo systemctl is-active onpf.service
sudo systemctl status "$TEST_UNIT.service" --no-pager
sudo systemctl list-timers onpf-monitor.timer --no-pager
```

If you reconnected, assign `TEST_UNIT` to its recorded exact name first. A completed transient unit may already be unloaded; application status, journal and external HTTPS are the recovery evidence. Verify browser login, then wait for fresh healthy samples, the alarm's OK transition and the OK email. Record what actually arrived; no email or transition means the corresponding check remains incomplete.

**Emergency recovery only, or if automatic restoration failed:** this block intentionally ends the test early. Do not paste it during the normal waiting phase. An early restart requires a later full-duration retest.

```bash
sudo systemctl start onpf.service
sudo systemctl start onpf-monitor.service
sudo systemctl is-active onpf.service
```

The armed timer can safely issue another `start` when due. Investigate failed restoration without weakening mount guards. This test covers local application-health alarm delivery, not missing-monitor, stale-backup, failed-upload, public DNS/TLS availability or realistic load. Run failure-injection checks on a separate disposable recovery environment, not by unmounting a busy production volume.

## 10. Finish acceptance and keep recovery possible

Complete the [live acceptance checklist](public-source-release.md), including browser CSRF, secure cookies, invitation/review links and revocation, approved exports/imports, upload limits, native rate limits/shared-IP use and bearer-token log privacy. Record expected simultaneous workflows and measured latency; a small single-server SQLite installation has no proven concurrent-user capacity or automatic failover. Local `ApplicationHealthy` does not test public Nginx, DNS or TLS; keep an external HTTPS check in operational practice.

Before admitting participants, rehearse [logical recovery into new storage](aws-deployment.md#logical-restore-to-new-storage-after-data-loss), retaining the old installation. Use the backup's exact release commit, trusted artifact checksum and this guide's recorded full S3 release prefix (which includes the delivery ID). A version label such as `0.1.0` is insufficient. Verify history/corrections and nonempty frozen-release/hash fixtures, fresh secret/login, revoked links, removals and a fresh off-host backup. The earlier zero-frozen-release fixture does not prove that preservation case. Never run `initialize-db` before restore, and never format a retained disk.

Keep the private deployment record, release artifacts/checksums, backup receipt/version mapping, chosen retention/removal register and completed/remaining acceptance results outside EC2. Preserve pending failed backup copies; do not clear stale flags merely to silence an alarm. Follow [troubleshooting](aws-deployment.md#12-troubleshooting) for refusals and [deliberate teardown](aws-deployment.md#13-deliberate-teardown-and-retained-costs) when retiring a test or installation.

Deleting the stack normally removes EC2/root disk/network/Elastic IP but **retains the data volume and both versioned buckets**. Inventory and review those resources, temporary recovery-transfer versions, private local/CloudShell copies, DNS and domain renewal separately. Retained storage continues billing. Do not delete the only recoverable data, bulk-empty buckets or assume stopping EC2 ends all charges.
