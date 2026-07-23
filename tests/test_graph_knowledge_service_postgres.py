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
