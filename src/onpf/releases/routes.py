"""Review exact frozen content and explicitly record owner approval."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from onpf.auth.service import get_principal, login_required
from onpf.errors import DomainError
from onpf.programs.service import get_program
from onpf.permissions.service import permission_readiness, review_required
from onpf.releases import service
from onpf.inquiries.service import clarification_overview

bp = Blueprint('releases', __name__)


@bp.after_request
def protect_pages(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def _index(program_id, values=None, error=None):
    actor = get_principal()
    program = get_program(actor, program_id)
    return render_template('releases/candidate.html', program=program, candidate=None,
                           candidates=service.list_candidates(actor, program_id),
                           releases=service.list_releases(actor, program_id),
                           is_owner=actor.user_id in program['owner_ids'],
                           clarification_rounds=clarification_overview(actor, program_id),
                           permission_readiness=permission_readiness(program_id), permission_review_required=review_required(program), lifecycle_warnings=service.lifecycle_warnings(program),
                           values=values or {}, error=error), error.status if error else 200


@bp.route('/programs/<program_id>/releases', methods=['GET', 'POST'])
@login_required
def index(program_id):
    if request.method == 'GET':
        return _index(program_id)
    values = {key: request.form.get(key, '') for key in ('expected_revision', 'change_notes', 'permission_reviewed', 'clarifications_reviewed')}
    try:
        try:
            revision = int(values['expected_revision'])
        except ValueError:
            raise DomainError('invalid_revision', 'Reload the form to obtain its revision.', 422)
        candidate = service.prepare_candidate(get_principal(), program_id, revision, change_notes=values['change_notes'], permission_reviewed=values['permission_reviewed'] == 'yes', clarifications_reviewed=values['clarifications_reviewed'] == 'yes')
    except DomainError as error:
        return _index(program_id, values, error)
    return redirect(url_for('releases.candidate', candidate_id=candidate['id']))


def _candidate(candidate_id, error=None):
    actor = get_principal()
    candidate = service.get_candidate(actor, candidate_id)
    program = get_program(actor, candidate['program_id'])
    return render_template('releases/candidate.html', candidate=candidate, program=program,
                           is_owner=actor.user_id in program['owner_ids'] and actor.user_id in candidate['owner_ids'],
                           actor_id=actor.user_id, permission_readiness=permission_readiness(program['id']), permission_review_required=review_required(program), lifecycle_warnings=service.lifecycle_warnings(program), error=error), error.status if error else 200


@bp.get('/candidates/<candidate_id>')
@login_required
def candidate(candidate_id):
    return _candidate(candidate_id)


@bp.post('/candidates/<candidate_id>/approve')
@login_required
def approve(candidate_id):
    try:
        result = service.approve_candidate(get_principal(), candidate_id, permission_reviewed=request.form.get('permission_reviewed') == 'yes')
    except DomainError as error:
        return _candidate(candidate_id, error)
    if result['state'] == 'released':
        return redirect(url_for('releases.detail', release_id=result['release_id']))
    flash('Your explicit approval is recorded. Other fixed owners must approve this same candidate.')
    return redirect(url_for('releases.candidate', candidate_id=candidate_id))


@bp.get('/releases/<release_id>')
@login_required
def detail(release_id):
    actor = get_principal()
    release = service.get_release(actor, release_id)
    program = get_program(actor, release['program_id'])
    candidate = service.get_candidate(actor, release['candidate_id'])
    return render_template('releases/detail.html', release=release, program=program,
                           is_owner=actor.user_id in program['owner_ids'], pending_input_count=candidate['pending_input_count'])


@bp.post('/releases/<release_id>/material')
@login_required
def share_material(release_id):
    release = service.get_release(get_principal(), release_id)
    version = service.material_from_release(get_principal(), release_id)
    flash(f"Editable release documents are available as shared material version {version['version_number']}.")
    return redirect(url_for('materials.library', program_id=release['program_id']))
