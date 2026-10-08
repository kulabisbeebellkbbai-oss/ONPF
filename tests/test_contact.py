"""Public contact delivery and privacy boundaries."""
import io
import re
import sqlite3
from pathlib import Path

from PIL import Image, PngImagePlugin
import pytest
from werkzeug.datastructures import FileStorage


def token(client):
    page = client.get('/contact')
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
    assert match, page.text
    return match.group(1)


def test_contact_link_and_fields_are_available_without_sign_in(client):
    for path in ('/login', '/license', '/contact'):
        page = client.get(path)
        assert page.status_code == 200
        assert 'href="/contact"' in page.text
        assert 'onpf@proton.me' not in page.text
    page = client.get('/contact').text
    for label in ('Comment', 'Request a new workspace or project', 'Technical support',
                  'Contact a workspace administrator'):
        assert label in page
    assert 'maxlength="2000"' in page
    assert 'accept="image/png,image/jpeg,image/gif,image/webp"' in page


def test_successful_contact_sends_email_and_logs_only_type_and_time(app, client, monkeypatch):
    sent = []

    class SMTP:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ('smtp.example.org', 587, 10)

        def __enter__(self): return self
        def __exit__(self, *_): pass
        def starttls(self, context): pass
        def login(self, username, password):
            assert (username, password) == ('sender', 'private-password')
        def send_message(self, message): sent.append(message)

    monkeypatch.setattr('onpf.contact.service.smtplib.SMTP', SMTP)
    app.config.update(CONTACT_SMTP_HOST='smtp.example.org', CONTACT_SMTP_FROM='sender@example.org',
                      CONTACT_SMTP_USER='sender', CONTACT_SMTP_PASSWORD='private-password')
    response = client.post('/contact', data={
        'csrf_token': token(client), 'message_type': 'comment', 'name': 'A visitor',
        'reply_email': 'visitor@example.org', 'workspace': 'Garden group',
        'message': 'Private message body',
    })
    assert response.status_code == 200
    assert 'Message sent' in response.text
    assert len(sent) == 1
    assert sent[0]['To'] == app.config['CONTACT_TO']
    assert sent[0]['From'] == 'sender@example.org'
    assert sent[0]['Reply-To'] == 'visitor@example.org'
    assert 'Private message body' in sent[0].get_body(preferencelist=('plain',)).get_content()
    assert 'onpf@proton.me' not in response.text
    with sqlite3.connect(app.config['DATABASE']) as db:
        columns = [row[1] for row in db.execute('PRAGMA table_info(contact_sent_log)')]
        rows = db.execute('SELECT * FROM contact_sent_log').fetchall()
    assert columns == ['message_type', 'sent_at']
    assert len(rows) == 1 and rows[0][0] == 'comment'
    assert 'Private message body' not in str(rows)


def test_contact_requires_valid_message_and_does_not_log_failure(app, client):
    app.config.update(CONTACT_SMTP_HOST='smtp.example.org', CONTACT_SMTP_FROM='sender@example.org')
    for values in ({'message_type': 'other', 'message': ''},
                   {'message_type': 'invalid', 'message': 'Hello'},
                   {'message_type': 'comment', 'message': 'x' * 2001}):
        response = client.post('/contact', data={'csrf_token': token(client), **values})
        assert response.status_code == 422
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM contact_sent_log').fetchone()[0] == 0


def test_contact_rejects_nonimage_even_with_image_extension(app, client):
    app.config.update(CONTACT_SMTP_HOST='smtp.example.org', CONTACT_SMTP_FROM='sender@example.org')
    response = client.post('/contact', data={
        'csrf_token': token(client), 'message_type': 'comment', 'message': 'Attached',
        'images': (io.BytesIO(b'not an image'), 'fake.png', 'image/png'),
    }, content_type='multipart/form-data')
    assert response.status_code == 422
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM contact_sent_log').fetchone()[0] == 0


def test_contact_rejects_file_under_other_field(app, client):
    app.config.update(CONTACT_SMTP_HOST='smtp.example.org', CONTACT_SMTP_FROM='sender@example.org')
    response = client.post('/contact', data={
        'csrf_token': token(client), 'message_type': 'comment', 'message': 'Attached',
        'document': (io.BytesIO(b'not an image'), 'report.pdf', 'application/pdf'),
    }, content_type='multipart/form-data')
    assert response.status_code == 422


def test_invalid_contact_values_cannot_expand_into_a_disk_buffered_response(client):
    response = client.post('/contact', data={
        'csrf_token': token(client), 'message_type': 'comment',
        'message': '&' * 250_000,
    }, content_type='multipart/form-data')
    assert response.status_code == 422
    assert 'Enter a message of 1 to 2000 characters.' in response.text
    assert '&amp;' in response.text  # Keep an escaped, bounded draft for correction.
    assert len(response.data) < 32 * 1024


@pytest.mark.parametrize('format_name,filename', [
    ('PNG', 'photo.png'), ('JPEG', 'photo.jpg'),
    ('GIF', 'photo.gif'), ('WEBP', 'photo.webp'),
])
def test_supported_image_formats_are_reencoded(format_name, filename):
    from onpf.contact.service import prepare_images

    source = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(source, format=format_name)
    upload = FileStorage(stream=io.BytesIO(source.getvalue()), filename=filename)
    prepared = prepare_images([upload])
    assert len(prepared) == 1
    with Image.open(io.BytesIO(prepared[0][2])) as image:
        assert image.format == format_name


def test_message_accepts_exact_character_limit():
    from onpf.contact.service import validate_fields

    values = validate_fields({'message_type': 'comment', 'message': 'x' * 2000})
    assert len(values['message']) == 2000


