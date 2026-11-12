"""Organizer workflow tests: privilege boundaries, review, allocations and CSV."""

import csv
import io

import pytest

from irice.repository import repo
from irice.models import AuditLog, Booth, Category, CheckIn, Project, Team, User


@pytest.fixture()
def organizer_app(app, admin):
    with app.app_context():
        category = repo.first(Category, name="Computing & AI")
        other = repo.first(Category, name="Engineering & Technology")
        users = [User(name=name, email=f"u{i}@example.test", role="exhibitor", password_hash="test")
                 for i, name in enumerate(["Alpha", "Beta", "Gamma"], 1)]
        attendee = repo.save(User(name="Visitor", email="visitor@example.test", role="attendee", password_hash="test"))
        evaluator = repo.save(User(name="Evaluator", email="evaluator@example.test", role="evaluator", password_hash="test"))
        projects = []
        for i, user in enumerate(users):
            repo.save(user)
            team = repo.save(Team(user_id=user.id, name=f"Team {user.name}"))
            project = repo.save(Project(team_id=team.id, category_id=category.id, title=f"Project {user.name}", abstract="Meaningful abstract", status="approved" if i < 2 else "pending"))
            projects.append(project)
        for code, category_id in [("A01", category.id), ("A02", category.id), ("B01", other.id), ("G01", None)]:
            booth = repo.save(Booth(code=code, category_id=category_id))
            repo.atomic(lambda booth=booth: repo.reserve_unique("booth_codes", booth.code, booth.id))
        app.config["TEST_IDS"] = {
            "admin": admin, "participant": users[0].id, "attendee": attendee.id,
            "evaluator": evaluator.id, "projects": [p.id for p in projects],
            "category": category.id,
        }
    return app


@pytest.fixture()
def organizer_client(organizer_app):
    client = organizer_app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = organizer_app.config["TEST_IDS"]["admin"]
    return client


def _booth_id(app, code):
    with app.app_context():
        return repo.first(Booth, code=code).id


def test_participant_cannot_review_or_allocate(organizer_app):
    client = organizer_app.test_client()
    project_id = organizer_app.config["TEST_IDS"]["projects"][0]
    # Authentication is required before any organizer workflow can be read.
    assert client.get("/admin/").status_code == 302
    with client.session_transaction() as session:
        session["user_id"] = organizer_app.config["TEST_IDS"]["participant"]
    for path in ["/admin/", "/admin/report.csv", "/admin/attendees"]:
        assert client.get(path).status_code == 403
    assert client.post(f"/admin/applications/{project_id}/review", data={"status": "approved"}).status_code == 403
    assert client.post(f"/admin/applications/{project_id}/booth", data={"booth_id": "1"}).status_code == 403


def test_rejection_requires_comment_then_revokes_booth_and_pass(organizer_app, organizer_client):
    project_id = organizer_app.config["TEST_IDS"]["projects"][0]
    booth_id = _booth_id(organizer_app, "A01")
    organizer_client.post(f"/admin/applications/{project_id}/booth", data={"booth_id": booth_id})
    response = organizer_client.post(f"/admin/applications/{project_id}/review", data={"status": "rejected", "comment": ""})
    assert response.status_code == 302
    with organizer_app.app_context():
        assert repo.get(Project, project_id).status == "approved"
    organizer_client.post(f"/admin/applications/{project_id}/review", data={"status": "rejected", "comment": "Please clarify the research methodology."})
    with organizer_app.app_context():
        project = repo.get(Project, project_id)
        assert project.status == "rejected"
        assert project.review_comment == "Please clarify the research methodology."
        assert project.booth is None
        assert project.team.user.token_version == 2
        assert repo.first(AuditLog, action="project.review") is not None


def test_allocation_enforces_approval_category_and_exclusive_occupancy(organizer_app, organizer_client):
    p1, p2, pending = organizer_app.config["TEST_IDS"]["projects"]
    a1 = _booth_id(organizer_app, "A01")
    a2 = _booth_id(organizer_app, "A02")
    b1 = _booth_id(organizer_app, "B01")
    organizer_client.post(f"/admin/applications/{pending}/booth", data={"booth_id": a1})
    organizer_client.post(f"/admin/applications/{p1}/booth", data={"booth_id": b1})
    with organizer_app.app_context():
        assert repo.get(Project, pending).booth is None
        assert repo.get(Project, p1).booth is None
    organizer_client.post(f"/admin/applications/{p1}/booth", data={"booth_id": a1})
    organizer_client.post(f"/admin/applications/{p2}/booth", data={"booth_id": a1})
    with organizer_app.app_context():
        assert repo.get(Booth, a1).project_id == p1
        assert repo.get(Project, p2).booth is None
    # Reassignment must release the original booth atomically.
    organizer_client.post(f"/admin/applications/{p1}/booth", data={"booth_id": a2})
    with organizer_app.app_context():
        assert repo.get(Booth, a1).project_id is None
        assert repo.get(Booth, a2).project_id == p1
    organizer_client.post(f"/admin/applications/{p2}/booth", data={"booth_id": a1})
    organizer_client.post(f"/admin/applications/{p1}/booth", data={"booth_id": ""})
    with organizer_app.app_context():
        assert repo.get(Booth, a1).project_id == p2
        assert repo.get(Booth, a2).project_id is None


