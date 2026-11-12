"""Organizer workflows backed by transactional application repositories."""

import csv
import io
import re
from datetime import datetime

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from .auth import admin_required
from .models import AuditLog, Booth, Category, CheckIn, Project, Team, User, utcnow
from .repository import DuplicateValue, repo

bp = Blueprint("admin", __name__, url_prefix="/admin")
_STATUSES = {"pending", "approved", "rejected"}
_MAX_ID = 2_147_483_647


class _WorkflowError(ValueError):
    """A rejected business operation; raising rolls back all buffered writes."""

    def __init__(self, message, category="warning"):
        super().__init__(message)
        self.category = category


def _require_id(value):
    if not 1 <= value <= _MAX_ID:
        abort(404)


def _require_actor(actor_id):
    actor = repo.get(User, actor_id)
    if actor is None or actor.role != "admin":
        abort(403)


def _parse_id(value):
    if not value.isascii() or not value.isdigit() or len(value) > 10:
        return None
    result = int(value)
    return result if 1 <= result <= _MAX_ID else None


def _latest(records, limit=None):
    ordered = sorted(records, key=lambda record: (record.created_at or datetime.min, record.id or 0), reverse=True)
    return ordered if limit is None else ordered[:limit]


def _metrics(users, teams, projects, booths, checkins):
    people = [user for user in users if user.role != "admin"]
    team_owners = {team.id: team.user_id for team in teams}
    exhibitor_ids = {user.id for user in people if user.role == "exhibitor"}
    approved_exhibitors = {
        team_owners.get(project.team_id) for project in projects
        if project.status == "approved" and team_owners.get(project.team_id) in exhibitor_ids
    }
    approved_visitors = sum(
        user.role in {"attendee", "evaluator"} and user.approval_status == "approved"
        for user in people
    )
    return {
        "total_projects": len(projects),
        "pending_projects": sum(project.status == "pending" for project in projects),
        "approved_projects": sum(project.status == "approved" for project in projects),
        "rejected_projects": sum(project.status == "rejected" for project in projects),
        "total_booths": len(booths),
        "allocated_booths": sum(booth.project_id is not None for booth in booths),
        "total_people": len(people),
        "approved_people": len(approved_exhibitors) + approved_visitors,
        "checked_in": len(checkins),
        "checkin_rate": round(100 * len(checkins) / len(people), 1) if people else 0,
    }


def dashboard_metrics():
    """Counts represent projects, booths and individual registered pass holders."""
    return _metrics(repo.all(User), repo.all(Team), repo.all(Project), repo.all(Booth), repo.all(CheckIn))


@bp.get("/")
@admin_required
def dashboard():
    users, teams = repo.all(User), repo.all(Team)
    projects, booths, checkins = repo.all(Project), repo.all(Booth), repo.all(CheckIn)
    categories = sorted(repo.all(Category), key=lambda category: category.name.casefold())
    category_counts = {}
    for project in projects:
        category_counts[project.category_id] = category_counts.get(project.category_id, 0) + 1
    return render_template(
        "admin/dashboard.html",
        metrics=_metrics(users, teams, projects, booths, checkins),
        recent_projects=_latest(projects, 6),
        recent_checkins=_latest(checkins, 6),
        category_stats=[{"name": category.name, "color": category.color, "count": category_counts.get(category.id, 0)} for category in categories],
    )


@bp.get("/applications")
@admin_required
def applications():
    status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()[:200]
    raw_category = request.args.get("category_id", "").strip()
    category_id = _parse_id(raw_category) if raw_category else None
    projects = repo.all(Project)
    if status in _STATUSES:
        projects = [project for project in projects if project.status == status]
    if category_id is not None:
        projects = [project for project in projects if project.category_id == category_id]
    if q:
        team_names = {team.id: team.name.casefold() for team in repo.all(Team)}
        needle = q.casefold()
        projects = [project for project in projects if needle in project.title.casefold() or needle in team_names.get(project.team_id, "")]
    return render_template("admin/applications.html", projects=_latest(projects), q=q, status=status, category_id=category_id)


@bp.get("/applications/<int:project_id>")
@admin_required
def application(project_id):
    _require_id(project_id)
    project = repo.get_or_404(Project, project_id)
    available_booths = sorted(
        [booth for booth in repo.all(Booth)
         if booth.project_id in (None, project.id) and booth.category_id in (None, project.category_id)],
        key=lambda booth: booth.code,
    )
    return render_template("admin/application.html", project=project, available_booths=available_booths)


def _assigned_booth(project):
    """Read the stored inverse, refusing to overwrite an unrelated allocation."""
    if project.booth_id is None:
        return None
    booth = repo.get(Booth, project.booth_id)
    if booth is None or booth.project_id != project.id:
        raise _WorkflowError("The booth allocation is inconsistent. Refresh the application and contact the organizer if it persists.", "danger")
    return booth


