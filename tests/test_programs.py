import pytest

from onpf.errors import DomainError


def test_two_independent_programs(owner):
    from onpf.programs.service import create_program
    broader = create_program(owner, {"title": "Fictional community program", "module_keys": ["art", "gardening"]})
    art = create_program(owner, {"title": "Fictional guided art", "module_keys": ["art"]})
    assert broader["id"] != art["id"]
    assert broader["approval_rule"] == art["approval_rule"] == "all"
    assert broader["operating_status"] == art["operating_status"] == "pending"
    assert broader["owner_ids"] == [owner.user_id]
    assert broader["revision"] == 1


def test_stale_draft_rejected(owner, program):
    from onpf.programs.service import save_document, get_program
    saved = save_document(owner, program["id"], "overview", {"sections": {"purpose": "Fictional purpose"}}, program["revision"])
    assert saved["revision"] == program["revision"] + 1
    with pytest.raises(DomainError) as error:
        save_document(owner, program["id"], "overview", {"sections": {"purpose": "Overwritten"}}, program["revision"])
    assert error.value.status == 409
    assert get_program(owner, program["id"])["documents"]["overview"]["sections"]["purpose"] == "Fictional purpose"


def test_framework_is_generic(app_context):
    from onpf.programs.framework import load_framework
    framework = load_framework()
    assert len(framework["stages"]) == 7
    assert {p["stage"] for p in framework["modules"]["core"]["prompts"]} == set(range(1, 8))
    assert len(framework["modules"]["core"]["prompts"]) == 21
    for module in framework["modules"].values():
        for prompt in module["prompts"]:
            assert {"id", "stage", "text", "answer_type", "document_key", "depends_on"} <= prompt.keys()
            assert "bridge" not in prompt["text"].lower()
            assert "busby" not in prompt["text"].lower()
        if module["key"] != "core":
            assert len(module["prompts"]) == 4
            assert module["readiness_checks"]
    assert set(framework["documents"]) == {"overview", "delivery", "budget", "volunteers", "session-plan", "feedback", "adoption"}


def test_last_owner_cannot_be_removed(owner, program):
    from onpf.programs.service import update_program, get_program
    with pytest.raises(DomainError) as error:
        update_program(owner, program["id"], {"owner_ids": []}, program["revision"])
    assert error.value.status == 422
    assert get_program(owner, program["id"])["revision"] == program["revision"]


def test_owner_change_increments_revision(owner, other_owner, program):
    from onpf.programs.service import update_program, get_program
    changed = update_program(owner, program["id"], {"owner_ids": [owner.user_id, other_owner.user_id], "approval_rule": "any"}, program["revision"])
    assert set(changed["owner_ids"]) == {owner.user_id, other_owner.user_id}
    assert changed["revision"] == program["revision"] + 1
    assert changed["approval_rule"] == "any"
    assert get_program(other_owner, program["id"])["id"] == program["id"]


def test_facilitator_can_draft_but_cannot_change_authority(facilitator, program):
    from onpf.programs.service import save_document, update_program
    saved = save_document(facilitator, program["id"], "delivery", {"sections": {"activities": "Fictional activities"}}, program["revision"])
    with pytest.raises(DomainError) as error:
        update_program(facilitator, program["id"], {"approval_rule": "any"}, saved["revision"])
    assert error.value.status == 403


def test_nonmember_cannot_read_or_edit(other_owner, program):
    from onpf.programs.service import get_program, save_document
    for operation in (lambda: get_program(other_owner, program["id"]), lambda: save_document(other_owner, program["id"], "overview", {"sections": {}}, program["revision"])):
        with pytest.raises(DomainError) as error:
            operation()
        assert error.value.status == 403


@pytest.mark.parametrize("payload", [{"title": ""}, {"title": "Example", "operating_status": "approved"}, {"title": "Example", "module_keys": ["missing"]}, {"title": "Example", "owner_ids": ["missing"]}])
def test_invalid_setup_has_no_partial_program(owner, payload):
    from onpf.programs.service import create_program, list_programs
    before = len(list_programs(owner))
    with pytest.raises(DomainError) as error:
        create_program(owner, payload)
    assert error.value.status == 422
    assert len(list_programs(owner)) == before


def test_budget_preserves_unknown_and_confirmed_costs(owner, program):
    from onpf.programs.service import save_document
    saved = save_document(owner, program["id"], "budget", {"sections": {}, "rows": [{"item": "Paint", "quantity": "2", "unit_cost": None, "cost_status": "estimated", "notes": "Awaiting quote"}, {"item": "Paper", "quantity": "1", "unit_cost": "12.50", "cost_status": "confirmed", "notes": "Fictional quote"}]}, program["revision"])
    rows = saved["documents"]["budget"]["rows"]
    assert rows[0]["unit_cost"] is None
    assert rows[1]["unit_cost"] == "12.50"
    assert rows[1]["cost_status"] == "confirmed"


def test_invalid_document_key_and_budget_status(owner, program):
    from onpf.programs.service import save_document
    with pytest.raises(DomainError) as error:
        save_document(owner, program["id"], "missing", {"sections": {}}, program["revision"])
    assert error.value.status == 404
    with pytest.raises(DomainError) as error:
        save_document(owner, program["id"], "budget", {"sections": {}, "rows": [{"item": "Paper", "cost_status": "approved"}]}, program["revision"])
    assert error.value.status == 422


