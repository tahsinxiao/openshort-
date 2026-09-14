from editorial_brain import critic_check, rank_windows


def test_rank_windows_prefers_complete_surprising_dense_text():
    ranked = rank_windows([
        {"id": "weak", "start": 0, "end": 30, "text": "And then it was kind of okay"},
        {"id": "strong", "start": 30, "end": 45,
         "text": "The result was actually ten times more dangerous."},
    ])
    assert ranked[0]["id"] == "strong"
    assert ranked[0]["editorial_pre_score"] > ranked[1]["editorial_pre_score"]


def test_critic_flags_context_dependent_opening_and_cutoff():
    result = critic_check("And that is why", "the story continues")
    assert result["approved"] is False
    assert len(result["issues"]) == 2
