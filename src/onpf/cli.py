"""Local administration. No default account or password is created."""
import argparse
import getpass
import sys
import os
from pathlib import Path

from onpf.app import create_app
from onpf.auth.service import create_user
from onpf.errors import DomainError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the local ONPF installation.")
    parser.add_argument("--instance", type=Path, help="Private installation directory (default: ./instance)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize storage without creating an account")
    ai = sub.add_parser('ai-check', help='Validate provider settings without opening a project database')
    ai.add_argument('--connect', action='store_true', help='Send one fictional test prompt; provider usage may be charged')
    demo = sub.add_parser('demo', help='Create a separate FICTIONAL training database; refuse existing storage')
    demo.add_argument('--database', type=Path, required=True)
    private = sub.add_parser('backup', help='Local administrator: save a PRIVATE backup to a new JSON path')
    private.add_argument('destination', type=Path)
    restore = sub.add_parser('restore', help='Local administrator: restore PRIVATE JSON into a new nonexistent database path')
    restore.add_argument('archive', type=Path)
    restore.add_argument('destination', type=Path)
    redact = sub.add_parser('redact-response', help='Local administrator: record owner-authorized removal and report copies for review')
    redact.add_argument('response_id')
    redact.add_argument('--owner', required=True, help='Persisted username of a decision owner for this response program')
    redact.add_argument('--reason', required=True, choices=['privacy_request', 'retention_expired', 'captured_in_error', 'legal_requirement'], help='Nonpersonal reason: participant privacy request, expired retention, capture error, or required removal')
    account = sub.add_parser("create-user", help="Create an account with a secure password prompt")
    account.add_argument("--username", required=True)
    account.add_argument("--password-stdin", action="store_true", help="Read one password line from secure standard input")
    grant = sub.add_parser("grant-admin", help="Local operator recovery: grant installation administration to an existing account")
    grant.add_argument("--username", required=True)
    for command in (private, redact, account, grant):
        command.add_argument('--database', type=Path, help='Explicit source database (default: <instance>/onpf.sqlite3); use the same path as serve')
    server = sub.add_parser("serve", help="Run the service with Waitress")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument('--database', type=Path, help='Explicit database for a separate demo or restored instance')
    args = parser.parse_args(argv)
    config = {"INSTANCE_PATH": str(args.instance)} if args.instance else {}
    if args.command in ('serve', 'backup', 'redact-response', 'create-user', 'grant-admin') and args.database:
        config['DATABASE'] = str(args.database.resolve())
    try:
        if args.command == 'ai-check':
            import json
            from onpf.drafting.config import drafting_environment, validate_settings
            from onpf.drafting.gateway import complete, _credential, GatewayError
            try:
                settings = validate_settings(drafting_environment(os.environ))
            except ValueError:
                raise DomainError('ai_unavailable', 'AI gateway configuration is invalid.', 503) from None
            if not settings['AI_DRAFTING_ENABLED']:
                raise DomainError('ai_unconfigured', 'No AI provider is configured.', 503)
            try:
                _credential(settings['AI_GATEWAY_KEY_FILE'])
                if args.connect:
                    result = complete(settings, [{'role':'user','content':'FICTIONAL acceptance only. Return JSON exactly {"fictional":true}.'}])
                    if json.loads(result) != {'fictional': True}:
                        raise ValueError('invalid acceptance')
            except (GatewayError, ValueError, OSError):
                raise DomainError('ai_unavailable', 'AI configuration or fictional acceptance failed.', 503) from None
            print('fictional_acceptance_passed' if args.connect else 'ai_configuration_validated_no_request_sent')
            return 0
        if args.command == 'demo':
            if args.instance:
                parser.error('Demo chooses a separate companion instance; do not supply --instance.')
            from onpf.demo import create_demo
            result = create_demo(args.database)
            print('FICTIONAL training only. No actual Bridge contributions or operating approval.')
            print(f"Database: {result['database']}\nInstance: {result['instance']}\nUsername: {result['username']}\nOne-time generated demo password: {result['password']}")
            print('Store the generated demo password privately. No default production credentials exist.')
            return 0
        if args.command == 'restore':
            from onpf.archives.service import restore_private
            restore_private(args.archive, args.destination)
            print(f'Restored private database to {args.destination}. Users must log in again; regenerate invitations/review links in a new instance.')
            return 0
        source = Path(config.get('DATABASE', (args.instance or Path.cwd() / 'instance') / 'onpf.sqlite3')).resolve()
        if args.command in ('backup', 'redact-response', 'grant-admin') and not source.is_file():
            raise DomainError('missing_database', f'Choose an existing local database. Selected source: {source}', 404)
        if args.command == 'backup':
            from onpf.archives.service import backup_private
            backup_private(source, args.destination)
            print(f'PRIVATE backup from {source} saved to {args.destination.resolve()}. Protect it like the database.')
            return 0
        if args.command == "create-user":
            if args.password_stdin:
                password = sys.stdin.readline().rstrip("\r\n")
            elif sys.stdin.isatty():
                password = getpass.getpass("Password (at least 12 characters): ")
                if password != getpass.getpass("Confirm password: "):
                    parser.error("The passwords do not match.")
            else:
                parser.error("An interactive password prompt or --password-stdin is required.")
            if not password:
                parser.error("A password is required; no account was created.")
        app = create_app(config)
        if args.command == "init":
            print(f"Initialized ONPF storage in {app.instance_path}. No account was created.")
        elif args.command == "create-user":
            with app.app_context():
                create_user(args.username, password)
            print(f"Account created in database {source}.")
        elif args.command == "grant-admin":
            from onpf.db import get_db, transaction
            with app.app_context(), transaction() as connection:
                account_row = get_db().execute("SELECT id FROM users WHERE username=?", (args.username,)).fetchone()
                if account_row is None:
                    raise DomainError("not_found", "Choose an existing account for local admin recovery.", 404)
                connection.execute("UPDATE users SET is_admin=1 WHERE id=?", (account_row["id"],))
            print(f"Installation administration granted to {args.username} in database {source}.")
        elif args.command == 'redact-response':
            import json
            from onpf.archives.service import redact_response
            from onpf.auth.models import Principal
            from onpf.db import get_db
            with app.app_context():
                user = get_db().execute('SELECT id FROM users WHERE username=?', (args.owner,)).fetchone()
                if not user:
                    raise DomainError('forbidden', 'Choose a persisted decision owner for this response program.', 403)
                result = redact_response(Principal(user['id'], None, None), args.response_id, args.reason)
                event = get_db().execute('SELECT report FROM redaction_events WHERE id=?', (result['event_id'],)).fetchone()
                print(json.dumps({'database': str(source), **json.loads(event['report'])}, ensure_ascii=False, indent=2))
        else:
            from waitress import serve
            from onpf.server_options import REQUEST_BODY_OPTIONS
            app.debug = False
            serve(app, host=args.host, port=args.port, **REQUEST_BODY_OPTIONS)
    except (DomainError, OSError, RuntimeError) as error:
        print(f"ONPF: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
