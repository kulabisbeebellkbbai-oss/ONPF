"""Flask application factory."""
from importlib import import_module
from importlib.util import find_spec
import os
import io
from pathlib import Path

from flask import Flask, Request, render_template, request
from flask_wtf.csrf import CSRFError, CSRFProtect

from onpf import db
from onpf.config import DEFAULTS, contact_environment, persistent_secret, readiness_identity
from onpf.db import Record
from onpf.drafting.config import drafting_environment, validate_settings
from onpf.errors import DomainError, handle_domain_error

csrf = CSRFProtect()


class OnpfRequest(Request):
    """Keep contact uploads in memory; other import paths retain their usual limits."""

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        if self.path == '/contact':
            return io.BytesIO()
        return super()._get_file_stream(total_content_length, content_type, filename, content_length)


def create_app(config: Record | None = None) -> Flask:
    supplied = dict(config or {})
    instance = Path(supplied.pop("INSTANCE_PATH", Path.cwd() / "instance")).resolve()
    app = Flask(__name__, instance_path=str(instance), instance_relative_config=True)
    from onpf.presentation import record_label, source_label
    app.jinja_env.globals.update(record_label=record_label, source_label=source_label)
    app.request_class = OnpfRequest
    app.config.from_mapping(DEFAULTS)
    app.config.update(contact_environment(os.environ))
    app.config.update(drafting_environment(os.environ))
    app.config.update(supplied)
    app.config.update(validate_settings(app.config))
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = persistent_secret(instance)
    instance.mkdir(parents=True, exist_ok=True)
    app.config.setdefault("DATABASE", str(instance / "onpf.sqlite3"))
    app.config['INSTANCE_ID'] = readiness_identity(instance, Path(app.config['DATABASE']))
    db.migrate(Path(app.config["DATABASE"]))
    app.teardown_appcontext(db.close_db)
    @app.before_request
    def public_import_request_limit():
        if request.method=='POST' and request.view_args and request.view_args.get('program_id') and request.endpoint != 'programs.retirement':
            from onpf.programs.retirement import ensure_editable
            ensure_editable(request.view_args['program_id'])
        # Set before CSRF parses multipart data. The file itself is capped at 25 MiB.
        if request.path == '/exports/import' and request.method == 'POST':
            request.max_content_length = 26 * 1024 * 1024
        if request.path == '/contact' and request.method == 'POST':
            request.max_content_length = 7 * 1024 * 1024
        if request.method == 'POST' and ('/drafting' in request.path or request.path.endswith('/questions/save-drafts')):
            request.max_content_length = 1024 * 1024

        if request.endpoint == 'permissions.index' and request.method == 'POST':
            request.max_content_length = 6 * 1024 * 1024
        if request.endpoint in {'additional_documents.index', 'additional_documents.detail'} and request.method == 'POST':
            request.max_content_length = 6 * 1024 * 1024

    csrf.init_app(app)
    app.register_error_handler(DomainError, handle_domain_error)

    @app.context_processor
    def workflow_context():
        from onpf.presentation import record_label, source_label
        def rounds(program):
            if not isinstance(program, dict) or program.get('access_role') not in {'owner', 'facilitator'}:
                return []
            from onpf.auth.service import get_principal
            from onpf.inquiries.service import clarification_overview
            return clarification_overview(get_principal(), program['id'])
        return {'workflow_rounds': rounds, 'record_label': record_label, 'source_label': source_label}

    @app.get('/health')
    def health():
        return {'application': 'onpf', 'version': '0.3.0', 'instance_id': app.config['INSTANCE_ID']}

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return handle_domain_error(DomainError("csrf_failed", "The form expired or could not be verified. Reload the page and try again.", 400))

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", error=DomainError("not_found", "That page is unavailable.", 404), submitted={}), 404

    from onpf.auth.routes import bp
    app.register_blueprint(bp)
    # Only planned modules can register routes; errors in present modules surface.
    for feature in ("programs", "inquiries", "contributions", "refinement", "materials", "releases", "exports", "archives", "contact", "management", "permissions", "drafting", "publications", "additional_documents"):
        package = f"onpf.{feature}"
        if find_spec(package) is not None and find_spec(f"{package}.routes") is not None:
            app.register_blueprint(import_module(f"{package}.routes").bp)
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    from onpf.administration import create_admin_app
    app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {'/admin':create_admin_app(app)})
    return app
