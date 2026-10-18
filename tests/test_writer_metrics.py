from app.services import writer_metrics as WM


def test_sentence_length_stats_measure_rhythm():
    flat = "One two three four five six. Seven eight nine ten eleven twelve. Aa bb cc dd ee ff."
    varied = "Short. This sentence is a good deal longer than the one before it by a fair margin. Tiny."
    assert WM.sentence_length_stats(flat)["variance"] == 0.0
    v = WM.sentence_length_stats(varied)
    assert v["variance"] > 20 and v["short_share"] == round(2 / 3, 3)


def test_repeated_trigrams_are_counted_and_clean_text_scores_zero():
    assert WM.repeated_trigram_rate("a b c d e f g h") == 0.0
    assert WM.repeated_trigram_rate("the old house the old house the old house") > 0.5


def test_stock_phrases_are_found_per_language_case_insensitively():
    assert WM.stock_phrase_hits("Little did they know the Shocking Truth.", "en") == [
        "little did", "shocking truth"]
    assert WM.stock_phrase_hits("آنچه بعد رخ داد تکان‌دهنده بود", "fa") == ["آنچه بعد رخ داد"]
    assert WM.stock_phrase_hits("anything", "xx") == []


def test_length_error_is_signed_against_the_plan():
    text = " ".join(["w"] * 120)
    assert WM.length_error(text, 100) == 0.2
    assert WM.length_error(text, 200) == -0.4
    assert WM.beat_metrics(text, "en", 100)["length_error"] == 0.2


def test_stored_quality_reads_the_validators_own_record():
    notes = {"score": 71, "quality_gates": {"grounding_score": 0.88,
             "grounding_unsupported_claims": 3, "failures": ["grounding"]},
             "section_scores": [{"id": "act1", "score": 70}]}
    q = WM.stored_quality(notes)
    assert (q["grounding_score"], q["unsupported_claims"], q["engagement_score"]) == (0.88, 3, 71)
    assert q["gate_failures"] == ["grounding"]


def test_one_line_paragraph_share_counts_single_sentence_paragraphs_and_skips_act_markers():
    text = ("[[ACT:act1]]\n\nHe left. Nobody saw him go.\n\nShe waited.\n\nThe door was open. The lamp was lit.\n\n"
            "[[ACT:act2]]\n\nIt rained.")
    assert WM.one_line_paragraph_share(text) == 0.5          # 2 of 4 paragraphs are one sentence
    assert WM.one_line_paragraph_share("") == 0.0
    assert WM.beat_metrics(text, "en")["one_line_paragraph_share"] == 0.5
