"""Human-editable block controls; no executable markup or online answer collection."""
from flask import request,render_template,redirect,url_for,flash
from onpf.errors import DomainError
from onpf.auth.service import get_principal
from onpf.additional_documents.supporting import validate,fields


def blank_structure():
    return {'version':1,'layout':'letter','brief':'','purpose':'','audience':'','blocks':[{'type':'paragraph','text':''}]}


def form_structure(form,prefix='',baseline=None):
    if prefix+'block_count' not in form:
        return baseline or blank_structure()
    try:
        count=int(form[prefix+'block_count'])
    except ValueError:
        raise DomainError('invalid_structure','Use the labeled content blocks.',422)
    if not 1<=count<=100:
        raise DomainError('invalid_structure','Use one to 100 content blocks.',422)
    value={'version':1,'layout':form.get(prefix+'layout','letter'),
           **{key:form.get(prefix+key,'') for key in ('brief','purpose','audience')},'blocks':[]}
    for index in range(count):
        stem=prefix+f'block_{index}_'
        kind=form.get(stem+'type','paragraph')
        block={'type':kind,'text':form.get(stem+'text',''),'label':form.get(stem+'label','')}
        if form.get(stem+'review_guard'):
            block['review_guard']=form[stem+'review_guard']
        if kind=='list':
            block['items']=form.get(stem+'items','').splitlines()
        if kind=='table':
            block['rows']=[line.split('|') for line in form.get(stem+'rows','').splitlines()]
        if kind=='image':
            block['asset']='attachment'
        value['blocks'].append(block)
    return validate(value)


def payload(item=None):
    item=item or {}
    return {'template_key':'supporting','title':request.form.get('title',''),'sections':{},
      'structured':form_structure(request.form),
      **{key:request.form.get(key,item.get(key,default)) for key,default in
         [('ownership_basis','own_work'),('permission_basis','Created by this project'),('license','MIT-0'),('notices','')]},
      'include_in_public':request.form.get('include_in_public')=='yes',
      'is_template':request.form.get('is_template')=='yes','reviewed':request.form.get('reviewed')=='yes',
      'template_source_id':item.get('template_source_id'),'template_source_revision':item.get('template_source_revision')}


def render_editor(program,item=None):
    from onpf.additional_documents.service import save_additional
    values=item or {'title':'','structured':blank_structure(),'ownership_basis':'own_work',
                    'permission_basis':'Created by this project','license':'MIT-0','notices':''}
    error=None
    if request.method=='POST':
        try:
            values=payload(item)
            uploaded=request.files.get('attachment')
            from onpf.additional_documents.service import MAX_UPLOAD
            saved=save_additional(get_principal(),program['id'],values,int(request.form.get('expected_revision','')),
                item['id'] if item else None,upload=uploaded.stream.read(MAX_UPLOAD+1) if uploaded and uploaded.filename else None,
                filename=uploaded.filename if uploaded and uploaded.filename else None,
                remove_upload=request.form.get('remove_upload')=='yes',ai_receipt=request.form.get('ai_receipt') or None)
            flash('Supporting material saved. Public selection and publication remain separate.')
            return redirect(url_for('additional_documents.detail',program_id=program['id'],document_id=saved['id']))
        except ValueError:
            error=DomainError('invalid_revision','Reload the current save revision.',422)
        except DomainError as problem:
            error=problem
    return render_template('additional_documents/supporting.html',program=program,item=item,values=values,error=error,
        expected_revision=request.form.get('expected_revision',program['revision']),
        can_edit=program['access_role'] in {'owner','facilitator'},template_fields=fields(values['structured'])),error.status if error else 200
