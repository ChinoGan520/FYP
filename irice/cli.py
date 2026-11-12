"""Explicit, non-destructive Firestore setup and trusted organizer provisioning."""
import re
import secrets
import click
from flask.cli import with_appcontext
from .repository import BackendUnavailable, DuplicateValue, repo
from .models import Booth, Category, Project, Team, User

DEFAULT_CATEGORIES = [
    ('Engineering & Technology', 'Practical engineering and connected systems.', '#167d75'),
    ('Computing & AI', 'Software, data science and artificial intelligence.', '#5366b0'),
    ('Sustainability', 'Ideas for a healthier, more sustainable future.', '#60945a'),
    ('Creative Innovation', 'Design, learning and interdisciplinary innovation.', '#bc8148'),
]


def seed_defaults():
    def initialize():
        existing_categories = {category.name.casefold(): category for category in repo.all(Category)}
        for name, description, color in DEFAULT_CATEGORIES:
            category = existing_categories.get(name.casefold())
            if category is None:
                category = repo.save(Category(name=name, description=description, color=color))
            repo.reserve_unique('category_names', name.casefold(), category.id)
        for index in range(1, 21):
            code = f'A-{index:02d}'
            booth = repo.first(Booth, code=code)
            if booth is None:
                booth = repo.save(Booth(code=code, location='Main exhibition hall'))
            repo.reserve_unique('booth_codes', code, booth.id)
    repo.atomic(initialize)


@click.command('init-db')
@with_appcontext
def init_db():
    """Add default categories and 20 booths once; preserve existing records."""
    try:
        repo.health()
        seed_defaults()
    except BackendUnavailable as error:
        raise click.ClickException(str(error)) from None
    click.echo('Data initialized. Default categories and 20 booths are available.')


@click.command('create-admin')
@click.option('--email', prompt=True)
@click.option('--name', prompt=True, default='Event organizer')
@click.password_option(confirmation_prompt=True)
@with_appcontext
def create_admin(email, name, password):
    """Create an organizer using a trusted terminal; no public bootstrap route."""
    email, name = email.strip().lower(), name.strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254:
        raise click.ClickException('Enter a valid email address.')
    if not 2 <= len(name) <= 120 or not 10 <= len(password) <= 128:
        raise click.ClickException('Use a name of 2–120 characters and a password of 10–128 characters.')
    # Hash once outside transaction retries; retain no plaintext in the store.
    password_holder = User()
    password_holder.set_password(password)
    def create():
        if repo.first(User, email=email):
            raise DuplicateValue('That account already exists. No roles or passwords were changed.')
        user = repo.save(User(name=name, email=email, role='admin', approval_status='approved', password_hash=password_holder.password_hash))
        repo.reserve_unique('emails', email, user.id)
        repo.audit(user.id, 'organizer.created', 'Organizer provisioned through the trusted CLI.')
    try:
        repo.atomic(create)
    except (DuplicateValue, BackendUnavailable) as error:
        raise click.ClickException(str(error)) from None
    click.echo('Organizer created. Sign in using the credentials you just chose.')


@click.command('seed-demo')
@with_appcontext
def seed_demo():
    """Add labelled fictional examples. No known passwords or admin accounts."""
    seed_defaults()
    examples = [
        ('AquaSense', 'Smarter water for every community', 'Engineering & Technology', 'approved'),
        ('Neural Nexus', 'An accessible learning companion powered by AI', 'Computing & AI', 'pending'),
        ('Green Circuit', 'Turning food waste into a circular opportunity', 'Sustainability', 'pending'),
        ('Studio Bloom', 'Tactile storytelling for inclusive classrooms', 'Creative Innovation', 'approved'),
        ('Urban Pulse', 'Low-cost sensing for more liveable cities', 'Engineering & Technology', 'rejected'),
    ]
    added = 0
    for index, (team_name, title, category_name, status) in enumerate(examples, 1):
        holder = User()
        holder.set_password(secrets.token_urlsafe(32))
        def create_example():
            email = f'demo.exhibitor{index}@example.test'
            if repo.first(User, email=email):
                return 0
            user = repo.save(User(name=f'Sample Exhibitor {index}', email=email, role='exhibitor', organization='Southern University College', password_hash=holder.password_hash))
            repo.reserve_unique('emails', email, user.id)
            team = repo.save(Team(user_id=user.id, name=f'{team_name} [DEMO]', institution='Southern University College', members='Fictional sample data'))
            category = repo.first(Category, name=category_name)
            project = repo.save(Project(team_id=team.id, category_id=category.id, title=title, abstract=f'Fictional demonstration entry. {title}. This sample illustrates the exhibition registration and review workflow and is not a real submission.', status=status, requirements='One table and a power outlet.', review_comment='Please clarify your evaluation method.' if status == 'rejected' else ''))
            if status == 'approved':
                available = sorted(repo.find(Booth, project_id=None), key=lambda b: b.code)
                if available:
                    booth = available[0]
                    booth.project_id, project.booth_id = project.id, booth.id
                    repo.save(booth)
                    repo.save(project)
            return 1
        added += repo.atomic(create_example)
    click.echo(f'Added {added} fictional demo projects. Sample accounts have random, undisclosed passwords.')


def init_app(app):
    for command in (init_db, create_admin, seed_demo):
        app.cli.add_command(command)
