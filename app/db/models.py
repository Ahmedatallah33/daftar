from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, default="")
    zoom_link = Column(String, default="")
    price_per_session = Column(Float, nullable=False, default=0.0)
    sessions_per_cycle = Column(Integer, nullable=False, default=8)
    weekly_schedule = Column(String, default="[]")
    session_time = Column(String, default="")
    day_schedules = Column(String, default="{}")
    # Built-in optional fields (same slot for every student)
    parent_phone = Column(String, default="")
    zoom_link_name = Column(String, default="")
    whatsapp_group_link = Column(String, default="")
    # Dynamic user-defined fields: JSON list of
    # [{"label": str, "value": str, "show_in_popup": bool}, ...]
    custom_fields = Column(Text, default="[]")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)

    sessions = relationship(
        "Session", back_populates="student", cascade="all, delete-orphan"
    )
    videos = relationship(
        "Video", back_populates="student", cascade="all, delete-orphan"
    )
    invoices = relationship(
        "Invoice", back_populates="student", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    session_date = Column(DateTime, default=datetime.now)
    cycle_number = Column(Integer, default=1)
    counted = Column(Boolean, default=True)
    is_free = Column(Boolean, default=False)
    lesson_summary = Column(Text, default="")
    notes = Column(Text, default="")

    student = relationship("Student", back_populates="sessions")


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    sent_date = Column(DateTime, default=datetime.now)
    description = Column(String, default="")
    counted = Column(Boolean, default=True)

    student = relationship("Student", back_populates="videos")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    issued_at = Column(DateTime, default=datetime.now)
    sessions_count = Column(Integer, default=0)
    videos_count = Column(Integer, default=0)
    amount = Column(Float, default=0.0)
    pdf_path = Column(String, default="")
    notes = Column(Text, default="")
    is_paid = Column(Boolean, default=False)
    paid_at = Column(DateTime, nullable=True)

    student = relationship("Student", back_populates="invoices")


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(Text, default="")


class WhatsAppGroup(Base):
    __tablename__ = "whatsapp_groups"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    invite_link = Column(String(255), nullable=False)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
