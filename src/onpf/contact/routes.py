"""Public contact page; submitted content is never persisted."""
from flask import Blueprint, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from onpf.contact.service import MESSAGE_TYPES, mail_ready, prepare_images, send_contact, validate_fields
from onpf.errors import DomainError

bp = Blueprint('contact', __name__)


def page(values=None, error=None, sent=False, status=200):
    return render_template('contact.html', message_types=MESSAGE_TYPES,
                           values=values or {}, error=error, sent=sent, available=mail_ready()), status


@bp.route('/contact', methods=['GET', 'POST'])
def contact_page():
    if request.method == 'GET':
        kind = request.args.get('type', '')
        return page(values={'message_type': kind} if kind in dict(MESSAGE_TYPES) else {})
    # Validate the original values below, but never reflect an unbounded draft:
    # HTML escaping can expand rejected input past Waitress's output spill limit.
    values = {key: request.form.get(key, '')[:limit] for key, limit in
              (('message_type', 40), ('name', 100), ('reply_email', 254),
               ('workspace', 150), ('message', 2000))}
    try:
        if request.form.get('website'):
            return page(sent=True)
        if set(request.files) - {'images'}:
            raise DomainError('invalid_contact_file', 'Attach images using the image field only.', 422)
        checked = validate_fields(request.form)
        images = prepare_images(request.files.getlist('images'))
        send_contact(checked, images)
    except DomainError as error:
        return page(values, error.message, status=error.status)
    return page(sent=True)


@bp.app_errorhandler(RequestEntityTooLarge)
def contact_too_large(error):
    if request.path == '/contact':
        return page(error='The attachment upload is too large. Use up to three images of 2 MB each.', status=413)
    return error
