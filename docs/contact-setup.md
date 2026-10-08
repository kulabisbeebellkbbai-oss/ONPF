# Contact form setup

The **Contact** link is in the footer of every ONPF page and is open to visitors who have not signed in. The form accepts a message type, up to 2000 characters of text, optional name and reply address, an optional workspace or project reference, and up to three PNG, JPEG, GIF or WebP images of 2 MiB each. The application re-encodes images before emailing them, removing metadata while retaining transparency. Animated images may be reduced to one frame.

Both supported Waitress entry points keep request bodies in memory through the 26 MiB server limit, including the final socket receive before rejecting chunked input. The contact route applies its tighter 7 MiB limit and uses memory-only image streams. Invalid drafts are capped before redisplay so HTML escaping cannot cause response content to spill to disk. Self-hosters using a different server or proxy must likewise disable request and response temporary-file buffering for this route.

Messages and images are sent through SMTP with STARTTLS and are not saved in the ONPF database. Successful sends add only `message_type` and `sent_at` to `contact_sent_log`. Failed sends add no entry. The configured recipient is not shown on the site. If SMTP is not configured, the form says it is unavailable and does not claim delivery.

## Local installation

Set these environment variables **before starting ONPF**. `ONPF_CONTACT_TO` changes the private recipient; when omitted, ONPF uses its built-in current recipient. The SMTP account and sender must be allowed by your mail provider to send to the recipient.

| Variable | Purpose |
| --- | --- |
| `ONPF_CONTACT_TO` | Optional recipient override; one email address. |
| `ONPF_SMTP_HOST` | SMTP server hostname. Required for sending. |
| `ONPF_SMTP_PORT` | STARTTLS port, default `587`. |
| `ONPF_SMTP_FROM` | Sender address authorized by the SMTP provider. Required for sending. |
| `ONPF_SMTP_USER` | Optional SMTP login username. |
| `ONPF_SMTP_PASSWORD_FILE` | Path to a file containing only the SMTP password; recommended when a username is used. |
| `ONPF_SMTP_PASSWORD` | Direct password alternative for a local process environment. Avoid putting it in a script or repository. |

For a local PowerShell session, for example:

```powershell
$env:ONPF_SMTP_HOST = 'smtp.example.org'
$env:ONPF_SMTP_FROM = 'sender@example.org'
$env:ONPF_SMTP_USER = 'sender@example.org'
$env:ONPF_SMTP_PASSWORD_FILE = 'C:\private\onpf-smtp-password.txt'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-ONPF.ps1
```

Create the password file outside the repository and restrict it to the service account. Restart the service after configuration changes. A launcher that reuses an already running process cannot apply new environment settings until that process is restarted.

## AWS installation

The private setup JSON used by `deploy/aws/install.py configure` accepts optional `contact_to`, `smtp_host`, `smtp_port`, `smtp_from`, `smtp_user`, and `smtp_password_file` keys; the [example](../deploy/aws/templates/backup.example.json) includes them. Leave `contact_to` empty to use the built-in recipient, or set it to a different private address. The installer copies nonsecret contact settings into `/etc/onpf/production.env` and never writes the SMTP password there or into the setup JSON. If SMTP authentication is needed, `smtp_password_file` must be `/etc/onpf/smtp-password`.

For an already running AWS instance, follow the [contact page update guide](aws-contact-update.md) rather than first-installation steps.

For the selected Resend provider, use the [Resend SMTP setup guide](resend-setup.md) for domain verification, restricted credentials and delivery checks.

On the server, after `configure` creates `/etc/onpf`, create and edit the password file with restricted permissions:

```bash
sudo install -m 0640 -o root -g onpf /dev/null /etc/onpf/smtp-password
sudoedit /etc/onpf/smtp-password
```

Set `smtp_host`, `smtp_from`, `smtp_user`, and `smtp_password_file` in the private setup JSON, then rerun `configure --config /etc/onpf/backup.json --https` and restart the service. Create the password file before restarting. The application user must be able to read it. Keep this file out of release bundles and backups; retain its credential through your usual secret recovery process.

## Verify delivery

From the site, send a short test message of type **Technical support** with a small image. Confirm the email arrives at the configured recipient, that the image opens, and that the reply address works if supplied. On the server, only the type and UTC time should appear in the contact log:

```sql
SELECT message_type, sent_at FROM contact_sent_log ORDER BY sent_at DESC LIMIT 5;
```

Do not enable raw request-body or SMTP debug logs for this test. SMTP acceptance does not guarantee the recipient's provider will deliver to the inbox; check the mailbox as well.