def test_browser_setup_and_seven_stage_document_flow(login_client, csrf_token):
    page = login_client.get("/programs/new")
    assert page.status_code == 200
    token = csrf_token(login_client, "/programs/new")
    response = login_client.post("/programs/new", data={"csrf_token": token, "title": "Fictional browser program", "module_keys": "art"})
    assert response.status_code == 302
    workspace = login_client.get(response.headers["Location"])
    assert workspace.status_code == 200
    for number in range(1, 8):
        assert f'id="stage-{number}"' in workspace.text
    program_url = response.headers["Location"]
    for key in ("overview", "delivery", "budget", "volunteers", "session-plan", "feedback", "adoption"):
        document = login_client.get(f"{program_url}/documents/{key}")
        assert document.status_code == 200
        assert '<textarea' in document.text
        assert "Not yet drafted" in document.text
    token = csrf_token(login_client, f"{program_url}/documents/overview")
    saved = login_client.post(f"{program_url}/documents/overview", data={"csrf_token": token, "expected_revision": "1", "section_purpose": "<script>Fictional text</script>"}, follow_redirects=True)
    assert saved.status_code == 200
    assert "&lt;script&gt;Fictional text&lt;/script&gt;" in saved.text
    assert "Draft saved" in saved.text


def test_failed_form_preserves_text_and_revision(login_client, csrf_token, program):
    url = f'/programs/{program["id"]}/documents/overview'
    token = csrf_token(login_client, url)
    first = login_client.post(url, data={"csrf_token": token, "expected_revision": program["revision"], "section_purpose": "First text"})
    assert first.status_code == 302
    stale = login_client.post(url, data={"csrf_token": token, "expected_revision": program["revision"], "section_purpose": "Keep this unsaved text"})
    assert stale.status_code == 409
    assert "Keep this unsaved text" in stale.text
    assert f'name="expected_revision" value="{program["revision"]}"' in stale.text


def test_initial_working_titles_are_pending(login_client, owner, csrf_token):
    from onpf.programs.service import list_programs
    token = csrf_token(login_client, "/programs/new")
    response = login_client.post("/programs/setup-working-programs", data={"csrf_token": token})
    assert response.status_code == 302
    programs = list_programs(owner)
    assert {p["title"] for p in programs} == {"Bridge creative and growing program", "Volunteer-led guided art program"}
    assert all(p["operating_status"] == "pending" for p in programs)


def test_setup_and_save_require_csrf(login_client, program):
    assert login_client.post("/programs/new", data={"title": "Bad request"}).status_code == 400
    assert login_client.post(f'/programs/{program["id"]}/documents/overview', data={"expected_revision": program["revision"]}).status_code == 400


def test_settings_browser_adds_named_owner_and_preserves_invalid_change(login_client, owner, other_owner, csrf_token, program):
    from onpf.programs.service import get_program
    url = f'/programs/{program["id"]}/settings'
    token = csrf_token(login_client, url)
    page = login_client.get(url)
    assert "other-owner" in page.text
    response = login_client.post(url, data={"csrf_token": token, "title": "Changed fictional title", "expected_revision": program["revision"], "membership_editor": "1", f"role_{owner.user_id}": "owner", f"role_{other_owner.user_id}": "owner"})
    assert response.status_code == 302
    current = get_program(owner, program["id"])
    assert set(current["owner_ids"]) == {owner.user_id, other_owner.user_id}
    response = login_client.post(url, data={"csrf_token": token, "title": "Unsaved fictional title", "expected_revision": current["revision"], "membership_editor": "1"})
    assert response.status_code == 422
    assert "Unsaved fictional title" in response.text
    assert get_program(owner, program["id"])["title"] == "Changed fictional title"


def test_budget_browser_unknown_cost_and_safe_print_view(login_client, owner, csrf_token, program):
    from onpf.programs.service import get_program
    url = f'/programs/{program["id"]}/documents/budget'
    token = csrf_token(login_client, url)
    response = login_client.post(url, data={"csrf_token": token, "expected_revision": program["revision"], "item_0": "<img src=x onerror=alert(1)>", "quantity_0": "2", "unit_cost_0": "", "cost_status_0": "estimated", "notes_0": "Awaiting quote"}, follow_redirects=True)
    assert response.status_code == 200
    assert "Unknown" in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text
    from html.parser import HTMLParser
    class Forms(HTMLParser):
        def __init__(self):
            super().__init__()
            self.forms = []
        def handle_starttag(self, tag, attributes):
            if tag == 'form':
                self.forms.append(dict(attributes))
    forms = Forms()
    forms.feed(response.text)
    assert any(form.get('method') == 'post' and 'no-print' in form.get('class', '').split()
               for form in forms.forms)
    assert get_program(owner, program["id"])["documents"]["budget"]["rows"][0]["unit_cost"] is None


def test_document_structure_can_be_added_without_code_changes(monkeypatch, tmp_path, login_client, owner, csrf_token):
    import shutil
    from onpf.programs import framework
    from onpf.programs.service import create_program, get_program
    source = tmp_path / "framework"
    shutil.copytree(framework.FRAMEWORK_PATH, source)
    (source / "documents" / "local-guide.md").write_text("# Local guide\n\n## local-notes | Local notes\nDescribe local arrangements.\n", encoding="utf-8")
    monkeypatch.setattr(framework, "FRAMEWORK_PATH", source)
    created = create_program(owner, {"title": "Fictional extensible program"})
    url = f'/programs/{created["id"]}/documents/local-guide'
    workspace = login_client.get(f'/programs/{created["id"]}')
    assert f'href="{url}"' in workspace.text
    page = login_client.get(url)
    assert page.status_code == 200
    assert "Describe local arrangements." in page.text
    saved = login_client.post(url, data={"csrf_token": csrf_token(login_client, url), "expected_revision": "1", "section_local-notes": "Fictional local content"})
    assert saved.status_code == 302
    assert get_program(owner, created["id"])["documents"]["local-guide"]["sections"]["local-notes"] == "Fictional local content"
