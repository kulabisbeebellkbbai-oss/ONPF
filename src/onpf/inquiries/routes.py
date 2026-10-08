"""Labeled forms for drafting, issuing, inviting and printing inquiries."""
import json
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from onpf.auth.service import get_principal, login_required
from onpf.errors import DomainError
from onpf.inquiries import service, clarifications
from onpf.programs.framework import load_framework
from onpf.programs.service import get_program

bp = Blueprint('inquiries', __name__)
INVITATION_COOKIE = 'onpf_invitation'


@bp.after_request
def protect_invitation_page(response):
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


def get_invitation_principal():
    """Resolve only the invite cookie, regardless of a logged-in account."""
    return service.resolve_invitation(request.cookies.get(INVITATION_COOKIE, ''))


def _revision():
    try:
        return int(request.form.get('expected_revision', ''))
    except ValueError:
        raise DomainError('invalid_revision', 'Reload to obtain the current revision.', 422)


def _context(program_id):
    actor = get_principal()
    program = get_program(actor, program_id)
    return {'program': program, 'framework': load_framework(), 'is_owner': actor.user_id in program['owner_ids'], 'fields': service.decision_fields(actor, program_id)}


def _source_options(program_id, framework):
    from onpf.refinement.service import coverage, list_proposals, list_decisions
    actor = get_principal()
    options = [('program', program_id, 'Program context')]
    options += [('response', row['id'], row['reference'] + ': ' + row['question_text'][:80]) for row in coverage(actor, program_id)]
    options += [('proposal', row['id'], 'Proposal: ' + row['title']) for row in list_proposals(actor, program_id)]
    options += [('decision', row['id'], 'Decision: ' + row['outcome'][:80]) for row in list_decisions(actor, program_id)]
    options += [('document', key + ':' + section['key'], document['title'] + ': ' + section['label']) for key, document in framework['documents'].items() for section in document['sections']]
    return options


@bp.get('/programs/<program_id>/batches')
@login_required
def index(program_id):
    return render_template('inquiries/detail.html', **_context(program_id), batches=service.list_batches(get_principal(), program_id), rounds=clarifications.rounds(get_principal(), program_id), batch=None)


@bp.route('/programs/<program_id>/batches/new', methods=['GET', 'POST'], defaults={'clarification': False})
@bp.route('/programs/<program_id>/rounds/new', methods=['GET', 'POST'], defaults={'clarification': True})
@login_required
def compose(program_id, clarification):
    context = _context(program_id)
    questions = service.question_catalog(get_principal(), program_id)
    clarification_mode = not clarification and (request.args.get('round') == '1' or request.form.get('clarification_mode') == '1')
    values, error = {'intake_copy': service.DEFAULT_INTAKE, 'question_ids': [] if clarification_mode else [q['id'] for q in questions if q['relevant']], 'after_step': 'after_proposals', 'sources': {}}, None
    revision = context['program']['revision']
    if request.method == 'POST':
        values = {key: request.form.get(key, '') for key in ('title', 'instructions', 'target_group', 'due_date', 'intake_copy')}
        values['intake_copy'] = request.form.get('intake_copy', service.DEFAULT_INTAKE)
        values['question_ids'] = request.form.getlist('question_ids')
        values['overrides'] = {key: request.form.get(f'override_{key}', '') for key in values['question_ids'] if request.form.get(f'override_{key}', '').strip()}
        values['intake_copy_approved'] = request.form.get('intake_copy_approved') == 'yes'
        if clarification_mode:
            values['after_step'] = request.form.get('after_step', '')
            values['purpose'] = request.form.get('purpose', '')
            values['sources'] = {key: {'source_type': (request.form.get(f'source_{key}', 'program|'+program_id).split('|', 1) + [''])[:2][0],
                                       'source_id': (request.form.get(f'source_{key}', 'program|'+program_id).split('|', 1) + [''])[:2][1],
                                       'why': request.form.get(f'why_{key}', ''), 'group_label': request.form.get(f'group_{key}', '')} for key in values['question_ids']}
            values['clarification'] = {'after_step': values['after_step'], 'purpose': values['purpose'], 'sources': values['sources']}
        try:
            if clarification:
                revision = request.form.get('expected_revision', '')
                values['kind'] = request.form.get('kind', 'clarification')
                values['reason'] = request.form.get('reason', '')
                if 'sources' in request.form:
                    values['sources'] = _json_form('sources', '[]')
                try:
                    values['stage'] = int(request.form['stage']) if request.form.get('stage') else None
                except ValueError:
                    raise DomainError('invalid_stage', 'Choose a clarification stage.', 422)
                batch = clarifications.issue_round(get_principal(), program_id, values, _revision())
            else:
                batch = service.issue_batch(get_principal(), program_id, values)
            return redirect(url_for('inquiries.detail', batch_id=batch['id']))
        except DomainError as problem:
            error = problem
    if clarification_mode:
        for question in questions:
            values['sources'].setdefault(question['id'], {'source_type': question.get('source_type') or 'program', 'source_id': question.get('source_id') or program_id, 'why': question.get('why', ''), 'group_label': question.get('group_label', '')})
    return render_template('inquiries/compose.html', **context, mode='round' if clarification else 'batch', questions=questions, values=values, error=error, expected_revision=revision, clarification_mode=clarification_mode, legacy_source_options=_source_options(program_id, context['framework']) if clarification_mode else []), error.status if error else 200


