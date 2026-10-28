"""Tests for the public "I'm interested" form."""
import pytest

from app.db import connect
from tests.test_web import app, client, logged_in, seed  # noqa: F401  (pytest fixtures)

GOOD = {"name": "Sam Taylor", "education": "University", "message": "Looks great"}


def rows(app):
    return connect(app.config["DATABASE_PATH"]).execute("SELECT * FROM interest").fetchall()


@pytest.fixture
def sent(monkeypatch, app):
    """Pretend to be Resend: record emails instead of sending them."""
    app.config.update(RESEND_API_KEY="re_test", NOTIFY_EMAIL="me@example.com")
    calls = []

    class Response:
        ok = True

    def fake_post(url, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json})
        return Response()

    monkeypatch.setattr("app.interest.requests.post", fake_post)
    return calls


def test_form_is_public_but_nothing_else_is(client):
    assert client.get("/interest").status_code == 200
    assert client.get("/bank").status_code == 302  # still needs login
    assert b"Register your interest" in client.get("/login").data


def test_submission_saved_and_emailed(app, client, sent):
    r = client.post("/interest", data=GOOD)
    assert r.status_code == 302 and "/interest/thanks" in r.headers["Location"]
    [row] = rows(app)
    assert (row["name"], row["education"], row["emailed"]) == ("Sam Taylor", "University", 1)
    [email] = sent
    assert email["url"] == "https://api.resend.com/emails"
    assert email["json"]["to"] == ["me@example.com"]
    assert "Sam Taylor" in email["json"]["subject"] and "University" in email["json"]["html"]


def test_user_input_is_escaped_in_the_email(client, sent):
    client.post("/interest", data=GOOD | {"name": "<script>x</script>"})
    assert "<script>" not in sent[0]["json"]["html"]


def test_saved_even_without_email_settings(app, client):
    client.post("/interest", data=GOOD)
    assert rows(app)[0]["emailed"] == 0


@pytest.mark.parametrize("bad", [{"name": ""}, {"name": "x" * 101}, {"education": "Wizard"},
                                 {"message": "x" * 1001}])
def test_invalid_input_rejected(app, client, bad):
    assert client.post("/interest", data=GOOD | bad).status_code == 400
    assert rows(app) == []


def test_honeypot_catches_bots(app, client, sent):
    r = client.post("/interest", data=GOOD | {"website": "http://spam.example"})
    assert r.status_code == 302  # looks like success to the bot...
    assert rows(app) == [] and sent == []  # ...but nothing is saved or sent


def test_rate_limited_per_ip(app, client):
    for _ in range(3):
        assert client.post("/interest", data=GOOD).status_code == 302
    assert client.post("/interest", data=GOOD).status_code == 400
    assert len(rows(app)) == 3


def test_signups_listed_in_settings(logged_in):
    logged_in.post("/interest", data=GOOD)
    assert b"Sam Taylor" in logged_in.get("/settings").data
