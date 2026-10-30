"""Fixed sample content for the graphics tests (design samples only: never shown in a film)."""

TEXT = {
    "en": {"q": "Was the garage door open when the officers arrived?", "a": "Officer Harlan",
           "ac": "The garage door stood open and a single set of footprints led to the rear fence.",
           "as": "Incident report, page 3", "b": "Neighbour Mrs Cole",
           "bc": "The garage door was shut when I walked my dog past the house at six that morning.",
           "bs": "Witness statement, page 1", "sum": "Two accounts of the same door",
           "fact": "Four people were found dead at the house.", "open": "Who opened the garage door?"},
    "de": {"q": "War das Garagentor offen, als die Beamten eintrafen?", "a": "Beamter Harlan",
           "ac": "Das Garagentor stand offen, eine einzelne Fußspur führte zum hinteren Zaun.",
           "as": "Einsatzbericht, Seite 3", "b": "Nachbarin Frau Cole",
           "bc": "Das Garagentor war geschlossen, als ich um sechs Uhr morgens mit dem Hund vorbeiging.",
           "bs": "Zeugenaussage, Seite 1", "sum": "Zwei Darstellungen derselben Tür",
           "fact": "Vier Menschen wurden tot im Haus gefunden.", "open": "Wer öffnete das Garagentor?"},
    "fa": {"q": "آیا درِ گاراژ هنگام رسیدن افسرها باز بود؟", "a": "افسر هارلن",
           "ac": "درِ گاراژ باز بود و یک ردپا تا حصار پشتی می‌رفت.", "as": "گزارش حادثه، صفحه ۳",
           "b": "همسایه، خانم کول", "bc": "درِ گاراژ بسته بود؛ ساعت شش صبح با سگم از کنار خانه رد شدم.",
           "bs": "اظهارات شاهد، صفحه ۱", "sum": "دو روایت از یک در",
           "fact": "چهار نفر در خانه مرده پیدا شدند.", "open": "چه کسی در گاراژ را باز کرد؟"},
    "ar": {"q": "هل كان باب المرآب مفتوحاً عند وصول الضباط؟", "a": "الضابط هارلان",
           "ac": "كان باب المرآب مفتوحاً وتؤدي آثار أقدام إلى السياج الخلفي.", "as": "تقرير الحادث، صفحة 3",
           "b": "الجارة السيدة كول", "bc": "كان باب المرآب مغلقاً حين مررت بالمنزل مع كلبي في السادسة صباحاً.",
           "bs": "إفادة الشاهدة، صفحة 1", "sum": "روايتان عن الباب نفسه",
           "fact": "عُثر على أربعة أشخاص موتى في المنزل.", "open": "من فتح باب المرآب؟"},
}


def conflict(language: str, status: str = "open") -> dict:
    t = TEXT[language]
    return {"conflict": {"id": "C3", "number": 3, "title": t["q"], "kind": "witnesses", "status": status,
                         "summary": t["sum"]},
            "sides": [{"who": t["a"], "claim": t["ac"], "source": t["as"], "fact_id": "F12"},
                      {"who": t["b"], "claim": t["bc"], "source": t["bs"], "fact_id": "F15"}]}


KEY_WORDS = {"en": ("Four found dead", "Two accounts, one door", "Door opened by whom?"),
             "de": ("Vier Tote gefunden", "Zwei Darstellungen, eine Tür", "Wer öffnete das Tor?"),
             "fa": ("چهار نفر مرده یافت شدند", "دو روایت از یک در", "چه کسی در را باز کرد؟"),
             "ar": ("العثور على أربعة موتى", "روايتان عن باب واحد", "من فتح الباب؟")}


def recap(language: str) -> dict:
    fact, disputed, unknown = KEY_WORDS[language]
    return {"confirmed": [{"text": fact, "fact_id": "F1"}], "disputed": [{"text": disputed, "fact_id": "F4"}],
            "unknown": [{"text": unknown, "fact_id": "Q1"}]}


def timeline(language: str) -> dict:
    t = TEXT[language]
    points = [{"date": {"year": 2022, "month": 11, "day": 13}, "labels": [t["fact"]], "fact_id": "F1"},
              {"date": {"year": 2022, "month": 12, "day": 30}, "labels": [t["sum"], t["open"]], "fact_id": "F2"},
              {"date": {"year": 2023, "month": 5}, "labels": [t["open"]], "fact_id": "F3"}]
    return {"title": t["q"], "points": points}


def document() -> dict:
    return {"document": {"id": 7, "type": "Affidavit of probable cause", "issuer": "Franklin County Court",
                         "case_number": "22CR-4471", "date": "Dec 30, 2022", "page": "4", "language": "en",
                         "exhibit": True},
            "quote": {"text": "Officers found the garage door open.", "fact_id": "F12", "verified": True},
            "context_before": ["On the morning of March 3, officers responded to a call at the home of John Doe."],
            "context_after": ["The officers then secured the scene."], "redactions": ["John Doe"]}


def headline() -> dict:
    return {"headline": "Four students found dead in off-campus home", "source": "The New York Times",
            "url": "https://www.nytimes.com/2022/11/14/us/idaho.html", "published": {"year": 2022, "month": 11, "day": 14},
            "fact_id": "F1", "language": "en"}


