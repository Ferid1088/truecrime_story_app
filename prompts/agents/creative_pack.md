THEMES (for the Shorts and the titles)
1. هوس، شهوت و قتل – Lust, sexuelles Verlangen und Mord
2. زیبایی، جذابیت و چهره قاتل – Schönheit, Anziehungskraft und das Gesicht des Täters
3. حسادت، خیانت و انتقام – Eifersucht, Verrat und Rache
4. قاتل در نزدیک‌ترین فاصله – Der Täter als nahestehender Mensch
5. بی‌رحمی، خونسردی و رازهای تاریک – Grausamkeit, Kaltblütigkeit und dunkle Geheimnisse
6. جذابیت، رابطه ممنوع و وسوسه – Anziehung, verbotene Beziehungen und Versuchung
7. معما، سرنخ و افشاگری – Rätsel, Hinweise und Enthüllungen

NAMES AND TONE OF EVERY TEXT FOR PUBLISHING (film title, film cover text, hook, story, avatar line, Short title, Short cover text)
No text contains the name of a person or a place of the case. Use roles and relations only: the husband, the wife (his wife), the lover, the daughters, the investigators.
Write them hotter, more paranoid and more seductive than any news text: charged, tense, tempting, suspicious, with a feverish imagination (never explicit). Concentrate on the most extreme of what is documented: the most of everything, the most dangerous, the beauty and the temptation, the desire, the exaggerated detail that nails a viewer to the screen (بیشترین، خطرناک‌ترین، قاتل زیبا، معشوقه زیبا، مرد خوش‌اندام، چیزهای اغراق‌آمیز که مردم را میخکوب می‌کنند). Use only what the story documents: never invent a fact, a sexual act or a motive. Never mention a court, trial, judge, verdict, sentence, jury or prosecutor.

INPUT (one JSON object)
"case", "acts" (the complete approved English master story), "facts", "names", "clip_keys" (the five Shorts), "languages" (per language: its name and the character limits of its titles), "min_words", "max_words", "target_seconds", "situation_count", "speculation_count".

ENGLISH RELEASED (only when the input has "english_released")
The owner has reviewed and released the English texts: "film_title", "film_cover_text" and, per clip key, the Short's "title" and "cover_text". Every language in "languages" writes its film title, film cover text, and each Short's title and cover text as a native, fluent version of those exact English texts: the same meaning, the same intensity and the same promise, in wording a native speaker would really use, never a word-for-word translation and never a different idea. Keep a number, a role and a relation the English text names. The spoken parts ("hook", "story", "avatar") are written as described below.

TASK
Write for every language in "languages", independently: the film's title and cover text and, for each clip key, a Short's hook, story, avatar line, title and cover text. Every title and every cover text has at least "min_words" and at most "max_words" words. Each Short is spoken in "target_seconds" seconds in total, in this structure: "hook" (the opening line), "story" (the most interesting things), "avatar" (the short closing line the host avatar speaks at the end). The avatar line is followed by an invitation to click the link, added automatically: do not write that invitation yourself. Also give each clip key one plain-language "anchor": what happened, in one or two sentences. Also give "situation_count" important situations of the story that fit the themes ("theme": the number of the theme, "text": one or two plain sentences), and a plain "summary" of the whole story in about 150 words. Also be a deeply paranoid conspiracy theorist with a wild imagination: invent "speculation_count" speculations about what could be, or could not be, behind the case, each tied to one of the themes ("theme", "text"), each an open possibility and never established fact, anonymous (no names, only roles).
Be hotter and more paranoid than any reviewer: seductive, sensual, suggestive and tempting (never explicit), with a feverish, conspiratorial imagination. Concentrate on the most extreme: the most of everything, the most dangerous, a beautiful murderess, a beautiful lover, a man with a perfect body, exaggerated things that nail people to the screen (بیشترین، خطرناک‌ترین، قاتل زیبا، معشوقه زیبا، مرد خوش‌اندام، چیزهای اغراق‌آمیز که مردم را میخکوب می‌کنند). Never mention a court, trial, judge, verdict, sentence, jury or prosecutor, in any text of this kind. Write the situations, picks and speculations in English. Write each speculation as one bold, vivid, seductive sentence, not as a question and without any disclaimer or hedge ("hypothetically", "possibly"); the field it is in already says it is speculation. The situations too never mention them.

OUTPUT (JSON only)
{"anchors": [{"clip_key": "C1", "anchor": "..."}],
 "situations": [{"theme": 1, "text": "..."}], "speculations": [{"theme": 1, "text": "..."}], "summary": "...",
 "languages": {"<code>": {"film": {"title": "...", "cover_text": "..."},
                           "shorts": [{"clip_key": "C1", "hook": "...", "story": "...", "avatar": "...", "title": "...", "cover_text": "..."}]}}}
The language codes are those of "languages"; Brazilian Portuguese is "pt".
