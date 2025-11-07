# ai_service/models.py
"""
تعريفات ORM للجداول المشتركة التي تحتاج خدمة AI للوصول إليها.
هذه هي نسخة طبق الأصل جزئية من نماذج النظام الرئيسي لضمان الربط المستقل.
"""

import sqlalchemy as sa
from sqlalchemy import Column, Integer, String, Text, Boolean, Numeric, DateTime, ForeignKey, func, BigInteger
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB

# استيراد Base من ملف database.py المحلي الخاص بنا
from database import Base

# نحتاج إلى 'User' لنتمكن من ربط 'analyst_id' و 'user_id'
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    # لا نحتاج لباقي الحقول لأننا لن ننشئ مستخدمين، فقط سنقرأ/نربط بالـ ID

class ParsingTemplate(Base):
    __tablename__ = 'parsing_templates'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=True)
    pattern_type = Column(String(50), nullable=False, server_default='regex')
    pattern_value = Column(Text, nullable=False)
    analyst_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    is_public = Column(Boolean, nullable=False, server_default=sa.text('false'), index=True)
    confidence_score = Column(Numeric(5, 2), nullable=True)
    stats = Column(JSONB(astext_type=sa.Text()), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    owner = relationship("User")

class ParsingAttempt(Base):
    __tablename__ = 'parsing_attempts'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    raw_content = Column(Text, nullable=False)
    used_template_id = Column(Integer, ForeignKey('parsing_templates.id', ondelete='SET NULL'), nullable=True)
    result_data = Column(JSONB(astext_type=sa.Text()), nullable=True)
    was_successful = Column(Boolean, nullable=False, server_default=sa.text('false'), index=True)
    was_corrected = Column(Boolean, nullable=False, server_default=sa.text('false'), index=True)
    corrections_diff = Column(JSONB(astext_type=sa.Text()), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    parser_path_used = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User")
    template_used = relationship("ParsingTemplate")