def _json_form(key, default):
    try:
        return json.loads(request.form.get(key, default))
    except (ValueError, TypeError):
        raise DomainError('invalid_payload', 'Use the labeled question and source fields.', 422) from None


@bp.post('/programs/<program_id>/questions/save-drafts')
@login_required
def save_drafts(program_id):
    from onpf.drafting.routes import native_fields, destination, json_state
    program = get_program(get_principal(), program_id)
    from onpf.auth.service import require_role
    require_role(get_principal(), program_id, service.EDIT_ROLES)
    questions, sources = [], []
    revision = request.form.get('expected_revision', '')
    receipt = request.form.get('ai_receipt') or None
    try:
        if 'question_count' in request.form:
            # Preserve form positions before domain validation, including blank
            # rows, so errors cannot move identities or drop later dependencies.
            questions = native_fields({'kind': 'questions'}, request.form, retain_empty_questions=True)['questions']
            for index, question in enumerate(questions):
                if request.form.get(f'q{index}_id'):
                    question['id'] = request.form[f'q{index}_id']
                question['depends_on'] = [{'field': field['key'], 'equals': request.form.get(f'q{index}_equals_{field["key"]}', '')}
                    for field in service.decision_fields(get_principal(), program_id) if request.form.get(f'q{index}_dep_{field["key"]}')]
        else:
            questions = json_state(request.form.get('questions'), [])
        sources = json_state(request.form.get('sources'), [])
        clarifications.save_draft_questions(get_principal(), program_id,
            {'questions': questions, 'sources': sources}, _revision(), ai_receipt=receipt)
    except DomainError as problem:
        if problem.status == 403:
            raise
        if 'question_count' not in request.form:
            raise
        if not isinstance(questions, list) or any(not isinstance(q, dict) for q in questions):
            raise
        if not isinstance(sources, list) or any(not isinstance(s, dict) for s in sources):
            sources = []
        return destination(program, {'kind':'questions'}, {'questions':questions}, receipt, sources, revision, problem)
    flash('Draft questions saved. Review and issue a clarification round when ready.')
    return redirect(url_for('inquiries.compose', program_id=program_id, clarification=True))


