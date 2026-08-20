from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func

from .base import Base, JSON_TYPE


class WebCommandAudit(Base):
    """Durable idempotency and audit record for privileged Web → Core commands."""

    __tablename__ = "web_command_audit"

    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(160), nullable=False, unique=True, index=True)
    command_type = Column(String(64), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    target_type = Column(String(64), nullable=False)
    target_id = Column(Integer, nullable=False, index=True)
    request_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, server_default="COMPLETED", index=True)
    response_json = Column(JSON_TYPE, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
