# Open-Source Non-Profit Project Framework (ONPF)

ONPF is a locally hosted workspace for developing community programs with several perspectives. Collect inquiry and clarification rounds, preserve responses and corrections, draft proposals and documents, record owner decisions, and publish reusable project versions. Optional AI drafting works through a configured OpenAI-compatible endpoint with explicit consent for each use; no AI account is required for the rest of ONPF.

The code and original framework use the [MIT No Attribution (MIT-0) license](LICENSE). Commercial use and proprietary adaptations are permitted. Contributing improvements back is encouraged, not required. Retain applicable third-party notices. No AI account, cloud service, or Codex installation is required to run ONPF.

This public source release is version **0.3.0**. It includes local account management, inquiry and clarification rounds, response corrections, refinement and owner decisions, immutable releases, optional AI drafting, document/media support, exports/imports, and private backup/recovery. Use fictional data when evaluating it. Organizational approval and permission to operate a real program remain separate decisions.

Contributions are welcome through issues and pull requests. Start with [CONTRIBUTING](CONTRIBUTING.md), use the setup below, and read the [public source release boundaries](docs/public-source-release.md).

## First-time setup on Windows

Install Python 3.11 or later from your usual trusted distributor, including the Python launcher. Download/extract or clone the ONPF source into a writable local directory. Open PowerShell there. These explicit setup commands install dependencies once; later launch scripts never install silently.

```powershell
py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock
.venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
.venv/Scripts/python.exe -m pip check
.venv/Scripts/python.exe -m onpf.cli init
.venv/Scripts/python.exe -m onpf.cli create-user --username your-owner-name
```

The account command prompts privately for a password of at least 12 characters and confirmation. There is no default production username/password and no public registration. Repeat it to create each facilitator/owner account. The default private installation is `instance/` inside this directory. Protect that folder and its signing secret; public program ZIPs do not include it.

Launch after setup:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-ONPF.ps1
```

For one-click use, create a Windows shortcut with target `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\YOUR\ONPF\scripts\Start-ONPF.ps1"`, replacing the path with the extracted source folder. The script resolves its own folder, binds only `127.0.0.1`, verifies readiness, and opens your default browser at [local sign-in](http://127.0.0.1:8765/login). It reuses the selected running installation or reports a conflicting listener without stopping it. Optional arguments: `-InstancePath C:/private/onpf`, `-DatabasePath C:/private/recovery.sqlite3`, `-Port 8877`, `-NoBrowser`.

When using a custom database, pass that same path to administration commands after the command name: `backup --database PATH DESTINATION`, `redact-response --database PATH RESPONSE-UUID --owner USER --reason privacy_request`, and `create-user --database PATH --username USER`. Keep `--instance PATH` before the command for the matching signing secret and export cache. Without `--database`, these commands select `<instance>/onpf.sqlite3`; the server's previous choice is not remembered. Backup and removal refuse missing source storage, and successful administration reports the chosen database. See the [recovery examples](docs/backup-and-retention.md).

The background service writes `server.stdout.log`, `server.stderr.log` and `.server.pid` into the chosen instance. To stop a service you started, verify that PID's command line in Task Manager and end that process tree, or run `taskkill /PID VERIFIED-PID /T /F`. Do not stop an unrelated listener. For a foreground alternative with Ctrl+C shutdown, run `.venv/Scripts/python.exe -m onpf.cli serve`.

Readiness combines a nonsecret installation UUID with the normalized selected database path and returns only a hash. This prevents accidentally reusing another dataset; it is an identity check, not authentication. Account access still requires sign-in.

The public **Contact** page needs an SMTP server before it can send. Configure its private recipient and SMTP settings using the [contact setup guide](docs/contact-setup.md); the site does not display the recipient address. The form stores only the type and time of successful sends. Existing AWS installations use the [contact update guide](docs/aws-contact-update.md).

