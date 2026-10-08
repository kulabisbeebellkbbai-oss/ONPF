import re
import pytest

@pytest.fixture
def app(tmp_path):
    from onpf.app import create_app
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "onpf.sqlite3"),
                       "INSTANCE_PATH": str(tmp_path / "instance")})

@pytest.fixture
def app_context(app):
    with app.app_context():
        yield

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def owner(app_context):
    from onpf.auth.models import Principal
    from onpf.auth.service import create_user
    return Principal(create_user("owner", "fictional-owner-password"), None, None)

@pytest.fixture
def facilitator(app_context):
    from onpf.auth.models import Principal
    from onpf.auth.service import create_user
    return Principal(create_user("facilitator", "fictional-facilitator-password"), None, None)

@pytest.fixture
def other_owner(app_context):
    from onpf.auth.models import Principal
    from onpf.auth.service import create_user
    return Principal(create_user("other-owner", "fictional-other-password"), None, None)

@pytest.fixture
def program(owner, facilitator):
    from onpf.programs.service import create_program
    return create_program(owner, {"title": "Fictional test program", "memberships": [{"user_id": facilitator.user_id, "role": "facilitator"}]})

@pytest.fixture
def batch(owner, program):
    from onpf.inquiries.service import issue_batch
    return issue_batch(owner, program['id'], {'title': 'Fictional test batch', 'question_ids': ['core-1-1', 'core-4-3']})

@pytest.fixture
def submitted(owner, batch):
    from onpf.contributions.service import submit_responses
    return submit_responses(owner, batch['id'], 'fictional-fixture-submission', [
        {'question_id': batch['questions'][0]['id'], 'text': 'Fictional first perspective', 'attribution': 'anonymous'},
        {'question_id': batch['questions'][0]['id'], 'text': 'Fictional opposing perspective', 'attribution': 'alias', 'display_name': 'Fictional alias'},
    ])

@pytest.fixture
def candidate(owner, program):
    from onpf.releases.service import prepare_candidate
    return prepare_candidate(owner, program['id'], program['revision'])

@pytest.fixture
def csrf_token():
    def read(client, path="/login"):
        page = client.get(path)
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
        assert match, page.text
        return match.group(1)
    return read

@pytest.fixture
def login_client(client, owner, csrf_token):
    token = csrf_token(client)
    response = client.post("/login", data={"username": "owner", "password": "fictional-owner-password", "csrf_token": token})
    assert response.status_code == 302
    return client
