"""Ephemeral browser drafting. Generate and Apply are read-only POST actions."""
import json

from flask import Blueprint, current_app, render_template, request

from onpf.auth.service import get_principal, login_required, require_role
from onpf.drafting import contracts, evidence, provenance, service
from onpf.drafting.config import _gateway_url
from onpf.errors import DomainError
from onpf.inquiries import service as inquiries
from onpf.programs.framework import load_framework
from onpf.programs.service import get_program
from onpf.refinement import service as refinement

bp = Blueprint('drafting', __name__)
STATE_BYTES = 262144


@bp.route('/programs/<program_id>/clarifications/<clarification_id>',methods=['GET','POST'])
@login_required
def organizer_statement(program_id,clarification_id):
    from onpf.drafting import organizer
    from onpf.db import get_db
    program=get_program(get_principal(),program_id)
    history=organizer.history(get_principal(),program_id,clarification_id)
    if not history:
        raise DomainError('not_found','This clarification is unavailable.',404)
    if request.method=='POST':
        revision=request.form.get('expected_revision','')
        if not revision.isdigit():
            raise DomainError('invalid_revision','Review the current statement revision.',422)
        organizer.save(get_principal(),program_id,request.form.get('text',''),history[-1]['context'],clarification_id,int(revision))
        history=organizer.history(get_principal(),program_id,clarification_id)
    for record in history:
        row=get_db().execute('SELECT username FROM users WHERE id=?',(record['author_id'],)).fetchone()
        record['author_name']=row['username'] if row else 'Historical author'
    return render_template('drafting/clarification.html',program=program,history=history,can_edit=program['access_role'] in {'owner','facilitator'})


def _invalid(message='Use the labeled drafting controls and reload malformed state.'):
    raise DomainError('invalid_drafting_form', message, 422)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def json_state(value, default=None, maximum=STATE_BYTES):
    if value is None:
        return default
    try:
        if len(value.encode('utf8')) > maximum:
            _invalid()
        return json.loads(value, object_pairs_hook=_unique,
                          parse_constant=lambda _: _invalid())
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _invalid()


def _count(form, key, maximum):
    try:
        value = int(form.get(key, '0'))
    except ValueError:
        _invalid()
    if not 0 <= value <= maximum:
        _invalid()
    return value


