"""Targets API routes — CRUD for subreddits and users to archive.

Replaces the old targets.txt flat file with full DB-driven management.
All routes require API key authentication.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from reddarr.database import get_db
from reddarr.models import Target, Post, Media
from reddarr.api.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["targets"], dependencies=[Depends(require_api_key)])


@router.get("/targets")
def list_targets(db: Session = Depends(get_db)):
    """List all targets with post/media counts."""
    targets = db.query(Target).order_by(Target.type, Target.name).all()

    results = []
    for t in targets:
        # Get counts
        if t.type == "subreddit":
            post_count = (
                db.query(func.count(Post.id))
                .filter(func.lower(Post.subreddit) == t.name.lower())
                .scalar()
                or 0
            )
        else:
            post_count = (
                db.query(func.count(Post.id))
                .filter(func.lower(Post.author) == t.name.lower())
                .scalar()
                or 0
            )

        results.append(
            {
                "id": t.id,
                "type": t.type,
                "name": t.name,
                "enabled": t.enabled,
                "status": t.status or "active",
                "icon_url": t.icon_url,
                "last_created": t.last_created.isoformat() if t.last_created else None,
                "post_count": post_count,
            }
        )

    return {"targets": results}


class TargetRequest(BaseModel):
    type: str  # 'subreddit' or 'user'
    name: str
    enabled: bool = True


@router.post("/targets")
def add_target(req: TargetRequest, db: Session = Depends(get_db)):
    """Add a new target."""
    if req.type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="type must be 'subreddit' or 'user'")

    existing = db.query(Target).filter_by(name=req.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Target '{req.name}' already exists")

    target = Target(type=req.type, name=req.name, enabled=req.enabled)
    db.add(target)
    db.commit()

    logger.info(f"Added target: {req.type}:{req.name}")
    return {
        "status": "added",
        "target": {"id": target.id, "type": target.type, "name": target.name},
    }


@router.delete("/targets/{target_id}")
def delete_target(target_id: int, db: Session = Depends(get_db)):
    """Delete a target by ID."""
    target = db.query(Target).filter_by(id=target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    name = target.name
    db.delete(target)
    db.commit()

    logger.info(f"Deleted target: {name}")
    return {"status": "deleted", "name": name}


@router.patch("/targets/{target_id}")
def update_target(
    target_id: int,
    enabled: bool = Query(None),
    db: Session = Depends(get_db),
):
    """Update a target (enable/disable)."""
    target = db.query(Target).filter_by(id=target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    if enabled is not None:
        target.enabled = enabled

    db.commit()
    return {"status": "updated", "name": target.name, "enabled": target.enabled}


@router.get("/target/{target_type}/{target_name}/stats")
def target_stats(
    target_type: str,
    target_name: str,
    db: Session = Depends(get_db),
):
    """Detailed stats for a specific target."""
    if target_type == "subreddit":
        post_filter = func.lower(Post.subreddit) == target_name.lower()
    else:
        post_filter = func.lower(Post.author) == target_name.lower()

    post_count = db.query(func.count(Post.id)).filter(post_filter).scalar() or 0

    # Join for media stats
    from sqlalchemy import and_

    media_stats = (
        db.query(
            func.count(Media.id),
            func.count(Media.id).filter(Media.status == "done"),
            func.count(Media.id).filter(Media.status == "failed"),
            func.count(Media.id).filter(Media.status == "pending"),
        )
        .join(Post, Media.post_id == Post.id)
        .filter(post_filter)
        .first()
    )

    return {
        "type": target_type,
        "name": target_name,
        "posts": post_count,
        "media_total": media_stats[0] or 0,
        "media_downloaded": media_stats[1] or 0,
        "media_failed": media_stats[2] or 0,
        "media_pending": media_stats[3] or 0,
    }


@router.post("/target/{target_type}/{target_name}/toggle")
def toggle_target(
    target_type: str,
    target_name: str,
    db: Session = Depends(get_db),
):
    """Toggle target enabled state."""
    target = db.query(Target).filter_by(type=target_type, name=target_name).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    target.enabled = not target.enabled
    db.commit()
    return {"status": "toggled", "name": target.name, "enabled": target.enabled}


@router.post("/target/{target_type}/{target_name}/status")
def set_target_status(
    target_type: str,
    target_name: str,
    new_status: str = Query(...),
    db: Session = Depends(get_db),
):
    """Set target status (active, paused, error, etc)."""
    target = db.query(Target).filter_by(type=target_type, name=target_name).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    target.status = new_status
    db.commit()
    return {"status": "updated", "name": target.name, "status": target.status}


@router.post("/target/{target_type}/{target_name}/rescan")
def rescan_target(
    target_type: str,
    target_name: str,
    db: Session = Depends(get_db),
):
    """Trigger a rescan for a target."""
    from reddarr.tasks.ingest import ingest_target

    task = ingest_target.delay(target_type, target_name)
    return {"status": "queued", "task_id": task.id}


@router.delete("/target/{target_type}/{target_name}")
def delete_target_by_name(
    target_type: str,
    target_name: str,
    db: Session = Depends(get_db),
):
    """Delete a target by type and name."""
    target = db.query(Target).filter_by(type=target_type, name=target_name).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    name = target.name
    db.delete(target)
    db.commit()

    logger.info(f"Deleted target: {name}")
    return {"status": "deleted", "name": name}


@router.get("/target/{target_type}/{target_name}/audit")
def audit_target(
    target_type: str,
    target_name: str,
    db: Session = Depends(get_db),
):
    """Audit a target - check for missing media, duplicates, etc."""
    if target_type == "subreddit":
        post_filter = func.lower(Post.subreddit) == target_name.lower()
    else:
        post_filter = func.lower(Post.author) == target_name.lower()

    posts = db.query(Post).filter(post_filter).all()
    post_ids = [p.id for p in posts]

    if not post_ids:
        return {"posts": 0, "media": 0, "issues": []}

    total_media = db.query(func.count(Media.id)).filter(Media.post_id.in_(post_ids)).scalar() or 0
    downloaded_media = (
        db.query(func.count(Media.id))
        .filter(Media.post_id.in_(post_ids), Media.status == "done")
        .scalar()
        or 0
    )

    # Find posts with no media
    posts_no_media = (
        db.query(func.count(Post.id))
        .filter(
            post_filter,
            ~Post.id.in_(db.query(Media.post_id).filter(Media.status == "done").subquery()),
        )
        .scalar()
        or 0
    )

    return {
        "posts": len(posts),
        "media": total_media,
        "downloaded": downloaded_media,
        "posts_without_media": posts_no_media,
    }


@router.post("/target/{target_type}/{target_name}/rescrape")
def rescrape_target(
    target_type: str,
    target_name: str,
    db: Session = Depends(get_db),
):
    """Rescrape a target - re-download all media for posts."""
    from reddarr.tasks.ingest import trigger_backfill

    task = trigger_backfill.delay(target_type, target_name, sort="new", passes=1)
    return {"status": "queued", "task_id": task.id}
