"""ORM models for the temp adoption-run database.

Deliberately holds only run state and Mist-issued adoption codes — never
credentials (Mist API token, AOS-CX password), which are passed through
memory only for the lifetime of a single run (see app/worker.py).
"""
from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class AdoptionJob(Base):
    __tablename__ = "adoption_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    mist_host: Mapped[str] = mapped_column(String(255))
    org_id: Mapped[str] = mapped_column(String(255))
    switch_count: Mapped[int] = mapped_column(Integer, default=0)
    # pending -> collecting_codes -> pushing -> complete | complete_with_errors | failed
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    codes: Mapped[list["AdoptionCode"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="AdoptionCode.id"
    )
    switches: Mapped[list["SwitchResult"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="SwitchResult.order_index",
    )


class AdoptionCode(Base):
    """A single-use AOS-CX Mist registration code, minted one-per-switch by
    GET /orgs/{org_id}/aoscx/register_cmd (see app/mist_client.py). Unlike
    the earlier (incorrect) inventory-based design, Mist does not associate
    a freshly-minted code with any particular device/MAC/serial ahead of
    time, so there is no per-device metadata to store here."""

    __tablename__ = "adoption_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("adoption_jobs.id"))
    registration_code: Mapped[str] = mapped_column(String(1024))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    job: Mapped[AdoptionJob] = relationship(back_populates="codes")


class SwitchResult(Base):
    """Per-switch outcome for one adoption run, in dashboard IP-list order."""

    __tablename__ = "switch_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("adoption_jobs.id"))
    order_index: Mapped[int] = mapped_column(Integer)
    ip: Mapped[str] = mapped_column(String(64))
    # pending -> adopting -> success | failed
    status: Mapped[str] = mapped_column(String(32), default="pending")
    registration_code_used: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )

    job: Mapped[AdoptionJob] = relationship(back_populates="switches")
