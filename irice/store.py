"""Document storage with buffered, read-before-write transactions.

Firestore is the deployed backend. SQLite is an explicit local/test backend,
never an automatic fallback when Firebase is unavailable.
"""
from contextlib import closing
import copy
import json
import os
import re
import sqlite3
import time
import random
import threading
from datetime import datetime
from pathlib import Path


class BackendUnavailable(Exception):
    """Safe application-facing failure; never includes credentials or raw RPC data."""
    CODES = frozenset({'BACKEND_UNAVAILABLE', 'PROJECT_ID_MISSING', 'SERVICE_ACCOUNT_MISSING',
        'SERVICE_ACCOUNT_JSON_INVALID', 'PROJECT_ID_MISMATCH', 'SERVICE_ACCOUNT_INVALID',
        'EMULATOR_NOT_ALLOWED', 'FIRESTORE_PERMISSION_DENIED', 'FIRESTORE_NOT_FOUND',
        'FIRESTORE_UNAUTHENTICATED', 'FIRESTORE_NETWORK_UNAVAILABLE', 'FIRESTORE_CONFIG_INVALID'})

    def __init__(self, message, code='BACKEND_UNAVAILABLE'):
        super().__init__(message)
        self.code = code if code in self.CODES else 'BACKEND_UNAVAILABLE'


def firebase_failure(error):
    from google.api_core import exceptions
    from google.auth.exceptions import GoogleAuthError
    codes = ((exceptions.PermissionDenied, 'FIRESTORE_PERMISSION_DENIED'),
             (exceptions.NotFound, 'FIRESTORE_NOT_FOUND'),
             (exceptions.Unauthenticated, 'FIRESTORE_UNAUTHENTICATED'),
             (GoogleAuthError, 'FIRESTORE_UNAUTHENTICATED'),
             ((exceptions.ServiceUnavailable, exceptions.DeadlineExceeded), 'FIRESTORE_NETWORK_UNAVAILABLE'))
    for kind, code in codes:
        if isinstance(error, kind):
            return BackendUnavailable('Firebase is unavailable. Check the health diagnostic code.', code)
    return BackendUnavailable('Firebase could not be reached.')


class UnitOfWork:
    def __init__(self, reader, scanner):
        self.reader, self.scanner = reader, scanner
        self.writes = {}
        self.reads = {}

    def get(self, collection, key):
        pair = (collection, str(key))
        if pair in self.writes:
            return copy.deepcopy(self.writes[pair])
        if pair not in self.reads:
            self.reads[pair] = self.reader(*pair)
        return copy.deepcopy(self.reads[pair])

    def scan(self, collection, filters):
        rows = self.scanner(collection, filters)
        merged = {str(row['id']): row for row in rows}
        for (name, key), data in self.writes.items():
            if name == collection:
                merged.pop(key, None)
                if data is not None and all(data.get(k) == v for k, v in filters.items()):
                    merged[key] = copy.deepcopy(data)
        return list(merged.values())

    def put(self, collection, key, data):
        self.writes[(collection, str(key))] = copy.deepcopy(data)

    def delete(self, collection, key):
        self.writes[(collection, str(key))] = None

    def new_id(self, collection):
        value = self.get('_counters', collection) or {'next_id': 1}
        result = value['next_id']
        self.put('_counters', collection, {'next_id': result + 1})
        return result


class SQLiteStore:
    def __init__(self, path):
        self.path = str(path)
        if self.path == ':memory:':
            raise ValueError('Use a temporary SQLite file so transactional connections share state.')
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS documents (collection TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (collection,key))')

    def connect(self):
        return sqlite3.connect(self.path, timeout=15, isolation_level=None)

    def _get(self, connection, collection, key):
        row = connection.execute('SELECT data FROM documents WHERE collection=? AND key=?', (collection, str(key))).fetchone()
        return json.loads(row[0]) if row else None

    def _scan(self, connection, collection, filters):
        rows = [json.loads(row[0]) for row in connection.execute('SELECT data FROM documents WHERE collection=?', (collection,))]
        return [row for row in rows if all(row.get(k) == v for k, v in filters.items())]

    def get(self, collection, key):
        with closing(self.connect()) as connection:
            return self._get(connection, collection, key)

    def scan(self, collection, filters):
        with closing(self.connect()) as connection:
            return self._scan(connection, collection, filters)

    def atomic(self, callback):
        connection = self.connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            uow = UnitOfWork(lambda c, k: self._get(connection, c, k), lambda c, f: self._scan(connection, c, f))
            result = callback(uow)
            for (collection, key), data in uow.writes.items():
                if data is None:
                    connection.execute('DELETE FROM documents WHERE collection=? AND key=?', (collection, key))
                else:
                    connection.execute('INSERT INTO documents(collection,key,data) VALUES(?,?,?) ON CONFLICT(collection,key) DO UPDATE SET data=excluded.data', (collection, key, json.dumps(data, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value))))
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def health(self):
        with closing(self.connect()) as connection:
            connection.execute('SELECT 1').fetchone()


