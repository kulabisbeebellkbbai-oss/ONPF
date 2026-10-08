"""Shared library, private drafts, version previews and intentional improvement sharing."""
import copy
import json

from flask import Blueprint, flash, redirect, render_template, request, url_for

from onpf.auth.service import get_principal, login_required
from onpf.errors import DomainError
from onpf.materials import service
from onpf.programs.service import get_program

bp = Blueprint('materials', __name__)


@bp.after_request
def protect_pages(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def _context(program_id):
    program = get_program(get_principal(), program_id)
    return {'program': program, 'is_owner': get_principal().user_id in program['owner_ids'], 'section_field_name': _section_field_name}


def _section_field_name(document_key, section_key):
    """Encode each key separately so arbitrary keys cannot collide or break HTML IDs."""
    return f"section_{document_key.encode('utf-8').hex()}_{section_key.encode('utf-8').hex()}"


def _revision():
    try:
        return int(request.form.get('expected_revision', ''))
    except ValueError:
        raise DomainError('invalid_revision', 'Reload the form to obtain its revision.', 422)


def _library(program_id, values=None, error=None):
    actor = get_principal()
    context = _context(program_id)
    return render_template('materials/library.html', **context, drafts=service.list_materials(actor, program_id), versions=service.list_versions(actor), adoptions=service.list_adoptions(actor, program_id), improvements=service.list_improvements(actor, program_id) if context['is_owner'] else [], values=values or {}, error=error), error.status if error else 200


@bp.get('/programs/<program_id>/materials')
@login_required
def library(program_id):
    return _library(program_id)


def _form_values(current):
    values = {key: request.form.get(key, '') for key in ('title', 'ownership_basis', 'permission_basis', 'license', 'notices')}
    if 'content' in request.form:
        # Preserve even malformed content posted by older/import-oriented clients.
        values['content'] = request.form['content']
        try:
            values['content'] = service.validate_content(json.loads(values['content']))
        except (ValueError, RecursionError):
            return values, DomainError('invalid_content', 'The supplied material content is invalid. Your input is preserved.', 422)
        except DomainError as error:
            values['content'] = request.form['content']
            return values, error
    elif current and current['content']['kind'] == 'document_bundle':
        content = copy.deepcopy(current['content'])
        for document_key, document in content['documents'].items():
            document['title'] = request.form.get(f'title_{document_key}', document['title'])
            for section in document['sections']:
                section['text'] = request.form.get(_section_field_name(document_key, section['key']), '')
            for index, row in enumerate(document.get('rows', [])):
                for key in row:
                    row[key] = request.form.get(f'row_{document_key}_{index}_{key}', '')
                if not row['unit_cost']:
                    row['unit_cost'] = None
        values['content'] = content
    else:
        values['content'] = {'schema_version': 1, 'kind': 'text', 'text': request.form.get('text', '')}
    return values, None


@bp.route('/programs/<program_id>/materials/new', methods=['GET', 'POST'], defaults={'material_id': None})
@bp.route('/programs/<program_id>/materials/<material_id>', methods=['GET', 'POST'])
@login_required
def edit(program_id, material_id):
    context = _context(program_id)
    current = service.get_material(get_principal(), program_id, material_id) if material_id else None
    values = current or {'title': '', 'ownership_basis': 'own_work', 'permission_basis': '', 'license': 'MIT-0', 'notices': '', 'content': {'schema_version': 1, 'kind': 'text', 'text': ''}}
    revision, error = values.get('revision', ''), None
    if request.method == 'POST':
        values, error = _form_values(current)
        revision = request.form.get('expected_revision', '')
        if material_id:
            values['id'] = material_id
        if not error:
            try:
                saved = service.save_material(get_principal(), program_id, values, _revision() if material_id else None)
                flash('Private material draft saved. Releasing and adopting are separate owner choices.')
                return redirect(url_for('materials.edit', program_id=program_id, material_id=saved['id']))
            except DomainError as problem:
                error = problem
    return render_template('materials/edit.html', **context, values=values, current=current, material_id=material_id, expected_revision=revision, versions=service.list_versions(get_principal(), material_id) if material_id else [], error=error), error.status if error else 200


@bp.post('/programs/<program_id>/materials/<material_id>/release')
@login_required
def release(program_id, material_id):
    service.get_material(get_principal(), program_id, material_id)
    try:
        service.release_material(get_principal(), material_id, _revision())
        flash('This exact material draft is released as a reusable version. Existing adopters keep their chosen version.')
        return redirect(url_for('materials.edit', program_id=program_id, material_id=material_id))
    except DomainError as error:
        return _library(program_id, dict(request.form), error)


@bp.post('/programs/<program_id>/materials/adopt')
@login_required
def adopt(program_id):
    try:
        service.adopt_material(get_principal(), program_id, request.form.get('version_id', ''), _revision())
        flash('The selected released version is now adopted by this program.')
        return redirect(url_for('materials.library', program_id=program_id))
    except DomainError as error:
        return _library(program_id, dict(request.form), error)


@bp.post('/programs/<program_id>/materials/remove')
@login_required
def remove(program_id):
    try:
        service.remove_adoption(get_principal(), program_id, request.form.get('material_id', ''), _revision())
        flash('The current material selection was removed. Prepare a new candidate for owner approval; previous releases retain their original selections.')
        return redirect(url_for('materials.library', program_id=program_id))
    except DomainError as error:
        return _library(program_id, dict(request.form), error)


@bp.post('/programs/<program_id>/materials/derive')
@login_required
def derive(program_id):
    try:
        draft = service.derive_material(get_principal(), program_id, request.form.get('version_id', ''))
        flash('A private local draft was created from the selected version, retaining its source terms.')
        return redirect(url_for('materials.edit', program_id=program_id, material_id=draft['id']))
    except DomainError as error:
        return _library(program_id, dict(request.form), error)


@bp.post('/programs/<program_id>/materials/<material_id>/improve')
@login_required
def improve(program_id, material_id):
    context = _context(program_id)
    current = service.get_material(get_principal(), program_id, material_id)
    try:
        service.propose_improvement(get_principal(), program_id, material_id, request.form.get('note', ''), _revision(), share_content=request.form.get('share_content') == 'yes')
        flash('Your improvement proposal was sent internally to the source decision owners. The source material has not changed.')
        return redirect(url_for('materials.edit', program_id=program_id, material_id=material_id))
    except DomainError as error:
        return render_template('materials/edit.html', **context, values=current, current=current, material_id=material_id, expected_revision=current['revision'], versions=service.list_versions(get_principal(), material_id), error=error, improvement_values=dict(request.form)), error.status


@bp.get('/programs/<program_id>/materials/compare')
@login_required
def compare(program_id):
    context = _context(program_id)
    comparison = service.compare_versions(get_principal(), request.args.get('left', ''), request.args.get('right', ''))
    return render_template('materials/compare.html', **context, comparison=comparison)