def test_visitor_review_and_exhibitor_review_boundary(organizer_app, organizer_client):
    ids = organizer_app.config["TEST_IDS"]
    organizer_client.post(f"/admin/attendees/{ids['attendee']}/review", data={"status": "approved"})
    organizer_client.post(f"/admin/attendees/{ids['attendee']}/review", data={"status": "rejected", "comment": "Event capacity reached."})
    assert organizer_client.post(f"/admin/attendees/{ids['participant']}/review", data={"status": "approved"}).status_code == 400
    with organizer_app.app_context():
        visitor = repo.get(User, ids["attendee"])
        assert visitor.approval_status == "rejected"
        assert visitor.token_version == 2
        assert repo.get(User, ids["participant"]).approval_status == "pending"


def test_real_dashboard_counts_and_category_breakdown(organizer_app, organizer_client, monkeypatch):
    from irice import admin
    ids = organizer_app.config["TEST_IDS"]
    with organizer_app.app_context():
        visitor = repo.get(User, ids["attendee"])
        visitor.approval_status = "approved"
        repo.save(visitor)
        repo.save(CheckIn(id=visitor.id, user_id=visitor.id, scanner_id=ids["admin"]))
    captured = {}
    monkeypatch.setattr(admin, "render_template", lambda template, **context: captured.update(context) or "dashboard")
    assert organizer_client.get("/admin/").status_code == 200
    metrics = captured["metrics"]
    assert metrics["total_projects"] == 3
    assert metrics["approved_projects"] == 2
    assert metrics["pending_projects"] == 1
    assert metrics["total_people"] == 5
    assert metrics["approved_people"] == 3
    assert metrics["checked_in"] == 1
    assert metrics["checkin_rate"] == 20
    assert {row["name"]: row["count"] for row in captured["category_stats"]} == {"Computing & AI": 3, "Engineering & Technology": 0, "Sustainability": 0, "Creative Innovation": 0}


def test_csv_export_preserves_data_without_spreadsheet_formulas(organizer_app, organizer_client):
    ids = organizer_app.config["TEST_IDS"]
    with organizer_app.app_context():
        user = repo.get(User, ids["participant"])
        user.name = " =HYPERLINK(\"https://example.test\")"
        user.organization = "@SUM(1,2)"
        repo.save(user)
    response = organizer_client.get("/admin/report.csv")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
    assert len(rows) == 6
    row = next(row for row in rows[1:] if row[1] == "u1@example.test")
    assert row[0].startswith("' =")
    assert row[3] == "'@SUM(1,2)"
    assert row[6] == "Project Alpha"


def test_booth_and_category_creation_validate_without_mutating_existing(organizer_app, organizer_client):
    organizer_client.post("/admin/booths", data={"code": "A01", "location": "Duplicate"})
    organizer_client.post("/admin/booths", data={"code": "../secret", "location": "Invalid"})
    organizer_client.post("/admin/booths", data={"code": "C01", "location": "Hall C"})
    assert organizer_client.post("/admin/booths", data={"code": "BAD01", "category_id": "²"}).status_code == 302
    assert organizer_client.post("/admin/booths", data={"code": "BAD02", "category_id": "9" * 200}).status_code == 302
    organizer_client.post("/admin/categories", data={"name": "Science", "color": "not-a-color"})
    organizer_client.post("/admin/categories", data={"name": "Science", "description": "A" * 241})
    organizer_client.post("/admin/categories", data={"name": "Science", "description": "Discovery", "color": "#457eac"})
    with organizer_app.app_context():
        booths = repo.all(Booth)
        assert len(booths) == 25
        assert next(booth for booth in booths if booth.code == "A01").location != "Duplicate"
        assert repo.first(Category, name="Science").description == "Discovery"


def test_concurrent_allocations_cannot_double_book(app, admin):
    """Two organizer sessions racing for one booth must admit only one owner."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    with app.app_context():
        category = repo.first(Category)
        projects = []
        for i in range(2):
            user = repo.save(User(name=f"Concurrent exhibitor {i}", email=f"concurrent-{i}@example.test", role="exhibitor", password_hash="test"))
            team = repo.save(Team(user_id=user.id, name=f"Concurrent team {i}", institution="Example College"))
            project = repo.save(Project(team_id=team.id, title=f"Concurrent project {i}", category_id=category.id, abstract="Concurrency test abstract.", status="approved"))
            projects.append(project)
        booth = repo.save(Booth(code="RACE-01", category_id=category.id, location="Concurrency test hall"))
        project_ids = [project.id for project in projects]
        booth_id = booth.id

    barrier = Barrier(2)

    def claim(project_id):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = admin
        barrier.wait(timeout=10)
        response = client.post(f"/admin/applications/{project_id}/booth", data={"booth_id": booth_id})
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim, project_id) for project_id in project_ids]
        assert [future.result(timeout=30) for future in futures] == [302, 302]

    with app.app_context():
        assigned = repo.get(Booth, booth_id)
        assert assigned.project_id in project_ids
        owners = [repo.get(Project, item) for item in project_ids]
        assert sum(project.booth is not None for project in owners) == 1
        assert sum(1 for project in owners if project.booth and project.booth.id == booth_id) == 1
        assert len(repo.find(AuditLog, action="booth.allocate")) == 1