def native_fields(target, form, prefix='', baseline=None, *, retain_empty_questions=False):
    """Read labeled controls; absence of an editor leaves the hidden baseline intact."""
    baseline = dict(baseline or {})
    if prefix and form.get(prefix + 'editor') != 'yes' and not any(key.startswith(prefix) for key in form):
        return baseline
    kind = target['kind']
    def text(key, default=''):
        return form.get(prefix + key, default)
    if kind == 'questions':
        if prefix + 'question_count' not in form:
            return baseline
        questions = []
        for index in range(_count(form, prefix + 'question_count', 20)):
            stem = prefix + f'q{index}_'
            try:
                stage = int(form.get(stem + 'stage', '1'))
            except ValueError:
                _invalid('Choose a development stage for each question.')
            question = {'text': form.get(stem + 'text', ''), 'stage': stage,
                        'document_key': form.get(stem + 'document_key', 'overview'),
                        'answer_type': 'text'}
            for key in ('group_label', 'why', 'source_type', 'source_id'):
                if stem + key in form or key in (baseline.get('questions') or [{}])[min(index, len(baseline.get('questions') or [{}])-1)]:
                    question[key] = form.get(stem + key, (baseline.get('questions') or [{}])[min(index, len(baseline.get('questions') or [{}])-1)].get(key, ''))
            reason = form.get(stem + 'reason', '')
            if stem + 'reason' in form:
                question['why'] = reason
            handles = form.getlist(stem + 'source_handles')
            if reason:
                question['reason'] = reason
            if handles:
                question['source_handles'] = handles
            if retain_empty_questions or question['text'].strip() or reason or handles:
                questions.append(question)
        return {'questions': questions}
    if kind == 'review':
        return {}
    if kind == 'supporting':
        from onpf.additional_documents.editor import form_structure
        return {'title':text('title',baseline.get('title','')),
                'structured':form_structure(form,prefix,baseline.get('structured'))}
    if kind in {'proposal', 'decision'}:
        keys = ('title', 'text', 'theme') if kind == 'proposal' else ('outcome', 'rationale')
        result = {key: text(key, baseline.get(key, '')) for key in keys}
        links = ('response_ids',) if kind == 'proposal' else ('response_ids', 'proposal_ids')
        for key in links:
            result[key] = form.getlist(prefix + key) if form.get(prefix + 'editor') == 'yes' or prefix + key in form else baseline.get(key, [])
        if kind == 'decision':
            result['supersedes_id'] = text('supersedes_id', baseline.get('supersedes_id') or '') or None
        return result
    structure = load_framework()['documents'][target['document_key']]
    result = {'sections': {section['key']: text('section_' + section['key'], baseline.get('sections', {}).get(section['key'], ''))
                           for section in structure['sections']},
              'decision_ids': form.getlist(prefix + 'decision_ids') if form.get(prefix + 'editor') == 'yes' or prefix + 'decision_ids' in form else baseline.get('decision_ids', [])}
    if target['document_key'] == 'budget':
        indexes = {key[len(prefix + 'item_'):] for key in form if key.startswith(prefix + 'item_')}
        if len(indexes) > 500 or any(not index.isdigit() or len(index) > 3 for index in indexes):
            _invalid()
        rows = []
        for index in sorted(indexes, key=int):
            row = {key: text(f'{key}_{index}') for key in ('item', 'quantity', 'unit_cost', 'cost_status', 'notes')}
            if any(row[key].strip() for key in ('item', 'quantity', 'unit_cost', 'notes')):
                row['unit_cost'] = row['unit_cost'] or None
                rows.append(row)
        result['rows'] = rows if indexes else baseline.get('rows', [])
    return result


def _target():
    if request.method == 'POST' and 'target' in request.form:
        target = json_state(request.form['target'], maximum=2048)
        if request.form.get('generation_mode') in {'initial','assist','regenerate'} and target.get('kind') == 'document':
            target['mode'] = request.form['generation_mode']
        return evidence.validate_target(target)
    values = request.args if request.method == 'GET' else request.form
    target = {'kind': values.get('draft_kind', values.get('kind', 'questions'))}
    for key in ('document_key', 'record_key'):
        value = values.get('draft_' + key, values.get(key))
        if value:
            target[key] = value
    if values.get('draft_stage'):
        try:
            target['stage'] = int(values['draft_stage'])
        except ValueError:
            _invalid()
    return evidence.validate_target(target)


def _authorize(program_id, target):
    require_role(get_principal(), program_id, {'owner'} if target['kind'] == 'decision' else {'owner', 'facilitator'})


def _initial_fields(program, target):
    kind, key = target['kind'], target.get('record_key')
    if kind == 'document':
        return {**program['documents'][target['document_key']],
                'decision_ids': program['document_decision_ids'].get(target['document_key'], [])}
    if kind == 'supporting':
        from onpf.additional_documents.service import get_additional
        from onpf.additional_documents.editor import blank_structure
        item=get_additional(get_principal(),program['id'],key) if key else {}
        return {'title':item.get('title',''),'structured':item.get('structured') or blank_structure()}
    if key and kind == 'proposal':
        proposal = refinement.get_proposal(get_principal(), program['id'], key)
        return {name: proposal[name] for name in ('title', 'text', 'theme', 'response_ids')}
    if key and kind == 'decision':
        decision = next(d for d in refinement.list_decisions(get_principal(), program['id']) if d['id'] == key)
        return {**{name: decision[name] for name in ('outcome', 'rationale', 'proposal_ids', 'response_ids')},
                'supersedes_id': key}
    if key and kind == 'questions':
        question = next(q for q in inquiries.question_catalog(get_principal(), program['id']) if q['id'] == key)
        item={name:question[name] for name in ('text','stage','document_key','answer_type')}
        item.update({name:question.get(name,'') for name in ('why','group_label','source_type','source_id')})
        item['reason']=question.get('reason') or question.get('why','')
        item['source_handles']=question.get('source_handles',[])
        if not item['source_handles'] and item['source_type'] and item['source_id']:
            source_key=item['source_id'].split(':',1)[0] if item['source_type']=='document' else item['source_id']
            handle=f'{item["source_type"]}:{program["id"]}:{source_key}'
            if any(s['handle']==handle for s in evidence.catalog(get_principal(),program['id'],target)):
                item['source_handles']=[handle]
        return {'questions':[item]}
    return {}


