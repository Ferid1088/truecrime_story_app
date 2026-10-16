from app.services.figures import added_figures, figures


def test_figures_compare_digits_of_every_script_as_ascii():
    assert figures("در سال ۱۸۹۲ و 1,500 دلار؛ 7 نفر") == {"1892", "1500"}
    assert figures("عام ١٩٢٤") == {"1924"}


def test_a_rewrite_that_adds_a_number_is_flagged_and_one_from_the_evidence_is_not():
    pack = {"facts": [{"id": "F1", "claim": "The trial began in June 1893.",
                       "supporting_text": "forty-one whacks, 1893"}]}
    master = "Der Mord geschah 1892."
    assert added_figures("Der Mord geschah 1892, der Prozess 1893.", master, pack) == []
    assert added_figures("Der Mord geschah 1892, mit 4000 Dollar.", master, pack) == ["4000"]
