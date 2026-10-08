"""Contribution forms and private, count-only invitation receipts."""
import re
from uuid import uuid4

from flask import Blueprint, redirect, render_template, request, session, url_for

from onpf.auth.service import get_principal, login_required
from onpf.contributions import service
from onpf.errors import DomainError
from onpf.inquiries.routes import get_invitation_principal
from onpf.inquiries.service import get_batch

bp = Blueprint('contributions', __name__)
ROW_FIELDS = ('question_id', 'text', 'answer_state', 'attribution', 'display_name', 'entry_method', 'publication_permission')


@bp.after_request
def private_pages(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def entry_context(batch, *, invitation=True):
    return {'rows': [{'row_id': str(index), 'question_id': q['id'], 'text': '',
                      'answer_state': 'answered', 'attribution': 'anonymous',
                      'display_name': '', 'entry_method': 'direct', 'publication_permission': 'none'}
                     for index, q in enumerate(batch['questions'])],
            'submission_key': str(uuid4()), 'invitation': invitation,
            'entry_action': url_for('contributions.invited_entry') if invitation else url_for('contributions.entry', batch_id=batch['id'])}


def _form_rows():
    ids = request.form.getlist('row_id')
    if len(ids) > 500 or len(ids) != len(set(ids)) or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', key) for key in ids):
        raise DomainError('invalid_rows', 'The response form is unavailable. Reload the batch form.', 422)
    defaults = {'answer_state': 'answered', 'attribution': 'anonymous', 'entry_method': 'direct', 'publication_permission': 'none'}
    return [{'row_id': key, **{field: request.form.get(f'{field}_{key}', defaults.get(field, '')) for field in ROW_FIELDS}} for key in ids]


def _entry(actor, batch, invitation):
    context = entry_context(batch, invitation=invitation)
    error = None
    if request.method == 'POST':
        context['submission_key'] = request.form.get('submission_key', '')
        try:
            context['rows'] = _form_rows()
            if request.form.get('add_question_id'):
                question_id = request.form['add_question_id']
                if question_id not in {q['id'] for q in batch['questions']} or len(context['rows']) >= 500:
                    raise DomainError('invalid_question', 'Choose an available question and at most 500 responses.', 422)
                context['rows'].append({**entry_context(batch)['rows'][0], 'row_id': uuid4().hex, 'question_id': question_id})
            else:
                rows = [{field: row[field] for field in ROW_FIELDS} for row in context['rows']
                        if row['answer_state'] != 'skip' and (row['text'].strip() or row['answer_state'] != 'answered')]
                result = service.submit_responses(actor, batch['id'], context['submission_key'], rows)
                if invitation:
                    # Signed session holds only this browser's last count/reference.
                    # A shared invitation does not authorize receipt lookup in storage.
                    session['contribution_receipt'] = {'submission_id': result['submission_id'], 'invite_id': actor.invite_id,
                                                      'response_count': len(result['response_ids'])}
                    return redirect(url_for('contributions.invited_receipt', submission_id=result['submission_id']))
                return redirect(url_for('contributions.receipt', submission_id=result['submission_id']))
        except DomainError as problem:
            error = problem
    return render_template('inquiries/answer.html', batch=batch, error=error, **context), error.status if error else 200


@bp.route('/batches/<batch_id>/contributions', methods=['GET', 'POST'])
@login_required
def entry(batch_id):
    actor = get_principal()
    return _entry(actor, get_batch(actor, batch_id), False)


@bp.post('/invitations/contributions')
def invited_entry():
    actor = get_invitation_principal()
    return _entry(actor, get_batch(actor, actor.batch_id), True)


@bp.get('/invitations/receipts/<submission_id>')
def invited_receipt(submission_id):
    actor = get_invitation_principal()
    receipt = session.get('contribution_receipt', {})
    if receipt.get('submission_id') != submission_id or receipt.get('invite_id') != actor.invite_id:
        raise DomainError('forbidden', 'This receipt is available only to the browser that submitted it.', 403)
    return render_template('contributions/receipt.html', receipt=receipt, invitation=True)


@bp.get('/submissions/<submission_id>/receipt')
@login_required
def receipt(submission_id):
    result = service.get_receipt(get_principal(), submission_id)
    return render_template('contributions/receipt.html', receipt={**result, 'response_count': len(result['response_ids'])}, invitation=False)


@bp.get('/programs/<program_id>/responses')
@login_required
def index(program_id):
    return render_template('contributions/history.html', responses=service.list_responses(get_principal(), program_id), response=None)


def _history(response_id, error=None):
    response = service.get_response(get_principal(), response_id)
    values = {'text': response['text'], 'reason': '', 'expected_revision': response['revision'], 'original_id': '', 'duplicate_reason': ''}
    if request.method == 'POST':
        values.update({key: request.form.get(key, values[key]) for key in values})
    conflict = bool(error and error.status == 409) or bool(request.form.get('conflict_revision'))
    if conflict:
        # Preserve the draft, but an explicit acknowledgement binds the next attempt.
        values['expected_revision'] = response['revision']
    return render_template('contributions/history.html', response=response, values=values, error=error, conflict=conflict), error.status if error else 200


@bp.get('/responses/<response_id>')
@login_required
def history(response_id):
    return _history(response_id)


@bp.post('/responses/<response_id>/revise')
@login_required
def revise(response_id):
    try:
        try:
            revision = int(request.form.get('expected_revision', ''))
        except ValueError:
            raise DomainError('invalid_revision', 'Reload the response to obtain its current revision.', 422)
        conflict_revision = request.form.get('conflict_revision')
        if conflict_revision and (conflict_revision != str(revision) or request.form.get('acknowledge_revision') != conflict_revision):
            raise DomainError('acknowledgement_required', 'Review the current response and acknowledge its revision before appending your retained correction.', 422)
        service.revise_response(get_principal(), response_id, request.form.get('text', ''), request.form.get('reason', ''), revision)
        return redirect(url_for('contributions.history', response_id=response_id))
    except DomainError as error:
        return _history(response_id, error)


@bp.post('/responses/<response_id>/duplicate')
@login_required
def duplicate(response_id):
    try:
        service.mark_duplicate(get_principal(), response_id, request.form.get('original_id', ''), request.form.get('duplicate_reason', ''))
        return redirect(url_for('contributions.history', response_id=response_id))
    except DomainError as error:
        return _history(response_id, error)
