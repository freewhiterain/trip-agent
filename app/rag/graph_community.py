"""图社区检测的简化实现：每个实体各自成一个社区，重要度全部相等。

真实的 Leiden 社区检测 + PageRank 重要度升级路径见
docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md。
本阶段不接入 Worker 或离线构建脚本的主流程，只交付可独立测试的接口。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommunityEntity:
    """独立于 SQLAlchemy ORM 的轻量实体表示，供社区检测使用。"""

    id: str
    name: str


@dataclass(frozen=True)
class CommunityRelation:
    from_entity_id: str
    to_entity_id: str
    relation_type: str


@dataclass
class Community:
    entities: list[CommunityEntity]
    importance: float
    summary: str


class GraphCommunityService:
    def build_communities(
        self,
        entities: list[CommunityEntity],
        relations: list[CommunityRelation],
    ) -> list[Community]:
        # relations 暂未使用：简化版不做真实图聚类，接口先接受它以匹配未来
        # 真实算法（需要遍历关系构图）的签名，避免升级时改调用方。
        return [
            Community(entities=[entity], importance=1.0, summary=entity.name)
            for entity in entities
        ]


def get_graph_community_service() -> GraphCommunityService:
    return GraphCommunityService()
