import uuid
from datetime import datetime
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select

from app.api.dependencies import get_current_user
from app.api.v1.conversations import create_conversation, get_conversation
from app.main import app
from app.models.base import async_session_maker, get_db, init_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.conversation import ConversationCreate


POSTGRES_TESTS_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"
POSTGRES_SKIP_REASON = "set RUN_POSTGRES_TESTS=1 to enable PostgreSQL integration tests"


class RecordingSession:
    def __init__(self, *, fail_commit=False):
        self.conversations = []
        self.messages = []
        self.add_events = []
        self.commit_snapshots = []
        self.commit_attempts = 0
        self.commit_count = 0
        self.committed_records = []
        self.persisted_conversations = []
        self.persisted_messages = []
        self.rolled_back = False
        self.fail_commit = fail_commit
        self._committed_event_count = 0

    def add(self, entity):
        if self.commit_attempts:
            raise AssertionError("entities must be added before commit")
        if isinstance(entity, Conversation):
            self.conversations.append(entity)
            self.add_events.append("Conversation")
        elif isinstance(entity, Message):
            self.messages.append(entity)
            self.add_events.append("Message")
        else:
            raise AssertionError(f"unexpected entity: {type(entity).__name__}")

    async def flush(self):
        for conversation in self.conversations:
            if conversation.id is None:
                conversation.id = uuid.uuid4()
        for message in self.messages:
            if message.id is None:
                message.id = uuid.uuid4()

    def _populate_defaults(self):
        for conversation in self.conversations:
            conversation.extra_info = conversation.extra_info or {}
            conversation.created_at = conversation.created_at or datetime.now()
            conversation.updated_at = conversation.updated_at or conversation.created_at
        for message in self.messages:
            message.extra_info = message.extra_info or {}
            message.created_at = message.created_at or datetime.now()

    async def commit(self):
        self.commit_attempts += 1
        self.commit_snapshots.append(
            tuple(self.add_events[self._committed_event_count:])
        )
        self._committed_event_count = len(self.add_events)
        if self.fail_commit:
            raise RuntimeError("commit failed")

        await self.flush()
        self._populate_defaults()
        self.commit_count += 1
        self.committed_records = list(self.add_events)
        self.persisted_conversations = list(self.conversations)
        self.persisted_messages = list(self.messages)

    async def refresh(self, entity):
        await self.flush()
        self._populate_defaults()

    async def rollback(self):
        self.rolled_back = True

    async def execute(self, statement):
        return ScalarResult(self.conversations[0] if self.conversations else None)


class ScalarResult:
    def __init__(self, entity):
        self.entity = entity

    def scalar_one_or_none(self):
        return self.entity


def make_user():
    return User(
        id=uuid.uuid4(),
        username="traveler",
        email="traveler@example.com",
        password_hash="hash",
    )


