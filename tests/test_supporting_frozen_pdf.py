import time


def test_saved_public_pdf_bytes_do_not_change_with_render_time(owner,program):
    from test_bridge_cafe_boundaries import material
    from onpf.additional_documents.service import save_additional,public_item
    from onpf.additional_documents.files import document_file
    item=public_item(save_additional(owner,program['id'],material(),program['revision']))
    first=document_file(item,'pdf')[0]
    time.sleep(1.1)
    assert document_file(item,'pdf')[0]==first
