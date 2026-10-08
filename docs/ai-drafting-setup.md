# Optional private AI drafting

AI drafting is disabled by default. ONPF works without an AI account. When enabled,
authenticated users can request a suggestion and explicitly apply it to the visible
form. Generating or applying a suggestion does not issue questions, save a program
record, change a disposition, prepare a candidate, approve a release or publish.
Review the form and use its ordinary submit action to save. Prompts and unsaved or
rejected suggestions are not retained in ONPF generation history or logs.

The supported initial gateway is **LiteLLM 1.103.2**, separate from the ONPF Python
runtime, on Ubuntu 24.04 x86_64 with CPython 3.12. The initial model is
`openai/gpt-4o-mini-2024-07-18`; the helper also accepts the GPT-4o
`2024-08-06` snapshot, which requires separate account/model acceptance. The alias
is always `onpf-drafting`. Reasoning models requiring `max_completion_tokens`
need a reviewed transport change; changing the model name alone is insufficient.
The initial snapshot uses `max_tokens` and JSON response mode. ONPF independently
validates the returned JSON and requires a complete, untruncated completion.

## Private boundaries

The gateway binds only **127.0.0.1:4000**. ONPF sends bearer-authenticated requests
to `/v1/chat/completions`. There is no gateway Nginx route or security-group port.
The persistent `gateway_asgi.py` shim exposes only POST to that exact path and
passes lifespan events to the pinned proxy unchanged. Every other HTTP route
returns a fixed 404, including the upstream admin UI, its `/ui`, `/_next` and
`/litellm-asset-prefix/_next` assets, admin APIs, OpenAPI/docs and health routes.
WebSockets are denied. LiteLLM's ordinary completion authentication still applies.
The provider connection uses outbound HTTPS; package setup also needs HTTPS to
PyPI. Do not add inbound port 4000. Remote ONPF gateway URLs require HTTPS;
unencrypted HTTP is accepted only for literal loopback addresses.

The gateway has a separate `onpf-ai-gateway` system account, no login shell,
read-only runtime, private temporary space, no ONPF data-directory access, no
database, no response cache, no fallback model and no configured external
callbacks. It runs one fixed Uvicorn worker. An occupied port fails startup; it
does not choose another port or terminate the owner of the existing listener.
`WORKER_CONFIG` explicitly disables proxy telemetry and parameter dropping.
It sets `request_timeout: 30` explicitly because direct startup's default of
600 seconds otherwise overrides a model entry's 30-second timeout. ONPF keeps
its independent 45-second total network deadline.
The fresh process disables SDK telemetry, payload logging, spend/error database
logs and access logs. It uses the bundled cost map without a background pricing
fetch. These settings do not reliably redact every provider error traceback;
both stdout and stderr are discarded by the launcher and by systemd. Cores are
disabled. Use generic service/HTTP diagnostics below, never raw provider output.

LiteLLM treats the gateway credential as its master administrator internally.
The ASGI shim hides its administrative HTTP routes. Keep this master credential
private even on the local host. ONPF receives
only `/etc/onpf/ai-gateway-key`, **owned by onpf, mode 0400** (group/other bits
must be zero), readable through the existing root:onpf 0750 `/etc/onpf` directory.
Only the gateway's root-owned 0600 environment file contains `OPENAI_API_KEY`.
The gateway account cannot read `/etc/onpf`. Root administrators remain trusted.
Provider-side data handling and automatic prompt caching are separate from local
gateway persistence. This setup does not promise OpenAI zero data retention.

## Explicit Ubuntu installation

Use **EC2 Session Manager on the intended instance**, not CloudShell. First verify
the instance identity and trusted release checksum according to the existing AWS
runbook. The existing ONPF account, `/etc/onpf`, and app unit must already exist.
Install distribution Python 3.12 with venv support through the normal reviewed
Ubuntu package process. The helper does not install host packages or modify
Nginx, the firewall, AWS resources, app storage or the running app service.

Copy `deploy/aws/ai-gateway.example.json` from the trusted release to a private
operator file. It contains no secrets. Keep the exact gateway version, host,
port, paths and lock digest. Choose one supported model snapshot. Run:

```sh
/usr/bin/python3.12 /opt/onpf/current/deploy/aws/setup_ai_gateway.py validate --config /root/ai-gateway.json
```

Validation checks the nonsecret configuration and bundled lock; it does not read
credentials, install packages or contact the provider. Explicit installation is
Linux/root only, checks Ubuntu/architecture/interpreter and path ownership, and
refuses existing runtime, gateway config, unit/drop-ins, app key or AI env file.
It rejects redirected paths. A failed attempt can leave its new files and system
account; inspect those paths before any manual cleanup or retry. There is no
automatic overwrite, rollback, upgrade or deletion.

Enter the provider key through an interactive hidden prompt. Generate a distinct
random gateway key locally. This example refuses an existing credentials directory
and files and never puts either key in shell arguments, command history or output:

```sh
sudo /usr/bin/python3.12 - <<'PY'
import getpass, os, pathlib, re, secrets
p = pathlib.Path('/etc/onpf-ai-gateway-keys')
if p.exists() or p.is_symlink() or pathlib.Path('/etc').resolve() != pathlib.Path('/etc'):
    raise SystemExit('credential_directory_requires_review')
provider = getpass.getpass('OpenAI provider key (hidden): ')
if not re.fullmatch(r'sk-[A-Za-z0-9_-]{20,4000}', provider):
    raise SystemExit('invalid_credential')
p.mkdir(mode=0o700)
p.chmod(0o700)
for name, value in [('master-key', 'sk-' + secrets.token_urlsafe(48)), ('provider-key', provider)]:
    fd = os.open(p / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, 'w') as f:
        f.write(value + '\n')
print('private_credentials_created')
PY
sudo /usr/bin/python3.12 /opt/onpf/current/deploy/aws/setup_ai_gateway.py install --config /root/ai-gateway.json
```