async def cleanup_postgres_user(user_id):
    async with async_session_maker() as db:
        try:
            result = await db.execute(
                select(Conversation.id).where(Conversation.user_id == user_id)
            )
            conversation_ids = result.scalars().all()
            if conversation_ids:
                await db.execute(
                    delete(Message).where(Message.conversation_id.in_(conversation_ids))
                )
            await db.execute(delete(Conversation).where(Conversation.user_id == user_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
        except Exception:
            await db.rollback()
            raise


@pytest.fixture
async def postgres_user():
    await init_db()
    user_id = uuid.uuid4()
    username = f"task4_{user_id.hex}"
    user = User(
        id=user_id,
        username=username,
        email=f"{username}@example.com",
        password_hash="task4-integration",
        preferences={},
    )

    async with async_session_maker() as db:
        async with db.begin():
            db.add(user)

    try:
        yield user
    finally:
        await cleanup_postgres_user(user_id)


def override_dependencies(db, user):
    async def override_db():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = override_db


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_new_conversation_has_one_persisted_greeting_and_reads_do_not_duplicate():
    db = RecordingSession()
    user = make_user()

    response = await create_conversation(ConversationCreate(title="Tokyo"), user, db)

    assert db.add_events == ["Conversation", "Message"]
    assert db.commit_snapshots == [("Conversation", "Message")]
    assert db.commit_attempts == 1
    assert db.commit_count == 1
    assert len(db.messages) == 1
    assert db.messages[0].role == "assistant"
    assert db.messages[0].content == "需要我帮你规划一下旅行吗？"
    assert db.messages[0].extra_info == {"kind": "conversation_offer"}
    assert response.initial_message.content == "需要我帮你规划一下旅行吗？"
    assert response.initial_message.role == "assistant"
    assert response.initial_message.extra_info == {"kind": "conversation_offer"}

    await get_conversation(str(response.id), user, db)
    await get_conversation(str(response.id), user, db)

    assert db.add_events == ["Conversation", "Message"]
    assert len(db.messages) == 1
    assert db.commit_count == 1


def test_create_conversation_http_returns_serializable_initial_message_and_read_is_read_only():
    db = RecordingSession()
    user = make_user()
    override_dependencies(db, user)
    client = TestClient(app, raise_server_exceptions=False)

    try:
        response = client.post("/api/v1/conversations", json={"title": "Tokyo"})
        payload = response.json()

        assert response.status_code == 200
        assert {"id", "user_id", "title", "status", "extra_info", "created_at", "updated_at"} <= payload.keys()
        assert payload["title"] == "Tokyo"
        initial_message = payload["initial_message"]
        assert initial_message["conversation_id"] == payload["id"]
        assert initial_message["role"] == "assistant"
        assert initial_message["content"] == "需要我帮你规划一下旅行吗？"
        assert initial_message["extra_info"]["kind"] == "conversation_offer"
        assert db.commit_attempts == 2
        assert db.commit_count == 2
        assert db.commit_snapshots == [("Conversation", "Message"), ()]

        add_events_before_read = list(db.add_events)
        message_count_before_read = len(db.messages)
        commit_attempts_before_read = db.commit_attempts
        commit_count_before_read = db.commit_count
        commit_snapshots_before_read = list(db.commit_snapshots)
        read_response = client.get(f"/api/v1/conversations/{payload['id']}")

        assert read_response.status_code == 200
        assert read_response.json()["id"] == payload["id"]
        assert db.add_events == add_events_before_read
        assert db.commit_attempts == commit_attempts_before_read + 1
        assert db.commit_count == commit_count_before_read + 1
        assert db.commit_snapshots == commit_snapshots_before_read + [()]
        assert len(db.messages) == message_count_before_read == 1
    finally:
        client.close()


def test_create_conversation_http_rolls_back_dependency_commit_failure():
    db = RecordingSession(fail_commit=True)
    override_dependencies(db, make_user())
    client = TestClient(app, raise_server_exceptions=False)

    try:
        response = client.post("/api/v1/conversations", json={"title": "Tokyo"})
    finally:
        client.close()

    assert response.status_code == 500
    assert db.add_events == ["Conversation", "Message"]
    assert db.commit_snapshots == [("Conversation", "Message")]
    assert db.commit_attempts == 1
    assert db.commit_count == 0
    assert db.committed_records == []
    assert db.persisted_conversations == []
    assert db.persisted_messages == []
    assert db.rolled_back is True


@pytest.mark.skipif(not POSTGRES_TESTS_ENABLED, reason=POSTGRES_SKIP_REASON)
@pytest.mark.asyncio
async def test_postgres_create_persists_one_conversation_and_greeting(postgres_user):
    async with async_session_maker() as db:
        response = await create_conversation(
            ConversationCreate(title="Task 4 PostgreSQL"),
            postgres_user,
            db,
        )

    async with async_session_maker() as db:
        conversation_result = await db.execute(
            select(Conversation).where(Conversation.user_id == postgres_user.id)
        )
        conversations = conversation_result.scalars().all()
        message_result = await db.execute(
            select(Message).where(Message.conversation_id == response.id)
        )
        messages = message_result.scalars().all()

        assert len(conversations) == 1
        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert messages[0].content == "需要我帮你规划一下旅行吗？"
        assert messages[0].extra_info == {"kind": "conversation_offer"}


@pytest.mark.skipif(not POSTGRES_TESTS_ENABLED, reason=POSTGRES_SKIP_REASON)
@pytest.mark.asyncio
async def test_postgres_rollback_removes_staged_conversation_and_greeting(postgres_user):
    staged_types = []
    staged_conversation_ids = []
    staged_message_ids = []

    async with async_session_maker() as db:
        def fail_after_both_entities_flush(sync_session):
            staged_entities = list(sync_session.identity_map.values())
            staged_types.extend(type(entity) for entity in staged_entities)
            staged_conversation_ids.extend(
                entity.id
                for entity in staged_entities
                if isinstance(entity, Conversation)
            )
            staged_message_ids.extend(
                entity.id
                for entity in staged_entities
                if isinstance(entity, Message)
            )
            if Conversation not in staged_types or Message not in staged_types:
                return
            raise RuntimeError("forced Task 4 rollback")

        event.listen(
            db.sync_session,
            "before_commit",
            fail_after_both_entities_flush,
        )
        try:
            with pytest.raises(RuntimeError, match="forced Task 4 rollback"):
                await create_conversation(
                    ConversationCreate(title="Task 4 rollback"),
                    postgres_user,
                    db,
                )
        finally:
            event.remove(
                db.sync_session,
                "before_commit",
                fail_after_both_entities_flush,
            )
            await db.rollback()

    assert Conversation in staged_types
    assert Message in staged_types
    assert len(staged_conversation_ids) == 1
    assert len(staged_message_ids) == 1

    async with async_session_maker() as db:
        conversation = await db.scalar(
            select(Conversation).where(Conversation.id == staged_conversation_ids[0])
        )
        message = await db.scalar(select(Message).where(Message.id == staged_message_ids[0]))

        assert conversation is None
        assert message is None
