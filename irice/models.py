"""Firestore domain records. Relationships are resolved through the repository."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar
from werkzeug.security import check_password_hash, generate_password_hash


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class User:
    __collection__: ClassVar[str] = 'users'
    id: int | None = None
    name: str = ''
    email: str = ''
    password_hash: str = ''
    role: str = 'exhibitor'
    organization: str = ''
    phone: str = ''
    approval_status: str = 'pending'
    review_comment: str = ''
    token_version: int = 1
    created_at: datetime = field(default_factory=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def team(self):
        from .repository import repo
        return repo.first(Team, user_id=self.id)

    @property
    def checkin(self):
        from .repository import repo
        return repo.first(CheckIn, user_id=self.id)


@dataclass
class Team:
    __collection__: ClassVar[str] = 'teams'
    id: int | None = None
    user_id: int | None = None
    name: str = ''
    institution: str = ''
    members: str = ''

    @property
    def user(self):
        from .repository import repo
        return repo.get(User, self.user_id)

    @property
    def project(self):
        from .repository import repo
        return repo.first(Project, team_id=self.id)


@dataclass
class Category:
    __collection__: ClassVar[str] = 'categories'
    id: int | None = None
    name: str = ''
    description: str = ''
    color: str = '#167d75'


@dataclass
class Project:
    __collection__: ClassVar[str] = 'projects'
    id: int | None = None
    team_id: int | None = None
    category_id: int | None = None
    title: str = ''
    abstract: str = ''
    requirements: str = ''
    poster_filename: str | None = None
    status: str = 'pending'
    review_comment: str = ''
    booth_id: int | None = None
    created_at: datetime = field(default_factory=utcnow)
    reviewed_at: datetime | None = None

    @property
    def team(self):
        from .repository import repo
        return repo.get(Team, self.team_id)

    @property
    def category(self):
        from .repository import repo
        return repo.get(Category, self.category_id)

    @property
    def booth(self):
        from .repository import repo
        return repo.get(Booth, self.booth_id) if self.booth_id else None


@dataclass
class Booth:
    __collection__: ClassVar[str] = 'booths'
    id: int | None = None
    code: str = ''
    category_id: int | None = None
    location: str = 'Exhibition hall'
    project_id: int | None = None

    @property
    def project(self):
        from .repository import repo
        return repo.get(Project, self.project_id)

    @property
    def category(self):
        from .repository import repo
        return repo.get(Category, self.category_id)


@dataclass
class CheckIn:
    __collection__: ClassVar[str] = 'check_ins'
    id: int | None = None
    user_id: int | None = None
    scanner_id: int | None = None
    created_at: datetime = field(default_factory=utcnow)

    @property
    def user(self):
        from .repository import repo
        return repo.get(User, self.user_id)

    @property
    def scanner(self):
        from .repository import repo
        return repo.get(User, self.scanner_id)


@dataclass
class AuditLog:
    __collection__: ClassVar[str] = 'audit_logs'
    id: int | None = None
    user_id: int | None = None
    action: str = ''
    details: str = ''
    created_at: datetime = field(default_factory=utcnow)

    @property
    def user(self):
        from .repository import repo
        return repo.get(User, self.user_id)