@bp.post("/applications/<int:project_id>/review")
@admin_required
def review(project_id):
    _require_id(project_id)
    status = request.form.get("status", "").strip()
    comment = request.form.get("comment", "").strip()
    if status not in _STATUSES or len(comment) > 2000:
        flash("Choose a valid review decision and keep comments within 2,000 characters.", "danger")
        return redirect(url_for("admin.application", project_id=project_id))
    if status == "rejected" and not comment:
        flash("Add a comment so the team knows how to improve its submission.", "danger")
        return redirect(url_for("admin.application", project_id=project_id))
    actor_id = g.user.id

    def apply_review():
        _require_actor(actor_id)
        # Every read occurs inside the transaction. A scan reads this project
        # and writes the same user, so Firestore retries either side on races.
        project = repo.get_or_404(Project, project_id)
        team = repo.get_or_404(Team, project.team_id)
        user = repo.get_or_404(User, team.user_id)
        old_booth = _assigned_booth(project)
        previous = project.status
        if previous == "approved" and status != "approved":
            user.token_version += 1
            repo.save(user)
        if status != "approved":
            if old_booth is not None:
                old_booth.project_id = None
                repo.save(old_booth)
            project.booth_id = None
        project.status = status
        project.review_comment = comment
        project.reviewed_at = utcnow()
        repo.save(project)
        repo.audit(actor_id, "project.review", f"Project #{project.id}: {previous} → {status}.")

    try:
        repo.atomic(apply_review)
        flash(f"Application marked {status}.", "success")
    except _WorkflowError as error:
        flash(str(error), error.category)
    return redirect(url_for("admin.application", project_id=project_id))


@bp.post("/applications/<int:project_id>/booth")
@admin_required
def allocate(project_id):
    _require_id(project_id)
    raw_booth = request.form.get("booth_id", "").strip()
    booth_id = _parse_id(raw_booth) if raw_booth else None
    if raw_booth and booth_id is None:
        flash("Choose a valid exhibition booth.", "danger")
        return redirect(url_for("admin.application", project_id=project_id))
    actor_id = g.user.id

    def apply_allocation():
        _require_actor(actor_id)
        project = repo.get_or_404(Project, project_id)
        if project.status != "approved":
            raise _WorkflowError("Approve the application before assigning an exhibition booth.")
        old_booth = _assigned_booth(project)
        target = repo.get(Booth, booth_id) if booth_id is not None else None
        if booth_id is not None and target is None:
            raise _WorkflowError("That booth no longer exists. Choose another booth.", "danger")
        if target and target.project_id not in (None, project.id):
            raise _WorkflowError("That booth has already been allocated. Choose an available booth.")
        if target and target.category_id not in (None, project.category_id):
            raise _WorkflowError("Choose a booth assigned to this project's category or a general booth.")
        previous = old_booth.code if old_booth else "unassigned"
        if old_booth and (target is None or old_booth.id != target.id):
            old_booth.project_id = None
            repo.save(old_booth)
        if target:
            target.project_id = project.id
            repo.save(target)
        project.booth_id = target.id if target else None
        repo.save(project)
        repo.audit(actor_id, "booth.allocate", f"Project #{project.id}: {previous} → {target.code if target else 'unassigned'}.")

    try:
        repo.atomic(apply_allocation)
        flash("Booth allocation updated.", "success")
    except _WorkflowError as error:
        flash(str(error), error.category)
    return redirect(url_for("admin.application", project_id=project_id))


@bp.route("/booths", methods=["GET", "POST"])
@admin_required
def booths():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        location = request.form.get("location", "").strip()
        category_raw = request.form.get("category_id", "").strip()
        category_id = _parse_id(category_raw) if category_raw else None
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,19}", code) or len(location) > 120:
            flash("Use a booth code of up to 20 letters, digits, dots or hyphens and a location of up to 120 characters.", "danger")
            return redirect(url_for("admin.booths"))
        if category_raw and category_id is None:
            flash("Choose an existing category or leave the booth open to all categories.", "danger")
            return redirect(url_for("admin.booths"))
        actor_id = g.user.id

        def create_booth():
            _require_actor(actor_id)
            category = repo.get(Category, category_id) if category_id is not None else None
            if category_id is not None and category is None:
                raise _WorkflowError("Choose an existing category or leave the booth open to all categories.", "danger")
            booth = repo.save(Booth(code=code, location=location, category_id=category_id))
            repo.reserve_unique("booth_codes", code, booth.id)
            repo.audit(actor_id, "booth.create", f"Created booth {code}.")

        try:
            repo.atomic(create_booth)
            flash(f"Booth {code} added.", "success")
        except DuplicateValue:
            flash("That booth code already exists.", "danger")
        except _WorkflowError as error:
            flash(str(error), error.category)
        return redirect(url_for("admin.booths"))
    all_booths = sorted(repo.all(Booth), key=lambda booth: booth.code)
    return render_template("admin/booths.html", booths=all_booths)


