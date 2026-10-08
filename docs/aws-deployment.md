# AWS pilot deployment and recovery

For another agency or a separate installation, use [Set up another independent ONPF AWS instance](aws-new-instance-setup.md). It provides a reusable sequence with operator-selected inputs; this document retains the pilot-specific examples and detailed recovery procedures.

This guide prepares one Ubuntu 24.04 server in **US East (Ohio), `us-east-2`**, your existing AWS region for approximately ten initial Toronto-area users and the additional single-user kitchen-design pilot. That is a sizing assumption, not a measured concurrency guarantee. The operator supplied `CloudShell region: us-east-2`; the commands and configuration below now use that code. Verify the deployment account in section 3. The application is version 0.2.0 and MIT-0 licensed. Local preparation does not establish that AWS, Ubuntu, DNS or HTTPS deployment works: complete the [acceptance record](public-source-release.md) before admitting participants.

Have these ready: an AWS account with payment and administrative access; an organizational administrator/contact email; access to the registered `onpf.example.org` domain in Route 53; a chosen budget and retention period; the reviewed source commit and independently retained release checksum. There is no default login. Do not initialize fictional demo content in production.

Follow sections 1–10 for first installation. Section 11 handles upgrades and recovery, section 12 diagnosis, and section 13 shutdown. Every command block identifies its environment. Replace uppercase placeholders or quoted `REPLACE_...` values with reviewed values; do not paste placeholders into paid operations. Use separate administrative operator credentials through your normal AWS sign-in process; never put AWS secret keys in application configuration, source, chat or user-data.

