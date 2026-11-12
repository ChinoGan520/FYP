import pytest
from google.api_core import exceptions
from irice import create_app
from irice.repository import BackendUnavailable, repo
from irice.store import FirestoreStore, firebase_failure


@pytest.mark.parametrize('credential,project,expected', [
    ('', '', 'PROJECT_ID_MISSING'),
    ('not-json', 'test-project', 'SERVICE_ACCOUNT_JSON_INVALID'),
    ('{"apiKey":"not-a-service-account"}', 'test-project', 'SERVICE_ACCOUNT_JSON_INVALID'),
    ('{"type":"service_account","project_id":"other"}', 'test-project', 'PROJECT_ID_MISMATCH'),
    ('{"type":"service_account","project_id":"test-project"}', 'test-project', 'SERVICE_ACCOUNT_INVALID'),
])
def test_configuration_has_actionable_secret_free_code(monkeypatch, credential, project, expected):
    monkeypatch.delenv('FIRESTORE_EMULATOR_HOST', raising=False)
    store = FirestoreStore({'FIREBASE_PROJECT_ID':project,'FIREBASE_SERVICE_ACCOUNT_JSON':credential})
    with pytest.raises(BackendUnavailable) as error:
        store.health()
    assert error.value.code == expected
    assert 'not-a-service-account' not in str(error.value)


@pytest.mark.parametrize('error,expected', [
    (exceptions.PermissionDenied('private diagnostic'), 'FIRESTORE_PERMISSION_DENIED'),
    (exceptions.NotFound('private diagnostic'), 'FIRESTORE_NOT_FOUND'),
    (exceptions.Unauthenticated('private diagnostic'), 'FIRESTORE_UNAUTHENTICATED'),
    (exceptions.ServiceUnavailable('private diagnostic'), 'FIRESTORE_NETWORK_UNAVAILABLE'),
])
def test_rpc_errors_are_classified_without_exposing_details(error, expected):
    result = firebase_failure(error)
    assert result.code == expected
    assert 'private diagnostic' not in str(result)


def test_health_returns_only_safe_diagnostic(app, client, monkeypatch):
    with app.app_context():
        repository = repo._get_current_object()
    def fail():
        raise BackendUnavailable('a-sensitive-value', 'PROJECT_ID_MISMATCH')
    monkeypatch.setattr(repository, 'health', fail)
    result=client.get('/health')
    assert result.status_code == 503
    assert result.json['reason'] == 'PROJECT_ID_MISMATCH'
    assert b'a-sensitive-value' not in result.data


def test_aborted_read_retries_complete_transaction(monkeypatch):
    from google.cloud import firestore
    attempts=[]
    store=FirestoreStore({'FIREBASE_PROJECT_ID':'demo-irice-test'})
    class Client:
        def transaction(self, **kwargs):
            return object()
    store._client=Client()
    def transactional(callback):
        def run(transaction):
            attempts.append(transaction)
            if len(attempts)==1:
                raise exceptions.Aborted('read lock contention')
            return 'completed'
        return run
    monkeypatch.setattr(firestore, 'transactional', transactional)
    monkeypatch.setattr('irice.store.time.sleep', lambda _:None)
    assert store.atomic(lambda _:None)=='completed'
    assert len(attempts)==2
