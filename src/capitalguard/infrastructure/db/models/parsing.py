# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/models/parsing.py ---
"""SQLAlchemy ORM models for the parsing infrastructure."""

import sqlalchemy as sa
from sqlalchemy import Column, Integer, String, Text, Boolean, Numeric, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from .base import Base  # Assuming Base is defined in models/base.py


class ParsingTemplate(Base):
    __tablename__ = 'parsing_templates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=True)  # NEW COLUMN to match DB
    pattern_type = Column(String(50), nullable=False, server_default='regex')  # e.g., 'regex', 'spacy_rule'
    pattern_value = Column(Text, nullable=False)
    # Foreign key to users.id (analyst who created/owns it)
    analyst_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    # Approved for global use
    is_public = Column(Boolean, nullable=False, server_default=sa.text('false'), index=True)
    version = Column(Integer, nullable=False, server_default='1')
    # Calculated score based on performance
    confidence_score = Column(Numeric(5, 2), nullable=True)
    # How often users correct its output
    user_correction_rate = Column(Numeric(5, 2), nullable=True)
    # { usage_count: N, success_count: M, last_used: timestamp }
    stats = Column(JSONB(astext_type=sa.Text()), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationship to the analyst who owns it
    owner = relationship("User")

    def __repr__(self):
        return (
            f"<ParsingTemplate(id={self.id}, name='{self.name}', "
            f"type='{self.pattern_type}', owner={self.analyst_id}, public={self.is_public})>"
        )


class ParsingAttempt(Base):
    __tablename__ = 'parsing_attempts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    # User who forwarded
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    raw_content = Column(Text, nullable=False)
    # Which template matched (if any)
    used_template_id = Column(Integer, ForeignKey('parsing_templates.id', ondelete='SET NULL'), nullable=True)
    # The structured data extracted
    result_data = Column(JSONB(astext_type=sa.Text()), nullable=True)
    # Did parsing yield required fields?
    was_successful = Column(Boolean, nullable=False, server_default=sa.text('false'), index=True)
    # Did the user modify the result?
    was_corrected = Column(Boolean, nullable=False, server_default=sa.text('false'), index=True)
    # JSON diff showing user changes {field: {old: X, new: Y}}
    corrections_diff = Column(JSONB(astext_type=sa.Text()), nullable=True)
    # Time taken for parsing
    latency_ms = Column(Integer, nullable=True)
    # 'regex', 'ner', 'ocr', 'vlm', 'failed'
    parser_path_used = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User")
    template_used = relationship("ParsingTemplate")

    def __repr__(self):
        status = "Success" if self.was_successful else "Fail"
        corrected = " (Corrected)" if self.was_corrected else ""
        return (
            f"<ParsingAttempt(id={self.id}, user={self.user_id}, "
            f"status='{status}{corrected}', path='{self.parser_path_used}')>"
        )

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/models/parsing.py ---