**Looking for upload instructions?** Use [CloudShell Upload Instructions — infrastructure files](#cloudshell-upload-instructions--infrastructure-files) in section 3 first. After the stack exists, use [CloudShell Upload Instructions — application files](#cloudshell-upload-instructions--application-files) in section 4. The upload button is in AWS CloudShell's **Actions** menu.

## 1. Account, region and billing

### Keep the existing project region and account experience

Account-interface guidance checked against AWS documentation on 2026-09-28. Stay in the AWS project/account and region you already use. You do not need to change regions or activate advanced features just to follow this deployment. AWS lists the core services used here, including EC2, EBS, VPC, S3, CloudFormation, CloudShell, IAM, Systems Manager, CloudWatch and SNS, in its [supported services for the simplified experience](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html). Service listing does not prove your role has every required permission or that your account has sufficient quotas.

If trying to change regions opens the advanced-features page, return to your existing project and open CloudShell there. If a required operation is denied in the existing region, record the exact service/action and have the account administrator inspect role permissions, organization policies, quotas and plan restrictions. Do not change organization policies or upgrade the account blindly. A [Paid Plan upgrade](https://docs.aws.amazon.com/accounts/latest/reference/upgrade-account.html) and [advanced-feature activation](https://docs.aws.amazon.com/accounts/latest/reference/activate-advanced-features.html) are separate choices; advanced activation is irreversible and removes project spending caps. Neither is an automatic step in this guide.

Record the deployment account ID and existing region code in your private deployment record. This profile keeps the server, EBS data and S3 backups in that region. For this Ohio deployment, the pilot's primary application storage and backups will be in the United States; regional hosting alone does not establish organizational data-handling compliance. Changing a console selector does not move existing resources. This guide covers a single commercial AWS region, not cross-region migration or China/GovCloud deployments.

### Configure the deployment account

In the **AWS console**, use the project's authorized administrative identity and protect your sign-in with MFA. Where root credentials are available, protect them with MFA and reserve them for tasks requiring root. For an organization, deploy ONPF into its project/member account. Record who may deploy, restore, approve purchases and handle participant data. Keep US East (Ohio), `us-east-2`. Choose an AWS Budget and notification recipients/thresholds suitable for your organization; preserve any existing project spend limits. Budget notifications are warnings, not a spending cap. These settings and purchases are operator decisions.

Estimate the actual architecture in the [AWS calculator](https://calculator.aws/): Linux `t3.small`, 16 GiB gp3 root, 20 GiB gp3 data, one public IPv4 address, S3 storage/requests/version retention, CloudWatch alarms/custom metrics, SNS, DNS queries and data transfer. Check current [EC2](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS](https://aws.amazon.com/ebs/pricing/), [IPv4/VPC](https://aws.amazon.com/vpc/pricing/), [S3](https://aws.amazon.com/s3/pricing/) and [CloudWatch](https://aws.amazon.com/cloudwatch/pricing/) prices. Use US East (Ohio) prices; do not copy another region's estimate or assume free-tier eligibility.

Keep domain registration/annual renewal separate from DNS hosting and server costs. [Route 53 pricing](https://aws.amazon.com/route53/pricing/) currently lists USD 0.50/month for each of the first 25 hosted zones, plus applicable queries; recheck before purchase. Domain prices depend on the suffix and renewal terms. [Let's Encrypt certificates](https://letsencrypt.org/about/) have no certificate purchase fee; operating this server and DNS still costs money. Stopping EC2 does not stop EBS, S3, IPv4, hosted-zone or domain-renewal charges.

## 2. Confirm the registered domain

**This pilot:** the operator reports that **`onpf.example.org` is registered through Route 53**. Do not register it again. This setup uses `onpf.example.org` as its canonical website hostname; the parameter and backup examples are prefilled. The intended URL is `https://onpf.example.org` after deployment and certificate issuance. Registration does not establish that its DNS record points to the future server; complete section 7 after obtaining the stack's `ElasticIp`.

For a different deployment needing a new domain, check availability, organizational ownership, registration/renewal price, auto-renewal and contact/privacy requirements before purchasing in the **registrar/AWS console**. Follow [Route 53 domain registration](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-register.html), or use your organization's registrar. Confirm the registrar's verification email promptly; incomplete verification can suspend the domain. Keep access to the contact email and recovery information. Registration and renewal continue independently of the application stack.

Use `onpf.example.org` for `PublicHost` and `public_host`, without `https://`, a path or `www`. The installer rejects URLs, ports, spaces, Unicode names and shell characters. DNS records and certificates are deliberately outside CloudFormation. In Route 53, use the correct authoritative public hosted zone for `onpf.example.org` and verify registrar name-server delegation; avoid creating a duplicate zone unintentionally. Preserve existing email and other records. Do not create an AAAA record for this IPv4-only deployment.

## 3. Inspect and create the stack

**Open CloudShell first:** sign in to your existing AWS project/deployment account, leave its region unchanged, search the console for **CloudShell**, and open it. Wait for the command prompt; Bash is the default. If another shell is active, enter `bash`. CloudShell runs in your browser and uses your console identity; no local Bash installation or access-key setup is required. See [AWS CloudShell getting started](https://docs.aws.amazon.com/cloudshell/latest/userguide/getting-started.html).

**AWS CloudShell (Bash), read-only region discovery:** CloudShell sets a region environment variable. Print it without changing it:

```bash
printf 'CloudShell region: %s\n' "${AWS_REGION:-${AWS_DEFAULT_REGION:-NOT_SET}}"
aws sts get-caller-identity
```

Your supplied result was `CloudShell region: us-east-2`, which is [US East (Ohio)](https://docs.aws.amazon.com/accounts/latest/reference/project-regions.html). This is the recorded deployment region for this pilot. All region assignments below are filled in as `REGION=us-east-2`, including commands run through EC2 Session Manager. Check that `aws sts get-caller-identity` identifies your intended deployment account. If a later CloudShell session displays a different region or `NOT_SET`, inspect the project/session selection before continuing; do not change this deployment's recorded region automatically. See [CloudShell region configuration](https://docs.aws.amazon.com/cloudshell/latest/userguide/working-with-aws-cloudshell.html).

Repeat the assignments when opening a new shell. Every AWS command below uses the recorded region explicitly; the EC2 shell does not inherit CloudShell variables. An identity check alone does not prove deployment permissions. If AMI inspection is denied, investigate the actual error as described in section 1.

First inspect [infrastructure.json](../deploy/aws/infrastructure.json) and [parameter examples](../deploy/aws/parameters.example.json) locally. Defaults include `t3.small`, 20 GiB data, 30-day backup lifecycle and no alarm email. Choose `AgencyTag`, retention and optional notification address. Ubuntu's AMI is explicitly pinned. The example `ami-00000000000000000` is unusable.

**AWS CloudShell (Bash), read-only AMI inspection:**

```bash
REGION=us-east-2
aws sts get-caller-identity
AMI=$(aws ssm get-parameter --region "$REGION" --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
aws ec2 describe-images --region "$REGION" --image-ids "$AMI" --query 'Images[].{Id:ImageId,Owner:OwnerId,Name:Name,Architecture:Architecture,State:State}'
```

Require Canonical owner `099720109477`, Ubuntu 24.04, x86_64/amd64, and an available image in this region. Copy that exact AMI ID into your private parameter file. Changing the AMI later can replace the server. Upload the files using the instructions below after checking instance availability.

**AWS CloudShell (Bash), read-only instance availability check, same session:**

```bash
aws ec2 describe-instance-type-offerings --region "$REGION" --location-type availability-zone --filters Name=instance-type,Values=t3.small --query 'InstanceTypeOfferings[].{Type:InstanceType,Zone:Location}' --output table
```

Check the selected instance type is offered in the deployment region and the subnet's availability zone. If no offerings are returned, review one of the template's permitted x86 instance types and its regional price, then update `InstanceType` in `parameters.json` and repeat this check for that type. Offerings do not guarantee capacity or account quota. Validate the template and inspect the change set before resource creation.

### CloudShell Upload Instructions — infrastructure files

Use the **latest setup package** identified by its `START-HERE.md`. Open `verified-source/deploy/aws/parameters.example.json` in a text editor, fill in the verified Ohio AMI and your chosen settings, keep `PublicHost` as `onpf.example.org`, and save your copy as **`parameters.json`** (not `parameters.json.txt`). The example AMI is not usable.

1. In CloudShell, run `cd ~` to work in its home directory.
2. Above the terminal, choose **Actions → Upload file → Browse**. If the terminal panel is cramped, use **Open in new browser tab** to view CloudShell's full interface.
3. Select a file on your computer, choose **Open**, then **Upload**, and wait for confirmation. Repeat for both files in the table. AWS documents this control under [Upload a file to AWS CloudShell](https://docs.aws.amazon.com/cloudshell/latest/userguide/getting-started.html). Use a standard CloudShell environment; file upload is unavailable in CloudShell VPC environments.

| File on your computer | Name after upload |
| --- | --- |
| `verified-source/deploy/aws/infrastructure.json` in the setup package | `infrastructure.json` |
| Your edited copy of the parameter example | `parameters.json` |

**AWS CloudShell (Bash), confirm both uploads and valid parameter JSON:**

```bash
cd ~
ls -l infrastructure.json parameters.json
python3 -m json.tool parameters.json > /dev/null
```

Both filenames must appear and the JSON command must finish without an error. If a file already exists from an earlier attempt, confirm which version you are replacing. Uploading these files only copies them into CloudShell; it does not create the server.

### Validate and create the stack

**AWS CloudShell (Bash):** validate, then create an inspectable change set. `create-change-set` creates a stack record; executing it creates paid resources. Choose a unique stack name; this example is not an upgrade command.

```bash
REGION=us-east-2
STACK=REPLACE_UNIQUE_STACK_NAME
aws cloudformation validate-template --region "$REGION" --template-body file://infrastructure.json
aws cloudformation create-change-set --region "$REGION" --stack-name "$STACK" --change-set-name initial-reviewed --change-set-type CREATE --template-body file://infrastructure.json --parameters file://parameters.json --capabilities CAPABILITY_IAM
aws cloudformation wait change-set-create-complete --region "$REGION" --stack-name "$STACK" --change-set-name initial-reviewed
aws cloudformation describe-change-set --region "$REGION" --stack-name "$STACK" --change-set-name initial-reviewed
```

Review region/account, public 80/443 only, no SSH key/ingress, role permissions, encrypted disks, private buckets, resources and estimated costs. Only after organizational approval, **AWS CloudShell (Bash), paid deployment:**

```bash
aws cloudformation execute-change-set --region "$REGION" --stack-name "$STACK" --change-set-name initial-reviewed
aws cloudformation wait stack-create-complete --region "$REGION" --stack-name "$STACK"
aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" --query 'Stacks[0].Outputs' --output json > stack-outputs.json
```

If a waiter fails, inspect stack events rather than executing another creation blindly. Securely retain outputs: `InstanceId`, `ElasticIp`, `DataVolumeId`, `DataAvailabilityZone`, `ArtifactBucketName`/`ArtifactPrefix` (`releases/`), `BackupBucketName`/`BackupPrefix` (`backups/`), `AlarmTopicArn`, `UbuntuImageId`, `PublicHost`, `Region`, `MetricNamespace` and `MetricStackName`. Store resource IDs independently of the server for recovery. Confirm any SNS email subscription sent by actual deployment. Unconfirmed recipients do not receive alerts.

## 4. Build, check and upload the release

**If using the prepared ONPF AWS delivery package:** go directly to [CloudShell Upload Instructions — application files](#cloudshell-upload-instructions--application-files) below. Its `START-HERE.md` identifies the ready-made bundle, receipt and bootstrap files. You do not need to install or run ONPF locally.

### Optional: build a new release from source

Skip these PowerShell build steps when using the prepared package. They are for someone producing a new source release.

The builder includes **committed HEAD blobs**, not working edits or untracked files. Review and commit intended changes first. It includes source, AWS tools, documents, lock, metadata and MIT-0 license; it excludes live storage and private keys. The bundle does not vendor dependency wheels, local launch scripts, tests or Git history. Ubuntu installation downloads the exact locked Python versions; platform availability remains an acceptance check.

**Local PowerShell, repository root:** use a fresh AWS-specific folder so an existing local 0.1.0 delivery remains intact.

```powershell
$awsOutput = 'output/ONPF-AWS-0.2.0-REPLACE_DELIVERY_DATE'
New-Item -ItemType Directory -Path $awsOutput -ErrorAction Stop
.venv/Scripts/python.exe deploy/aws/build_bundle.py --source . --output "$awsOutput/onpf-aws.tar.gz"
$receipt = Get-Content -LiteralPath "$awsOutput/onpf-aws.tar.gz.sha256.json" -Raw | ConvertFrom-Json
if ((Get-FileHash -LiteralPath "$awsOutput/onpf-aws.tar.gz" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $receipt.sha256) { throw 'Bundle checksum mismatch' }
git show "$($receipt.commit):deploy/aws/install.py" | Out-Null
```

**Local PowerShell, same session:** export bootstrap bytes from that same commit without PowerShell text/EOL conversion.

```powershell
@'
import json, subprocess, sys
from pathlib import Path
out = Path(sys.argv[1])
commit = json.loads((out/'onpf-aws.tar.gz.sha256.json').read_text())['commit']
bootstrap = out/'bootstrap'
bootstrap.mkdir()
for name in ('install.py', 'build_bundle.py'):
    data = subprocess.run(['git', 'show', f'{commit}:deploy/aws/{name}'], check=True, capture_output=True).stdout
    (bootstrap/name).write_bytes(data)
'@ | .venv/Scripts/python.exe - $awsOutput
Get-FileHash -LiteralPath "$awsOutput/bootstrap/install.py","$awsOutput/bootstrap/build_bundle.py" -Algorithm SHA256
```

Retain the commit, bundle HEX checksum and bootstrap HEX checksums in a trusted local release index. A checksum fetched alongside code is not independent publisher authentication.

### CloudShell Upload Instructions — application files

Complete section 3 first so the stack and its private artifact bucket exist. Use all four files from the **same latest setup package**, not a mixture of earlier deliveries.

In CloudShell, run `cd ~`, then use **Actions → Upload file → Browse → Open → Upload** for each file:

| File in the setup package | Name after upload |
| --- | --- |
| `onpf-aws.tar.gz` | `onpf-aws.tar.gz` |
| `onpf-aws.tar.gz.sha256.json` | `onpf-aws.tar.gz.sha256.json` |
| `bootstrap/install.py` | `install.py` |
| `bootstrap/build_bundle.py` | `build_bundle.py` |

**AWS CloudShell (Bash), confirm uploads and inspect checksums:**

```bash
cd ~
ls -l onpf-aws.tar.gz onpf-aws.tar.gz.sha256.json install.py build_bundle.py
sha256sum onpf-aws.tar.gz install.py build_bundle.py
```

Compare all three checksums to your trusted copy of the latest package's `START-HERE.md` or verification manifest. Continue only if they match. The `.tar.gz` stays compressed.

### Copy the uploaded application files into S3

**AWS CloudShell (Bash), operator identity with artifact PutObject:** set `COMMIT` from the receipt and `ARTIFACT_BUCKET` from stack outputs, not example bucket names. Keep all ordinary release artifacts for exact-version recovery.

```bash
REGION=us-east-2
ARTIFACT_BUCKET=REPLACE_ARTIFACT_BUCKET
COMMIT=REPLACE_FULL_RELEASE_COMMIT
aws s3 cp onpf-aws.tar.gz "s3://$ARTIFACT_BUCKET/releases/$COMMIT/onpf-aws.tar.gz" --region "$REGION" --sse AES256
aws s3 cp onpf-aws.tar.gz.sha256.json "s3://$ARTIFACT_BUCKET/releases/$COMMIT/onpf-aws.tar.gz.sha256.json" --region "$REGION" --sse AES256
aws s3 cp install.py "s3://$ARTIFACT_BUCKET/releases/$COMMIT/bootstrap/install.py" --region "$REGION" --sse AES256
aws s3 cp build_bundle.py "s3://$ARTIFACT_BUCKET/releases/$COMMIT/bootstrap/build_bundle.py" --region "$REGION" --sse AES256
```

The instance role can GetObject at known artifact keys and PutObject at backup keys. It cannot list buckets, retrieve/list backups, delete objects or retrieve backup versions. Uploading and restoring require a separate operator identity. No application credentials belong in these buckets' release keys.

## 5. Connect and verify bootstrap

Use **AWS console → Systems Manager → Session Manager** to start a session for the recorded `InstanceId`; no port 22 is opened. The canonical image must have its SSM snap. If the instance is not online, inspect EC2 status, network/role and user-data completion before proceeding.

**EC2 Session Manager (Ubuntu Bash):**

```bash
sudo cloud-init status --wait
sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service --no-pager
/usr/local/bin/aws --version
sudo systemctl is-active nginx
```

Bootstrap installs apt Python/venv, Nginx, Certbot and disk tools, plus the pinned AWS CLI **2.37.4 native x86_64 vendor distribution** at `/usr/local/bin/aws` (files under `/usr/local/aws-cli`). It disables Nginx after apt installation. Brief package-default HTTP exposure may occur during apt setup, before any ONPF data exists. Stop on bootstrap errors. The CLI is separately pinned and does **not** auto-update. The operator owns security/update review and must repeat actual-unit backup/monitor acceptance after a reviewed CLI update. A successful root/shell `aws --version` does **not** prove it works inside hardened services: section 9 is mandatory.

The bootstrap follows the [AWS native installer signature procedure](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html): an embedded publisher key with fingerprint `FB5DB77FD5C118B80511ADA8A6310ACC4672475C`, an isolated temporary keyring, and detached signature verification **before** extraction or installer execution. Import, fingerprint, download or signature failure stops bootstrap. Never bypass a failed or expired-key check; review the official key and release. Added apt dependencies are `ca-certificates`, `curl`, `unzip`, `gnupg`, `groff` and `less`. SSM continues using the Canonical image's existing agent snap. This CLI choice avoids a privileged snap launcher inside ONPF's hardened jobs.

The [AWS changelog](https://raw.githubusercontent.com/aws/aws-cli/v2/CHANGELOG.rst) and versioned [S3 PutObject](https://awscli.amazonaws.com/v2/documentation/api/2.37.4/reference/s3api/put-object.html) / [CloudWatch PutMetricData](https://awscli.amazonaws.com/v2/documentation/api/2.37.4/reference/cloudwatch/put-metric-data.html) references establish the selected version and required checksum/metric/pager arguments. Recheck release advisories before deployment. For an existing host, cloud-init does not rerun just because the template changes: during planned maintenance, stop the operational timers/jobs, review and execute the same signature-verified native installation sequence (use the vendor's `--update` only for an existing native installation), verify the recorded version, then restore timers and complete section 9. Do not run the whole bootstrap on a working server or remove the SSM snap.

**EC2 Session Manager (Ubuntu Bash):** download using role credentials to a new root-private staging directory. Repeat assignments in each new session.

```bash
REGION=us-east-2
ARTIFACT_BUCKET=REPLACE_ARTIFACT_BUCKET
COMMIT=REPLACE_FULL_RELEASE_COMMIT
sudo install -d -m 0700 /root/onpf-stage
sudo /usr/local/bin/aws s3api get-object --region "$REGION" --bucket "$ARTIFACT_BUCKET" --key "releases/$COMMIT/onpf-aws.tar.gz" /root/onpf-stage/onpf-aws.tar.gz
sudo /usr/local/bin/aws s3api get-object --region "$REGION" --bucket "$ARTIFACT_BUCKET" --key "releases/$COMMIT/bootstrap/install.py" /root/onpf-stage/install.py
sudo /usr/local/bin/aws s3api get-object --region "$REGION" --bucket "$ARTIFACT_BUCKET" --key "releases/$COMMIT/bootstrap/build_bundle.py" /root/onpf-stage/build_bundle.py
sudo sha256sum /root/onpf-stage/onpf-aws.tar.gz /root/onpf-stage/install.py /root/onpf-stage/build_bundle.py
```

Compare all three hashes to the trusted local index **before running bootstrap code**. Do not use `tar -xf` to bootstrap. The installer uses the sibling verified builder to validate all archive members and release hashes before writing a fresh release directory.

## 6. Identify new storage, install and initialize

**EC2 Session Manager (Ubuntu Bash), read-only disk inspection:**

```bash
sudo lsblk --json --paths --output PATH,TYPE,SERIAL,FSTYPE,MOUNTPOINTS
sudo nvme list
```

Identify the unique whole-disk device whose serial matches `DataVolumeId` after hyphen removal; do not assume `/dev/sdf` or an NVMe number. Confirm attachment to this instance/AZ and that it is the intended **blank** data volume. The installer refuses filesystems, signatures, mounted devices, children, wrong identity or nonempty mount destinations. `prepare-storage` is destructive and belongs only to first blank-volume setup; recovery of an existing filesystem uses section 11.

**EC2 Session Manager (Ubuntu Bash), first installation only:** replace volume/device/SHA values before use.

```bash
sudo /usr/bin/python3 /root/onpf-stage/install.py prepare-storage --volume-id REPLACE_VOLUME_ID --device /dev/REPLACE_IDENTIFIED_BLANK_DEVICE
sudo findmnt --json --target /var/lib/onpf --output TARGET,UUID,FSTYPE
sudo /usr/bin/python3 /root/onpf-stage/install.py install-release --bundle /root/onpf-stage/onpf-aws.tar.gz --sha256 REPLACE_TRUSTED_HEX_SHA256
```

Storage is ext4 on encrypted EBS, mounted at `/var/lib/onpf` with an exact private volume marker. Code is root-owned under `/opt/onpf/releases/<bundle-sha>` with `/opt/onpf/current` selecting it. State is owned by the no-login/no-home `onpf` system account. A startup refusal never initializes a replacement database on the root disk.

Create `/root/onpf-stage/backup.json` with `sudoedit` (a private, nonsecret configuration file). The prefilled region `us-east-2` must match the stack output `Region`; this config drives both S3 backups and CloudWatch monitoring. JSON does not expand shell variables, so enter the literal code, not `$REGION`. Use actual stack outputs and the exact standard paths:

```json
{
  "public_host": "onpf.example.org",
  "volume_id": "REPLACE_DATA_VOLUME_ID",
  "data_root": "/var/lib/onpf",
  "instance": "/var/lib/onpf/instance",
  "database": "/var/lib/onpf/instance/onpf.sqlite3",
  "staging": "/var/lib/onpf/backups",
  "bucket": "REPLACE_BACKUP_BUCKET",
  "prefix": "backups/",
  "region": "us-east-2",
  "stack": "REPLACE_METRIC_STACK_NAME",
  "local_keep": 3,
  "request_rate_per_second": 10,
  "request_burst": 40,
  "login_rate_per_minute": 6,
  "login_burst": 5,
  "contact_to": "",
  "smtp_host": "",
  "smtp_port": 587,
  "smtp_from": "",
  "smtp_user": "",
  "smtp_password_file": ""
}
```

**EC2 Session Manager (Ubuntu Bash), first installation only:**

```bash
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py configure --config /root/onpf-stage/backup.json
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py initialize-db --config /etc/onpf/backup.json
```

Configuration initially serves only HTTP ACME challenges; other canonical HTTP requests receive 503 and unknown hosts are rejected. `initialize-db` explicitly creates the database without users and refuses an existing database. Do not run it during an upgrade or restore. Generated `/etc/onpf/production.env` is root-only; `/etc/onpf/backup.json` is root:onpf 0640 with directory 0750. Nothing under the private mount is a public Nginx root.

Before using the Contact page, enter its SMTP settings in the private setup JSON and follow [contact form setup](contact-setup.md), including the separate password file and an actual delivery test. The recipient setting is private and can be changed per installation.

### Proxy request limits and shared public addresses

These settings in the same private JSON configuration are optional with the following defaults. They must be JSON integers within the bounds; zero, booleans, strings and out-of-range values are rejected before writing configuration.

| Setting | Default | Allowed range | Scope |
| --- | --- | --- | --- |
| `request_rate_per_second` | 10 | 1-100 | All canonical HTTPS application requests per client IP per second |
| `request_burst` | 40 | 1-500 | General excess-request burst |
| `login_rate_per_minute` | 6 | 1-60 | Additional limit for `POST /login` per client IP per minute, regardless of username |
| `login_burst` | 5 | 1-30 | Login excess-request burst |

[Nginx request limiting](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html) uses the direct TCP client address and normalized URI. Login POSTs consume both limits; login page GETs use the general limit. Bursts are admitted immediately (`nodelay`) and then drain at the configured rate; excess requests receive **429** and should be retried after waiting, without rapid resubmission. Defaults allow roughly one login plus five immediate excess attempts from one IP; the login burst drains in about 50 seconds if no further attempts arrive. Limits apply to requests, not uploaded bytes; the existing 26 MiB body ceiling and streaming/no-buffering policy remain.

A classroom, agency Wi-Fi or carrier NAT can put many legitimate people behind one public IP. Stagger first logins and measure normal group use before adjusting. For example, trial `login_burst: 12` for a group starting together, retaining `login_rate_per_minute: 6` initially; raise sustained rates only if observed legitimate use warrants it. More allowance also permits more expensive password checks. General requests and browser assets share a budget too. Record selected values, peak simultaneous users, 429 recovery and other users' response times; do not disable the limits or trust incoming forwarding headers to separate users. This profile assumes direct Nginx ingress, not an added CDN/load balancer.

To change limits, retain the previous private config, edit the root-private source JSON, then run the existing `install.py configure --config /root/onpf-stage/backup.json --https` command once a certificate exists. This validates/renders, runs `nginx -t` and reloads Nginx. **Keep `--https` for an existing HTTPS site**; omitting it installs the ACME-only site. Recheck the generated site and the native acceptance tests. Do not edit a generated site and expect a later configure step to preserve the edit.

The existing per-account login throttle still applies. These per-IP limits do not promise sustained-load resilience: password verification currently occurs inside a `BEGIN IMMEDIATE` transaction and holds SQLite's single writer lock. Many allowed expensive logins can delay unrelated writes; distributed clients are not globally capped. Existing expired `auth_sessions` and stale `login_attempts` rows do not have automatic age-based cleanup. Logout/successful login remove some records, but expiry alone does not prune all rows. Watch database size and pilot latency and plan a separately reviewed maintenance/change procedure; no automatic SQL deletion or auth/database refactor is supplied here.

## 7. DNS and HTTPS certificate

In **Route 53 → Hosted zones → onpf.example.org**, use the authoritative public zone and create or update the apex A record for `onpf.example.org` to the stack's `ElasticIp`. Preserve unrelated records; review the actual change before saving. Check authoritative delegation and public resolution from a different network before issuance. Use a short initial TTL such as 300 seconds, chosen under your DNS policy.

**Local PowerShell, external DNS:**

```powershell
Resolve-DnsName onpf.example.org -Type A
```

First verify actual account permissions and ACME directory traversal. **EC2 Session Manager (Ubuntu Bash):**

```bash
sudo stat -c '%a %U:%G %n' /etc/onpf /etc/onpf/backup.json /var/www /var/www/onpf-acme
sudo -u onpf test -r /etc/onpf/backup.json
sudo -u onpf test -x /etc/onpf
sudo /usr/sbin/nginx -T
```

Require 0750 root:onpf and 0640 root:onpf for configuration, and 0755 root:root for both web directories. Confirm the actual Nginx worker account (normally `www-data`) in `nginx -T`. Inspect locally: configuration output is not for public sharing. **EC2 Session Manager (Ubuntu Bash), using that worker account:**

```bash
sudo install -d -m 0755 /var/www/onpf-acme/.well-known/acme-challenge
printf 'onpf-acme-acceptance\n' | sudo tee /var/www/onpf-acme/.well-known/acme-challenge/onpf-check >/dev/null
sudo chmod 0644 /var/www/onpf-acme/.well-known/acme-challenge/onpf-check
sudo namei -l /var/www/onpf-acme/.well-known/acme-challenge/onpf-check
sudo -u www-data test -r /var/www/onpf-acme/.well-known/acme-challenge/onpf-check
```

**External machine (PowerShell):** require HTTP 200 and the exact nonsecret sentinel, then remove it on the server.

```powershell
curl.exe --fail http://onpf.example.org/.well-known/acme-challenge/onpf-check
```

**EC2 Session Manager (Ubuntu Bash), certificate issuance and HTTPS activation:**

```bash
sudo rm /var/www/onpf-acme/.well-known/acme-challenge/onpf-check
sudo /usr/bin/certbot certonly --webroot --webroot-path /var/www/onpf-acme --domain onpf.example.org --email REPLACE_OPERATOR_EMAIL --agree-tos
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py configure --config /etc/onpf/backup.json --https
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py start --config /etc/onpf/backup.json
sudo /usr/bin/certbot renew --dry-run
sudo systemctl status certbot.timer --no-pager
sudo /usr/sbin/nginx -t
```

Use apt's `/usr/bin/certbot` and its existing timer, not another snap/scheduler. `certonly --webroot` leaves the reviewed proxy intact. Check the installed deploy hook and its successful Nginx validation/reload on renewal; rehearse that hook independently if the installed Certbot dry run does not execute deploy hooks. Do not distribute participant links before HTTPS and the remaining checks pass.

Production responses use `Referrer-Policy: same-origin`: HTTPS forms retain the same-origin Referer required by Flask-WTF, while requests to other origins receive no referrer. Nginx suppresses the upstream policy and emits one matching header. Keep CSRF token and strict HTTPS referrer validation enabled. Verify login/logout from a freshly loaded browser page; manually supplying Referer in an HTTP test can hide a conflicting browser policy.

## 8. Create owner accounts

**EC2 Session Manager (Ubuntu Bash), interactive terminal:**

```bash
sudo /usr/sbin/runuser -u onpf -- /opt/onpf/current/.venv/bin/python -m onpf.cli --instance /var/lib/onpf/instance create-user --database /var/lib/onpf/instance/onpf.sqlite3 --username REPLACE_CHOSEN_USERNAME
```

Use the private password prompt (at least 12 characters plus confirmation). Repeat for each authorized facilitator/owner; application program memberships/owner decisions remain explicit local choices. Do not pass passwords as command arguments or create shared default credentials. Visit `https://YOUR_CANONICAL_HOST/login` externally and verify a fresh login/logout.

## 9. Backups, hardened services and alerts

**EC2 Session Manager (Ubuntu Bash):** this is acceptance in the **actual service context**, including no home, `NoNewPrivileges`, capability restrictions and filesystem protection. A shell/root CLI test is insufficient. The native CLI avoids snap launcher privileges, but native Ubuntu/systemd/IAM execution remains unverified locally. Keep the hardening in place. If either service fails, stop pilot acceptance and diagnose the installed native version, role/network access and exact unit context.

```bash
sudo systemctl cat onpf-backup.service onpf-monitor.service
sudo systemctl show onpf-backup.service onpf-monitor.service -p User -p Group -p NoNewPrivileges -p ProtectHome -p ProtectSystem -p RestrictSUIDSGID -p CapabilityBoundingSet -p ExecStart
sudo systemctl start onpf-backup.service
sudo systemctl show onpf-backup.service -p Result -p ExecMainStatus
sudo systemctl start onpf-monitor.service
sudo systemctl show onpf-monitor.service -p Result -p ExecMainStatus
sudo systemctl list-timers onpf-backup.timer onpf-monitor.timer
sudo journalctl -u onpf-backup.service -u onpf-monitor.service --since '30 minutes ago' --no-pager
sudo ls -l /var/lib/onpf/backups
```

Require both `Result=success`, `ExecMainStatus=0`, a completed local per-object receipt and `last-success.json`. Inspect these privately: they record object key, version if returned, base64 checksum, original release commit and upload time. Check S3 and CloudWatch with the separate operator identity below. A coherent local archive alone is not an off-host success. The upload is a single PUT below 5 GiB with a SHA256 request checksum and matching API response. Private JSON contains account password hashes and personal contributions, not just public documents.

Daily backup is 08:00 UTC with up to ten minutes' jitter and catch-up after downtime; monitoring is every 300 seconds. Three completed local copies are the example retention, plus at most one pending copy under normal cleanup. Remote lifecycle uses chosen `BackupRetentionDays`; expiration is asynchronous and versioning affects actual lifetime. Keep a separate removal register: old backups can reintroduce removed personal content.

After upload failure, preserve the pending archive and receipt. The next invocation retries its exact bytes/key/release metadata, then captures and uploads one fresh snapshot automatically. Only fresh verified completion advances the heartbeat. `recovery-pending.json` keeps age stale until fresh completion; never delete it to clear an alarm. Inspect disk/service/IAM/network and rerun `systemctl start onpf-backup.service` after correcting the cause.

**AWS CloudShell (Bash), operator verification:** assign exact recorded values. Backup GET/HEAD requires operator permissions, not the runtime role.

```bash
REGION=us-east-2
STACK=REPLACE_METRIC_STACK_NAME
BACKUP_BUCKET=REPLACE_BACKUP_BUCKET
KEY=REPLACE_KNOWN_RECEIPT_KEY
aws s3api head-object --region "$REGION" --bucket "$BACKUP_BUCKET" --key "$KEY" --checksum-mode ENABLED
aws cloudwatch list-metrics --region "$REGION" --namespace ONPF/Operations --dimensions "Name=StackName,Value=$STACK"
aws cloudwatch describe-alarms --region "$REGION" --alarm-name-prefix "$STACK"
```

Verify completed object checksum/metadata matches its receipt and a real fresh `BackupSuccess` datapoint appears (a metric definition alone is not a datapoint). In the CloudWatch console inspect namespace `ONPF/Operations`, sole dimension `StackName`: `ApplicationHealthy` Count=1, `DataDiskUsedPercent` Percent, `BackupAgeHours` None below 30, and fresh `BackupSuccess` Count=1. Alarms breach for app failure, data usage ≥85%, backup age >30 hours or missing monitor data; custom alarms require three consecutive 300-second periods. EC2 status uses two periods. Check actual configured alarm names in stack resources. SNS sends ALARM/OK transitions only after recipient confirmation; no automatic repair is configured.

**Coverage limit:** `ApplicationHealthy` probes the local loopback application health endpoint. It does not traverse public Nginx, TLS or DNS and can remain healthy during a public proxy outage, expired certificate or wrong DNS record. EC2 status and these local metrics do not establish end-to-end availability. Keep public HTTPS/certificate checks in operator practice; independent external synthetic monitoring is outside this profile.

Rehearse a controlled app outage and monitor outage with no participant work, wait through the configured evaluation window, verify real alarm/OK email delivery, then restore timers/app. Rehearse failed upload and stale age in an isolated recovery/pilot environment. Preserve private data and retained copies. Record observed events, not just alarm configuration.

## 10. External pilot acceptance

Complete the [verification checklist](public-source-release.md) and record operator/date/evidence. Test from outside the server/network: valid certificate and HTTP redirect; correct canonical invitation/review URLs; login/logout and Secure/HttpOnly/SameSite cookies; form CSRF; invited input and revocation; approved public export/import; realistic multipart uploads and >26 MiB request refusal. Test hostile Host/forwarded headers and absent state-directory access. Check sample bearer tokens do not occur in Nginx access/error logs, journald or application/Waitress logs. Use disposable nonpersonal test content.

In an isolated pilot with disposable accounts, send bursts above each configured rate and verify native Nginx 429 responses, no-store/same-origin referrer-policy headers and recovery after the burst drains. For the login test, cycle usernames from the same IP while using valid CSRF so per-account throttling cannot mask the proxy boundary; a second source IP should have its own allowance. Verify normal shared-IP group login/navigation after tuning, and near-limit valid multipart imports both while limits permit them and after rejection/recovery. Rejected requests must leave no partial import. Repeat bearer-sentinel log checks for rate-limit errors because Nginx can normally log the rejected URL; the supplied error log remains discarded.

Verify reboot, mount identity/permissions and app binding only at 127.0.0.1:8765. In a separate recovery environment rehearse absent/wrong mount: service must refuse without creating root-disk storage and monitor must report failure. Do not unmount a busy production disk as an acceptance shortcut. Exercise a complete restore with history/approval/hash checks, new secret/fresh logins and a new off-host backup before gathering real participant content. Measure your anticipated simultaneous work; one SQLite process and a small instance do not establish ten simultaneous-user capacity or automatic failover.

## 11. Upgrades and recovery

Use the original installation's recorded region and stack output `Region` for every recovery command, S3 bucket, AMI lookup and replacement host. Do not rediscover a different region from a newly opened console session. This procedure restores within that region; moving to another region is a separate migration.

### Upgrade during planned downtime

Keep the exact old release artifact/checksum and a fresh confirmed off-host backup. Inspect any CloudFormation UPDATE change set before execution. Do not change AMI, instance or availability-zone parameters casually: the attachment cannot safely migrate the sole live data disk automatically. Retain recorded old volume ID/AZ and plan explicit recovery if replacement is required. `DeletionPolicy`/`UpdateReplacePolicy` retention preserves resources, not an automatic working attachment.

Download/check a **new** reviewed bundle as in sections 4–5. **EC2 Session Manager (Ubuntu Bash):**

```bash
sudo systemctl start onpf-backup.service
sudo systemctl show onpf-backup.service -p Result -p ExecMainStatus
sudo systemctl stop nginx onpf-backup.timer onpf-backup.service onpf.service
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py install-release --bundle /root/onpf-stage/REPLACE_NEW_BUNDLE.tar.gz --sha256 REPLACE_NEW_TRUSTED_HEX_SHA256
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py configure --config /etc/onpf/backup.json --https
sudo /usr/bin/python3 /opt/onpf/current/deploy/aws/install.py start --config /etc/onpf/backup.json
sudo systemctl start onpf-backup.service onpf-monitor.service
```

First require verified fresh S3 completion before the stop/install commands. During startup, packaged application migrations may change the database. Code-pointer reversal alone is not a migration rollback. Keep services stopped after any refusal; inspect retained partial releases rather than overwrite a SHA directory. Do not initialize the database. Repeat acceptance and verify new off-host completion.

### Recover an intact retained volume without formatting

Stop old traffic, timers and app first; after host loss, prevent old-host access/traffic through deliberate EC2/network controls. Attach the recorded retained volume to the recovery host in the **same AZ**, ensuring it is not mounted by another host. No automatic failover is supplied. Never call `prepare-storage` or `initialize-db` on retained state.

**Recovery EC2 Session Manager (Ubuntu Bash):** identify unique whole-disk serial against retained EBS ID and inspect ext4 UUID.

```bash
sudo systemctl stop nginx onpf-backup.timer onpf-backup.service onpf.service
sudo lsblk --json --paths --output PATH,TYPE,SERIAL,FSTYPE,MOUNTPOINTS
sudo blkid /dev/REPLACE_IDENTIFIED_RETAINED_DEVICE
sudo install -d -m 0700 /root/onpf-retained-inspect
sudo mount -o ro,noload /dev/REPLACE_IDENTIFIED_RETAINED_DEVICE /root/onpf-retained-inspect
sudo cat /root/onpf-retained-inspect/.onpf-volume-id
sudo ls -l /root/onpf-retained-inspect/instance
sudo umount /root/onpf-retained-inspect
```

Require the marker to match the retained ID exactly and expected database/history to exist. Read-only inspection may require specialist filesystem recovery after an unclean crash; do not force a repair or format. Reconcile one reviewed `/etc/fstab` row with the verified UUID: `UUID=VERIFIED_UUID /var/lib/onpf ext4 defaults,nodev,nosuid,noexec 0 2`. The mount directory must be empty; preserve any unexpected state for investigation. **Recovery EC2 Session Manager (Ubuntu Bash):**

```bash
sudo systemctl daemon-reload
sudo mount /var/lib/onpf
sudo findmnt --json --target /var/lib/onpf --output TARGET,UUID,FSTYPE
```

Require exact target/UUID/ext4. Install matching supported code, which provisions the `onpf` account. Reconcile old numeric UID/GID only on this positively identified filesystem: root root:onpf 0750, marker onpf:onpf 0400, instance/backups onpf with private permissions. Preserve marker identity and update config to retained `volume_id`. Restore/issue the correct certificate; configure/start only after private inspection. This resumes existing state and secrets; use logical recovery below if fresh credential revocation is required. Verify backup and acceptance before changing traffic.

### Logical restore to new storage after data loss

Preserve original disks and failed copies; do not overwrite the original installation. Stop traffic/app/timers or isolate the old host. Provision a distinct recovery host and a **new blank encrypted data volume**, recording its new ID/AZ. Complete blank storage preparation and install the matching supported release, but **do not run `initialize-db` or `start`**. Configure using the new volume ID. The new `/var/lib/onpf/instance` must have no database or `.secret`; do not copy the old secret/cache/session state.

Choose a known backup key/version from a trusted receipt or separate authorized bucket inventory. The runtime role cannot read backups. The [AWS GetObject command](https://docs.aws.amazon.com/cli/latest/reference/s3api/get-object.html) requires separate permission for an exact object version. **AWS CloudShell (Bash), separate operator identity with backup GetObject (and GetObjectVersion if selecting a version):**

```bash
umask 077
REGION=us-east-2
BACKUP_BUCKET=REPLACE_BACKUP_BUCKET
KEY=REPLACE_KNOWN_BACKUP_KEY
mkdir -m 0700 private-recovery
aws s3api get-object --region "$REGION" --bucket "$BACKUP_BUCKET" --key "$KEY" --checksum-mode ENABLED --output json private-recovery/backup.private.json > private-recovery/object-response.json
python3 - <<'PY'
import base64, hashlib, json
from pathlib import Path
p = Path('private-recovery')
r = json.loads((p/'object-response.json').read_text())
h = hashlib.sha256()
with (p/'backup.private.json').open('rb') as f:
    for chunk in iter(lambda: f.read(1024*1024), b''):
        h.update(chunk)
digest = base64.b64encode(h.digest()).decode()
assert digest == r['ChecksumSHA256'], 'Backup checksum mismatch'
print('Verified backup checksum:', digest)
print('Required release commit:', r['Metadata']['onpf-release-commit'])
PY
```

Add `--version-id REPLACE_RECORDED_VERSION_ID` to the GET when selecting an exact version. Compare response/version/checksum/release identity to the independently retained receipt. A missing checksum/release mapping blocks restore; do not guess a version. The backup uses **base64 SHA256**, while release bundles use a separate **HEX SHA256**. Find that exact source commit in the trusted release index, retrieve its retained bundle and verify its independent bundle checksum and internal `release.json` commit through the safe installer. Restore requires the archive's exact supported migration sequence; application version `0.1.0` alone does not identify the release.

To deliver the private archive through SSM-only administration, use a new temporary key under the recovery host's **private artifact bucket**. Operator upload and deletion are separate privileges; the host can GetObject only under its artifact `releases/` prefix. This temporarily places private data in a normally public-source area of a still-private bucket: limit administrative access, never publish a URL or share this key, and remove all versions after recovery transfer.

**AWS CloudShell (Bash), authorized operator:**

```bash
RECOVERY_ARTIFACT_BUCKET=REPLACE_RECOVERY_ARTIFACT_BUCKET
TRANSFER_KEY=releases/recovery/REPLACE_UNIQUE_RECOVERY_ID/backup.private.json
aws s3api put-object --region "$REGION" --bucket "$RECOVERY_ARTIFACT_BUCKET" --key "$TRANSFER_KEY" --body private-recovery/backup.private.json --server-side-encryption AES256 --output json > private-recovery/transfer-response.json
```

Record the returned transfer `VersionId` privately and carry the already verified expected base64 checksum to the recovery administrator through the trusted operator record, not an unsigned same-location manifest. **Recovery EC2 Session Manager (Ubuntu Bash):**

```bash
REGION=us-east-2
RECOVERY_ARTIFACT_BUCKET=REPLACE_RECOVERY_ARTIFACT_BUCKET
TRANSFER_KEY=releases/recovery/REPLACE_UNIQUE_RECOVERY_ID/backup.private.json
sudo install -d -m 0700 /var/lib/onpf/recovery-staging
sudo /usr/local/bin/aws s3api get-object --region "$REGION" --bucket "$RECOVERY_ARTIFACT_BUCKET" --key "$TRANSFER_KEY" /var/lib/onpf/recovery-staging/backup.private.json
sudo /usr/bin/python3 - <<'PY'
import base64, hashlib
with open('/var/lib/onpf/recovery-staging/backup.private.json', 'rb') as f:
    actual = base64.b64encode(hashlib.file_digest(f, 'sha256').digest()).decode()
assert actual == 'REPLACE_TRUSTED_BACKUP_BASE64_SHA256', 'Transferred backup mismatch'
print('Private transfer checksum verified')
PY
sudo chown onpf:onpf /var/lib/onpf/recovery-staging /var/lib/onpf/recovery-staging/backup.private.json
sudo chmod 0700 /var/lib/onpf/recovery-staging
sudo chmod 0600 /var/lib/onpf/recovery-staging/backup.private.json
sudo test ! -e /var/lib/onpf/instance/onpf.sqlite3
sudo test ! -e /var/lib/onpf/instance/.secret
sudo /usr/sbin/runuser -u onpf -- /opt/onpf/current/.venv/bin/python -m onpf.cli restore /var/lib/onpf/recovery-staging/backup.private.json /var/lib/onpf/instance/onpf.sqlite3
```

Require both absence checks to pass before restore. Restore refuses an existing destination and validates archive/table hashes, schema, constraints, history and frozen approvals before publishing a new database. It revokes sessions/invitations/review links. Start in the new instance only after restoring/issuing certificates and configuring HTTPS; a new secret is created, password hashes remain, and users must log in freshly. Reissue invitation/review links. Inspect original/corrected responses, ownership, drafts, frozen release hashes/material notices and recorded removals against trusted records. Reapply later removals from the separate register before serving participants; an older backup may restore content removed since capture.

With traffic still blocked at DNS/network controls, run start, real service backup/monitor and recovery acceptance. Test via the canonical host using a controlled hosts-file/`curl --resolve` mapping to the recovery IP before changing public traffic. After successful checks, change the reviewed A record or deliberately reassign the original EIP, verify public HTTPS and fresh logins, and retain old resources until the operator signs off.

**AWS CloudShell (Bash), after verified transfer, separate operator deletion:** delete the exact recorded transfer object version so private data is not left in the artifact bucket's indefinitely retained versions.

```bash
aws s3api delete-object --region "$REGION" --bucket "$RECOVERY_ARTIFACT_BUCKET" --key "$TRANSFER_KEY" --version-id REPLACE_RECORDED_TRANSFER_VERSION_ID
```

If upload was retried, inventory and remove **every version of this exact transfer key**, including delete markers, with operator authority. A plain versioned `delete-object` only adds a marker. Confirm with operator `list-object-versions`, filter to the exact key and verify no versions remain. Remove CloudShell/recovery staging copies under your private-data retention policy after the recovery and new backup are accepted. This is file deletion, not a claim of secure media erasure.

## 12. Troubleshooting

| Symptom | Inspect and next step |
| --- | --- |
| Region selector asks for advanced features | Return to the existing project/region. This profile does not require a region change. |
| Regional API access denied | Check the recorded account/region, exact denied action, role and organization policies, and plan/quotas. Do not automatically activate advanced features or remove guardrails. |
| Session Manager unavailable | EC2 checks, instance profile/SSM snap, internet route and cloud-init; do not add open SSH as an automatic workaround. |
| Installation fails | Correct host/config/checksums, disk serial/UUID, storage signatures, active services and partial release; do not force formatting or overwrite a release. |
| Startup refuses storage | `findmnt`, exact marker/volume ID, private permissions and existing initialized database; never initialize a replacement to silence the refusal. |
| Certificate challenge fails | Authoritative DNS/A record, port 80, actual Nginx worker traversal, challenge sentinel and Certbot diagnostics; retain ACME-only config until issuance works. |
| Login/link/CSRF fails | Canonical HTTPS URL, clock, certificate, fixed proxy Host/scheme and Secure cookies; do not broaden host/proxy trust. |
| Backup/monitor fails | Actual service Result/exit and safe journal, `/usr/local/bin/aws` under exact unit hardening/no-home context, disk space, pending receipts, IAM/checksum response and CloudWatch access. Keep stale alarms until fresh confirmed completion. |
| Disk nearly full | Investigate private export cache/staging and retained completed receipts; preserve the only pending copy. Review gp3 expansion and filesystem resize as a separate operator operation. |
| Alarm email missing | SNS confirmation, recipients, alarm state/evaluation windows, metric namespace/dimension/unit and actual datapoints; missing data is intentionally breaching. |

Request logs are intentionally suppressed/sanitized to protect bearer tokens and participant input; there is no CloudWatch request-log collector. Diagnose with safe status and health events. Do not turn raw logging on with live participant data without an explicit reviewed privacy decision.

## 13. Deliberate teardown and retained costs

Export approved public documents and verify a final recoverable off-host private backup if retention policy allows. Stop traffic, app and timers. Inventory releases, private copies, the volume ID/AZ, bucket versions, DNS, contact subscriptions and organizational retention obligations before disposal. CloudFormation deletion normally removes the instance/root disk/network/EIP, but **retains data EBS, backup bucket and artifact bucket on deletion and replacement**. Retained resources become separately administered and continue billing. Root disk termination does not erase the retained data disk or S3 versions.

Only after operator approval, delete the stack through the console or reviewed CLI operation; inspect actual events and separately manage retained encrypted storage. Do not delete disks or all bucket versions merely to make stack deletion easy. Remove obsolete A records/delegation/hosted zones carefully, preserving mail/other services. Stop or transfer domain auto-renewal separately: stack deletion does not cancel registration. Track retained resource costs until each authorized disposal completes, including superseded versions and replacement volumes. Destroying a private bucket or disk is irreversible and is not an automated step in this guide.
