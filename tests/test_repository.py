"""Atomic persistence and uniqueness must match across both document backends."""
import pytest

from irice.models import User
from irice.repository import DuplicateValue, repo


def test_failed_transaction_does_not_leave_partial_user_or_unique_reservation(app):
    with app.app_context():
        def aborted():
            user = repo.save(User(name="Aborted account", email="rollback@example.test"))
            repo.reserve_unique("emails", user.email, user.id)
            raise ValueError("Intentional business rejection")
        with pytest.raises(ValueError, match="Intentional business rejection"):
            repo.atomic(aborted)
        assert repo.first(User, email="rollback@example.test") is None
        def successful():
            user = repo.save(User(name="Successful account", email="rollback@example.test"))
            repo.reserve_unique("emails", user.email, user.id)
            return user.id
        user_id = repo.atomic(successful)
        assert repo.get(User, user_id).name == "Successful account"


def test_duplicate_unique_reservation_rolls_back_second_record(app):
    with app.app_context():
        def insert(name):
            user = repo.save(User(name=name, email="unique@example.test"))
            repo.reserve_unique("emails", user.email, user.id)
            return user.id
        first_id = repo.atomic(lambda: insert("First account"))
        with pytest.raises(DuplicateValue):
            repo.atomic(lambda: insert("Duplicate account"))
        assert [user.id for user in repo.find(User, email="unique@example.test")] == [first_id]


def test_atomic_read_your_writes_and_updates_are_not_stale(app):
    with app.app_context():
        user = repo.save(User(name="Original", email="fresh@example.test"))
        assert repo.get(User, user.id).name == "Original"
        def update():
            current = repo.get(User, user.id)
            current.name = "Updated"
            repo.save(current)
            assert repo.get(User, user.id).name == "Updated"
            assert repo.first(User, name="Updated").id == user.id
        repo.atomic(update)
        assert repo.get(User, user.id).name == "Updated"


def test_firestore_retry_exhaustion_becomes_safe_backend_unavailable(monkeypatch):
    """The SDK wraps exhausted Aborted retries in ValueError, not an RPC error."""
    from types import SimpleNamespace
    from google.api_core.exceptions import Aborted
    from google.cloud import firestore
    from irice.store import BackendUnavailable, FirestoreStore

    store = FirestoreStore({"FIREBASE_PROJECT_ID": "demo-irice-test"})
    store._client = SimpleNamespace(transaction=lambda **kwargs: object())

    def exhausted_transaction(_function):
        def execute(_transaction):
            raise ValueError("SDK retry limit exhausted") from Aborted("private RPC diagnostics")
        return execute

    monkeypatch.setattr(firestore, "transactional", exhausted_transaction)
    with pytest.raises(BackendUnavailable) as caught:
        store.atomic(lambda _uow: pytest.fail("An exhausted transaction must not run its callback"))
    assert "try again" in str(caught.value).lower()
    assert "private RPC" not in str(caught.value)


def test_firestore_business_value_error_is_not_misreported_as_outage(monkeypatch):
    from types import SimpleNamespace
    from google.cloud import firestore
    from irice.store import FirestoreStore

    store = FirestoreStore({"FIREBASE_PROJECT_ID": "demo-irice-test"})
    store._client = SimpleNamespace(transaction=lambda **kwargs: object())
    monkeypatch.setattr(firestore, "transactional", lambda function: function)
    business_error = ValueError("This booth is already allocated")

    def reject(_uow):
        raise business_error

    with pytest.raises(ValueError) as caught:
        store.atomic(reject)
    assert caught.value is business_error


def test_initialization_reuses_existing_case_insensitive_category(app, tmp_path):
    """An organizer's lowercase default category must survive repeat setup."""
    import uuid
    from irice import create_app
    from irice.models import Booth, Category

    config = dict(app.config)
    config.update(SQLITE_PATH=str(tmp_path / "casefold.sqlite"), FIRESTORE_COLLECTION_PREFIX="test_casefold_" + uuid.uuid4().hex + "_")
    fresh_app = create_app(config)
    try:
        with fresh_app.app_context():
            def create_category():
                category = repo.save(Category(name="computing & ai", description="Existing organizer category"))
                repo.reserve_unique("category_names", category.name.casefold(), category.id)
                return category.id
            existing_id = repo.atomic(create_category)
        runner = fresh_app.test_cli_runner()
        first = runner.invoke(args=["init-db"])
        second = runner.invoke(args=["init-db"])
        assert first.exit_code == 0, first.output
        assert second.exit_code == 0, second.output
        with fresh_app.app_context():
            matches = [category for category in repo.all(Category) if category.name.casefold() == "computing & ai"]
            assert len(matches) == 1
            assert matches[0].id == existing_id
            assert matches[0].description == "Existing organizer category"
            assert len(repo.all(Category)) == 4
            assert len(repo.all(Booth)) == 20
    finally:
        client = getattr(fresh_app.extensions["repository"].store, "_client", None)
        if client is not None:
            client.close()
