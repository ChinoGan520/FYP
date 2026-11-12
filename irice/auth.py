"""Account registration and server-side role enforcement."""
import re
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from .repository import DuplicateValue, repo
from .models import Team, User

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.before_app_request
def load_user():
    user_id = session.get("user_id")
    g.user = None
    if isinstance(user_id, int) and request.endpoint != 'health':
        g.user = repo.get(User, user_id)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user.role != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("participant.dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "exhibitor")
        organization = request.form.get("organization", "").strip()
        error = None
        if not 2 <= len(name) <= 120:
            error = "Enter a name between 2 and 120 characters."
        elif len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            error = "Enter a valid email address."
        elif not 10 <= len(password) <= 128:
            error = "Use a password between 10 and 128 characters."
        elif role not in {"exhibitor", "attendee", "evaluator"}:
            error = "Choose an exhibitor, attendee or evaluator account."
        elif len(organization) > 180:
            error = "Organization must be 180 characters or fewer."
        if error:
            flash(error, "error")
            return render_template("auth/register.html"), 400
        user = User(name=name, email=email, role=role, organization=organization)
        user.set_password(password)
        def create_account():
            if repo.first(User, email=email):
                raise DuplicateValue('Email already registered.')
            # Allocate inside each transaction attempt; aborted retries must not
            # retain an ID allocated by an uncommitted counter update.
            account = User(name=name, email=email, role=role, organization=organization, password_hash=user.password_hash)
            repo.save(account)
            repo.reserve_unique('emails', email, account.id)
            if role == 'exhibitor':
                repo.save(Team(user_id=account.id, name=f"{name[:100]}'s team", institution=organization))
            return account
        try:
            user = repo.atomic(create_account)
        except DuplicateValue:
            flash('An account with that email already exists. Sign in instead.', 'error')
            return render_template('auth/register.html'), 400
        session.clear()
        session["user_id"] = user.id
        session.permanent = True
        flash("Welcome to I-RICE. Your account is ready.", "success")
        return redirect(url_for("participant.dashboard"))
    return render_template("auth/register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("admin.dashboard" if g.user.role == "admin" else "participant.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = repo.first(User, email=email)
        if not user or len(password) > 128 or not user.check_password(password):
            flash("The email or password is incorrect.", "error")
            return render_template("auth/login.html"), 401
        session.clear()
        session["user_id"] = user.id
        session.permanent = True
        target = request.args.get("next", "")
        parsed = urlsplit(target)
        if target.startswith("/") and not target.startswith("//") and not parsed.netloc and not parsed.scheme and "\\" not in target:
            return redirect(target)
        return redirect(url_for("admin.dashboard" if user.role == "admin" else "participant.dashboard"))
    return render_template("auth/login.html")


@bp.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have signed out.", "success")
    return redirect(url_for("participant.index"))