def _current(program, target):
    if 'current_fields' in request.form:
        fields = json_state(request.form['current_fields'], {}, maximum=STATE_BYTES)
        if not isinstance(fields, dict):
            _invalid()
        _renderable_fields(target, fields)
        fields = native_fields(target, request.form, 'current_', fields)
        _renderable_fields(target, fields)
        return fields
    fields = _initial_fields(program, target)
    if request.method == 'POST':
        if target['kind'] == 'questions' and 'text' in request.form:
            try:
                question = {**(fields.get('questions') or [{}])[0], 'text': request.form['text'], 'stage': int(request.form.get('stage', '1')),
                            'document_key': request.form.get('document_key', 'overview'), 'answer_type': 'text'}
            except ValueError:
                _invalid()
            fields = {'questions': [question]}
            question['reason'] = request.form.get('why', question.get('reason', ''))
            question['why'] = question['reason']
            question['group_label'] = request.form.get('group_label', question.get('group_label', ''))
            source = request.form.get('source', '').split('|', 1)
            if len(source) == 2:
                question['source_type'], question['source_id'] = source
                key = source[1].split(':', 1)[0] if source[0] == 'document' else source[1]
                handle = f'{source[0]}:{program["id"]}:{key}'
                question['source_handles'] = [handle] if any(s['handle'] == handle for s in evidence.catalog(get_principal(), program['id'], target)) else []
        else:
            fields = native_fields(target, request.form, baseline=fields)
    _renderable_fields(target, fields)
    return fields


def _renderable_fields(target, fields):
    """Check structure before templating, retaining valid text even if links go stale."""
    try:
        if not isinstance(fields, dict) or len(evidence._encode(fields).encode('utf8')) > STATE_BYTES:
            _invalid()
        checked = dict(fields)
        # Link existence belongs to generation selection and ordinary Save.
        for key in ('response_ids', 'proposal_ids', 'decision_ids'):
            if key in checked:
                if not isinstance(checked[key], list) or len(checked[key]) > 100 or any(not isinstance(x, str) or len(x) > 250 for x in checked[key]):
                    _invalid()
                checked[key] = []
        if 'supersedes_id' in checked:
            if checked['supersedes_id'] is not None and (not isinstance(checked['supersedes_id'], str) or len(checked['supersedes_id']) > 100):
                _invalid()
            checked['supersedes_id'] = None
        if target['kind'] == 'questions':
            if not isinstance(checked.get('questions', []), list):
                _invalid()
            questions = []
            for question in checked.get('questions', []):
                if not isinstance(question, dict):
                    _invalid()
                question = dict(question)
                if 'source_handles' in question:
                    evidence._handles(question['source_handles'])
                    question['source_handles'] = []
                questions.append(question)
            checked['questions'] = questions
        contracts._fields(target, checked, set(), current=True)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, DomainError):
        _invalid()


