import logging

from sqlalchemy import select, update

from app.models.data import Checklist, ChecklistVersion
from app.services.errors import ServiceError, require


def snapshot(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "checklist_id",
            "name",
            "version_number",
            "created_at",
            "criteria",
        )
    }


def create(session, payload, *, seed_key=None):
    checklist = Checklist(name=payload.name, seed_key=seed_key)
    session.add(checklist)
    session.flush()
    version = ChecklistVersion(
        checklist_id=checklist.id,
        name=payload.name,
        version_number=1,
        criteria=[c.model_dump() for c in payload.criteria],
    )
    session.add(version)
    session.flush()
    checklist.current_version_id = version.id
    session.flush()
    session.info["cqa_checklist_versions"] = session.info.get("cqa_checklist_versions", 0) + 1
    return snapshot(version)


def add_version(session, checklist_id, payload):
    # Claim the revision with a single write before reading (safe for SQLite races).
    changed = session.execute(
        update(Checklist)
        .where(
            Checklist.id == checklist_id,
            Checklist.current_version_number == payload.expected_version_number,
        )
        .values(current_version_number=payload.expected_version_number + 1)
    )
    if changed.rowcount != 1:
        require(session.get(Checklist, checklist_id))
        logging.getLogger("cqa.checklists").warning(
            "event=checklist_version_conflict error=VERSION_CONFLICT"
        )
        raise ServiceError(409, "VERSION_CONFLICT", "Чек-лист уже изменён")
    checklist = session.get(Checklist, checklist_id)
    version = ChecklistVersion(
        checklist_id=checklist_id,
        name=checklist.name,
        version_number=payload.expected_version_number + 1,
        criteria=[c.model_dump() for c in payload.criteria],
    )
    session.add(version)
    session.flush()
    checklist.current_version_id = version.id
    session.flush()
    session.info["cqa_checklist_versions"] = session.info.get("cqa_checklist_versions", 0) + 1
    return snapshot(version)


def get_version(session, version_id):
    return snapshot(require(session.get(ChecklistVersion, version_id)))


def metadata(row):
    return dict(
        id=row.id,
        name=row.name,
        current_version_number=row.current_version_number,
        current_version_id=row.current_version_id,
    )


def detail(session, checklist_id):
    row = require(session.get(Checklist, checklist_id))
    versions = session.scalars(
        select(ChecklistVersion)
        .where(ChecklistVersion.checklist_id == checklist_id)
        .order_by(ChecklistVersion.version_number)
    ).all()
    return dict(
        **metadata(row),
        versions=[snapshot(v) for v in versions],
        current_version=get_version(session, row.current_version_id),
    )
