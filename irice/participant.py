"""Participant workflows backed by Firestore. Poster uploads are paused."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from .auth import login_required
from .repository import repo
from .models import Category, Project, Team, User

bp = Blueprint('participant', __name__)


@bp.get('/')
def index():
    return render_template('index.html')


@bp.get('/dashboard')
@login_required
def dashboard():
    if g.user.role == 'admin':
        return redirect(url_for('admin.dashboard'))
    from .passes import pass_eligibility
    eligible, reason = pass_eligibility(g.user)
    team = g.user.team
    return render_template('dashboard.html', team=team, project=team.project if team else None, eligible=eligible, eligibility_reason=reason, checkin=g.user.checkin)


def exhibitor_context():
    user = repo.get(User, g.user.id)
    if not user or user.role != 'exhibitor' or not user.team:
        abort(403)
    team = user.team
    return user, team, team.project


def require_editable(project):
    if project and project.status == 'approved':
        raise ValueError('Your approved team and project are locked. Ask the organizer to reopen the application before making changes.')


@bp.route('/team', methods=['GET', 'POST'])
@login_required
def team():
    user, team, project = exhibitor_context()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        institution = request.form.get('institution', '').strip()
        members = request.form.get('members', '').strip()
        if not 2 <= len(name) <= 120 or not 2 <= len(institution) <= 180 or len(members) > 2000 or len(members.splitlines()) > 10:
            flash('Check your team name (2–120 characters), institution (2–180), and up to 10 members (2,000 characters).', 'error')
            return render_template('team.html', team=team, project=project), 400
        def update_team():
            actor, current, application = exhibitor_context()
            require_editable(application)
            current.name, current.institution, current.members = name, institution, members
            repo.save(current)
            # Shared user write serializes edits against project review/check-in.
            repo.save(actor)
            repo.audit(actor.id, 'team_updated', f'Team {current.id}: {name}')
        try:
            repo.atomic(update_team)
            flash('Your team profile has been saved.', 'success')
        except ValueError as error:
            flash(str(error), 'error')
        return redirect(url_for('participant.team'))
    return render_template('team.html', team=team, project=project)


@bp.route('/project', methods=['GET', 'POST'])
@login_required
def project():
    user, team, project = exhibitor_context()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        abstract = request.form.get('abstract', '').strip()
        requirements = request.form.get('requirements', '').strip()
        category_id = request.form.get('category_id', type=int)
        category = repo.get(Category, category_id) if category_id else None
        error = None
        if any(upload.filename for upload in request.files.values()):
            error = 'Poster uploads are paused in this Firestore-only version. Submit your project without a poster.'
        elif not 5 <= len(title) <= 180:
            error = 'Enter a project title between 5 and 180 characters.'
        elif not 50 <= len(abstract) <= 10000:
            error = 'Enter an abstract between 50 and 10,000 characters.'
        elif not category:
            error = 'Choose an available project category.'
        elif len(requirements) > 2000:
            error = 'Booth requirements must be 2,000 characters or fewer.'
        if error:
            flash(error, 'error')
            return render_template('project.html', project=project, team=team), 400
        def submit_project():
            actor, current_team, application = exhibitor_context()
            require_editable(application)
            if not current_team.institution:
                raise ValueError('Complete your team institution before submitting your project.')
            if not repo.get(Category, category_id):
                raise ValueError('That category is no longer available.')
            if application is None:
                application = Project(team_id=current_team.id)
            if application.booth:
                booth = application.booth
                booth.project_id = None
                repo.save(booth)
            application.title, application.abstract = title, abstract
            application.category_id, application.requirements = category_id, requirements
            application.status, application.review_comment = 'pending', ''
            application.reviewed_at, application.booth_id = None, None
            repo.save(application)
            actor.token_version += 1
            repo.save(actor)
            repo.audit(actor.id, 'project_submitted', title)
        try:
            repo.atomic(submit_project)
        except ValueError as error:
            flash(str(error), 'error')
            return render_template('project.html', project=project, team=team), 400
        flash('Project submitted. The organizing committee will review your application.', 'success')
        return redirect(url_for('participant.dashboard'))
    return render_template('project.html', project=project, team=team)


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        organization = request.form.get('organization', '').strip()
        phone = request.form.get('phone', '').strip()
        if not 2 <= len(name) <= 120 or len(organization) > 180 or len(phone) > 40:
            flash('Check your name (2–120 characters), organization (up to 180), and phone (up to 40).', 'error')
            return render_template('profile.html'), 400
        def update_profile():
            user = repo.get(User, g.user.id)
            if not user:
                abort(403)
            user.name, user.organization, user.phone = name, organization, phone
            repo.save(user)
        repo.atomic(update_profile)
        flash('Your profile has been updated.', 'success')
        return redirect(url_for('participant.profile'))
    return render_template('profile.html')


@bp.get('/posters/<int:project_id>')
@login_required
def poster(project_id):
    # Old local uploads are deliberately not exposed or silently lost in /tmp.
    abort(404)
