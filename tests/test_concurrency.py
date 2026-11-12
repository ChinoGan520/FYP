"""Real concurrent document transactions must preserve application invariants."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from irice.models import Project, Team, User
from irice.repository import repo


def test_simultaneous_first_submissions_do_not_duplicate_or_fail(app):
    with app.app_context():
        user = repo.save(User(name="Concurrent exhibitor", email="concurrent@example.test", role="exhibitor", password_hash="unused"))
        repo.save(Team(user_id=user.id, name="Concurrent team", institution="Southern University College"))
        user_id = user.id
    rendezvous = Barrier(2, timeout=10)

    def submit(index):
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["user_id"] = user_id
            rendezvous.wait()
            response = client.post("/project", data={
                "title": f"Concurrent submission {index}", "category_id": "1",
                "abstract": "A meaningful project abstract describing a reliable monitoring prototype and its intended community evaluation.",
                "requirements": "One table",
            })
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(submit, [1, 2]))
    assert statuses == [302, 302]
    with app.app_context():
        assert len(repo.all(Project)) == 1
        assert repo.first(Project).status == "pending"


def test_simultaneous_duplicate_email_creates_one_account(app):
    barrier = Barrier(2, timeout=10)

    def register(index):
        with app.test_client() as client:
            barrier.wait()
            return client.post("/auth/register", data={
                "name": f"Concurrent Visitor {index}", "email": "same@example.test",
                "password": "Long-test-password-123", "role": "attendee",
            }).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(register, [1, 2]))
    assert sorted(statuses) == [302, 400]
    with app.app_context():
        assert len(repo.find(User, email="same@example.test")) == 1
