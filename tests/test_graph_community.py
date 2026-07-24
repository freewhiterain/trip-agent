from app.rag.graph_community import CommunityEntity, CommunityRelation, GraphCommunityService


def test_each_entity_becomes_its_own_community_with_equal_importance():
    entities = [
        CommunityEntity(id="1", name="宽窄巷子"),
        CommunityEntity(id="2", name="武侯祠"),
    ]
    relations = [CommunityRelation(from_entity_id="1", to_entity_id="2", relation_type="near")]
    service = GraphCommunityService()

    communities = service.build_communities(entities, relations)

    assert len(communities) == 2
    assert {community.entities[0].name for community in communities} == {"宽窄巷子", "武侯祠"}
    assert all(community.importance == 1.0 for community in communities)


def test_build_communities_handles_empty_entity_list():
    service = GraphCommunityService()

    assert service.build_communities([], []) == []
