"""detect_language tells all nine film languages apart (it guards the Shorts writer: a script in another language than the requested one is refused)."""

from __future__ import annotations

import pytest

from app.utils import detect_language

SAMPLES = {
    "en": "When the police arrived at the house at three in the morning the neighbour who had called was no longer at the door but the light was still on",
    "de": "Als die Polizei um drei Uhr nachts am Haus ankam stand der Nachbar der angerufen hatte nicht mehr an der Tür aber das Licht brannte noch",
    "es": "Cuando la policía llegó a la casa a las tres de la madrugada el vecino que había llamado ya no estaba en la puerta pero la luz seguía encendida",
    "it": "Quando la polizia arrivò alla casa alle tre di notte il vicino che aveva chiamato non era più sulla porta ma la luce era ancora accesa",
    "fr": "Quand la police est arrivée à la maison à trois heures du matin le voisin qui avait appelé n'était plus sur le pas de la porte mais la lumière était encore allumée",
    "pt": "Quando a polícia chegou à casa às três da manhã o vizinho que tinha ligado já não estava na porta mas a luz da varanda continuava acesa",
    "tr": "Polis sabaha karşı saat üçte eve geldiğinde arayan komşu artık kapıda değildi ama verandanın ışığı hâlâ yanıyordu ve kimse konuşmak istemiyordu",
    "fa": "وقتی پلیس ساعت سه صبح به خانه رسید همسایه ای که زنگ زده بود دیگر در آستانه در نبود اما چراغ هنوز روشن بود و کسی نمی خواست حرف بزند",
    "ar": "عندما وصلت الشرطة إلى المنزل في الساعة الثالثة صباحا لم يكن الجار الذي اتصل عند الباب لكن الضوء كان لا يزال مضاء ولم يرد أحد أن يتكلم",
}


@pytest.mark.parametrize("language", sorted(SAMPLES))
def test_each_language_is_recognised_with_confidence(language):
    detected, confidence = detect_language(SAMPLES[language], min_chars=30)
    assert detected == language and confidence >= 0.6


def test_a_short_english_script_full_of_one_letter_words_is_not_taken_for_portuguese():
    text = "A finished evidence-led story. A finished evidence-led story. Watch the full evidence-led story. A finished evidence-led story."
    assert detect_language(text, min_chars=30)[0] == "en"


def test_too_little_text_decides_nothing():
    assert detect_language("Oi tudo bem", min_chars=30) == (None, 0.0)
