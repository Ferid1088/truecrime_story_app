# Arabic spoken numbers: rule audit

Layer: `app/documentary/spoken_numbers/ar.py`, `ar_words.py`, `app/documentary/arabic_numbers.py`. Options: `config/parameters/audio.json`, `voice.number_words.ar`.
Output is plain Modern Standard Arabic: no vowel marks, no case endings (the counted noun after 11 to 99 takes an alef: "دولارا").

Verdicts: **confirmed** (a fetched source agrees), **fixed** (a source or the case 15 text contradicted the old behaviour; a test pins the fix), **unverified** (no authoritative source reached; the behaviour is a documented convention).
The web tools reached grammar sites, Wiktionary, a Cairo Academy study, Al Jazeera and France 24 Arabic pages, a TTS paper abstract and the English Wikipedia emergency list. They did not reach the Academy decisions themselves, BBC Arabic or Sky News Arabia style guides (blocked or not found).

| # | Rule | Current behaviour | Sources | Verdict |
|---|------|-------------------|---------|---------|
| 1 | Cardinals 3-10 take the opposite gender of the counted noun's singular | 3 رجال: ثلاثة; 3 سنوات: ثلاث | al-dirassa.com/en/arabic-numbers; arabikey.com/arabic-number-rules; kalimah-center.com/arabic-numbers-grammar | confirmed |
| 2 | 11 and 12 agree with the noun | أحد عشر / إحدى عشرة; اثنا عشر / اثنتا عشرة | same | confirmed |
| 3 | 13-19: unit opposite, "ten" agrees | ثلاثة عشر / ثلاث عشرة | same | confirmed |
| 4 | Tens do not change with gender; units precede with "و" (21: واحد وعشرون; fem. إحدى وعشرون) | as stated | arabikey; al-dirassa | confirmed |
| 5 | Spelling of 100: مئة (Cairo Academy dropped the extra alef; مائة remains accepted) | مئة, مئتان, ثلاثمئة | iasj.rdd.edu.iq study of the Academy decision (1937); en.wiktionary Appendix:Arabic numerals | confirmed |
| 6 | 300-900 forms | ثلاثمئة ... تسعمئة (800: ثمانمئة) | Wiktionary appendix (writes ثمانيمائة); the exact 800 spelling is not decided by it | confirmed (800 spelling unverified) |
| 7 | Thousand, dual, plural 3-10 آلاف, millions/billions | ألف, ألفان, ثلاثة آلاف; ملايين, مليارات | al-dirassa | confirmed |
| 8 | Trillion spelling | now تريليون (was ترليون); input accepts both | WordReference en-ar lists تريليون; spelling authority not found | fixed |
| 9 | Counted noun case: 3-10 plural genitive, 11-99 singular accusative, 100+ singular genitive; complex numbers follow the last digits | noun_phrase() | al-dirassa; arabikey (2026 and 9504 examples) | confirmed |
| 10 | Endings 01 and 02 after a hundred (101, 102 + noun) | "مئة واثنان يوم" | arabikey recommends the paraphrase "مئة يوم ويومان"; the layer cannot reorder the noun | unverified (known limit) |
| 11 | Dual and tens oblique after a preposition or governing noun | اثنين, عشرين, مئتي ألف | al-dirassa | confirmed |
| 12 | Ordinals 1-10, feminine, 11-19 (الثالث عشر / الثالثة عشرة) | ordinal() | al-dirassa; Wiktionary appendix | confirmed |
| 13 | Ordinals 21-99 (الحادي والعشرون) | ordinal() | none fetched | unverified |
| 14 | Year after عام is annexed: genitive (عام ألفين وسبعة) | oblique year | annexation rule (al-dirassa, arabikey); no news style guide reached | confirmed (rule), news practice unverified |
| 15 | Year after سنة (feminine) might take a feminine unit | not applied (masculine) | none found | unverified |
| 16 | Hijri and Gregorian marks said as للهجرة / للميلاد | ERA_WORDS | none | unverified |
| 17 | Day of a written date said as an ordinal (الثالث عشر من نوفمبر) | ordinal_dates option, on | Al Jazeera pages print digits; the reading convention was not found in a guide | unverified (option) |
| 18 | Month names for MSA news: يناير, فبراير ... (Gulf, Egypt, Sudan, Yemen; used pan-Arab) rather than كانون الثاني (Levant, Iraq) or جانفي (Algeria, Tunisia) | month_names default gulf_egypt; Levant option; Maghreb names are read as written | edarabia.com/ar/?p=22046; mawdoo3 month list; Al Jazeera 2026/11/9 article prints "أكتوبر/تشرين الأول" pairs | confirmed |
| 19 | Clock: feminine ordinal hour (الواحدة ... الثانية عشرة), والنصف, والربع, إلا ربعا, إلا N دقائق; minutes cardinal | clock() | blogs.transparent.com/arabic (examples of الثالثة إلا ربعا, العاشرة إلا ثلثا) | confirmed |
| 20 | 20 and 40 minutes as والثلث / إلا ثلثا | said as "وعشرون دقيقة" / "وأربعون دقيقة" | the source attests إلا ثلثا only | unverified |
| 21 | Money: dollar plural دولارات, euro invariable, dinar دنانير, dirham دراهم, riyal subunit هللة, sterling adjective | CURRENCIES | grammar rules 1-9; the Saudi paper abstract (aclanthology 2026.cawl-1.11) confirms riyal/halala polarity | confirmed (polarity), subunit and plural words unverified |
| 22 | Percent word: بالمئة or في المئة | option percent_word, default بالمئة | France 24 (AFP) Arabic uses "في المئة"; Al Jazeera prints the % sign; no Academy ruling found | unverified (both attested) |
| 23 | Decimal point said فاصلة; one or two decimal digits as a number | spoken() | no style guide reached (industryarabic does not mention it) | unverified |
| 24 | Fractions نصف ثلث ربع ... ; 3/4 ثلاثة أرباع; 2/3 ثلثان | FRACTIONS | Wiktionary appendix | confirmed |
| 25 | Ranges, ages, scores (ثلاثة مقابل واحد), units, calibres, "رقم 5", phone numbers digit by digit | ranges(), units(), phones() | none | unverified |
| 26 | Eastern Arabic-Indic and Persian digits, ٫ ٬ ، as separators read like Western | normalized() | industryarabic.com/numbers-in-arabic (both systems in use; do not mix) | confirmed (digit systems), comma use unverified |
| 27 | Emergency numbers in an emergency context are said as digits (911 تسعة واحد واحد); outside it, as a number; two-digit codes (Morocco 19, 15) as numbers | digits_in_context; set extended with 104, 110, 113, 115, 160, 175, 193, 194, 198 | en.wikipedia List of emergency telephone numbers (Egypt 122/123/180, UAE 999/998/997, Iraq 104/122/115, Lebanon 112/140/160/175, Tunisia 197/190/198, Syria 110/112/113); u.ae emergencies page | fixed (set), how natives say each number unverified |
| 28 | A call named as such opens the emergency context ("مكالمة 911", "اتصال", "بلاغ", "نداء") and a list "997 أو 998" continues it | EMERGENCY_SERVICES, listed | case 15 sentence "استمعت الهيئة إلى مكالمة 911" was read as a number | fixed |
| 29 | "9-1-1" is a code, not a range | digit_chains stage; a "-" after a said span is no minus sign | case 15 text "9-1-1" came out as "من تسعة إلى واحدسالب واحد" | fixed |
| 30 | Gender lexicon (نقطة, تهمة, جلسة, مدينة, ولاية, ...) | extended; noun gender by singular | polarity rule (1); the noun genders are general lexical knowledge, not a fetched source | fixed (coverage), genders unverified |
| 31 | No digits left; text without digits unchanged; second pass changes nothing | tested | n/a | verified by test |

Tally: confirmed 13 (rows 1-7, 9, 11, 12, 18, 19, 24, plus 14 and 26 partly), fixed 5 (8, 27, 28, 29, 30), unverified 13 (10, 13, 15-17, 20, 22, 23, 25 and the partial parts above).
The rows marked "partly" appear in both columns on purpose.

What a native-speaker review is still needed for: rows 10, 13, 15, 17, 20, 22, 23, 25, the digit-by-digit reading of each national emergency number, and the plural/subunit words in row 21.
