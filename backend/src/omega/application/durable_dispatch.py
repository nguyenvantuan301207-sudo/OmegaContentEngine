"""Generic transactional Celery dispatch intents and bounded relay."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from omega.infrastructure.models import DurableDispatchIntent

PENDING = "PENDING"
CLAIMED = "CLAIMED"
RETRY = "RETRY"
SENT = "SENT"
DEAD_LETTER = "DEAD_LETTER"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sanitize_error(error: Exception) -> str:
    error_type = type(error).__name__
    message = str(error).split("\n", 1)[0]
    if any(marker in message.lower() for marker in ("://", "password", "secret", "token", "key=")):
        return f"{error_type}: broker publication failed (details redacted)"
    return f"{error_type}: {message[:500]}"


def _validate_args(args: Any) -> list[Any]:
    if not isinstance(args, list):
        raise ValueError("durable dispatch args must be a JSON array")
    try:
        json.dumps(args)
    except (TypeError, ValueError) as exc:
        raise ValueError("durable dispatch args must be JSON serializable") from exc
    return args


def _assert_immutable_identity(
    existing: DurableDispatchIntent,
    *,
    task_name: str,
    args: list[Any],
    purpose: str,
    max_attempts: int,
    correlations: dict[str, Any],
) -> None:
    """Raise ValueError when an existing intent has a conflicting immutable identity."""
    mismatches = []
    if existing.task_name != task_name:
        mismatches.append(f"task_name: existing={existing.task_name!r} new={task_name!r}")
    if existing.purpose != purpose:
        mismatches.append(f"purpose: existing={existing.purpose!r} new={purpose!r}")
    if existing.max_attempts != max_attempts:
        mismatches.append(
            f"max_attempts: existing={existing.max_attempts!r} new={max_attempts!r}"
        )
    if existing.args != args:
        mismatches.append(f"args: existing={existing.args!r} new={args!r}")
    for field, value in correlations.items():
        existing_value = getattr(existing, field, None)
        if existing_value != value:
            mismatches.append(f"{field}: existing={existing_value!r} new={value!r}")
    if mismatches:
        raise ValueError(
            "Idempotency key collision: existing intent has a conflicting immutable identity. "
            + "; ".join(mismatches)
        )


class DurableDispatchService:
    """Persist, claim, and settle generic Celery dispatch intents."""

    @staticmethod
    def enqueue(
        session: Session,
        *,
        idempotency_key: str,
        task_name: str,
        args: list[Any],
        purpose: str,
        max_attempts: int = 5,
        **correlations: uuid.UUID | None,
    ) -> DurableDispatchIntent:
        validated_args = _validate_args(args)
        existing = session.query(DurableDispatchIntent).filter_by(
            idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            _assert_immutable_identity(
                existing,
                task_name=task_name,
                args=validated_args,
                purpose=purpose,
                max_attempts=max_attempts,
                correlations=correlations,
            )
            return existing
        intent = DurableDispatchIntent(
            idempotency_key=idempotency_key,
            task_name=task_name,
            args=validated_args,
            purpose=purpose,
            max_attempts=max_attempts,
            **correlations,
        )
        try:
            with session.begin_nested():
                session.add(intent)
                session.flush()
        except IntegrityError:
            authoritative = session.query(DurableDispatchIntent).filter_by(
                idempotency_key=idempotency_key
            ).one()
            _assert_immutable_identity(
                authoritative,
                task_name=task_name,
                args=validated_args,
                purpose=purpose,
                max_attempts=max_attempts,
                correlations=correlations,
            )
            return authoritative
        return intent

    @staticmethod
    async def enqueue_async(
        session: AsyncSession,
        *,
        idempotency_key: str,
        task_name: str,
        args: list[Any],
        purpose: str,
        max_attempts: int = 5,
        **correlations: uuid.UUID | None,
    ) -> DurableDispatchIntent:
        validated_args = _validate_args(args)
        existing = (
            await session.execute(
                select(DurableDispatchIntent).where(
                    DurableDispatchIntent.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            _assert_immutable_identity(
                existing,
                task_name=task_name,
                args=validated_args,
                purpose=purpose,
                max_attempts=max_attempts,
                correlations=correlations,
            )
            return existing
        intent = DurableDispatchIntent(
            idempotency_key=idempotency_key,
            task_name=task_name,
            args=validated_args,
            purpose=purpose,
            max_attempts=max_attempts,
            **correlations,
        )
        try:
            async with session.begin_nested():
                session.add(intent)
                await session.flush()
        except IntegrityError:
            authoritative = (
                await session.execute(
                    select(DurableDispatchIntent).where(
                        DurableDispatchIntent.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one()
            _assert_immutable_identity(
                authoritative,
                task_name=task_name,
                args=validated_args,
                purpose=purpose,
                max_attempts=max_attempts,
                correlations=correlations,
            )
            return authoritative
        return intent

    @staticmethod
    def recover_stale_claims(session: Session, *, stale_before: datetime) -> int:
        now = _utcnow()
        # Stale CLAIMED rows that are exhausted go straight to DEAD_LETTER.
        dead_result = session.execute(
            update(DurableDispatchIntent)
            .where(
                DurableDispatchIntent.state == CLAIMED,
                DurableDispatchIntent.claimed_at < stale_before,
                DurableDispatchIntent.attempt >= DurableDispatchIntent.max_attempts,
            )
            .values(state=DEAD_LETTER, claimed_at=None, claim_token=None, next_attempt_at=None)
        )
        # Stale CLAIMED rows that still have attempts remaining go to RETRY.
        retry_result = session.execute(
            update(DurableDispatchIntent)
            .where(
                DurableDispatchIntent.state == CLAIMED,
                DurableDispatchIntent.claimed_at < stale_before,
                DurableDispatchIntent.attempt < DurableDispatchIntent.max_attempts,
            )
            .values(state=RETRY, claimed_at=None, claim_token=None, next_attempt_at=now)
        )
        session.commit()
        return int((dead_result.rowcount or 0) + (retry_result.rowcount or 0))

    @staticmethod
    def claim_batch(session: Session, *, limit: int = 25) -> list[DurableDispatchIntent]:
        now = _utcnow()
        intents = (
            session.query(DurableDispatchIntent)
            .filter(
                DurableDispatchIntent.state.in_((PENDING, RETRY)),
                # Do not reclaim exhausted intents — they must be dead-lettered first.
                DurableDispatchIntent.attempt < DurableDispatchIntent.max_attempts,
                or_(
                    DurableDispatchIntent.next_attempt_at.is_(None),
                    DurableDispatchIntent.next_attempt_at <= now,
                ),
            )
            .order_by(DurableDispatchIntent.created_at, DurableDispatchIntent.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        for intent in intents:
            intent.state = CLAIMED
            intent.claimed_at = now
            intent.claim_token = uuid.uuid4()
            intent.attempt += 1
            intent.updated_at = now
        session.commit()
        return intents

    @staticmethod
    def mark_sent(session: Session, intent_id: uuid.UUID, claim_token: uuid.UUID) -> bool:
        now = _utcnow()
        result = session.execute(
            update(DurableDispatchIntent)
            .where(
                DurableDispatchIntent.id == intent_id,
                DurableDispatchIntent.state == CLAIMED,
                DurableDispatchIntent.claim_token == claim_token,
            )
            .values(state=SENT, sent_at=now, claimed_at=None, claim_token=None, last_error=None)
        )
        session.commit()
        return bool(result.rowcount)

    @staticmethod
    def mark_retry_or_dead_letter(
        session: Session,
        intent_id: uuid.UUID,
        claim_token: uuid.UUID,
        error: Exception,
    ) -> str:
        intent = (
            session.query(DurableDispatchIntent)
            .filter(
                DurableDispatchIntent.id == intent_id,
                DurableDispatchIntent.state == CLAIMED,
                DurableDispatchIntent.claim_token == claim_token,
            )
            .with_for_update()
            .first()
        )
        if intent is None:
            session.rollback()
            return "STALE_CLAIM"
        intent.state = DEAD_LETTER if intent.attempt >= intent.max_attempts else RETRY
        intent.next_attempt_at = None if intent.state == DEAD_LETTER else _utcnow() + timedelta(seconds=30)
        intent.last_error = _sanitize_error(error)
        intent.claimed_at = None
        intent.claim_token = None
        session.commit()
        return intent.state

    @classmethod
    def relay_batch(cls, session: Session, *, publisher: Any, limit: int = 25) -> dict[str, int]:
        cls.recover_stale_claims(session, stale_before=_utcnow() - timedelta(minutes=5))
        claimed = cls.claim_batch(session, limit=limit)
        counts = {"claimed": len(claimed), "sent": 0, "retried": 0, "dead_letter": 0}
        for intent in claimed:
            token = intent.claim_token
            if token is None:
                continue
            try:
                args = _validate_args(intent.args)
                publisher.send_task(intent.task_name, args=args)
            except Exception as exc:
                state = cls.mark_retry_or_dead_letter(session, intent.id, token, exc)
                if state == RETRY:
                    counts["retried"] += 1
                elif state == DEAD_LETTER:
                    counts["dead_letter"] += 1
            else:
                if cls.mark_sent(session, intent.id, token):
                    counts["sent"] += 1
        return counts