def test_unconfigured_mail_is_not_claimed_sent(app, client):
    response = client.post('/contact', data={
        'csrf_token': token(client), 'message_type': 'comment', 'message': 'Hello',
    })
    assert response.status_code == 503
    assert 'not available' in response.text.lower()
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM contact_sent_log').fetchone()[0] == 0


def test_verified_image_is_attached_without_metadata_or_server_copy(app, client, monkeypatch):
    sent = []

    class SMTP:
        def __init__(self, *_args, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def starttls(self, **_kwargs): pass
        def send_message(self, message): sent.append(message)

    monkeypatch.setattr('onpf.contact.service.smtplib.SMTP', SMTP)
    app.config.update(CONTACT_SMTP_HOST='smtp.example.org', CONTACT_SMTP_FROM='sender@example.org')
    picture = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('PrivateNote', 'hidden-location')
    Image.new('RGB', (8, 8), 'green').save(picture, format='PNG', pnginfo=metadata)
    response = client.post('/contact', data={
        'csrf_token': token(client), 'message_type': 'tech_support', 'message': 'A screenshot',
        'images': (io.BytesIO(picture.getvalue()), 'screen.png', 'image/png'),
    }, content_type='multipart/form-data')
    assert response.status_code == 200
    assert len(sent[0].get_payload()) == 2
    attachment = next(sent[0].iter_attachments())
    assert attachment.get_content_type() == 'image/png'
    assert attachment.get_filename() == 'image-1.png'
    assert b'hidden-location' not in attachment.get_content()
    assert not list(Path(app.instance_path).glob('*.png'))
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT message_type FROM contact_sent_log').fetchall() == [('tech_support',)]


def test_smtp_failure_does_not_claim_delivery_or_write_log(app, client, monkeypatch):
    import smtplib

    def fail(*_args, **_kwargs):
        raise smtplib.SMTPException('private SMTP detail')

    monkeypatch.setattr('onpf.contact.service.smtplib.SMTP', fail)
    app.config.update(CONTACT_SMTP_HOST='smtp.example.org', CONTACT_SMTP_FROM='sender@example.org')
    response = client.post('/contact', data={
        'csrf_token': token(client), 'message_type': 'comment', 'message': 'Private body',
    })
    assert response.status_code == 503
    assert 'private SMTP detail' not in response.text
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT COUNT(*) FROM contact_sent_log').fetchone()[0] == 0


@pytest.mark.parametrize('format_name,filename,metadata', [
    ('GIF', 'image.gif', {'comment': b'private-image-marker'}),
    ('PNG', 'image.png', {'icc_profile': b'private-image-marker'}),
])
def test_image_metadata_is_removed_without_losing_transparency(format_name, filename, metadata):
    from onpf.contact.service import prepare_images

    source = io.BytesIO()
    picture = Image.new('RGBA', (8, 8), (20, 60, 80, 255))
    picture.putpixel((0, 0), (0, 0, 0, 0))
    picture.save(source, format=format_name, **metadata)
    with Image.open(io.BytesIO(source.getvalue())) as original:
        assert all(original.info[key] == value for key, value in metadata.items())
    prepared = prepare_images([FileStorage(stream=io.BytesIO(source.getvalue()), filename=filename)])
    assert b'private-image-marker' not in prepared[0][2]
    with Image.open(io.BytesIO(prepared[0][2])) as image:
        assert not ({'comment', 'icc_profile', 'exif'} & image.info.keys())
        assert image.convert('RGBA').getpixel((0, 0))[3] == 0
        assert image.convert('RGBA').getpixel((1, 1))[3] == 255


@pytest.mark.parametrize('entrypoint', ['production', 'local'])
@pytest.mark.parametrize('chunked', [False, True])
def test_waitress_never_spills_contact_body_before_rejection(entrypoint, chunked, tmp_path, monkeypatch):
    from waitress.adjustments import Adjustments
    from waitress.parser import HTTPRequestParser

    if entrypoint == 'production':
        from onpf.production import WAITRESS_OPTIONS
        options = WAITRESS_OPTIONS
    else:
        from onpf.cli import main
        options = {}
        monkeypatch.setattr('waitress.serve', lambda app, **kwargs: options.update(kwargs))
        assert main(['--instance', str(tmp_path / 'instance'), 'serve']) == 0

    def forbidden_disk_buffer(*args, **kwargs):
        pytest.fail('Contact body spilled to a temporary file before Flask could reject it')

    monkeypatch.setattr('waitress.buffers.TempfileBasedBuffer', forbidden_disk_buffer)
    adjustments = Adjustments(**options)
    parser = HTTPRequestParser(adjustments)
    # 9 MiB exceeds the contact form limit; chunked input also crosses the
    # whole-server limit, including the final receive before rejection.
    size = (27 if chunked else 9) * 1024 * 1024
    framing = 'Transfer-Encoding: chunked' if chunked else f'Content-Length: {size}'
    parser.received(f'POST /contact HTTP/1.1\r\nHost: localhost\r\n{framing}\r\n\r\n'.encode())
    remaining = size
    try:
        while remaining and not parser.completed:
            amount = min(4096, remaining)
            data = b'x' * amount
            if chunked:
                data = f'{amount:x}\r\n'.encode() + data + b'\r\n'
            # Feed exactly as the socket channel does, never more than recv_bytes.
            for offset in range(0, len(data), adjustments.recv_bytes):
                parser.received(data[offset:offset + adjustments.recv_bytes])
            remaining -= amount
        if chunked:
            assert parser.completed and parser.error.code == 413
        else:
            assert parser.completed and parser.error is None
    finally:
        parser.close()
