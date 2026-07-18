"""阶段 1 使用的本地知识读取辅助函数。"""

from pathlib import Path

from app.config import BASE_DIR
from app.schemas.planning import Evidence


DESTINATION_FILES = {
    "成都": "chengdu.md",
    "西安": "xian.md",
}


def load_destination_evidence(destination: str, topic: str) -> list[Evidence]:
    filename = DESTINATION_FILES.get(destination)
    if not filename:
        return []

    path = Path(BASE_DIR) / "data" / "documents" / "destinations" / filename
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    return [
        Evidence(
            content=content[:1500],
            source=f"本地目的地知识库：{path.stem}",
            source_url=None,
            confidence=0.75,
            metadata={"path": str(path), "topic": topic, "static": True},
        )
    ]
