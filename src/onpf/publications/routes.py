"""Explicit owner publication and unsigned public project library."""
from io import BytesIO

from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for

from onpf.auth.service import get_principal, login_required
from onpf.errors import DomainError
from onpf.publications import service
from onpf.publications.files import document_file, package_bytes
from onpf.additional_documents.files import document_file as additional_document_file
from onpf.additional_documents.service import attachment_bytes
from onpf.programs.service import get_program
from onpf.permissions.service import permission_readiness
from onpf.releases.service import lifecycle_warnings

bp = Blueprint('publications', __name__)


@bp.after_request
def publication_cache(response):
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    return response


@bp.get('/tour/community-garden')
def garden_tour():
    return render_template('publications/tour.html')


@bp.get('/projects')
def directory():
    return render_template('publications/directory.html', projects=service.list_publications())


@bp.get('/projects/<program_id>/versions/<int:version_number>')
def detail(program_id, version_number):
    source = service.get_publication(program_id, version_number)
    return render_template('publications/detail.html', source=source, versions=service.project_versions(program_id),
                           current_permission_evidence_status=permission_readiness(program_id)['status'])


@bp.get('/projects/<program_id>/versions/<int:version_number>/documents/<key>.<extension>')
def download_document(program_id, version_number, key, extension):
    source = service.get_publication(program_id, version_number)
    payload, mimetype = document_file(source, key, extension)
    return send_file(BytesIO(payload), mimetype=mimetype, as_attachment=extension != 'html',
                     download_name=f'{key}-v{version_number}.{extension}', conditional=False)


def _additional(source, document_id):
    item = next((entry for entry in source.get('additional_documents', []) if entry['id'] == document_id), None)
    if item is None:
        raise DomainError('not_found', 'This public additional document is unavailable.', 404)
    return item


@bp.get('/projects/<program_id>/versions/<int:version_number>/additional/<document_id>.<extension>')
def download_additional(program_id, version_number, document_id, extension):
    item = _additional(service.get_publication(program_id, version_number), document_id)
    payload, mimetype = additional_document_file(item, extension)
    return send_file(BytesIO(payload), mimetype=mimetype, as_attachment=extension != 'html',
                     download_name=f'additional-{document_id}-v{version_number}.{extension}', conditional=False)


@bp.get('/projects/<program_id>/versions/<int:version_number>/additional/<document_id>/attachment')
def download_additional_attachment(program_id, version_number, document_id):
    item = _additional(service.get_publication(program_id, version_number), document_id)
    attachment = item['attachment']
    payload = attachment_bytes(attachment)
    response = send_file(BytesIO(payload), mimetype=attachment['mime'], as_attachment=True,
                         download_name=attachment['name'], conditional=False)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.get('/projects/<program_id>/versions/<int:version_number>/package.zip')
def download_package(program_id, version_number):
    source = service.get_publication(program_id, version_number)
    payload = package_bytes(source)
    return send_file(BytesIO(payload), mimetype='application/zip', as_attachment=True,
                     download_name=f'onpf-project-{program_id}-v{version_number}.zip', conditional=False)


@bp.route('/programs/<program_id>/publish', methods=['GET', 'POST'])
@login_required
def publish_page(program_id):
    error = None
    if request.method == 'POST':
        try:
            revision = int(request.form.get('expected_revision', ''))
            published = service.publish(get_principal(), program_id, revision, reviewed=request.form.get('reviewed') == 'yes', selected_additional_ids=request.form.getlist('additional_ids'))
            flash('A frozen public project version was published. Its approval status is shown separately.')
            return redirect(url_for('publications.detail', program_id=program_id, version_number=published['version_number']))
        except ValueError:
            error = DomainError('invalid_revision', 'Reload and review the current workspace before publishing.', 422)
        except DomainError as problem:
            error = problem
    preview = service.preview_source(get_principal(), program_id)
    return render_template('publications/preview.html', source=preview, error=error,
                           selected_additional_ids=request.form.getlist('additional_ids') if request.method == 'POST' else [],
                           lifecycle_warnings=lifecycle_warnings(get_program(get_principal(), program_id)),
                           versions=service.project_versions(program_id)), error.status if error else 200
