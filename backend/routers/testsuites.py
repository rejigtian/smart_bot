"""Test suite management — upload XMind/MD files, list suites and cases."""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.test_parser import parse_file
from db.database import AsyncSessionLocal
from db.models import TestCase, TestResult, TestRun, TestSuite

router = APIRouter(prefix="/api/suites", tags=["suites"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


class SuiteOut(BaseModel):
    id: str
    name: str
    source_format: str
    case_count: int
    created_at: str


class CaseOut(BaseModel):
    id: str
    order: int
    path: str
    expected: str
    parameters: str = ""


class CaseIn(BaseModel):
    path: str
    expected: str = ""
    parameters: str = ""  # JSON array: [{"key": "val"}, ...]


@router.get("", response_model=List[SuiteOut])
async def list_suites(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TestSuite).order_by(TestSuite.created_at.desc()))
    suites = result.scalars().all()
    out = []
    for s in suites:
        count_res = await db.execute(
            select(TestCase).where(TestCase.suite_id == s.id)
        )
        count = len(count_res.scalars().all())
        out.append(SuiteOut(
            id=s.id,
            name=s.name,
            source_format=s.source_format,
            case_count=count,
            created_at=s.created_at.isoformat(),
        ))
    return out


@router.post("", response_model=SuiteOut, status_code=201)
async def upload_suite(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    filename = file.filename or "upload"
    content = await file.read()

    try:
        cases = parse_file(filename, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not cases:
        raise HTTPException(status_code=422, detail="No test cases found in file")

    ext = filename.rsplit(".", 1)[-1].lower()
    fmt = "xmind" if ext == "xmind" else "markdown"

    suite = TestSuite(name=filename, source_format=fmt)
    db.add(suite)
    await db.flush()  # get suite.id

    for i, c in enumerate(cases):
        db.add(TestCase(
            suite_id=suite.id,
            path=c.path,
            expected=c.expected,
            order=i,
        ))

    await db.commit()
    await db.refresh(suite)

    return SuiteOut(
        id=suite.id,
        name=suite.name,
        source_format=suite.source_format,
        case_count=len(cases),
        created_at=suite.created_at.isoformat(),
    )


@router.get("/{suite_id}", response_model=SuiteOut)
async def get_suite(suite_id: str, db: AsyncSession = Depends(get_db)):
    suite = await db.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    count_res = await db.execute(
        select(TestCase).where(TestCase.suite_id == suite_id)
    )
    count = len(count_res.scalars().all())
    return SuiteOut(
        id=suite.id,
        name=suite.name,
        source_format=suite.source_format,
        case_count=count,
        created_at=suite.created_at.isoformat(),
    )


@router.get("/{suite_id}/cases", response_model=List[CaseOut])
async def list_cases(suite_id: str, db: AsyncSession = Depends(get_db)):
    suite = await db.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    result = await db.execute(
        select(TestCase)
        .where(TestCase.suite_id == suite_id)
        .order_by(TestCase.order)
    )
    cases = result.scalars().all()
    return [CaseOut(id=c.id, order=c.order, path=c.path, expected=c.expected, parameters=c.parameters or "") for c in cases]


@router.delete("/{suite_id}", status_code=204)
async def delete_suite(suite_id: str, db: AsyncSession = Depends(get_db)):
    suite = await db.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    await db.delete(suite)
    await db.commit()


# ── Test case CRUD ────────────────────────────────────────────────────────────

@router.post("/{suite_id}/cases", response_model=CaseOut, status_code=201)
async def add_case(suite_id: str, body: CaseIn, db: AsyncSession = Depends(get_db)):
    suite = await db.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    res = await db.execute(
        select(func.max(TestCase.order)).where(TestCase.suite_id == suite_id)
    )
    max_order = res.scalar() or 0
    case = TestCase(suite_id=suite_id, path=body.path, expected=body.expected, order=max_order + 1, parameters=body.parameters)
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return CaseOut(id=case.id, order=case.order, path=case.path, expected=case.expected)


@router.put("/{suite_id}/cases/{case_id}", response_model=CaseOut)
async def update_case(
    suite_id: str, case_id: str, body: CaseIn, db: AsyncSession = Depends(get_db)
):
    case = await db.get(TestCase, case_id)
    if not case or case.suite_id != suite_id:
        raise HTTPException(status_code=404, detail="Case not found")
    case.path = body.path
    case.expected = body.expected
    case.parameters = body.parameters
    await db.commit()
    return CaseOut(id=case.id, order=case.order, path=case.path, expected=case.expected)


@router.delete("/{suite_id}/cases/{case_id}", status_code=204)
async def delete_case(suite_id: str, case_id: str, db: AsyncSession = Depends(get_db)):
    case = await db.get(TestCase, case_id)
    if not case or case.suite_id != suite_id:
        raise HTTPException(status_code=404, detail="Case not found")
    await db.delete(case)
    await db.commit()


# ── Trends ───────────────────────────────────────────────────────────────────

class TrendPoint(BaseModel):
    run_id: str
    created_at: str
    provider: str
    model: str
    passed: int
    failed: int
    errored: int
    total: int
    pass_rate: float  # 0.0 - 100.0


@router.get("/{suite_id}/trends", response_model=List[TrendPoint])
async def get_trends(suite_id: str, limit: int = 20, db: AsyncSession = Depends(get_db)):
    """Return pass rate trend for the last N runs of a suite."""
    suite = await db.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")

    runs_res = await db.execute(
        select(TestRun)
        .where(TestRun.suite_id == suite_id, TestRun.status == "done")
        .order_by(TestRun.created_at.desc())
        .limit(limit)
    )
    runs = list(reversed(runs_res.scalars().all()))  # oldest first for chart

    points = []
    for run in runs:
        res = await db.execute(
            select(TestResult).where(TestResult.run_id == run.id)
        )
        results = res.scalars().all()
        counts = {"pass": 0, "fail": 0, "error": 0}
        for r in results:
            if r.status in counts:
                counts[r.status] += 1
        total = len(results)
        points.append(TrendPoint(
            run_id=run.id,
            created_at=run.created_at.isoformat(),
            provider=run.provider,
            model=run.model,
            passed=counts["pass"],
            failed=counts["fail"],
            errored=counts["error"],
            total=total,
            pass_rate=round(counts["pass"] / total * 100, 1) if total > 0 else 0.0,
        ))

    return points
