"""The public walkthrough ends at an editable, unsent workspace request."""


def test_hero_link_opens_garden_tour_and_contact_type_is_preselected(client):
    landing = client.get('/login')
    assert b'href="/tour/community-garden"' in landing.data
    tour = client.get('/tour/community-garden')
    assert tour.status_code == 200
    assert tour.data.count(b'class="tour-slide"') == 9
    assert b'ArrowRight' in client.get('/static/tour.js').data
    assert b'/contact?type=new_workspace' in tour.data
    contact = client.get('/contact?type=new_workspace')
    assert contact.status_code == 200
    assert b'value="new_workspace" selected' in contact.data
    assert b'Message sent' not in contact.data
    assert b'name="message_type"' in contact.data
