from __future__ import annotations

from datetime import datetime

from app import db


class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="unstarted")
    public_access = db.Column(db.String(32), nullable=False, default="private")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    items = db.relationship(
        "Item", back_populates="project", cascade="all, delete-orphan", lazy=True
    )
    comparisons = db.relationship(
        "Comparison", back_populates="project", cascade="all, delete-orphan", lazy=True
    )
    aspects = db.relationship(
        "Aspect", back_populates="project", cascade="all, delete-orphan", lazy=True
    )
    access_entries = db.relationship(
        "ProjectAccess", back_populates="project", cascade="all, delete-orphan", lazy=True
    )


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    score = db.Column(db.Float, nullable=False, default=0.0)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    project = db.relationship("Project", back_populates="items")


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    access_entries = db.relationship(
        "ProjectAccess", back_populates="user", cascade="all, delete-orphan", lazy=True
    )


class ProjectAccess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role = db.Column(db.String(16), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    project = db.relationship("Project", back_populates="access_entries")
    user = db.relationship("User", back_populates="access_entries")

    __table_args__ = (db.UniqueConstraint("project_id", "user_id", name="uq_project_user"),)


class Aspect(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    order_index = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    project = db.relationship("Project", back_populates="aspects")


class Comparison(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    aspect_id = db.Column(db.Integer, db.ForeignKey("aspect.id"), nullable=True)
    item_a_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    item_b_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    outcome = db.Column(db.String(32), nullable=False)
    weight = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    project = db.relationship("Project", back_populates="comparisons")
    item_a = db.relationship("Item", foreign_keys=[item_a_id], lazy="joined")
    item_b = db.relationship("Item", foreign_keys=[item_b_id], lazy="joined")
    aspect = db.relationship("Aspect", foreign_keys=[aspect_id], lazy="joined")
