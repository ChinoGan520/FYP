"""Isolated document-store fixtures; never connect tests to a live Firebase project."""
import os
import uuid

import pytest

from irice import create_app
from irice.cli import seed_defaults
from irice.models import User
from irice.repository import repo


@pytest.fixture
def app(tmp_path):
    firestore = os.environ.get("IRICE_TEST_FIRESTORE") == "1"
    if firestore:
        host = os.environ.get("FIRESTORE_EMULATOR_HOST", "")
        project = os.environ.get("FIREBASE_PROJECT_ID", "")
        if project != "demo-irice-test" or host not in {"127.0.0.1:8080", "localhost:8080"}:
            raise RuntimeError("Firestore tests require demo-irice-test on the local emulator port8080")
    config = {
        "TESTING": True,
        "SECRET_KEY": "test-only-secret-key-32-characters-minimum",
        "WTF_CSRF_ENABLED": False,
        "DATA_BACKEND": "firestore" if firestore else "sqlite",
        "SQLITE_PATH": str(tmp_path / "documents.sqlite"),
        "FIREBASE_PROJECT_ID": "demo-irice-test" if firestore else "",
        "FIRESTORE_COLLECTION_PREFIX": "test_" + uuid.uuid4().hex + "_",
    }
    app = create_app(config)
    with app.app_context():
        seed_defaults()
    yield app
    firestore_client = getattr(app.extensions["repository"].store, "_client", None)
    if firestore_client is not None:
        firestore_client.close()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin(app):
    with app.app_context():
        user = User(name="Test organizer", email="organizer@example.test", role="admin", approval_status="approved")
        user.set_password("Testing-password-123")
        repo.save(user)
        return user.id


@pytest.fixture
def login(client):
    def sign_in(user_id):
        with client.session_transaction() as session:
            session.clear()
            session["user_id"] = user_id
    return sign_in
