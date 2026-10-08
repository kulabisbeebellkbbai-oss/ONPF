"""Explicitly fictional training data, isolated from any existing instance."""
import json
import secrets
from pathlib import Path

from onpf.app import create_app
from onpf.auth.models import Principal
from onpf.auth.service import create_user
from onpf.contributions.service import submit_responses, revise_response
from onpf.inquiries.service import issue_batch
from onpf.materials.service import material_from_release, adopt_material
from onpf.programs.service import create_program, get_program, save_document
from onpf.refinement.service import save_proposal, set_disposition, record_decision
from onpf.releases.service import prepare_candidate, approve_candidate


def create_demo(database: Path) -> dict:
    # Inspect the supplied entry before resolve() can follow an existing link,
    # including a dangling link whose target does not yet exist.
    if database.exists() or database.is_symlink():
        raise FileExistsError('Demo requires a new database path and a new companion instance directory.')
    database = database.resolve()
    instance = database.parent / (database.stem + '-instance')
    if database.exists() or database.is_symlink() or instance.exists():
        raise FileExistsError('Demo requires a new database path and a new companion instance directory.')
    database.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation closes the overwrite race before app initialization.
    with database.open('x'):
        pass
    instance.mkdir()
    app = create_app({'DATABASE': str(database), 'INSTANCE_PATH': str(instance)})
    source = json.loads((Path(__file__).parent / 'examples' / 'fictional-group-input.json').read_text(encoding='utf-8'))
    password = secrets.token_urlsafe(24)
    with app.app_context():
        actor = Principal(create_user('fictional-owner', password), None, None)
        art = create_program(actor, {'title': 'FICTIONAL guided art training', 'purpose': source['label'], 'module_keys': ['art']})
        broader = create_program(actor, {'title': 'FICTIONAL creative and growing training', 'purpose': source['label'], 'module_keys': ['art', 'hobby', 'maker', 'gardening']})
        batch = issue_batch(actor, art['id'], {'title': 'FICTIONAL training perspectives', 'question_ids': ['core-1-1', 'core-4-3']})
        submitted = submit_responses(actor, batch['id'], 'fictional-demo-intake', [{**row, 'question_id': batch['questions'][0]['id']} for row in source['responses']])
        revise_response(actor, submitted['response_ids'][0], source['correction'], 'FICTIONAL requested clarification', 1)
        proposal = save_proposal(actor, art['id'], {'title': 'FICTIONAL quiet choice', 'text': 'FICTIONAL offer optional quiet painting with breaks.', 'response_ids': [submitted['response_ids'][0]]}, None)
        for identifier, status in zip(submitted['response_ids'], source['dispositions']):
            set_disposition(actor, identifier, status, 'FICTIONAL training owner explanation; no actual group decision.', [proposal['id']] if status == 'incorporated' else [])
        decision = record_decision(actor, art['id'], {'outcome': 'FICTIONAL optional quiet painting', 'rationale': 'FICTIONAL training decision only.', 'proposal_ids': [proposal['id']], 'response_ids': [submitted['response_ids'][0]]})
        art = save_document(actor, art['id'], 'delivery', {'sections': {'activities': 'FICTIONAL optional quiet painting with breaks. Training example only.'}, 'decision_ids': [decision['id']]}, get_program(actor, art['id'])['revision'])
        candidate = prepare_candidate(actor, art['id'], art['revision'], change_notes='FICTIONAL demonstration approval; no permission to operate.')
        art_release = approve_candidate(actor, candidate['id'])['release_id']
        version = material_from_release(actor, art_release)
        adopt_material(actor, broader['id'], version['id'], broader['revision'])
        candidate = prepare_candidate(actor, broader['id'], get_program(actor, broader['id'])['revision'], permission_reviewed=True)
        broader_release = approve_candidate(actor, candidate['id'], permission_reviewed=True)['release_id']
    return {'instance': instance, 'database': database, 'username': 'fictional-owner', 'password': password, 'releases': [art_release, broader_release]}
