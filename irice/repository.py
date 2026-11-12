"""Domain repository shared by the real Firestore and local document backends."""
import hashlib
from contextvars import ContextVar
from dataclasses import asdict, fields
from datetime import datetime, timezone
from flask import abort, current_app, g
from werkzeug.local import LocalProxy
from .store import BackendUnavailable, FirestoreStore, SQLiteStore


class DuplicateValue(ValueError):
    pass


class Repository:
    def __init__(self, store):
        self.store = store
        self._active = ContextVar(f'irice_transaction_{id(self)}', default=None)

    def _cache(self):
        if not hasattr(g, '_irice_records'):
            g._irice_records = {}
        return g._irice_records

    def clear_cache(self):
        g._irice_records = {}

    def _record(self, model, data):
        if data is None:
            return None
        data = dict(data)
        for key, value in data.items():
            if key.endswith('_at') and value:
                if isinstance(value, str):
                    value = datetime.fromisoformat(value)
                if value.tzinfo is not None:
                    value = value.astimezone(timezone.utc).replace(tzinfo=None)
                data[key] = value
        names = {f.name for f in fields(model)}
        return model(**{key: value for key, value in data.items() if key in names})

    def get(self, model, record_id):
        if type(record_id) is not int or not 1 <= record_id <= 2_147_483_647:
            return None
        collection = model.__collection__
        active = self._active.get()
        if active:
            data = active.get(collection, record_id)
        else:
            key = (collection, record_id)
            cache = self._cache()
            if key not in cache:
                cache[key] = self.store.get(collection, record_id)
            data = cache[key]
        return self._record(model, data)

    def get_or_404(self, model, record_id):
        record = self.get(model, record_id)
        if record is None:
            abort(404)
        return record

    def find(self, model, **filters):
        active = self._active.get()
        if active:
            data = active.scan(model.__collection__, filters)
        else:
            cache = self._cache()
            key = (model.__collection__, tuple(sorted(filters.items())))
            if key not in cache:
                cache[key] = self.store.scan(model.__collection__, filters)
            data = cache[key]
        return [self._record(model, item) for item in data]

    def all(self, model):
        return self.find(model)

    def first(self, model, **filters):
        matches = self.find(model, **filters)
        return min(matches, key=lambda item: item.id) if matches else None

    def atomic(self, callback):
        if self._active.get():
            return callback()
        def execute(uow):
            self.clear_cache()
            token = self._active.set(uow)
            try:
                return callback()
            finally:
                self._active.reset(token)
        try:
            return self.store.atomic(execute)
        finally:
            self.clear_cache()

    def save(self, record):
        if self._active.get() is None:
            original_id = record.id
            def persist():
                record.id = original_id
                return self.save(record)
            return self.atomic(persist)
        active = self._active.get()
        if record.id is None:
            record.id = active.new_id(record.__collection__)
        if type(record.id) is not int or not 1 <= record.id <= 2_147_483_647:
            raise ValueError('Record identifier is out of range.')
        active.put(record.__collection__, record.id, asdict(record))
        return record

    def delete(self, record):
        if self._active.get() is None:
            return self.atomic(lambda: self.delete(record))
        self._active.get().delete(record.__collection__, record.id)

    def reserve_unique(self, namespace, value, record_id):
        if self._active.get() is None:
            raise RuntimeError('Reserve unique values inside an atomic operation.')
        key = hashlib.sha256(value.encode('utf-8')).hexdigest()
        collection = '_unique_' + namespace
        active = self._active.get()
        existing = active.get(collection, key)
        if existing and existing['owner_id'] != record_id:
            raise DuplicateValue('That value is already registered.')
        active.put(collection, key, {'owner_id': record_id})

    def audit(self, actor_id, action, details):
        from .models import AuditLog
        return self.save(AuditLog(user_id=actor_id, action=action, details=details))

    def health(self):
        return self.store.health()


def init_repository(app):
    backend = app.config['DATA_BACKEND']
    if backend == 'firestore':
        store = FirestoreStore(app.config)
    elif backend == 'sqlite':
        if app.config.get('VERCEL_DEPLOYMENT'):
            raise ValueError('SQLite is only supported for explicit local development and tests.')
        store = SQLiteStore(app.config['SQLITE_PATH'])
    else:
        raise ValueError('DATA_BACKEND must be firestore or sqlite.')
    app.extensions['repository'] = Repository(store)


repo = LocalProxy(lambda: current_app.extensions['repository'])