Optional AI drafting uses a separate private LiteLLM gateway and remains disabled
by default. Users review suggestions and explicitly submit forms to save; generating
or applying a suggestion does not issue questions, change dispositions, approve or
publish. Follow [private AI setup](docs/ai-drafting-setup.md) and the
[existing AWS AI update guide](docs/aws-ai-drafting-update.md). Provider credentials
belong only to the gateway; no AI SDK is installed in ONPF. Linux service and paid
provider acceptance remain separate operator checks.

## First-time setup on Linux

Install Python 3.11+ with its venv support through your distribution, extract/clone the source, and run from its directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python -m pip check
.venv/bin/python -m onpf.cli init
.venv/bin/python -m onpf.cli create-user --username your-owner-name
sh scripts/start-onpf.sh
```

Optional script arguments are instance directory, port, and database path, in that order. The lock was resolved/tested on Windows with CPython 3.12; a native Linux dependency and launch rehearsal remains unverified. The launcher uses the source folder's existing `.venv`, loopback readiness, and your configured default browser. Stop the verified process using `kill PID`; logs and PID are in the chosen instance.

## Optional AWS pilot

To deploy another independent installation, follow [Set up another ONPF AWS instance](docs/aws-new-instance-setup.md), with its own hostname, stack, accounts and backups.

For approximately ten initial users plus the single-user kitchen-design pilot, follow the [AWS start guide](docs/aws-deployment.md), [pilot requirements](docs/aws-pilots.md) and [AWS verification record](docs/public-source-release.md). It uses one Ubuntu 24.04 EC2 server in US East (Ohio), `us-east-2` (an example region), Nginx HTTPS, a retained encrypted data volume, private S3 backups and Session Manager administration. The guide has `us-east-2` filled in for deployment and recovery; switching regions and activating advanced features are not prerequisites. Setup examples use the reserved example hostname `onpf.example.org`; substitute a domain you control. The owner chooses the account/budget, retention and alert recipients. No cloud resource, registration or certificate is created by downloading this source. Real service, proxy, recovery and alarm acceptance remains required; the user count is not a concurrency guarantee.

The AWS bundle is a committed source subset for server installation, with a SHA256 receipt and safe installer; local launchers and development files remain in the full source repository/local delivery. Keep the supported release artifacts and their independently trusted checksums for recovery. Application version is 0.3.0; this source revision uses MIT-0 licensing.

## Separate fictional walkthrough

Do this only for training, using a new database path:

```powershell
.venv/Scripts/python.exe -m onpf.cli demo --database C:/private/onpf-training/example.sqlite3
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-ONPF.ps1 -InstancePath C:/private/onpf-training/example-instance -DatabasePath C:/private/onpf-training/example.sqlite3 -Port 8877
.venv/Scripts/python.exe -m onpf.cli --instance C:/private/onpf-training/example-instance backup --database C:/private/onpf-training/example.sqlite3 C:/private/backups/PRIVATE-training.json
```

Demo refuses an existing database or companion instance directory. It prints the `fictional-owner` username and a newly generated demo password; save that password privately. Every example program, contribution and decision is labeled FICTIONAL. The demo includes three perspectives, a correction, mixed dispositions, an art release, and an independent broader release selecting the art version. It never creates credentials in your ordinary installation. [Example input](examples/fictional-group-input.json) is editable source for review; the packaged copy is used by the installed demo command.

Open the programs, compare the response history and dispositions, inspect each release, download their public packages, and import one as an unapproved agency adaptation. An approval inside the software approves that document candidate; it does not grant permission to operate a real program.

Read the [facilitator guide](docs/facilitator-guide.md), [adoption guide](docs/adoption-guide.md), [private backup/recovery guide](docs/backup-and-retention.md), [opt-in network setup](docs/network-setup.md), [export format](docs/export-format.md), and [verification evidence and limits](docs/public-source-release.md). Developers should read [CONTRIBUTING](CONTRIBUTING.md) and [third-party notices](THIRD_PARTY_NOTICES.md).