@bp.route('/programs/<program_id>/questions/<question_id>/context', methods=['GET', 'POST'])
@login_required
def save_context(program_id, question_id):
    if request.method == 'GET':
        return jsonify(context=clarifications.question_context(get_principal(), program_id, question_id),
                       options=clarifications.context_options(get_principal(), program_id, question_id))
    from onpf.db import transaction
    with transaction():
        from onpf.auth.service import require_role
        require_role(get_principal(), program_id, service.EDIT_ROLES)
        service._revision(program_id, _revision())
        if request.form.get('source_selection') == 'yes':
            try:
                manifests = [json.loads(value) for value in request.form.getlist('source_manifest')]
            except ValueError:
                raise DomainError('invalid_sources', 'Choose available context sources.', 422) from None
        else:
            manifests = _json_form('sources', '[]')
        clarifications.save_question_context(get_principal(), program_id, question_id,
                                             request.form.get('reason', ''), manifests)
    flash('Draft question context saved. Issued rounds keep their context.')
    return redirect(url_for('inquiries.question', program_id=program_id, question_id=question_id))


@bp.post('/programs/<program_id>/rounds/<batch_id>/questions/<question_id>/defer')
@login_required
def defer(program_id, batch_id, question_id):
    clarifications.defer_question(get_principal(), program_id, batch_id, question_id,
                                 request.form.get('reason', ''), _revision())
    flash('Question explicitly deferred. Current and later answers remain visible.')
    return redirect(url_for('inquiries.detail', batch_id=batch_id))


@bp.route('/programs/<program_id>/questions/new', methods=['GET', 'POST'], defaults={'question_id': None})
@bp.route('/programs/<program_id>/questions/<question_id>', methods=['GET', 'POST'])
@login_required
def question(program_id, question_id):
    context = _context(program_id)
    clarification_mode = request.args.get('round') == '1' or request.form.get('clarification_mode') == '1'
    values = {'text': '', 'stage': 1, 'document_key': 'overview', 'depends_on': [], 'why': '', 'group_label': '', 'source_type': 'program', 'source_id': program_id}
    if question_id:
        values = next((q for q in service.question_catalog(get_principal(), program_id) if q['id'] == question_id), None)
        if values is None:
            raise DomainError('not_found', 'This draft question is unavailable.', 404)
    error, revision = None, context['program']['revision']
    if request.method == 'POST':
        source = request.form.get('source', 'program|' + program_id).split('|', 1)
        values = {'text': request.form.get('text', ''), 'stage': request.form.get('stage', ''), 'document_key': request.form.get('document_key', ''), 'depends_on': [{'field': field['key'], 'equals': request.form.get(f'equals_{field["key"]}', '')} for field in context['fields'] if request.form.get(f'dep_{field["key"]}')],
                  'why': request.form.get('why', ''), 'group_label': request.form.get('group_label', ''), 'source_type': source[0], 'source_id': source[1] if len(source) > 1 else ''}
        if question_id:
            values['id'] = question_id
        revision = request.form.get('expected_revision', '')
        try:
            try:
                values['stage'] = int(values['stage'])
            except ValueError:
                raise DomainError('invalid_stage', 'Choose a development stage.', 422)
            service.save_question(get_principal(), program_id, values, _revision(), ai_receipt=request.form.get('ai_receipt') or None)
            flash('Draft question saved. Existing issued batches keep their wording.')
            return redirect(url_for('inquiries.compose', program_id=program_id, round=1) if clarification_mode else url_for('inquiries.compose', program_id=program_id))
        except DomainError as problem:
            error = problem
    return render_template('inquiries/compose.html', **context, mode='question', values=values, error=error, expected_revision=revision, clarification_mode=clarification_mode, legacy_source_options=_source_options(program_id, context['framework']), ai_receipt=request.form.get('ai_receipt') or None, source_options=clarifications.context_options(get_principal(), program_id, question_id) if question_id else [], dependency_values={d['field']: d['equals'] for d in values['depends_on']}), error.status if error else 200


