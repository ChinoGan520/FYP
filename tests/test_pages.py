from irice.repository import repo
from irice.models import Category, Project, Team, User


def test_public_and_authenticated_pages_render(app, client, admin, login):
    for path in ["/", "/auth/login", "/auth/register"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"I-RICE" in response.data
    with app.app_context():
        category = repo.first(Category)
        user = User(name="Page test exhibitor", email="pages@example.test", role="exhibitor", password_hash="unused", organization="Southern University College")
        repo.save(user)
        team = repo.save(Team(user_id=user.id, name="Pages team", institution="Southern University College"))
        project = Project(team_id=team.id, category_id=category.id, title="Page rendering project", abstract="A project whose detailed review view and other authenticated pages should render properly.")
        repo.save(project)
        user_id, project_id = user.id, project.id
    login(user_id)
    for path in ["/dashboard", "/team", "/project", "/profile", "/passes/"]:
        assert client.get(path).status_code == 200, path
    login(admin)
    for path in ["/admin/", "/admin/applications", f"/admin/applications/{project_id}", "/admin/booths", "/admin/attendees", "/admin/activity", "/passes/scanner", "/profile"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/unknown-page").status_code == 404
