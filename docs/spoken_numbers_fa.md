# Persian numbers for the voice: research notes and rules

Code: `app/documentary/spoken_numbers/fa.py` (rules) and `fa_words.py` (cardinal engine, ordinals, tables). Options: `config/parameters/audio.json`, `voice.number_words.fa`. Tests: `tests/test_spoken_numbers_fa.py`.

Evidence marks: **SOURCE** = a page I opened or a search result states it; **PARTIAL** = a source supports part of it; **UNVERIFIED** = my own proposal from general knowledge of Persian, no source found and no native speaker has reviewed it. Search was limited (the search tool returned mostly learner sites; the Academy's own pages, Persian Wikipedia and the ParsiNorm PDF text could not be read), so most rules are PARTIAL or UNVERIFIED. No page text is copied.

## Sources actually used

- University of Texas Persian resources, spoken modifications of numbers: https://sites.la.utexas.edu/persian_online_resources/language-specific-grammar/diglossia-readings/7/ (opened)
- Counting words (UT): https://sites.la.utexas.edu/persian_online_resources/?p=76 (search result only)
- Sublearn pages on spoken forms and counters: https://sublearn.com/learn/fa/grammar/colloquial-spoken-forms and https://sublearn.com/learn/fa/grammar/ordinal-numbers-classifiers (search results only; learner site, not authoritative)
- ParsiNorm, a Persian text-normalisation toolkit for speech (abstract only): https://arxiv.org/abs/2111.03470 (the PDF body was not readable)
- Emergency numbers of Iran (110 police, 115 ambulance, 125 fire, 112 Red Crescent, 118 information, 123 social emergency, 120 road police): https://www.zoomit.ir/howto/442075-emergency-numbers-iran/ and https://www.entekhab.ir/fa/news/872164 (search summaries)
- Toman and rial (1 toman = 10 rial; people use toman in daily trade; a redenomination is under discussion and reports disagree): https://www.bahesab.ir/finance/rial/ and https://aftabnews.ir/fa/news/649720 (search summaries)
- Solar Hijri calendar and the Gregorian offset: https://www.timeanddate.com/calendar/persian-calendar.html (search result)
- Academy of Persian Language and Literature, Dastur-e Khatt-e Farsi (1394 and 1401 editions): the ordinal section could not be read.

## Rules, with evidence

| Topic | Rule implemented | Evidence |
|---|---|---|
| Cardinals | Academy written forms joined with و: دو هزار و بیست و شش; 1000 is هزار (not یک هزار); 1,000,000 یک میلیون; 10^12 هزار میلیارد | PARTIAL (the old engine and its tests; standard grammar) |
| Nine | 9 is said نُه (marked) because plain نه also means "no"; option `nine_word` | PARTIAL (carried over from the old reading stage's `respelled`); inside ordinals it is نهم |
| Colloquial forms | Default `speech_style: written`. Option `spoken`: پونزده، شونزده، پونصد، چارصد، شیشصد، چل، شیش، چار | PARTIAL: UT lists پونزده، شونزده، چل، شیش، چار (and others not used); پونصد، چارصد، شیشصد are my extension. UNVERIFIED which of them a narrator should use; the speech check (Whisper) writes standard forms, so spoken stays opt-in. Not implemented: یه for یک, چارده, هیژده, هیفده, پنجا, شص |
| Counted nouns | The noun after a number stays singular and unchanged; نفر، تا، عدد stay as written, so nothing to do | SOURCE (several grammars via search) |
| Ordinals | -م / -مین on the last word: سوم (3), سی‌ام (30, half-space), بیست و یکم, پنجاهمین, اولین; ۱ام is یکم, ۱مین is اولین. Day of month: اول or یکم (option) | PARTIAL (a university paper confirms -م/-مین on the last element); the half-space in سی‌ام and the Academy's exact spelling UNVERIFIED |
| Century, decade | قرن ۲۱ becomes قرن بیست و یکم; دهه ۱۳۶۰ and دهه ۹۰ become دهه شصت / دهه نود | UNVERIFIED |
| Years | Said as cardinals: سال هزار و سیصد و نود و هشت; Gregorian دو هزار و بیست و شش; the era mark is said in full: ۲۰۱۳م = میلادی, هـ.ش = هجری شمسی, هـ.ق = هجری قمری | PARTIAL (calendar facts from timeanddate); reading as cardinals UNVERIFIED by source |
| Dates | `۱۶ فوریه ۲۰۲۶` becomes شانزدهم فوریه + year; numeric dates y/m/d and d/m/y; a year in 1200-1500 uses the Persian month names, else Gregorian (option for ژوئیه/اوت or جولای/آگوست). A month name that is also a noun (تیر, مهر, دی, مه, می) is a date only with a year or "ماه" after it, so "۲ تیر شلیک شد" stays "دو تیر" | PARTIAL (ParsiNorm abstract: it handles three calendars and tells them apart by year); ordinal day UNVERIFIED |
| Times | ۱۴:۳۰ = چهارده و نیم; ۱۴:۱۵ = چهارده و ربع; other minutes "و N دقیقه"; hour kept as written (24 h) | PARTIAL: a learner page gives "دو و نیم", "دو و ربع". Converting 14 to "دو" (12-hour) and "ربع به" / "ده دقیقه به سه" are NOT implemented: needs the day part, UNVERIFIED |
| Money | Symbols and codes: $ دلار (+ سنت), € یورو, £ پوند, ﷼/IRR ریال; ۵۰۰ تومان stays a number plus the word. Rial and toman are not converted into each other | PARTIAL (toman = 10 rial); the cents/pence wording UNVERIFIED |
| Percent | ۵۰٪ = پنجاه درصد; 12.5% = دوازده و نیم درصد | UNVERIFIED |
| Decimals | ۳٫۵ = سه و نیم; ۳٫۱۴ = سه ممیز چهارده (up to two decimals as one number); more digits or a leading zero digit by digit; scale words: دو و نیم میلیون | UNVERIFIED (ممیز is the Academy's term as far as I know; the way decimals are grouped is my proposal) |
| Fractions | ۳/۴ = سه چهارم; ۱/۲ = نیم; ۲ ۱/۲ = دو و نیم; a pair that is no fraction (۷/۳) = هفت بر سه | UNVERIFIED |
| Ranges and scores | ۱۹۹۰-۹۵ = ... تا ...; ۳-۵ = سه تا پنج; a pair that does not rise (۲-۱) = دو بر یک; "بین ۳ تا ۵" works because تا is already a word | UNVERIFIED |
| Sign | -۵ = منفی پنج; +۵ = مثبت پنج | UNVERIFIED |
| Units | mm cm m km kg g mg l ml ha m² km² km/h °C ° are said in Persian (۹mm = نُه میلی‌متر); words already written stay (۹ میلی‌متری = نُه میلی‌متری); a bare calibre .38 = کالیبر سی و هشت | UNVERIFIED (calibre wording) |
| Phones | A number with a leading 0 or +country code, at least 8 digits: digit by digit, groups joined by «،»; the country code as a number; option `pairs`. Mobile 09123456789 grouped 4-3-4 | UNVERIFIED |
| Identifiers | After شماره, تلفن, کد, پلاک (with ezafe) a number of 5 digits or more is said digit by digit; shorter is a number (شمارهٔ ۱۰ = ده). A run of 11+ digits and a leading-zero run are digit by digit. # = شماره | PARTIAL (ported from the old stage, which had the same cues and the 5-digit limit) |
| Emergency numbers | Iranian codes (110, 112, 115, 118, 120, 123, 125) are said as numbers (صد و ده); 911 and 999 digit by digit near a call word (نُه یک یک), else as numbers (the owner decision of the other languages). Options `digits_in_context`, `digits_always`, `number_always` | The codes: SOURCE. That Iranians say them as numbers: UNVERIFIED (from my general knowledge, no source found). How a Persian narrator says the American 911: UNVERIFIED (نُه یازده is also used) |
| Separators | ٫ decimal, ٬ and Latin comma (groups of 3) thousands, 1.234.567 thousands, ٪ percent; Persian, Arabic-Indic and Latin digits are one writing | PARTIAL (ParsiNorm abstract names numbers and currency) |
| Protected | A1, 3D, 5G, 4x4, COVID-19, F-16, URLs, e-mails stay as written (spoken_numbers/base.py) | repo rule |

## What stays UNVERIFIED (summary)

Everything marked UNVERIFIED above, above all: how an Iranian says 110/115/125 and 911; the colloquial forms for a narrator; "ربع به"; 12-hour times; the ordinal day, century and decade phrasing; fraction, decimal and percent wording; the phone grouping; the toman redenomination (a future change of the unit). Not covered at all: Persian plates with a letter, Roman numerals, English ordinals (21st), a comma used as a decimal sign (3,5), and the plural counted-noun colloquial "یه".

## Behaviour of the old `reading/numbers.py` and what became of it

Ported: cardinal reading of every whole number; service numbers digit by digit (the config list is empty for Persian, so 911 is a plain number there); identifier digit by digit after the cue words with a 5-digit minimum; years as cardinals; ordinals by the suffixes ام, م, مین with an optional half-space (سوم, سی‌ام, یکم); نُه respelling.
Changed on purpose: ۱مین is اولین (the old stage gave یکمین); 4-digit م is the Gregorian era, not an ordinal; 911 near a call word is digit by digit (owner decision for all languages), the old stage always said نهصد و یازده.
Not ported: the "left for a person" case (۲:۳۱, ۱۰٫۵ were not read by code); they are read now. The cue lookup by key_of (vowel marks removed) is replaced by plain cue words plus ezafe forms.
