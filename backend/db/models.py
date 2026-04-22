import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, Float, String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    token: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, default="Unknown")
    status: Mapped[str] = mapped_column(String, default="offline")  # online/offline
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TestSuite(Base):
    """A parsed test file (XMind or Markdown)."""
    __tablename__ = "test_suites"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String)
    source_format: Mapped[str] = mapped_column(String)  # xmind / markdown
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    cases: Mapped[list["TestCase"]] = relationship(back_populates="suite", cascade="all, delete-orphan")
    runs: Mapped[list["TestRun"]] = relationship(back_populates="suite", cascade="all, delete-orphan")


class TestCase(Base):
    """A single test case extracted from a suite."""
    __tablename__ = "test_cases"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id"))
    path: Mapped[str] = mapped_column(Text)       # "Module > Scenario > Condition"
    expected: Mapped[str] = mapped_column(Text, default="")   # leaf node / last segment
    order: Mapped[int] = mapped_column(Integer, default=0)
    parameters: Mapped[str] = mapped_column(Text, default="")  # JSON: [{"key":"val"}, ...] for parameterized runs

    suite: Mapped["TestSuite"] = relationship(back_populates="cases")


class TestRun(Base):
    """One execution of a full suite against a device."""
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id"))
    device_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/running/done
    provider: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    suite: Mapped["TestSuite"] = relationship(back_populates="runs")
    results: Mapped[list["TestResult"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class TestResult(Base):
    """Result of a single test case within a run."""
    __tablename__ = "test_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id"))
    case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id"))
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/pass/fail/error/skip
    reason: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[int] = mapped_column(Integer, default=0)
    screenshot_b64: Mapped[str] = mapped_column(Text, default="")  # final screenshot
    log: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False)
    action_history_json: Mapped[str] = mapped_column(Text, default="")  # JSON list of {step,fn_name,args,result}
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped["TestRun"] = relationship(back_populates="results")
    step_logs: Mapped[list["TestStepLog"]] = relationship(back_populates="result", cascade="all, delete-orphan")


class TestStepLog(Base):
    """One agent step within a test result — screenshot + action for replay."""
    __tablename__ = "test_step_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    result_id: Mapped[str] = mapped_column(ForeignKey("test_results.id"), index=True)
    step: Mapped[int] = mapped_column(Integer)
    thought: Mapped[str] = mapped_column(Text, default="")   # AI reasoning before acting
    action: Mapped[str] = mapped_column(Text, default="")    # "tap_element({'index': 7})"
    action_result: Mapped[str] = mapped_column(Text, default="")  # "Tapped element 7 at (550,200)"
    screenshot_b64: Mapped[str] = mapped_column(Text, default="")  # annotated screenshot

    # Token usage
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Timing (milliseconds)
    perception_ms: Mapped[int] = mapped_column(Integer, default=0)  # screenshot + UI tree + annotate
    llm_ms: Mapped[int] = mapped_column(Integer, default=0)         # LLM call duration
    action_ms: Mapped[int] = mapped_column(Integer, default=0)      # tool dispatch duration

    # Subgoal context (null for single-agent runs)
    subgoal_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    subgoal_desc: Mapped[str] = mapped_column(Text, default="")

    result: Mapped["TestResult"] = relationship(back_populates="step_logs")


class LessonLearned(Base):
    """Negative experience extracted from past runs — injected to avoid repeating mistakes."""
    __tablename__ = "lessons_learned"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    case_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)  # specific case, or null for global
    suite_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)  # for suite-level lessons
    app_package: Mapped[str] = mapped_column(String, default="")   # e.g. "com.wepie.wespy"
    task_keyword: Mapped[str] = mapped_column(String, default="")  # key part of path for fuzzy matching across quick runs
    screen_context: Mapped[str] = mapped_column(String, default="")  # e.g. "派对房间内"
    lesson: Mapped[str] = mapped_column(Text, default="")  # "不要点击底部聊天输入框，会弹出键盘"
    source_run_id: Mapped[str] = mapped_column(String, default="")
    source_step: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
