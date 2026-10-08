"""Additional-document library and private print/download routes."""
from io import BytesIO

from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for

from onpf.additional_documents.files import document_file
from onpf.additional_documents.service import (attachment_bytes, get_additional, list_additional,
                                                public_item, save_additional)
from onpf.additional_documents.templates import TEMPLATES
from onpf.auth.service import get_principal, login_required
from onpf.errors import DomainError
from onpf.programs.service import get_program

bp = Blueprint('additional_documents', __name__, url_prefix='/programs/<program_id>/additional')


def _revision():
    try:
        return int(request.form.get('expected_revision', ''))
    except ValueError:
        raise DomainError('invalid_revision', 'Reload the current workspace revision before saving.', 422)


def _payload(template_key):
    structure = TEMPLATES[template_key]
    return {'template_key': template_key, 'title': request.form.get('title', ''),
            'sections': {section['key']: request.form.get('section_' + section['key'], '') for section in structure['sections']},
            'ownership_basis': request.form.get('ownership_basis', 'own_work'),
            'permission_basis': request.form.get('permission_basis', ''),
            'license': request.form.get('license', ''), 'notices': request.form.get('notices', ''),
            'include_in_public': request.form.get('include_in_public') == 'yes'}


def _attachment():
    uploaded = request.files.get('attachment')
    if not uploaded or not uploaded.filename:
        return None, None
    from onpf.additional_documents.service import MAX_UPLOAD
    data = uploaded.stream.read(MAX_UPLOAD + 1)
    return data, uploaded.filename


@bp.route('', methods=['GET', 'POST'])
@login_required
def index(program_id):
    program = get_program(get_principal(), program_id)
    can_edit = program['access_role'] in {'owner', 'facilitator'}
    template_key = request.form.get('template_key') if request.method == 'POST' else request.args.get('template', 'recipe-card')
    if template_key not in TEMPLATES:
        raise DomainError('invalid_template', 'Choose an available additional-document template.', 422)
    error = None
    values = {'title': '', 'sections': {}, 'ownership_basis': 'own_work', 'permission_basis': 'Created by this project',
              'license': 'MIT-0', 'notices': '', 'include_in_public': False}
    if request.method == 'POST':
        if not can_edit:
            raise DomainError('forbidden', 'View-only access cannot create additional documents.', 403)
        values = _payload(template_key)
        try:
            upload, filename = _attachment()
            item = save_additional(get_principal(), program_id, values, _revision(), upload=upload, filename=filename)
            flash('Additional document saved privately. An owner must review a public version before it appears publicly.')
            return redirect(url_for('.detail', program_id=program_id, document_id=item['id']))
        except DomainError as problem:
            error = problem
    return render_template('additional_documents/index.html', program=program, items=list_additional(get_principal(), program_id),
                           templates=TEMPLATES, template_key=template_key, values=values, error=error,
                           can_edit=can_edit), error.status if error else 200


@bp.route('/<document_id>', methods=['GET', 'POST'])
@login_required
def detail(program_id, document_id):
    program = get_program(get_principal(), program_id)
    item = get_additional(get_principal(), program_id, document_id)
    if item['template_key']=='supporting':
        from onpf.additional_documents.editor import render_editor
        return render_editor(program,item)
    can_edit = program['access_role'] in {'owner', 'facilitator'}
    error = None
    values = item
    if request.method == 'POST':
        if not can_edit:
            raise DomainError('forbidden', 'View-only access cannot edit additional documents.', 403)
        values = _payload(item['template_key'])
        try:
            upload, filename = _attachment()
            save_additional(get_principal(), program_id, values, _revision(), document_id,
                            upload=upload, filename=filename, remove_upload=request.form.get('remove_upload') == 'yes')
            flash('Additional document revised.')
            return redirect(url_for('.detail', program_id=program_id, document_id=document_id))
        except DomainError as problem:
            error = problem
    return render_template('additional_documents/detail.html', program=program, item=item, values=values,
                           structure=TEMPLATES[item['template_key']], error=error, can_edit=can_edit), error.status if error else 200


@bp.route('/new',methods=['GET','POST'])
@login_required
def create_supporting(program_id):
    from onpf.additional_documents.editor import render_editor
    return render_editor(get_program(get_principal(),program_id))


@bp.get('/<document_id>/history')
@login_required
def material_history(program_id,document_id):
    from onpf.additional_documents.supporting import history
    return render_template('additional_documents/history.html',program=get_program(get_principal(),program_id),
                           records=history(get_principal(),program_id,document_id),document_id=document_id)


@bp.post('/<document_id>/populate')
@login_required
def populate(program_id,document_id):
    from onpf.additional_documents.supporting import fields,derive
    item=get_additional(get_principal(),program_id,document_id)
    try:
        count=int(request.form.get('quantity',''))
        revision=int(request.form.get('template_revision',''))
    except ValueError:
        raise DomainError('invalid_batch','Choose an explicit item quantity and template revision.',422)
    if not 1<=count<=100:
        raise DomainError('material_batch_limit','Use a bounded quantity.',422)
    values={key:request.form.get('field_'+key,'').splitlines() for key in fields(item['structured'])}
    if any(len(v)!=count for v in values.values()):
        raise DomainError('invalid_template_fields','Enter one value per line for every requested item.',422)
    derive(get_principal(),program_id,document_id,revision,[{key:v[index] for key,v in values.items()} for index in range(count)])
    return redirect(url_for('.index',program_id=program_id))


@bp.get('/<document_id>.<extension>')
@login_required
def download(program_id, document_id, extension):
    item = public_item(get_additional(get_principal(), program_id, document_id))
    if item.get('structured'):
        from onpf.drafting.usage import limits
        from onpf.additional_documents.pdf import render
        render(item,page_limit=limits(get_principal(),program_id)['pages'])
    from onpf.drafting.usage import limits
    payload, mimetype = document_file(item, extension,page_limit=limits(get_principal(),program_id)['pages'])
    return send_file(BytesIO(payload), mimetype=mimetype, as_attachment=extension != 'html',
                     download_name=f'additional-{document_id}.{extension}', conditional=False)


@bp.get('/<document_id>/attachment')
@login_required
def attachment(program_id, document_id):
    item = get_additional(get_principal(), program_id, document_id)
    if item['upload_data'] is None:
        raise DomainError('not_found', 'This attachment is unavailable.', 404)
    payload = attachment_bytes({'data': item['upload_data'], 'sha256': item['upload_sha256']})
    response = send_file(BytesIO(payload), mimetype=item['upload_mime'], as_attachment=True,
                         download_name=item['upload_name'], conditional=False)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
