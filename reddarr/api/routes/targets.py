"""Targets API routes - CRUD for subreddits and users to archive.

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


@router.get("/target/{target_type}/{target_name}/failures")
def target_failures(
    target_type: str,
    target_name: str,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Get failed/errored media downloads for a specific target.

    Returns separate lists for media download failures and (a placeholder
    for) scrape failures so the frontend panel renders correctly.
    """
    if target_type == "subreddit":
        post_filter = func.lower(Post.subreddit) == target_name.lower()
    else:
        post_filter = func.lower(Post.author) == target_name.lower()

    failed_rows = (
        db.query(Media)
        .join(Post, Media.post_id == Post.id)
        .filter(post_filter, Media.status == "failed")
        .order_by(Media.downloaded_at.desc())
        .limit(limit)
        .all()
    )

    failures = [
        {
            "id": m.id,
            "post_id": m.post_id,
            "url": m.url,
            "status": m.status,
            "error_message": m.error_message,
            "retries": m.retries or 0,
            "created_at": m.downloaded_at.isoformat() if m.downloaded_at else None,
        }
        for m in failed_rows
    ]

    return {"failures": failures, "scrape_failures": []}


@router.post("/target/{target_type}/{target_name}/rescrape")
def rescrape_target(
    target_type: str,
    target_name: str,
    db: Session = Depends(get_db),
):
    """Re-queue failed media downloads for a target.

    Finds all Media records with status='failed' belonging to this target,
    resets them to 'pending', and dispatches a download task for each.
    Returns the number of items requeued so the frontend toast is correct.
    """
    from reddarr.tasks.download import download_media_item

    if target_type == "subreddit":
        post_filter = func.lower(Post.subreddit) == target_name.lower()
    else:
        post_filter = func.lower(Post.author) == target_name.lower()

    failed_media = (
        db.query(Media)
        .join(Post, Media.post_id == Post.id)
        .filter(post_filter, Media.status == "failed")
        .all()
    )

    # Reset all statuses first, then commit, then enqueue.
    # This ensures workers see the 'pending' state before tasks arrive and
    # prevents orphaned tasks if the commit fails.
    count = len(failed_media)
    for m in failed_media:
        m.status = "pending"
        m.error_message = None

    db.commit()

    for m in failed_media:
        download_media_item.delay(m.post_id, m.url)

    logger.info(f"Requeued {count} failed downloads for {target_type}:{target_name}")
    return {"requeued": count, "status": "queued"}