def _destination_state(program, target):
    state = json_state(request.form.get('destination_state'), {})
    if not isinstance(state, dict) or set(state) - {'depends_on','material'}:
        _invalid()
    if target['kind']=='supporting':
        if 'material' not in state:
            from onpf.additional_documents.service import get_additional
            item=get_additional(get_principal(),program['id'],target['record_key']) if target.get('record_key') else {}
            state['material']={key:item.get(key,default) for key,default in
                [('ownership_basis','own_work'),('permission_basis','Created by this project'),('license','MIT-0'),('notices',''),
                 ('is_template',False),('reviewed',False),('include_in_public',False),
                 ('template_source_id',None),('template_source_revision',None)]}
        if request.form.get('draft_kind')=='supporting':
            for key in ('ownership_basis','permission_basis','license','notices'):
                state['material'][key]=request.form.get(key,state['material'].get(key,''))
            for key in ('is_template','reviewed','include_in_public'):
                state['material'][key]=request.form.get(key)=='yes'
        material=state['material']
        if (not isinstance(material,dict) or set(material)-{'ownership_basis','permission_basis','license','notices','is_template','reviewed','include_in_public','template_source_id','template_source_revision'}
            or material.get('ownership_basis') not in {'own_work','third_party'}
            or any(not isinstance(material.get(key,''),str) or len(material.get(key,''))>maximum for key,maximum in [('permission_basis',5000),('license',5000),('notices',20000)])
            or any(type(material.get(key,False)) is not bool for key in ('is_template','reviewed','include_in_public'))):
            _invalid('Retain valid material rights and template settings before drafting.')
    elif 'material' in state:
        _invalid()
    if 'destination_state' not in request.form and target['kind'] == 'questions' and target.get('record_key'):
        question = next(q for q in inquiries.question_catalog(get_principal(), program['id']) if q['id'] == target['record_key'])
        state['depends_on'] = question.get('depends_on', [])
    if target['kind'] == 'questions' and request.form.get('draft_kind') == 'questions':
        state['depends_on'] = [{'field': field['key'], 'equals': request.form.get(f'equals_{field["key"]}', '')}
            for field in inquiries.decision_fields(get_principal(), program['id']) if request.form.get(f'dep_{field["key"]}')]
    if 'depends_on' in state and (not isinstance(state['depends_on'], list) or len(state['depends_on']) > 100):
        _invalid()
    for dependency in state.get('depends_on', []):
        if (not isinstance(dependency, dict) or set(dependency) != {'field','equals'}
                or not isinstance(dependency['field'], str) or len(dependency['field']) > 80
                or not isinstance(dependency['equals'], str) or len(dependency['equals']) > 2000):
            _invalid()
    return state


def _verified_result(program_id, target):
    result = json_state(request.form.get('result'), {})
    if not isinstance(result, dict) or set(result) != {'fields', 'sources', 'uncertainties', 'questions', 'receipt'}:
        _invalid('Generate a suggestion before applying it.')
    payload = provenance.validate_receipt(get_principal(), program_id, target, result['receipt'])
    original = {key: value for key, value in result.items() if key != 'receipt'}
    if evidence._hash(original) != payload['output_hash']:
        _invalid('The original suggestion state changed. Generate again.')
    return result


