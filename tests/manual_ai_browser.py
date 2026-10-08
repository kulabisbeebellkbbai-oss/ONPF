"""Disposable fictional browser acceptance fixture; no provider or production I/O.

Run with the project's Python: tests/manual_ai_browser.py serve --root OUTPUT.
OUTPUT must not exist. Stop by creating OUTPUT/stop. Use snapshot/compare with a
named checkpoint after login/form GET. Gateway failure mode is the out-of-band
OUTPUT/mode.json file; no control endpoint is exposed in the application.
"""
import argparse
import hashlib
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread


def snapshot(root):
    with sqlite3.connect(f'file:{(root / "fixture.sqlite3").as_posix()}?mode=ro', uri=True) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: {'count': len(rows), 'sha256': hashlib.sha256(json.dumps(rows,
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}
            for table in tables
            for rows in [[list(r) for r in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]]}


def serve(root):
    from onpf.app import create_app
    from onpf.auth.models import Principal
    from onpf.auth.service import create_user
    from onpf.programs.service import create_program, get_program, save_document
    from onpf.inquiries.clarifications import issue_round, save_question_context
    from onpf.contributions.service import submit_responses
    from onpf.refinement.service import set_disposition
    from onpf.releases.service import prepare_candidate
    from onpf.drafting import evidence
    from onpf.production import REQUEST_BODY_OPTIONS, _private_server_logs
    from waitress import create_server

    root.mkdir(parents=True, exist_ok=False)
    (root / 'mode.json').write_text('{"failure":false}', encoding='utf8')
    key = root / 'fictional-key'
    key.write_text('fictional-browser-only-key', encoding='utf8')
    key.chmod(0o600)
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != '/v1/chat/completions' or self.headers.get('Authorization') != 'Bearer fictional-browser-only-key':
                self.send_error(404)
                return
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            data = json.loads(request['messages'][1]['content'])
            kind = data['target']['kind']
            # Only fictional fixture requests are in memory. Persist fixed
            # counts/outcomes, never instructions/current wording or payloads.
            selected = data['evidence']
            handles = [source['handle'] for source in selected['sources']]
            observed.append({'kind': kind, 'source_count': len(handles),
                'contains_round_two_answer': 'Fictional second-round answer' in request['messages'][1]['content']})
            (root / 'gateway-observations.json').write_text(json.dumps(observed, indent=2), encoding='utf8')
            mode = json.loads((root / 'mode.json').read_text(encoding='utf8'))
            if mode.get('failure'):
                self.send_response(429)
                self.end_headers()
                self.wfile.write(b'fictional-provider-error-body-must-not-display')
                return
            question = {'text': 'Who can confirm the fictional operating permission?', 'stage': 1,
                'document_key': 'overview', 'reason': 'Fictional permission remains unresolved.', 'source_handles': handles[:1]}
            fields = {
                'questions': {'questions': [{**question, 'answer_type': 'text'}]},
                'proposal': {'title': 'Fictional generated proposal', 'text': 'Fictional generated option; permission remains pending.', 'theme': ''},
                'decision': {'outcome': 'Fictional generated owner wording', 'rationale': 'Fictional permission requires owner review.'},
                'document': {'sections': {'purpose': 'Fictional generated document purpose; permission remains pending.'}},
                'review': {'findings': [{'text': 'Fictional permission remains pending in current drafts.', 'reason': 'Compare this advice with the frozen candidate; obtain permission separately.', 'source_handles': handles[:1]}]},
            }[kind]
            output = {'fields': fields, 'sources': handles[:1], 'uncertainties': ['Fictional permission remains unresolved.'], 'questions': [question]}
            body = json.dumps({'choices': [{'message': {'content': json.dumps(output)}, 'finish_reason': 'stop'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    gateway = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    gateway.daemon_threads = True
    gateway_thread = Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()
    app = create_app({'INSTANCE_PATH': str(root / 'instance'), 'DATABASE': str(root / 'fixture.sqlite3'),
        'DEBUG': False, 'TESTING': False, 'CONTACT_SMTP_HOST': '', 'CONTACT_SMTP_PASSWORD': '',
        'CONTACT_SMTP_PASSWORD_FILE': '', 'CONTACT_TO': '', 'AI_DRAFTING_ENABLED': True,
        'AI_GATEWAY_KEY_FILE': str(key), 'AI_GATEWAY_URL': f'http://127.0.0.1:{gateway.server_port}/v1'})

    @app.after_request
    def native_headers(response):
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    with app.app_context():
        owner = Principal(create_user('owner', 'fictional-owner-password'), None, None)
        program = create_program(owner, {'title': 'Fictional browser program', 'purpose': 'Fictional planning exercise'})
        pid = program['id']
        revision = lambda: get_program(owner, pid)['revision']
        program_handle = next(row['handle'] for row in evidence.catalog(owner, pid, {'kind': 'questions'}) if row['kind'] == 'program')
        manifests = evidence.select(owner, pid, {'kind': 'questions'}, [program_handle])['sources']
        save_question_context(owner, pid, 'core-1-1', 'Fictional context for permission.', manifests)
        rounds, response_ids = [], []
        for number in (1, 2):
            batch = issue_round(owner, pid, {'question_ids': ['core-1-1'], 'stage': 1, 'kind': 'clarification',
                'title': f'Fictional round {number}', 'reason': f'Fictional round {number} retained reason'}, revision())
            result = submit_responses(owner, batch['id'], f'fictional-browser-submission-{number}',
                [{'question_id': batch['questions'][0]['id'], 'text': 'Fictional first-round answer' if number == 1 else 'Fictional second-round answer', 'attribution': 'anonymous'}])
            rounds.append(batch['id'])
            response_ids.extend(result['response_ids'])
        save_document(owner, pid, 'overview', {'sections': {'purpose': 'Fictional retained document purpose'}}, revision())
        for rid in response_ids:
            set_disposition(owner, rid, 'deferred', 'Fictional explained deferral for another design cycle.', [])
        candidate = prepare_candidate(owner, pid, revision(), change_notes='Fictional frozen candidate for advisory review')

    stop = Event()
    failures = []
    with _private_server_logs():
        server = create_server(app, host='127.0.0.1', port=0, threads=4, **REQUEST_BODY_OPTIONS)

        def run():
            try:
                while not stop.is_set():
                    server.asyncore.loop(timeout=0.05, map=server._map, count=1)
            except BaseException as error:
                failures.append(error)

        worker = Thread(target=run, daemon=True)
        worker.start()
        info = {'url': f'http://127.0.0.1:{server.effective_port}', 'program_id': pid,
                'candidate_id': candidate['id'], 'round_ids': rounds, 'scripts': 'blocked by fixture CSP script-src none'}
        (root / 'ready.json').write_text(json.dumps(info, indent=2), encoding='utf8')
        print(json.dumps(info), flush=True)
        try:
            while not (root / 'stop').exists():
                if failures:
                    raise failures[0]
                stop.wait(0.2)
        finally:
            stop.set()
            worker.join(3)
            assert not worker.is_alive()
            server.task_dispatcher.shutdown()
            server.close()
            gateway.shutdown()
            gateway.server_close()
            gateway_thread.join(3)
            assert not gateway_thread.is_alive()
        if failures:
            raise failures[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['serve', 'snapshot', 'compare'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--name', default='baseline')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == 'serve':
        serve(root)
    elif args.action == 'snapshot':
        (root / f'{args.name}.json').write_text(json.dumps(snapshot(root), indent=2), encoding='utf8')
        print(f'Checkpoint {args.name}: all SQLite tables hashed.')
    else:
        before = json.loads((root / f'{args.name}.json').read_text(encoding='utf8'))
        after = snapshot(root)
        changed = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
        print(json.dumps({'checkpoint': args.name, 'changed_tables': sorted(changed)}))


if __name__ == '__main__':
    main()
