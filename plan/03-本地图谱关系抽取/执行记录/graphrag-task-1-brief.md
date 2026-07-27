## Task 1: Knowledge Graph Data Model

**Files:**
- Create: `app/models/knowledge_graph.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_graph_knowledge_service_postgres.py` (only the schema
  assertion in this task; more tests added in Task 4)

**Interfaces:**
- Produces: `KnowledgeEntity` (`id, city, category, name, source_document,
  attributes, created_at`), `KnowledgeRelation` (`id, from_entity_id,
  to_entity_id, relation_type, source_document, confidence, created_at`).

- [ ] **Step 1: Write the model file**

```python
# app/models/knowledge_graph.py
"""本地知识图谱：实体与关系。"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KnowledgeEntity(Base):
    __table_args__ = (
        UniqueConstraint("city", "category", "name", name="uq_knowledge_entity_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    city: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_document: Mapped[str] = mapped_column(String(255))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())


class KnowledgeRelation(Base):
    __table_args__ = (
        UniqueConstraint(
            "from_entity_id", "to_entity_id", "relation_type", name="uq_knowledge_relation_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledgeentity.id", ondelete="CASCADE"), index=True
    )
    to_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledgeentity.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(40), index=True)
    source_document: Mapped[str] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
```

Table names come from `Base.__tablename__` (`declared_attr`, lower-cased class
name) — `KnowledgeEntity` → `knowledgeentity`, matching the project's existing
convention (see `app/models/governance.py`, no model there sets an explicit
`__tablename__`). The `ForeignKey` targets must use the literal table name
`"knowledgeentity"`.

- [ ] **Step 2: Register the models**

```python
# app/models/__init__.py
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.draft import TripDraft
from app.models.governance import Approval, SavedItinerary, TaskEvent, UserPreference
from app.models.knowledge_graph import KnowledgeEntity, KnowledgeRelation
from app.models.message import Message
from app.models.tool_invocation import ToolInvocation
from app.models.user import User

__all__ = [
    "Approval",
    "Base",
    "Conversation",
    "KnowledgeEntity",
    "KnowledgeRelation",
    "Message",
    "SavedItinerary",
    "TaskEvent",
    "ToolInvocation",
    "TripDraft",
    "User",
    "UserPreference",
]
```

- [ ] **Step 3: Write the failing opt-in schema test**

```python
# tests/test_graph_knowledge_service_postgres.py
import os
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

import app.models  # noqa: F401
from app.models.base import async_session_maker, init_db
from app.models.knowledge_graph import KnowledgeEntity, KnowledgeRelation


pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_TESTS") != "1",
        reason="requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL database",
    ),
]


@pytest.mark.asyncio
async def test_knowledge_entity_identity_is_unique():
    await init_db()
    async with async_session_maker() as session, session.begin():
        session.add(
            KnowledgeEntity(
                city="__test_city__", category="attractions", name="__test_entity__",
                source_document="tests/fixture.md",
            )
        )

    with pytest.raises(IntegrityError):
        async with async_session_maker() as session, session.begin():
            session.add(
                KnowledgeEntity(
                    city="__test_city__", category="attractions", name="__test_entity__",
                    source_document="tests/fixture.md",
                )
            )

    # No KnowledgeRelation rows were created in this test, so only entities need cleanup.
    async with async_session_maker() as session, session.begin():
        await session.execute(
            KnowledgeEntity.__table__.delete().where(KnowledgeEntity.city == "__test_city__")
        )
```

- [ ] **Step 4: Run the test (skipped without a live database is expected)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_knowledge_service_postgres.py -q`

Expected: `1 skipped` when `RUN_POSTGRES_TESTS` is not set. If you have a
reachable local Postgres, run with
`RUN_POSTGRES_TESTS=1 .venv\Scripts\python.exe -m pytest tests/test_graph_knowledge_service_postgres.py -q`
and expect `1 passed`.

- [ ] **Step 5: Run compileall to catch import errors**

Run: `.venv\Scripts\python.exe -m compileall -q app/models`

Expected: exit code 0.

---