def _panel(program, target, fields, result=None, error=None, edited=None):
    try:
        sources = evidence.catalog(get_principal(), program['id'], target)
    except DomainError as problem:
        if problem.code != 'invalid_target':
            raise
        # The target may have been removed while the gateway was working.
        # Authorized editors retain their unsaved form while choosing clean sources.
        sources = evidence.catalog(get_principal(), program['id'], {'kind':'questions'})
    if request.form.get('action') == 'followups' and error is None:
        selected = []
    elif request.method == 'POST' and (request.form.get('selection_reviewed') == 'yes' or request.form.get('action') in {'generate','edit','clarify','followups','refresh_sources'}):
        selected = request.form.getlist('handles')
    else:
        selected = [s['handle'] for s in sources if s['default_selected']][:100]
        selected = list(dict.fromkeys(selected + evidence.inherited_handles(sources, fields)))
    revision = get_program(get_principal(), program['id'])['revision']
    if target['kind'] == 'proposal' and target.get('record_key'):
        revision = refinement.get_proposal(get_principal(), program['id'], target['record_key'])['revision']
    expected = request.form.get('expected_revision', revision)
    if result is not None and request.form.get('action') == 'generate' and request.form.get('refresh_revision') == 'yes':
        expected = revision
    destination_notice = 'Your organization’s AI drafting service.'
    retained = None
    if result is None and target['kind'] != 'review' and any(key.startswith('suggested_') for key in request.form):
        try:
            retained = native_fields(target, request.form, 'suggested_')
            _renderable_fields(target, retained)
        except DomainError:
            retained = None
    from onpf.administration.service import effective
    from onpf.drafting.usage import limits,estimate
    resource_limits=limits(get_principal(),program['id'])
    charge=estimate(get_principal(),program['id'],resource_limits['request_bytes'],resource_limits['output_tokens'])
    from onpf.drafting import selection
    evidence_state=request.form.get('evidence_state')
    if not evidence_state or request.form.get('action') in {'refresh_sources','clarify','followups'} or request.form.get('refresh_revision')=='yes':
        evidence_state=selection.issue(get_principal(),program['id'],target,sources)
    try:
        reviewed=selection.load(get_principal(),program['id'],target,evidence_state)
        fingerprints={source['handle']:evidence._hash(evidence._manifest(source)) for source in sources}
        changed_handles=[handle for handle in selected if reviewed.get(handle)!=fingerprints.get(handle)]
    except DomainError:
        changed_handles=selected
    return render_template('drafting/panel.html', program=program, target=target,
        current_fields=fields, result=result, edited_fields=edited or (result['fields'] if result else {}), retained_fields=retained,
        sources=sources, selected_handles=selected, unavailable_handles=[handle for handle in selected if handle not in {s['handle'] for s in sources}], framework=load_framework(),
        hint_capped=sum(s['default_selected'] for s in sources) > 100,
        instructions=request.form.get('instructions', ''), error=error,
        expected_revision=expected, current_revision=revision,
        evidence_state=evidence_state,
        changed_handles=changed_handles,
        destination_state=_destination_state(program, target),
        ai_enabled=effective(get_principal(),program['id'])['enabled'],
        ai_policy=effective(get_principal(),program['id']),maximum_charge=charge,
        gateway_destination=destination_notice), error.status if error else 200


