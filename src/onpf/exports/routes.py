"""Owner-publication controls, public projections for printing, and new draft import."""
import tempfile
from io import BytesIO
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from onpf.auth.service import get_principal, login_required, require_project_creation
from onpf.errors import DomainError
from onpf.db import transaction
from onpf.exports.importer import UPLOAD_LIMIT, import_program
from onpf.exports.package import blank_session, build_package
from onpf.exports.projection import public_document
from onpf.releases.service import get_release

bp = Blueprint('exports', __name__, url_prefix='/exports')


def _directory():
    return Path(current_app.config.get('EXPORT_DIRECTORY', Path(current_app.instance_path) / 'exports'))


@bp.after_request
def private_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@bp.post('/releases/<release_id>/package')
@login_required
def package(release_id):
    with transaction():
        path = build_package(get_principal(), release_id, _directory())
        payload = path.read_bytes()
    return send_file(BytesIO(payload), mimetype='application/zip', as_attachment=True, download_name=path.name, conditional=False)


@bp.get('/releases/<release_id>/print')
@login_required
def print_release(release_id):
    with transaction():
        document = public_document(get_release(get_principal(), release_id))
        return render_template('exports/print.html', source=document, blank=blank_session(document))


@bp.route('/import', methods=['GET', 'POST'])
@login_required
def import_page():
    require_project_creation(get_principal())
    title, error = request.form.get('title', '') if request.method == 'POST' else '', None
    if request.method == 'POST':
        temporary = None
        try:
            upload = request.files.get('archive')
            if upload is None:
                raise DomainError('missing_archive', 'Choose a public ONPF ZIP package.', 422)
            directory = _directory()
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                total = 0
                while chunk := upload.stream.read(65536):
                    total += len(chunk)
                    if total > UPLOAD_LIMIT:
                        raise DomainError('oversize_archive', 'The public package upload limit is 25 MiB.', 422)
                    stream.write(chunk)
            program = import_program(get_principal(), temporary, title)
            flash('Public package imported as your new unapproved draft. Review local permissions and material terms before approval.')
            return redirect(url_for('programs.workspace', program_id=program['id']))
        except DomainError as problem:
            error = problem
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return render_template('exports/import.html', title=title, error=error), error.status if error else 200
