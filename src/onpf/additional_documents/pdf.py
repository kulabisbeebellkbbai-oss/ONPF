"""Deterministic bounded PDF output from saved literal supporting content."""
from io import BytesIO
from html import escape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import letter,A4
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image
from onpf.errors import DomainError

SIZES={'letter':letter,'a4':A4,'card':(432,288),'poster':(792,1224)}


def render(item,page_limit=20):
    stream=BytesIO()
    size=SIZES.get((item.get('structured') or {}).get('layout','letter'),letter)
    margin=24 if size==(432,288) else 40
    styles=getSampleStyleSheet()
    def paragraph(text,style='BodyText'):
        return Paragraph(escape(text).replace('\n','<br/>'),styles[style])
    story=[paragraph(item['title'],'Title'),Spacer(1,10)]
    for section in item['sections']:
        if section['label']:
            story.append(paragraph(section['label'],'Heading2'))
        block=section.get('block',{})
        if block.get('review_guard') and block.get('type') in {'table','image','checkbox'}:
            story.append(paragraph(block['review_guard']))
        if block.get('type')=='table':
            rows=[[paragraph(cell) for cell in row] for row in block['rows']]
            width=max(len(row) for row in rows)
            for row in rows:
                row.extend(['']*(width-len(row)))
            table=Table(rows,colWidths=[(size[0]-2*margin)/width]*width)
            table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.grey),('VALIGN',(0,0),(-1,-1),'TOP')]))
            story.append(table)
        elif block.get('type')=='image' and item.get('attachment'):
            from onpf.additional_documents.service import attachment_bytes
            if item['attachment']['mime'] not in {'image/png','image/jpeg'}:
                raise DomainError('invalid_image','Use a validated local PNG or JPEG image attachment.',422)
            image=Image(BytesIO(attachment_bytes(item['attachment'])))
            ratio=min((size[0]-2*margin)/image.imageWidth,(size[1]/2)/image.imageHeight,1)
            image.drawWidth=image.imageWidth*ratio; image.drawHeight=image.imageHeight*ratio
            story.append(image)
        elif block.get('type')=='checkbox':
            box=Table([['',paragraph(block.get('label',''))]],colWidths=[12,size[0]-2*margin-12])
            box.setStyle(TableStyle([('BOX',(0,0),(0,0),.7,colors.black)]))
            story.append(box)
        elif section['text']:
            story.append(paragraph(section['text'].replace('☐','[ ]')))
        story.append(Spacer(1,8))
    story.append(paragraph('License: '+item['license']+'\nPermission: '+item['permission_basis']+'\n'+item['notices']))
    class BoundedCanvas(Canvas):
        def __init__(self,*args,**kwargs):
            kwargs['invariant']=1
            super().__init__(*args,**kwargs)
        def showPage(self):
            if self.getPageNumber()>page_limit:
                raise DomainError('material_page_limit','The material exceeds its page allowance. Reduce content or change the layout.',422)
            super().showPage()
    try:
        SimpleDocTemplate(stream,pagesize=size,leftMargin=margin,rightMargin=margin,topMargin=margin,bottomMargin=margin).build(story,canvasmaker=BoundedCanvas)
    except DomainError:
        raise
    except Exception as error:
        from reportlab.platypus.doctemplate import LayoutError
        if isinstance(error,LayoutError):
            raise DomainError('material_layout_limit','A content block does not fit the selected size. Reduce it or use a larger layout.',422) from None
        raise
    return stream.getvalue()
