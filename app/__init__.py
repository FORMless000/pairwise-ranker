from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text


db = SQLAlchemy()


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = inspect(db.engine)
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    return column_name in columns


def _table_exists(table_name: str) -> bool:
    inspector = inspect(db.engine)
    return inspector.has_table(table_name)


def _ensure_schema() -> None:
    if _table_exists("project") and not _column_exists("project", "public_access"):
        db.session.execute(
            text("ALTER TABLE project ADD COLUMN public_access VARCHAR(32) DEFAULT 'private'")
        )
        db.session.commit()

    if _table_exists("comparison") and not _column_exists("comparison", "aspect_id"):
        db.session.execute(text("ALTER TABLE comparison ADD COLUMN aspect_id INTEGER"))
        db.session.commit()

    db.create_all()

    from app.models import Aspect, Comparison, Project

    projects = Project.query.all()
    for project in projects:
        if not project.public_access:
            project.public_access = "private"
        aspects = (
            Aspect.query.filter_by(project_id=project.id).order_by(Aspect.order_index.asc()).all()
        )
        if not aspects:
            db.session.add(Aspect(project_id=project.id, name="Overall", order_index=0))
    db.session.commit()

    for project in projects:
        first_aspect = (
            Aspect.query.filter_by(project_id=project.id).order_by(Aspect.order_index.asc()).first()
        )
        if not first_aspect:
            continue
        Comparison.query.filter_by(project_id=project.id, aspect_id=None).update(
            {"aspect_id": first_aspect.id}
        )
    db.session.commit()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.update(
        SECRET_KEY="dev-secret-change-me",
        SQLALCHEMY_DATABASE_URI="sqlite:///ranker.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    from app.routes import bp as routes_bp

    app.register_blueprint(routes_bp)

    with app.app_context():
        _ensure_schema()

    return app
