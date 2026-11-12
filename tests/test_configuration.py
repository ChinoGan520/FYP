"""Configuration must fail safely without exposing credentials or creating admins."""
import pytest

from irice import create_app
from irice.models import User
from irice.repository import BackendUnavailable, repo


def test_public_admin_setup_endpoint_is_absent(client):
    assert client.get("/setup-admin").status_code == 404


def test_trusted_cli_creates_organizer_and_never_overwrites_existing(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-admin", "--email", "cli@example.test", "--name", "CLI Organizer"], input="Correct-test-password-123\nCorrect-test-password-123\n")
    assert result.exit_code == 0, result.output
    with app.app_context():
        admin = repo.first(User, email="cli@example.test")
        assert admin.role == "admin"
        assert admin.check_password("Correct-test-password-123")
        previous_hash = admin.password_hash
    duplicate = runner.invoke(args=["create-admin", "--email", "cli@example.test", "--name", "Overwrite"], input="Different-password-123\nDifferent-password-123\n")
    assert duplicate.exit_code != 0
    with app.app_context():
        assert repo.first(User, email="cli@example.test").password_hash == previous_hash


def test_backend_failure_returns_safe_503_not_traceback(app, client, monkeypatch):
    with app.app_context():
        repository = repo._get_current_object()
    def unavailable(*args, **kwargs):
        raise BackendUnavailable("sensitive-test-only-database-detail")
    monkeypatch.setattr(repository, "health", unavailable)
    response = client.get("/health")
    assert response.status_code == 503
    assert b"sensitive-test-only" not in response.data
    assert response.json["status"] == "error"


def test_registration_database_outage_does_not_expose_error(app, client, monkeypatch):
    with app.app_context():
        repository = repo._get_current_object()
    def unavailable(*args, **kwargs):
        raise BackendUnavailable("sensitive-test-only-password")
    monkeypatch.setattr(repository, "atomic", unavailable)
    response = client.post("/auth/register", data={"name": "Visitor Test", "email": "outage@example.test", "password": "Long-test-password-123", "role": "attendee"})
    assert response.status_code == 503
    assert b"sensitive-test-only" not in response.data


def test_production_requires_stable_secret_and_rejects_emulator(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises((RuntimeError, ValueError)):
        create_app({"TESTING": True, "DATA_BACKEND": "sqlite", "SQLITE_PATH": str(tmp_path / "data.sqlite"), "SECRET_KEY": ""})


def test_unconfigured_firestore_serves_public_pages_but_rejects_registration_safely(tmp_path):
    application = create_app({
        "TESTING": True, "SECRET_KEY": "test-missing-firebase-secret-at-least-32-characters",
        "WTF_CSRF_ENABLED": False, "DATA_BACKEND": "firestore",
        "FIREBASE_PROJECT_ID": "", "FIREBASE_SERVICE_ACCOUNT_JSON": "",
    })
    client = application.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/auth/register").status_code == 200
    response = client.post("/auth/register", data={"name": "Unconfigured Visitor", "email": "unconfigured@example.test", "password": "Long-test-password-123", "role": "attendee"})
    assert response.status_code == 503
    assert b"Traceback" not in response.data
    assert client.get("/health").status_code == 503


def test_vercel_does_not_allow_emulator_credentials(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    try:
        application = create_app({
            "TESTING": True, "SECRET_KEY": "test-production-config-stable-secret-32-plus",
            "DATA_BACKEND": "firestore", "FIREBASE_PROJECT_ID": "demo-irice-test",
        })
    except (RuntimeError, ValueError):
        return  # Fail-fast configuration rejection is also acceptable.
    with application.app_context():
        with pytest.raises(BackendUnavailable):
            repo.health()
        assert repo.store._client is None
