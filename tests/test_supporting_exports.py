from io import BytesIO
from zipfile import ZipFile
import pytest
from PIL import Image
from onpf.errors import DomainError
from test_bridge_cafe_boundaries import material


def test_local_image_is_embedded_in_editable_odt(owner,program):
    from onpf.additional_documents.service import save_additional,public_item
    from onpf.additional_documents.files import document_file
    stream=BytesIO()
    Image.new('RGB',(60,40),'green').save(stream,format='PNG')
    payload=material()
    payload['structured']['blocks']=[{'type':'image','asset':'attachment','text':'Local fictional image'}]
    item=save_additional(owner,program['id'],payload,program['revision'],upload=stream.getvalue(),filename='fictional.png')
    with ZipFile(BytesIO(document_file(public_item(item),'odt')[0])) as output:
        images=[name for name in output.namelist() if name.startswith('Pictures/')]
        assert len(images)==1
        assert output.read(images[0])==stream.getvalue()
        assert b'draw:image' in output.read('content.xml')


def test_invalid_local_image_is_rejected_before_storage(owner,program):
    from onpf.additional_documents.service import save_additional
    payload=material()
    payload['structured']['blocks']=[{'type':'image','asset':'attachment','text':'Invalid image'}]
    with pytest.raises(DomainError) as error:
        save_additional(owner,program['id'],payload,program['revision'],upload=b'\x89PNG\r\n\x1a\ninvalid',filename='bad.png')
    assert error.value.code=='invalid_image'


def test_system_limits_can_increase_defaults_and_narrow_scopes_tighten(owner,program):
    from onpf.drafting.usage import set_limits,limits
    set_limits(owner,'system','',{'batch_items':'40'})
    assert limits(owner,program['id'])['batch_items']==40
    set_limits(owner,'project',program['id'],{'batch_items':'30'})
    assert limits(owner,program['id'])['batch_items']==30
    set_limits(owner,'user',owner.user_id,{'batch_items':'10'})
    assert limits(owner,program['id'])['batch_items']==10


def test_verified_zero_price_is_supported_under_a_cap(app,owner,program):
    from onpf.drafting.usage import set_limits,reserve
    app.config['AI_DRAFTING_ENABLED']=True
    set_limits(owner,'system','',{'input_micro_per_1000':'0','output_micro_per_1000':'0',
                                 'priced_model':'onpf-drafting','daily_budget_micro':'1'})
    assert reserve(owner,program['id'],100,100,100)
