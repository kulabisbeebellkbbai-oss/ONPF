"""Bounded structured project documentation and template-derived documents."""
import json
import re
from onpf.auth.service import require_role
from onpf.db import get_db,transaction
from onpf.errors import DomainError
from onpf.drafting.guards import outstanding
from onpf.drafting.usage import limits

BLOCKS={'heading','paragraph','list','table','response_space','checkbox','image'}
LAYOUTS={'letter','a4','card','poster'}
FIELDS=re.compile(r'\{\{([A-Za-z][A-Za-z0-9_]{0,39})\}\}')


def validate(value):
    if not isinstance(value,dict) or set(value)-{'version','layout','brief','purpose','audience','blocks'} or value.get('version')!=1 or value.get('layout') not in LAYOUTS:
        raise DomainError('invalid_structure','Choose a supported document layout and ordered content blocks.',422)
    blocks=value.get('blocks')
    if not isinstance(blocks,list) or not 1<=len(blocks)<=100:
        raise DomainError('invalid_structure','Use one to 100 content blocks.',422)
    for key in ('brief','purpose','audience'):
        if not isinstance(value.get(key,''),str) or len(value.get(key,''))>2000:
            raise DomainError('invalid_structure','Keep the brief, purpose and audience within 2000 characters each.',422)
    for block in blocks:
        if not isinstance(block,dict) or block.get('type') not in BLOCKS or set(block)-{'type','text','label','items','rows','asset','review_guard'}:
            raise DomainError('invalid_structure','Use headings, paragraphs, lists, tables, response spaces, checkboxes or approved local images.',422)
        for key in ('text','label','review_guard'):
            if not isinstance(block.get(key,''),str) or len(block.get(key,''))>20000:
                raise DomainError('invalid_structure','Keep block text within 20000 characters.',422)
        if block['type']=='list':
            items=block.get('items')
            if not isinstance(items,list) or len(items)>100 or any(not isinstance(s,str) or len(s)>2000 for s in items):
                raise DomainError('invalid_structure','Use at most 100 list entries of 2000 characters each.',422)
        if block['type']=='table':
            rows=block.get('rows')
            if not isinstance(rows,list) or not 1<=len(rows)<=100 or any(not isinstance(r,list) or not 1<=len(r)<=10 or any(not isinstance(s,str) or len(s)>2000 for s in r) for r in rows):
                raise DomainError('invalid_structure','Use a table of at most 100 rows and 10 columns.',422)
        if block['type']=='image' and block.get('asset')!='attachment':
            raise DomainError('invalid_structure','Images must use this document’s approved local image attachment.',422)
    encoded=json.dumps(value,ensure_ascii=False)
    from onpf.exports.odt import validate_literal_text
    validate_literal_text(value)
    if len(encoded.encode('utf8'))>262144:
        raise DomainError('invalid_structure','The material content exceeds 256 KiB.',422)
    return json.loads(encoded)


def sections(value):
    result=[]
    for index,block in enumerate(value['blocks']):
        kind=block['type']; label=block.get('label','')
        text=block.get('text','')
        if kind=='heading':
            label=text; text=''
        elif kind=='list':
            text='\n'.join('• '+s for s in block['items'])
        elif kind=='table':
            text='\n'.join(' | '.join(row) for row in block['rows'])
        elif kind=='response_space':
            text='\n____________________________\n____________________________\n____________________________'
        elif kind=='checkbox':
            text='☐ '+label; label=''
        elif kind=='image':
            text=block.get('text','Image attachment')
        if block.get('review_guard'):
            text=block['review_guard']+'\n\n'+text
        result.append({'key':f'block_{index}','label':label,'text':text,'block':block})
    return result


def fields(value):
    return sorted(set(FIELDS.findall(json.dumps(value,ensure_ascii=False))))


def history(actor,program_id,document_id):
    from onpf.additional_documents.service import get_additional
    get_additional(actor,program_id,document_id)
    records=[]
    for row in get_db().execute('SELECT * FROM additional_document_revisions WHERE document_id=? ORDER BY revision',(document_id,)):
        snapshot=json.loads(row['snapshot_json'])
        content=json.loads(snapshot['structured_json']) if snapshot.get('structured_json') else None
        displayed=sections(content) if content else [{'label':key.replace('_',' ').title(),'text':text} for key,text in json.loads(snapshot['sections_json']).items()]
        records.append({**dict(row),'snapshot':snapshot,'sections':displayed})
    return records


def derive(actor,program_id,template_id,revision,values):
    from onpf.additional_documents.service import get_additional,save_additional
    from onpf.programs.service import get_program
    with transaction():
        require_role(actor,program_id,{'owner','facilitator'})
        template=get_additional(actor,program_id,template_id)
        if template['revision']!=revision:
            raise DomainError('stale_revision','The template changed. Review its current version before populating items.',409)
        if not template['is_template'] or not template['reviewed'] or outstanding(template['structured']):
            raise DomainError('template_review_required','Review the reusable template before creating related items.',422)
        if not isinstance(values,list) or not 1<=len(values)<=limits(actor,program_id)['batch_items']:
            raise DomainError('material_batch_limit','Choose an explicit bounded number of related items (default maximum 20).',422)
        names=fields(template['structured'])
        output=[]
        for index,row in enumerate(values):
            if not isinstance(row,dict) or set(row)!=set(names) or any(not isinstance(v,str) or len(v)>2000 for v in row.values()):
                raise DomainError('invalid_template_fields','Populate every named field for each requested item.',422)
            def replace(value):
                if isinstance(value,str):
                    return FIELDS.sub(lambda match:row[match.group(1)] or 'Unknown: '+match.group(1),value)
                if isinstance(value,list):
                    return [replace(v) for v in value]
                if isinstance(value,dict):
                    return {k:replace(v) for k,v in value.items()}
                return value
            payload={key:template[key] for key in ('ownership_basis','permission_basis','license','notices')}
            payload.update(template_key='supporting',title=f'{template["title"]} — item {index+1}',sections={},
                           structured=replace(template['structured']),is_template=False,reviewed=False,
                           template_source_id=template_id,template_source_revision=revision)
            import base64
            output.append(save_additional(actor,program_id,payload,get_program(actor,program_id)['revision'],
                upload=base64.b64decode(template['upload_data']) if template['upload_data'] else None,
                filename=template['upload_name']))
        return output
