"""Flask application factory for the Firestore-backed I-RICE portal."""
import os
import secrets
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request
from flask_wtf.csrf import CSRFError
from .extensions import csrf
from .repository import BackendUnavailable, init_repository, repo


def _local_secret(instance):
    keyfile = instance / 'secret.key'
    candidate = instance / f'.secret-{secrets.token_hex(12)}.tmp'
    try:
        fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(secrets.token_hex(32))
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(candidate, keyfile)
        except FileExistsError:
            pass
    finally:
        candidate.unlink(missing_ok=True)
    return keyfile.read_text(encoding='utf-8').strip()


def create_app(test_config=None):
    deployed = bool(os.environ.get('VERCEL'))
    app = Flask(__name__, instance_relative_config=True, instance_path='/tmp/irice' if deployed else None)
    instance = Path(app.instance_path)
    instance.mkdir(parents=True, exist_ok=True)
    if not deployed:
        load_dotenv(instance / '.env', override=False)
    secret = (test_config or {}).get('SECRET_KEY') or os.environ.get('SECRET_KEY')
    if not secret and not deployed:
        secret = _local_secret(instance)
    if not secret or len(secret) < 32 or secret.startswith('REPLACE_WITH') or secret.startswith('fallback_secret'):
        raise RuntimeError('Set a random, persistent SECRET_KEY of at least 32 characters.')
    app.config.from_mapping(
        SECRET_KEY=secret,
        DATA_BACKEND=os.environ.get('DATA_BACKEND', 'firestore'),
        SQLITE_PATH=str(instance / 'irice-local.sqlite3'),
        FIREBASE_PROJECT_ID=os.environ.get('FIREBASE_PROJECT_ID'),
        FIREBASE_SERVICE_ACCOUNT_JSON=os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON'),
        FIRESTORE_COLLECTION_PREFIX='',
        VERCEL_DEPLOYMENT=deployed,
        POSTERS_ENABLED=False,
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '1' if deployed else '0') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        WTF_CSRF_TIME_LIMIT=8 * 60 * 60,
        EVENT_NAME='I-RICE 2026',
    )
    if test_config:
        app.config.update(test_config)
    init_repository(app)
    csrf.init_app(app)
    from . import admin, auth, cli, participant, passes
    app.register_blueprint(auth.bp)
    app.register_blueprint(participant.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(passes.bp)
    cli.init_app(app)

    @app.context_processor
    def shared_context():
        from .models import Category
        categories = []
        if request.endpoint in {'participant.project', 'admin.booths', 'admin.applications'}:
            try:
                categories = sorted(repo.all(Category), key=lambda record: record.name.casefold())
            except BackendUnavailable:
                pass  # A safe error page must not start another failing read.
        return {'current_user': getattr(g, 'user', None), 'categories': categories, 'poster_upload_enabled': False}

    @app.template_filter('datetime')
    def format_datetime(value, fmt='%d %b %Y, %I:%M %p'):
        if not value:
            return '—'
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(ZoneInfo('Asia/Kuala_Lumpur')).strftime(fmt)

    @app.template_filter('initials')
    def initials(value):
        return ''.join(part[0] for part in str(value or '?').split()[:2]).upper()

    @app.get('/health')
    def health():
        repo.health()
        return jsonify(status='ok', database='connected', backend=app.config['DATA_BACKEND'])

    @app.errorhandler(BackendUnavailable)
    def backend_failure(error):
        app.logger.warning('Application data backend unavailable: %s', error.code)
        if request.path == '/health':
            response = jsonify(status='error', database='unavailable', backend=app.config['DATA_BACKEND'], reason=error.code)
        elif request.is_json:
            response = jsonify(ok=False, message='The data service is temporarily unavailable. Please try again shortly.')
        else:
            response = app.make_response(render_template('error.html', code=503, message='The data service is temporarily unavailable. Please try again shortly or contact the organizer.'))
        response.status_code = 503
        response.headers['Retry-After'] = '30'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if getattr(g, 'user', None) or request.path.startswith('/auth'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(CSRFError)
    def csrf_failure(error):
        message = 'Your form session expired. Refresh the page and try again.'
        if request.is_json:
            return jsonify(ok=False, message=message), 400
        return render_template('error.html', code=400, message=message), 400

    for code, message in [(400, 'This request could not be processed.'), (403, 'You do not have access to this page.'), (404, 'We could not find this page.'), (413, 'The request is too large. Poster uploads are currently disabled.')]:
        def handle_error(error, status=code, description=message):
            if request.is_json:
                return jsonify(ok=False, message=description), status
            return render_template('error.html', code=status, message=description), status
        app.register_error_handler(code, handle_error)
    return app
