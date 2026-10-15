from app.rag.retrieve import fuse_scores, keyword_score, select_candidates


def test_reciprocal_rank_fusion_prefers_items_ranked_by_both_lists():
    fused = fuse_scores([["a", "b", "c"], ["b", "c", "d"]], k=60)
    assert fused["b"] > fused["a"]
    assert fused["b"] > fused["d"]


def test_similarity_floor_keeps_keyword_hits_and_drops_weak_vectors():
    chosen = select_candidates(
        fused_ids=["semantic", "exact", "weak"],
        vector_scores={"semantic": 0.9, "exact": 0.05, "weak": 0.1},
        keyword_scores={"exact": 1.0},
        top_k=8,
        similarity_floor=0.4,
    )
    assert chosen == ["semantic", "exact"]


def test_keyword_score_matches_policy_codes():
    assert keyword_score("What does POL-REF-30 cover?", "See policy POL-REF-30 for refunds.") > 0
    assert keyword_score("unrelated zebra question", "refund policy text") == 0