@bp.route('/programs/<program_id>/decisions', methods=['GET', 'POST'])
@login_required
def decisions(program_id):
    context = _context(program_id)
    if not context['is_owner']:
        raise DomainError('forbidden', 'Only an owner can set program decision fields.', 403)
    values = {'key': '', 'label': '', 'value': '', 'depends_on': []}
    if request.args.get('field'):
        values = next((field for field in context['fields'] if field['key'] == request.args['field']), values)
    error, revision = None, context['program']['revision']
    if request.method == 'POST':
        values = {key: request.form.get(key, '') for key in ('key', 'label', 'value')}
        values['value'] = values['value'] or None
        values['depends_on'] = request.form.getlist('depends_on')
        revision = request.form.get('expected_revision', '')
        try:
            service.set_decision_field(get_principal(), program_id, values, _revision())
            flash('Owner decision field saved. Participant responses remain separate.')
            return redirect(url_for('inquiries.decisions', program_id=program_id))
        except DomainError as problem:
            error = problem
    return render_template('inquiries/compose.html', **context, mode='decisions', values=values, error=error, expected_revision=revision), error.status if error else 200


def _detail(batch_id, invitation_link=None):
    actor = get_principal()
    batch = service.get_batch(actor, batch_id)
    round_record = next((r for r in clarifications.rounds(actor, batch['program_id']) if r['id'] == batch_id), None)
    return render_template('inquiries/detail.html', **_context(batch['program_id']), batch=round_record or batch, clarification=round_record is not None, invitations=service.list_invitations(actor, batch_id), invitation_link=invitation_link)


@bp.get('/batches/<batch_id>')
@login_required
def detail(batch_id):
    return _detail(batch_id)


@bp.post('/batches/<batch_id>/invitations')
@login_required
def invite(batch_id):
    token = service.create_invitation(get_principal(), batch_id)
    # Fragments never reach server request URLs/access logs.
    return _detail(batch_id, url_for('inquiries.answer', _external=True) + '#' + token)


@bp.post('/invitations/<invite_id>/revoke')
@login_required
def revoke(invite_id):
    # The service validates authority before revealing the owning batch.
    from onpf.db import get_db
    service.revoke_invitation(get_principal(), invite_id)
    batch_id = get_db().execute('SELECT batch_id FROM invitations WHERE id=?', (invite_id,)).fetchone()[0]
    flash('Invitation revoked.')
    return redirect(url_for('inquiries.detail', batch_id=batch_id))


@bp.post('/batches/<batch_id>/close')
@login_required
def close(batch_id):
    service.close_batch(get_principal(), batch_id)
    flash('Batch closed to further submissions.')
    return redirect(url_for('inquiries.detail', batch_id=batch_id))


@bp.post('/clarifications/questions/<question_id>/defer')
@login_required
def defer_clarification(question_id):
    batch_id = request.form.get('batch_id', '')
    batch = service.get_batch(get_principal(), batch_id)
    if question_id not in {question['id'] for question in batch['questions']}:
        raise DomainError('not_found', 'This question is unavailable in the chosen round.', 404)
    service.defer_clarification_question(get_principal(), question_id, request.form.get('reason', ''))
    flash('Clarification deferral recorded with its reason.')
    return redirect(url_for('inquiries.detail', batch_id=batch_id))


@bp.get('/batches/<batch_id>/print')
@login_required
def print_form(batch_id):
    batch = service.get_batch(get_principal(), batch_id)
    return render_template('inquiries/print.html', batch=batch)


@bp.get('/invitations/answer')
def answer():
    batch, error = None, None
    if request.cookies.get(INVITATION_COOKIE):
        try:
            actor = get_invitation_principal()
            batch = service.get_batch(actor, actor.batch_id)
        except DomainError as problem:
            error = problem
    from onpf.contributions.routes import entry_context
    context = entry_context(batch) if batch else {'invitation': True}
    return render_template('inquiries/answer.html', batch=batch, error=error, **context), error.status if error else 200


@bp.post('/invitations/accept')
def accept():
    token = request.form.get('token', '')
    service.resolve_invitation(token)
    response = redirect(url_for('inquiries.answer'))
    response.set_cookie(INVITATION_COOKIE, token, httponly=True, samesite='Lax', secure=current_app.config['SESSION_COOKIE_SECURE'], path='/invitations')
    return response
