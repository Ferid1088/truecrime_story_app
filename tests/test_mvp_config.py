import pytest

from app.core.ai_config import MvpVerificationConfig, ai_config

MVP_ROLES = ["mvp_needs_planner", "mvp_query_planner", "mvp_candidate_verifier", "mvp_second_verifier",
             "mvp_segment_scout", "mvp_scene_director"]
VISION_ROLES = ["mvp_candidate_verifier", "mvp_second_verifier", "mvp_segment_scout"]


def test_every_mvp_role_is_routed_and_has_saved_parameters():
    provider = ai_config.generation_provider()
    for role in MVP_ROLES:
        assert provider.routing[role] in provider.models
        assert ai_config.generation_for(role).max_tokens > 0


def test_roles_that_look_at_pictures_use_a_vision_model():
    provider = ai_config.generation_provider()
    for role in VISION_ROLES:
        model = provider.models[provider.routing[role]]
        assert provider.capabilities[model].vision, role


def test_search_rounds_are_one_initial_and_two_further():
    cfg = ai_config.mvp().search
    assert (cfg.initial_rounds, cfg.extra_rounds_max) == (1, 2)


def test_a_status_cannot_both_approve_and_never_approve():
    with pytest.raises(ValueError, match="both approve"):
        MvpVerificationConfig(escalate_conflicts_to_second_verifier=True, transient_failure_retries=1,
                              min_confidence_exact_person=0.8, min_confidence_other=0.6, min_quality=0.3,
                              max_candidates_per_requirement=5, max_requirements_per_asset=3,
                              rights_profile="preview",
                              approving_statuses=["approved"], never_approving_statuses=["approved"])