Source key files must be root-owned regular files with no group/other permission
bits, at most 4096 bytes. The helper reads them privately and copies the gateway
credential into the protected app file. It installs only the separate runtime
from the complete `gateway-requirements-linux-py312.lock`, enforcing every wheel
hash, binary-only installation and `pip check`, as the gateway system user.
The input, lock digest, 115-package inventory, exact wheel hashes, resolver version,
target platform and resolution command are in `gateway-lock-metadata.json`.
The graph was resolved for Linux/CPython 3.12.14 with uv 0.12.21 and narrowed to
downloaded wheels for Ubuntu's glibc 2.39. It is not a Windows freeze. Runtime
installation never resolves an upgrade. Ubuntu's reviewed Python/venv/ensurepip
packages supply bootstrap tooling; they are outside this Python wheel lock.
The selected release and artifact identities are published in
[versioned PyPI metadata](https://pypi.org/pypi/litellm/1.103.2/json) and the
[LiteLLM release](https://github.com/BerriAI/litellm/releases/tag/v1.103.2).
The lock follows [pip hash checking](https://pip.pypa.io/en/stable/topics/secure-installs/)
and [uv target resolution](https://docs.astral.sh/uv/concepts/resolution/).

Installation creates a persistent launcher, ASGI surface shim and runtime under `/opt/onpf-ai-gateway`
and persistent configuration under `/etc/onpf-ai-gateway`. It writes a new
`/etc/onpf/ai-drafting.env`, loaded after `production.env` by the supplied app
unit. Regenerating production.env during an app update preserves these AI settings.
ONPF defaults stay disabled on installations without that optional file.
Start only after reviewing the installed paths and unit:

```sh
sudo systemctl enable --now onpf-ai-gateway.service
sudo systemctl restart onpf.service
sudo systemctl show onpf-ai-gateway.service -p ActiveState -p SubState -p ExecMainStatus
sudo ss -ltn 'sport = :4000'
```

Verify exactly 127.0.0.1:4000; verify the gateway identity and its inability to
read `/var/lib/onpf`, `/etc/onpf` and `/opt/onpf` under the actual systemd sandbox.
Service state and the fixed socket confirm the running process and listener.
Provider/model readiness requires the separately authorized fictional completion
below. Health endpoints return 404 and do not provide a readiness workflow.
Ubuntu wheel installation, systemd sandbox/ownership, service restart and socket
acceptance remain operator checks. Windows fictional protocol testing does not
establish those Linux guarantees.

## Fictional provider acceptance and safe diagnosis

Only after the operator authorizes a paid provider call, run one small fictional
request. It tests account/model availability, outbound TLS, alias authentication,
JSON mode and the token cap without real participant/program evidence. This prints
only a fixed result code; it neither displays nor saves generated text:

```sh
sudo -u onpf /opt/onpf/current/.venv/bin/python - <<'PY'
import json
from onpf.drafting.gateway import complete, GatewayError
settings = dict(AI_DRAFTING_ENABLED=True, AI_GATEWAY_URL='http://127.0.0.1:4000/v1',
    AI_GATEWAY_KEY_FILE='/etc/onpf/ai-gateway-key', AI_MODEL='onpf-drafting',
    AI_TIMEOUT_SECONDS=45, AI_MAX_OUTPUT_TOKENS=512)
try:
    text = complete(settings, [{'role':'user', 'content':'FICTIONAL acceptance only. Return JSON exactly {"fictional":true}.'}])
    ok = json.loads(text) == {'fictional': True}
except Exception:
    ok = False
print('fictional_acceptance_passed' if ok else 'fictional_acceptance_failed')
raise SystemExit(0 if ok else 1)
PY
```

Record the date, model, gateway/app commit and result separately. A local stub
exercise does not prove paid OpenAI acceptance. Never run shell tracing (`set -x`),
raw curl response output, `systemctl status` or journal/provider traceback dumps
as this workflow's diagnostics. Service state, exit code, socket address and fixed
request outcome are sufficient. Missing/invalid keys, unsafe files, absent runtime
or occupied port fail generically. Check root-owned configuration and file modes
privately; do not paste keys, environment contents or generated text into support
logs. A suppressed gateway failure needs a fictional reproduction in a separate
protected scratch runtime, not production payload logging.

Disable drafting by setting `ONPF_AI_DRAFTING_ENABLED=false` in the private app
AI env file and explicitly restarting ONPF. Restarting the gateway independently
does not alter ONPF records or write/recreate gateway YAML. The release installer
does not manage this gateway service/runtime. Review upstream release/security
advisories before any deliberate future gateway upgrade; selecting a stable
release and matching hashes are artifact identity checks, not a security attestation.

## Future Ollama route behind the same alias

Ollama is a future operator acceptance step, not a verified provider in this
release. In the persistent `/etc/onpf-ai-gateway/config.yaml`, retain the single
`model_name: onpf-drafting` and privacy/cache/callback/retry/fallback restrictions.
Replace that entry's provider model with `ollama_chat/YOUR_REVIEWED_MODEL`, use
`api_base: http://127.0.0.1:11434`, and remove its `api_key` line. Remove
`OPENAI_API_KEY` from the private gateway environment. Retain the master gateway
key. The launcher accepts its absence when the config no longer references it,
and does not override the persistent route during restart or app releases.
Explicitly restart only the gateway, then repeat fictional JSON/token-cap/timeout
acceptance and confirm Ollama's own logging/retention and loopback binding.
There is no automatic fallback or provider change. ONPF's URL, protected gateway
key and alias stay the same; no provider SDK is added to ONPF.
