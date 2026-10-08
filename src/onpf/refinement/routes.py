"""Editor forms and a separate read-only review capability."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from onpf.auth.models import Principal
from onpf.auth.service import get_principal, login_required
from onpf.errors import DomainError
from onpf.programs.framework import load_framework
from onpf.programs.service import get_program
from onpf.refinement import service

bp = Blueprint('refinement', __name__)


@bp.after_request
def protect_pages(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def _context(program_id):
    actor = get_principal()
    program = get_program(actor, program_id)
    return {'program': program, 'is_owner': actor.user_id in program['owner_ids']}


@bp.route('/programs/<program_id>/refinement', methods=['GET', 'POST'])
@login_required
def board(program_id):
    context, values, error = _context(program_id), {}, None
    if request.method == 'POST':
        values = {key: request.form.get(key, '') for key in ('outcome', 'rationale', 'supersedes_id')}
        values.update({key: request.form.getlist(key) for key in ('proposal_ids', 'response_ids')})
        try:
            service.record_decision(get_principal(), program_id, values, ai_receipt=request.form.get('ai_receipt') or None)
            flash('Owner decision recorded; document adoption and release approval remain separate steps.')
            return redirect(url_for('refinement.board', program_id=program_id))
        except DomainError as problem:
            error = problem
    proposals = service.list_proposals(get_principal(), program_id)
    responses = service.coverage(get_principal(), program_id)
    return render_template('refinement/board.html', **context, proposals=proposals, decisions=service.list_decisions(get_principal(), program_id), responses=responses, proposal_groups=service.proposal_evidence(get_principal(), program_id, [row['id'] for row in proposals]), response_labels={row['id']: row['reference'] for row in responses}, values=values, error=error, ai_receipt=request.form.get('ai_receipt') or None), error.status if error else 200


@bp.route('/programs/<program_id>/refinement/proposals/new', methods=['GET', 'POST'], defaults={'proposal_id': None})
@bp.route('/programs/<program_id>/refinement/proposals/<proposal_id>', methods=['GET', 'POST'])
@login_required
def proposal(program_id, proposal_id):
    context = _context(program_id)
    values = service.get_proposal(get_principal(), program_id, proposal_id) if proposal_id else {'title': '', 'text': '', 'theme': '', 'response_ids': []}
    error, revision = None, values.get('revision', '')
    if request.method == 'POST':
        values = {key: request.form.get(key, '') for key in ('title', 'text', 'theme')}
        values['response_ids'] = request.form.getlist('response_ids')
        revision = request.form.get('expected_revision', '')
        if proposal_id:
            values['id'] = proposal_id
        try:
            expected = int(revision) if proposal_id else None
            saved = service.save_proposal(get_principal(), program_id, values, expected, ai_receipt=request.form.get('ai_receipt') or None)
            flash('Proposal saved with its source links.')
            return redirect(url_for('refinement.proposal', program_id=program_id, proposal_id=saved['id']))
        except ValueError:
            error = DomainError('invalid_revision', 'Reload to obtain the current proposal revision.', 422)
        except DomainError as problem:
            error = problem
    return render_template('refinement/proposal.html', **context, values=values, proposal_id=proposal_id, expected_revision=revision, responses=service.coverage(get_principal(), program_id), error=error, ai_receipt=request.form.get('ai_receipt') or None), error.status if error else 200


@bp.route('/programs/<program_id>/refinement/coverage', methods=['GET', 'POST'])
@login_required
def coverage(program_id):
    context, values, error = _context(program_id), {}, None
    if request.method == 'POST':
        values = {key: request.form.get(key, '') for key in ('response_id', 'status', 'reason', 'response_revision_id', 'expected_revision')}
        values['proposal_ids'] = request.form.getlist('proposal_ids')
        try:
            if not values['response_revision_id']:
                raise DomainError('invalid_revision', 'Reload the coverage form to obtain its current revision references.', 422)
            expected_revision = int(values['expected_revision'])
            service.set_disposition(get_principal(), values['response_id'], values['status'], values['reason'], values['proposal_ids'], program_id=program_id, expected_revision=expected_revision, expected_response_revision_id=values['response_revision_id'])
            flash('Overall disposition recorded for the current response revision.')
            return redirect(url_for('refinement.coverage', program_id=program_id))
        except DomainError as problem:
            error = problem
        except ValueError:
            error = DomainError('invalid_revision', 'Reload the coverage form to obtain its current revision references.', 422)
    return render_template('refinement/coverage.html', **context, responses=service.coverage(get_principal(), program_id), proposals=service.list_proposals(get_principal(), program_id), states=service.STATES, values=values, error=error), error.status if error else 200


@bp.route('/programs/<program_id>/refinement/reviews', methods=['GET', 'POST'])
@login_required
def reviews(program_id):
    context, values, created, error = _context(program_id), [], None, None
    if request.method == 'POST':
        values = request.form.getlist('document_keys')
        try:
            created = service.create_review_draft(get_principal(), program_id, values)
        except DomainError as problem:
            error = problem
    return render_template('refinement/review.html', **context, public=False, framework=load_framework(), reviews=service.list_review_drafts(get_principal(), program_id), created=created, values=values, error=error), error.status if error else 200


@bp.post('/programs/<program_id>/refinement/reviews/<review_id>/revoke')
@login_required
def revoke(program_id, review_id):
    _context(program_id)
    if review_id not in {row['id'] for row in service.list_review_drafts(get_principal(), program_id)}:
        raise DomainError('forbidden', 'Choose a review draft in this program.', 403)
    service.revoke_review_draft(get_principal(), review_id)
    flash('Review link revoked.')
    return redirect(url_for('refinement.reviews', program_id=program_id))


@bp.get('/reviews/<token>')
def public_review(token):
    # Account/invitation cookies cannot expand this capability's projection.
    try:
        draft = service.read_review_draft(token)
    except DomainError as error:
        return render_template('refinement/review.html', public=True, draft=None, error=error, principal=Principal(None, None, None)), error.status
    return render_template('refinement/review.html', public=True, draft=draft, error=None, principal=Principal(None, None, None))
