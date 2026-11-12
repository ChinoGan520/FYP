"""Admission security and behavior checks, including a real insert race."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from flask import session
from flask_wtf.csrf import generate_csrf
from itsdangerous import TimestampSigner

from irice.repository import repo
from irice.models import AuditLog, Booth, Category, CheckIn, Project, Team, User
from irice import passes


@pytest.fixture
def pass_environment(app, admin):
    with app.app_context():
        people = {
            "attendee": User(name="Approved Visitor", email="visitor@example.test", password_hash="unused", role="attendee", approval_status="approved"),
            "pending": User(name="Pending Visitor", email="pending@example.test", password_hash="unused", role="attendee", approval_status="pending"),
            "evaluator": User(name="Approved Evaluator", email="evaluator@example.test", password_hash="unused", role="evaluator", approval_status="approved"),
            "exhibitor": User(name="Exhibitor", email="exhibitor@example.test", password_hash="unused", role="exhibitor"),
        }
        for user in people.values():
            repo.save(user)
        category = repo.first(Category)
        team = repo.save(Team(name="Example Team", user_id=people["exhibitor"].id, members="Informational Member"))
        project = repo.save(Project(team_id=team.id, category_id=category.id, title="Water monitor", abstract="A working water monitoring system.", status="approved"))
        ids = {key: user.id for key, user in people.items()}
        ids.update(admin=admin, project=project.id)
    return app, ids


def _client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as stored:
        stored["user_id"] = user_id
    return client


def _issue(app, user_id, kind="live"):
    with app.app_context():
        return passes.issue_token(repo.get(User, user_id), kind)


def _scan(client, token):
    return client.post("/passes/check-in", json={"token": token})


def _count(app, model):
    with app.app_context():
        return len(repo.all(model))


@pytest.mark.parametrize("person", ["attendee", "evaluator", "exhibitor"])
def test_approved_pass_generates_refreshable_png_and_checks_in(pass_environment, person):
    app, ids = pass_environment
    response = _client(app, ids[person]).get("/passes/token")
    assert response.status_code == 200
    data = response.get_json()
    assert data["expires_in"] == 180
    assert datetime.fromisoformat(data["expires_at"]).tzinfo is not None
    assert base64.b64decode(data["qr_data"].split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n")
    assert "no-store" in response.headers["Cache-Control"]
    scanner = _client(app, ids["admin"])
    first = _scan(scanner, data["token"])
    assert first.status_code == 200
    assert first.get_json()["ok"] and not first.get_json()["duplicate"]
    assert first.get_json()["time"].endswith("+08:00")
    second = _scan(scanner, data["token"])
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True
    assert second.get_json()["time"] == first.get_json()["time"]
    assert _count(app, CheckIn) == 1
    assert _count(app, AuditLog) == 1


@pytest.mark.parametrize("person", ["pending", "admin"])
def test_ineligible_accounts_cannot_get_pass_or_pdf(pass_environment, person):
    app, ids = pass_environment
    participant = _client(app, ids[person])
    for path in ["/passes/token", "/passes/download", "/passes/certificate"]:
        assert participant.get(path).status_code == 403
    response = _scan(_client(app, ids["admin"]), _issue(app, ids[person]))
    assert response.status_code == 400
    assert _count(app, CheckIn) == 0


def test_tampering_and_unrecognized_payloads_are_rejected(pass_environment):
    app, ids = pass_environment
    value = _issue(app, ids["attendee"])
    tampered = ("A" if value[0] != "A" else "B") + value[1:]
    with app.app_context():
        serializer = passes._serializer()
        invalid = [serializer.dumps(item) for item in [
            [], {"uid": True, "v": 1, "kind": "live"},
            {"uid": ids["attendee"], "v": 1, "kind": "forever"},
            {"uid": ids["attendee"], "v": 1, "kind": ["live"]},
            {"uid": 99999, "v": 1, "kind": "live"},
        ]]
    scanner = _client(app, ids["admin"])
    for token in [tampered, "not-a-token", "", None, "x" * 4097, *invalid]:
        response = _scan(scanner, token)
        assert response.status_code == 400
        assert response.get_json()["ok"] is False
    assert _count(app, CheckIn) == 0


@pytest.mark.parametrize("kind,age,accepted", [
    ("live", 179, True), ("live", 181, False),
    ("download", 181, True), ("download", 86399, True),
    ("download", 86401, False), ("live", -30, False),
])
def test_signed_timestamp_enforces_kind_specific_expiration(pass_environment, monkeypatch, kind, age, accepted):
    app, ids = pass_environment
    now = int(datetime.now(timezone.utc).timestamp())
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: now - age)
    token = _issue(app, ids["attendee"], kind)
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: now)
    response = _scan(_client(app, ids["admin"]), token)
    assert response.status_code == (200 if accepted else 400)
    assert _count(app, CheckIn) == int(accepted)


@pytest.mark.parametrize("change", ["version", "approval", "project"])
def test_revoked_or_unapproved_pass_cannot_check_in(pass_environment, change):
    app, ids = pass_environment
    person = "exhibitor" if change == "project" else "attendee"
    token = _issue(app, ids[person])
    with app.app_context():
        user = repo.get(User, ids[person])
        if change == "version":
            user.token_version += 1
        elif change == "approval":
            user.approval_status = "rejected"
        else:
            project = user.team.project
            project.status = "rejected"
            repo.save(project)
        repo.save(user)
    response = _scan(_client(app, ids["admin"]), token)
    assert response.status_code == 400
    assert _count(app, CheckIn) == 0


def test_revocation_is_checked_before_duplicate_success(pass_environment):
    app, ids = pass_environment
    scanner = _client(app, ids["admin"])
    token = _issue(app, ids["attendee"])
    assert _scan(scanner, token).status_code == 200
    with app.app_context():
        user = repo.get(User, ids["attendee"])
        user.approval_status = "rejected"
        repo.save(user)
    assert _scan(scanner, token).status_code == 400
    assert _count(app, CheckIn) == 1


def test_only_organizer_can_scan_and_gets_csrf_protection(pass_environment):
    app, ids = pass_environment
    token = _issue(app, ids["attendee"])
    for person in ["attendee", "evaluator", "exhibitor"]:
        client = _client(app, ids[person])
        assert _scan(client, token).status_code == 403
        assert client.get("/passes/scanner").status_code == 403
    assert app.test_client().post("/passes/check-in", json={"token": token}).status_code in {302, 401}
    app.config["WTF_CSRF_ENABLED"] = True
    scanner = _client(app, ids["admin"])
    assert _scan(scanner, token).status_code == 400
    with app.test_request_context():
        signed = generate_csrf()
        raw = session["csrf_token"]
    with scanner.session_transaction() as stored:
        stored["csrf_token"] = raw
    response = scanner.post("/passes/check-in", json={"token": token}, headers={"X-CSRFToken": signed})
    assert response.status_code == 200
    assert _count(app, CheckIn) == 1


def test_qr_url_is_data_only_and_get_never_checks_in(pass_environment):
    app, ids = pass_environment
    token = _issue(app, ids["attendee"])
    scanner = _client(app, ids["admin"])
    assert scanner.get("/passes/check-in?token=" + token).status_code == 405
    assert _count(app, CheckIn) == 0
    assert _scan(scanner, "https://example.invalid/pass?token=" + token).status_code == 200
    assert _count(app, CheckIn) == 1


def test_real_concurrent_scans_record_attendance_and_audit_once(pass_environment):
    app, ids = pass_environment
    token = _issue(app, ids["attendee"])
    request_barrier = Barrier(2)

    def scan():
        client = _client(app, ids["admin"])
        request_barrier.wait(timeout=10)
        response = _scan(client, token)
        return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: scan(), range(2)))
    assert all(status == 200 for status, _ in responses)
    assert sorted(data["duplicate"] for _, data in responses) == [False, True]
    assert _count(app, CheckIn) == 1
    logs = []
    with app.app_context():
        logs = repo.find(AuditLog, action="check_in")
    assert len(logs) == 1


def test_pass_pdf_and_booth_certificate_require_correct_state(pass_environment):
    app, ids = pass_environment
    exhibitor = _client(app, ids["exhibitor"])
    response = exhibitor.get("/passes/download")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-")
    assert "attachment" in response.headers["Content-Disposition"]
    assert "no-store" in response.headers["Cache-Control"]
    assert exhibitor.get("/passes/certificate").status_code == 403
    with app.app_context():
        booth = repo.save(Booth(code="E-01", project_id=ids["project"]))
        project = repo.get(Project, ids["project"])
        project.booth_id = booth.id
        repo.save(project)
    certificate = exhibitor.get("/passes/certificate")
    assert certificate.status_code == 200
    assert certificate.data.startswith(b"%PDF-")
    assert _client(app, ids["attendee"]).get("/passes/certificate").status_code == 403


def test_downloaded_pdf_qr_uses_longer_lifetime_but_obeys_revocation(pass_environment, monkeypatch):
    app, ids = pass_environment
    encoded = []
    original = passes._qr_png

    def capture_qr(value):
        encoded.append(value)
        return original(value)

    monkeypatch.setattr(passes, "_qr_png", capture_qr)
    now = int(datetime.now(timezone.utc).timestamp())
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: now - 600)
    response = _client(app, ids["attendee"]).get("/passes/download")
    assert response.status_code == 200
    assert len(encoded) == 1
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: now)
    scanner = _client(app, ids["admin"])
    assert _scan(scanner, encoded[0]).status_code == 200
    with app.app_context():
        user = repo.get(User, ids["attendee"])
        user.token_version += 1
        repo.save(user)
    assert _scan(scanner, encoded[0]).status_code == 400
    assert _count(app, CheckIn) == 1