@bp.after_request
def protect(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@bp.app_context_processor
def drafting_helpers():
    def ready(program_id):
        return any(row['redaction'] is None for row in refinement.coverage(get_principal(), program_id))
    return {'drafting_ready': ready, 'drafting_origins': lambda program_id, target:
            provenance.origins(get_principal(), program_id, target)}


@bp.route('/programs/<program_id>/drafting', methods=['GET', 'POST'])
@login_required
def panel(program_id):
    target = _target()
    _authorize(program_id, target)
    program = get_program(get_principal(), program_id)
    if request.method == 'POST' and request.form.get('action') == 'clarify':
        from onpf.drafting.organizer import save
        save(get_principal(), program_id, request.form.get('clarification',''), target,
             request.form.get('clarification_id') or None,
             int(request.form['clarification_revision']) if request.form.get('clarification_revision','').isdigit() else None)
    evidence.catalog(get_principal(), program_id, target)
    fields = _current(program, target)
    result, error = None, None
    try:
        action = request.form.get('action', 'preview')
        if request.method == 'POST' and action == 'generate':
            from onpf.drafting import selection
            sources=evidence.catalog(get_principal(),program_id,target)
            token=request.form.get('evidence_state')
            if request.form.get('refresh_revision')=='yes':
                token=selection.issue(get_principal(),program_id,target,sources)
            reviewed=selection.verify(get_principal(),program_id,target,token,request.form.getlist('handles'),sources)
            result = service.generate(get_principal(), program_id, target, request.form.getlist('handles'),
                                      request.form.get('instructions', ''), fields,reviewed_sources=reviewed)
        elif request.method == 'POST' and action == 'followups':
            original = _verified_result(program_id, target)
            target = {'kind': 'questions'}
            # Follow-ups are retained wording, not reusable review authority.
            # In particular lifecycle manifests cannot enter saved question
            # contexts or archives. Require explicit fresh support selection.
            fields = {'questions': [{**q, 'answer_type': 'text', 'source_handles': []}
                                     for q in original['questions']]}
        elif request.method == 'POST' and action == 'edit' and request.form.get('result'):
            result = _verified_result(program_id, target)
        elif action not in {'preview', 'edit', 'clarify','refresh_sources'}:
            _invalid()
    except DomainError as problem:
        if problem.status == 403:
            raise
        error = problem
    edited = native_fields(target, request.form, 'suggested_', result['fields']) if result and action == 'edit' else None
    return _panel(program, target, fields, result, error, edited)


@bp.post('/programs/<program_id>/drafting/apply')
@login_required
def apply(program_id):
    target = _target()
    _authorize(program_id, target)
    program = get_program(get_principal(), program_id)
    fields = _current(program, target)
    result, edited = None, None
    try:
        result = _verified_result(program_id, target)
        if target['kind'] == 'review':
            _invalid('Review findings have no mutable destination form.')
        edited = native_fields(target, request.form, 'suggested_', result['fields'])
        choices = request.form.getlist('replace')
        if not choices or len(set(choices)) != len(choices) or any(key not in result['fields'] for key in choices):
            _invalid('Choose the fields you deliberately want to replace before applying.')
        merged = {**fields, **{key: edited[key] for key in choices}}
        available = evidence.catalog(get_principal(), program_id, target)
        try:
            checked = contracts._fields(target, merged, {s['handle'] for s in available}, current=True)
        except (ValueError, TypeError, KeyError, DomainError):
            _invalid('Review the labeled edited fields before applying.')
        merged = service._resolve_links(checked, [evidence._manifest(s) for s in available])
        if target['kind'] == 'questions' and merged.get('questions'):
            merged['questions'][0]['depends_on'] = _destination_state(program, target).get('depends_on', [])
        return destination(program, target, merged, result['receipt'], result['sources'],
                           request.form.get('expected_revision', program['revision']))
    except DomainError as problem:
        if problem.status == 403:
            raise
        return _panel(program, target, fields, result, problem, edited)


def destination(program, target, fields, receipt=None, sources=None, revision=None, error=None):
    """Render an unsaved ordinary destination; the domain Save owns all mutations."""
    actor, kind = get_principal(), target['kind']
    context = {'program': program, 'error': error, 'ai_receipt': receipt,
               'expected_revision': revision, 'can_edit': program['access_role'] in {'owner', 'facilitator'}, 'is_owner': actor.user_id in program['owner_ids']}
    if kind == 'questions':
        questions = fields.get('questions', [])
        if target.get('record_key') and len(questions) != 1:
            _invalid('An existing question can be replaced by one reviewed question.')
        if target.get('record_key'):
            questions[0]['id'] = target['record_key']
        return render_template('drafting/questions.html', **context, framework=load_framework(),
                               questions=questions, sources=sources or [],
                               fields=inquiries.decision_fields(actor, program['id'])), error.status if error else 200
    if kind == 'supporting':
        from onpf.additional_documents.service import get_additional
        item=get_additional(actor,program['id'],target['record_key']) if target.get('record_key') else None
        values={**(item or {}),**fields,**_destination_state(program,target)['material']}
        return render_template('additional_documents/supporting.html',**context,values=values,item=item,template_fields=[]),error.status if error else 200
    if kind == 'proposal':
        key = target.get('record_key')
        return render_template('refinement/proposal.html', **context, values=fields, proposal_id=key,
                               responses=refinement.coverage(actor, program['id'])), error.status if error else 200
    if kind == 'decision':
        proposals = refinement.list_proposals(actor, program['id'])
        responses = refinement.coverage(actor, program['id'])
        return render_template('refinement/board.html', **context, values=fields,
            proposals=proposals, decisions=refinement.list_decisions(actor, program['id']),
            responses=responses, proposal_groups=refinement.proposal_evidence(actor, program['id'], [row['id'] for row in proposals]),
            response_labels={row['id']: row['reference'] for row in responses}), error.status if error else 200
    structure = load_framework()['documents'][target['document_key']]
    return render_template('programs/document.html', **context, structure=structure, content=fields,
        budget_rows=fields.get('rows', []) + [{'item':'', 'quantity':'', 'unit_cost':None, 'cost_status':'estimated','notes':''} for _ in range(3)],
        decisions=refinement.list_decisions(actor, program['id']), decision_ids=fields.get('decision_ids', [])), error.status if error else 200
