"""Validate contact submissions and deliver verified images by email."""
import io
import re
import smtplib
import ssl
import warnings
from email.message import EmailMessage
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

from onpf.db import get_db, utcnow
from onpf.errors import DomainError

MESSAGE_TYPES = (
    ('comment', 'Comment'),
    ('new_workspace', 'Request a new workspace or project'),
    ('tech_support', 'Technical support'),
    ('workspace_admin', 'Contact a workspace administrator'),
    ('accessibility', 'Accessibility feedback'),
    ('privacy', 'Privacy question'),
    ('other', 'Other'),
)
TYPE_LABELS = dict(MESSAGE_TYPES)
IMAGE_FORMATS = {'PNG': ('png', 'image/png'), 'JPEG': ('jpg', 'image/jpeg'),
                 'GIF': ('gif', 'image/gif'), 'WEBP': ('webp', 'image/webp')}
EXTENSIONS = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG',
              '.gif': 'GIF', '.webp': 'WEBP'}
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGES = 3
MAX_PIXELS = 20_000_000
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}\Z')


def valid_email(value):
    return bool(value and len(value) <= 254 and EMAIL.fullmatch(value)
                and '..' not in value and not value.startswith('.') and '@.' not in value)


def validate_fields(form):
    kind = form.get('message_type', '')
    name = form.get('name', '').strip()
    reply = form.get('reply_email', '').strip()
    workspace = form.get('workspace', '').strip()
    body = form.get('message', '').strip()
    if kind not in TYPE_LABELS:
        raise DomainError('invalid_contact_type', 'Choose a message type.', 422)
    if not body or len(body) > 2000:
        raise DomainError('invalid_contact_message', 'Enter a message of 1 to 2000 characters.', 422)
    if len(name) > 100 or len(workspace) > 150:
        raise DomainError('invalid_contact_details', 'Name or workspace reference is too long.', 422)
    if reply and not valid_email(reply):
        raise DomainError('invalid_contact_reply', 'Enter a valid reply email address or leave it blank.', 422)
    return {'message_type': kind, 'name': name, 'reply_email': reply,
            'workspace': workspace, 'message': body}


def prepare_images(files):
    if any(not upload.filename and upload.stream.read(1) for upload in files):
        raise DomainError('invalid_contact_image', 'Choose a valid image file.', 422)
    uploads = [upload for upload in files if upload.filename]
    if len(uploads) > MAX_IMAGES:
        raise DomainError('too_many_images', 'Attach up to three images.', 422)
    prepared = []
    for number, upload in enumerate(uploads, 1):
        expected = EXTENSIONS.get(Path(upload.filename).suffix.lower())
        if not expected:
            raise DomainError('invalid_contact_image', 'Attach PNG, JPEG, GIF or WebP images only.', 422)
        raw = upload.stream.read(MAX_IMAGE_BYTES + 1)
        if len(raw) > MAX_IMAGE_BYTES:
            raise DomainError('contact_image_too_large', 'Each image must be 2 MB or smaller.', 422)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format != expected or image.width * image.height > MAX_PIXELS:
                        raise ValueError('Image format or dimensions do not match.')
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
                    # Materialize palette transparency before discarding info:
                    # otherwise stripping GIF/PNG metadata can erase alpha.
                    clean = ImageOps.exif_transpose(image).convert('RGB' if expected == 'JPEG' else 'RGBA')
                    clean.info.clear()
                    output = io.BytesIO()
                    clean.save(output, format=expected)
            data = output.getvalue()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning,
                Image.DecompressionBombError):
            raise DomainError('invalid_contact_image', 'Attach valid PNG, JPEG, GIF or WebP images only.', 422) from None
        if len(data) > MAX_IMAGE_BYTES:
            raise DomainError('contact_image_too_large', 'Each processed image must be 2 MB or smaller.', 422)
        extension, mime = IMAGE_FORMATS[expected]
        prepared.append((f'image-{number}.{extension}', mime, data))
    return prepared


def mail_ready():
    config = current_app.config
    return bool(config.get('CONTACT_SMTP_HOST') and valid_email(config.get('CONTACT_SMTP_FROM', ''))
                and valid_email(config.get('CONTACT_TO', '')))


def send_contact(values, images):
    config = current_app.config
    if not mail_ready():
        raise DomainError('contact_unavailable', 'The contact form is not available right now. Try again later.', 503)
    try:
        port = int(config['CONTACT_SMTP_PORT'])
        if not 1 <= port <= 65535:
            raise ValueError()
        password = config.get('CONTACT_SMTP_PASSWORD', '')
        password_file = config.get('CONTACT_SMTP_PASSWORD_FILE', '')
        if password_file:
            password = Path(password_file).read_text(encoding='utf-8').rstrip('\r\n')
        if config.get('CONTACT_SMTP_USER') and not password:
            raise ValueError()
    except (ValueError, OSError, UnicodeError):
        raise DomainError('contact_unavailable', 'The contact form is not available right now. Try again later.', 503) from None
    message = EmailMessage()
    message['Subject'] = f"ONPF contact: {TYPE_LABELS[values['message_type']]}"
    message['From'] = config['CONTACT_SMTP_FROM']
    message['To'] = config['CONTACT_TO']
    if values['reply_email']:
        message['Reply-To'] = values['reply_email']
    message.set_content('\n'.join((
        f"Message type: {TYPE_LABELS[values['message_type']]}",
        f"Name: {values['name'] or '(not provided)'}",
        f"Reply address: {values['reply_email'] or '(not provided)'}",
        f"Workspace or project: {values['workspace'] or '(not provided)'}",
        '', 'Message:', values['message'],
    )))
    for filename, mime, data in images:
        major, minor = mime.split('/')
        message.add_attachment(data, maintype=major, subtype=minor, filename=filename)
    try:
        with smtplib.SMTP(config['CONTACT_SMTP_HOST'], port, timeout=10) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if config.get('CONTACT_SMTP_USER'):
                smtp.login(config['CONTACT_SMTP_USER'], password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, TimeoutError):
        raise DomainError('contact_delivery_failed', 'The message could not be sent. Please try again later.', 503) from None
    get_db().execute('INSERT INTO contact_sent_log(message_type,sent_at) VALUES (?,?)',
                     (values['message_type'], utcnow()))