@bp.post("/categories")
@admin_required
def categories():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    color = request.form.get("color", "#147d78").strip()
    if not 2 <= len(name) <= 80 or len(description) > 240 or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        flash("Enter a category name (2–80 characters), description (up to 240), and a valid hex color.", "danger")
        return redirect(url_for("admin.booths"))
    actor_id = g.user.id

    def create_category():
        _require_actor(actor_id)
        category = repo.save(Category(name=name, description=description, color=color))
        repo.reserve_unique("category_names", name.casefold(), category.id)
        repo.audit(actor_id, "category.create", f"Created category {name}.")

    try:
        repo.atomic(create_category)
        flash("Category added.", "success")
    except DuplicateValue:
        flash("That category name already exists.", "danger")
    return redirect(url_for("admin.booths"))


@bp.get("/attendees")
@admin_required
def attendees():
    status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()[:200]
    people = [user for user in repo.all(User) if user.role != "admin"]
    if q:
        needle = q.casefold()
        people = [user for user in people if any(needle in value.casefold() for value in (user.name, user.email, user.organization))]
    if status in _STATUSES:
        teams_by_user = {team.user_id: team.id for team in repo.all(Team)}
        projects_by_team = {project.team_id: project for project in repo.all(Project)}

        def admission_status(user):
            if user.role != "exhibitor":
                return user.approval_status
            project = projects_by_team.get(teams_by_user.get(user.id))
            return project.status if project else "pending"

        people = [user for user in people if admission_status(user) == status]
    return render_template("admin/attendees.html", people=_latest(people), q=q, status=status)


@bp.post("/attendees/<int:user_id>/review")
@admin_required
def review_attendee(user_id):
    _require_id(user_id)
    status = request.form.get("status", "").strip()
    comment = request.form.get("comment", "").strip()
    actor_id = g.user.id

    def apply_review():
        _require_actor(actor_id)
        user = repo.get_or_404(User, user_id)
        if user.role not in {"attendee", "evaluator"}:
            abort(400, description="Exhibitor admission is managed through the project application.")
        if status not in _STATUSES or len(comment) > 2000 or (status == "rejected" and not comment):
            raise _WorkflowError("Choose a valid decision and include a comment when rejecting a registration.", "danger")
        previous = user.approval_status
        if previous == "approved" and status != "approved":
            user.token_version += 1
        user.approval_status = status
        user.review_comment = comment
        repo.save(user)
        repo.audit(actor_id, "registration.review", f"Registration #{user.id}: {previous} → {status}.")

    try:
        repo.atomic(apply_review)
        flash(f"Registration marked {status}.", "success")
    except _WorkflowError as error:
        flash(str(error), error.category)
    return redirect(url_for("admin.attendees"))


def _csv_safe(value):
    text = "" if value is None else str(value)
    # Quoting a CSV cell does not stop spreadsheet programs executing formulas.
    return "'" + text if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


@bp.get("/report.csv")
@admin_required
def report():
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Name", "Email", "Registration type", "Organization", "Approval", "Team", "Project", "Category", "Booth", "Checked in (UTC)"])
    teams = {team.user_id: team for team in repo.all(Team)}
    projects = {project.team_id: project for project in repo.all(Project)}
    categories = {category.id: category for category in repo.all(Category)}
    booths = {booth.id: booth for booth in repo.all(Booth)}
    checkins = {checkin.user_id: checkin for checkin in repo.all(CheckIn)}
    for user in sorted((user for user in repo.all(User) if user.role != "admin"), key=lambda user: user.id):
        team = teams.get(user.id)
        project = projects.get(team.id) if team else None
        category = categories.get(project.category_id) if project else None
        booth = booths.get(project.booth_id) if project else None
        checkin = checkins.get(user.id)
        row = [user.name, user.email, user.role, user.organization,
               project.status if project else ("not submitted" if user.role == "exhibitor" else user.approval_status),
               team.name if team else "", project.title if project else "",
               category.name if category else "", booth.code if booth else "",
               checkin.created_at.isoformat(timespec="seconds") if checkin else ""]
        writer.writerow([_csv_safe(value) for value in row])
    response = Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = "attachment; filename=irice-registration-report.csv"
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/activity")
@admin_required
def audit():
    return render_template("admin/activity.html", logs=_latest(repo.all(AuditLog), 200))