class FirestoreStore:
    def __init__(self, config):
        self.project = config.get('FIREBASE_PROJECT_ID')
        self.credential_json = config.get('FIREBASE_SERVICE_ACCOUNT_JSON')
        self.prefix = config.get('FIRESTORE_COLLECTION_PREFIX', '')
        self.deployed = bool(os.environ.get('VERCEL'))
        if not re.fullmatch(r'[A-Za-z0-9_-]*', self.prefix):
            raise ValueError('Invalid Firestore collection prefix.')
        self._client = None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    try:
                        from google.cloud import firestore
                        from google.oauth2 import service_account
                        from google.auth.credentials import AnonymousCredentials
                        if not self.project:
                            raise BackendUnavailable('Configure the Firebase project ID.', 'PROJECT_ID_MISSING')
                        if os.environ.get('FIRESTORE_EMULATOR_HOST'):
                            if self.deployed:
                                raise BackendUnavailable('Remove emulator settings from Vercel.', 'EMULATOR_NOT_ALLOWED')
                            credential = AnonymousCredentials()
                        elif self.credential_json:
                            try:
                                info = json.loads(self.credential_json)
                            except (ValueError, TypeError):
                                raise BackendUnavailable('Provide the complete service account JSON.', 'SERVICE_ACCOUNT_JSON_INVALID') from None
                            if not isinstance(info, dict) or info.get('type') != 'service_account':
                                raise BackendUnavailable('Provide a service account JSON, not web configuration.', 'SERVICE_ACCOUNT_JSON_INVALID')
                            if info.get('project_id') != self.project:
                                raise BackendUnavailable('Project ID and service account must match.', 'PROJECT_ID_MISMATCH')
                            try:
                                credential = service_account.Credentials.from_service_account_info(info)
                            except Exception:
                                raise BackendUnavailable('The service account key is incomplete or invalid.', 'SERVICE_ACCOUNT_INVALID') from None
                        else:
                            if self.deployed:
                                raise BackendUnavailable('Configure the service account JSON on Vercel.', 'SERVICE_ACCOUNT_MISSING')
                            credential = None  # Local Application Default Credentials, if configured.
                        self._client = firestore.Client(project=self.project, credentials=credential)
                    except BackendUnavailable:
                        raise
                    except Exception:
                        raise BackendUnavailable('Firebase configuration is missing or invalid.', 'FIRESTORE_CONFIG_INVALID') from None
        return self._client

    def collection(self, name):
        return self.client.collection(self.prefix + name)

    def _get(self, collection, key, transaction=None):
        snapshot = self.collection(collection).document(str(key)).get(transaction=transaction, timeout=10, retry=None)
        return snapshot.to_dict() if snapshot.exists else None

    def _scan(self, collection, filters, transaction=None):
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = self.collection(collection)
        for key, value in filters.items():
            query = query.where(filter=FieldFilter(key, '==', value))
        return [snapshot.to_dict() for snapshot in query.stream(transaction=transaction, timeout=10, retry=None)]

    def get(self, collection, key):
        try:
            return self._get(collection, key)
        except BackendUnavailable:
            raise
        except Exception as error:
            raise firebase_failure(error) from None

    def scan(self, collection, filters):
        try:
            return self._scan(collection, filters)
        except BackendUnavailable:
            raise
        except Exception as error:
            raise firebase_failure(error) from None

    def atomic(self, callback):
        from google.api_core.exceptions import Aborted, GoogleAPICallError, RetryError
        from google.cloud import firestore

        @firestore.transactional
        def execute(transaction):
            uow = UnitOfWork(lambda c, k: self._get(c, k, transaction), lambda c, f: self._scan(c, f, transaction))
            result = callback(uow)
            # All database reads have completed before the first actual write.
            for (collection, key), data in uow.writes.items():
                reference = self.collection(collection).document(key)
                if data is None:
                    transaction.delete(reference)
                else:
                    transaction.set(reference, data)
            return result
        try:
            # Firestore can abort a transaction while acquiring a read lock,
            # before the SDK's commit-only retry loop is entered. Retry that
            # complete attempt as well; never retry business validation errors.
            for attempt in range(5):
                try:
                    return execute(self.client.transaction(max_attempts=5))
                except Aborted:
                    if attempt == 4:
                        raise
                    time.sleep(random.uniform(0.02, 0.08) * (attempt + 1))
        except BackendUnavailable:
            raise
        except (GoogleAPICallError, RetryError) as error:
            raise firebase_failure(error) from None
        except ValueError as error:
            if isinstance(error.__cause__, Aborted):
                raise BackendUnavailable('Firebase is busy. Please try again shortly.') from None
            raise

    def health(self):
        self.get('_meta', 'health')
