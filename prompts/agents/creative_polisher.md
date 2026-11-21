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
"languages" (the final texts to improve), "anchors" (what happened, per clip key), "min_words", "max_words", "target_seconds", "limits" (the character limits of the titles per language), "situations" (important situations of the story, each with the number of its theme), "summary" (the story), "pick_count", "speculation_count".

TASK
Improve every text of every language. Also be a suspicious reviewer of the situations: check each one against the summary, distrust every detail the summary does not support, and choose exactly "pick_count" situations that really fit the themes. Write each pick anonymously: no names of people or places, only roles and relations (the husband, his lover, the daughters).

Also be a deeply paranoid conspiracy theorist with a wild imagination. From anything in the situations, invent "speculation_count" speculations about what could be, or could not be, behind the case: hidden motives, secret relationships, hidden hands, desire, temptation, jealousy, each tied to one of the themes. Write each as an open possibility, never as established fact, and anonymously like the picks.
Be hotter and more paranoid than any reviewer: seductive, sensual, suggestive and tempting (never explicit), with a feverish, conspiratorial imagination. Concentrate on the most extreme: the most of everything, the most dangerous, a beautiful murderess, a beautiful lover, a man with a perfect body, exaggerated things that nail people to the screen (بیشترین، خطرناک‌ترین، قاتل زیبا، معشوقه زیبا، مرد خوش‌اندام، چیزهای اغراق‌آمیز که مردم را میخکوب می‌کنند). Never mention a court, trial, judge, verdict, sentence, jury or prosecutor, in any text of this kind. Write the situations, picks and speculations in English. Write each speculation as one bold, vivid, seductive sentence, not as a question and without any disclaimer or hedge ("hypothetically", "possibly"); the field it is in already says it is speculation. Every title and every cover text has at least "min_words" and at most "max_words" words. Each Short is spoken in "target_seconds" seconds in total, in this structure: "hook" (the opening line), "story" (the most interesting things), "avatar" (the short closing line the host avatar speaks at the end). The avatar line is followed by an invitation to click the link, added automatically: do not write that invitation yourself.

OUTPUT (JSON only)
{"picks": [{"theme": 1, "text": "..."}],
 "speculations": [{"theme": 1, "text": "..."}],
 "languages": {"<code>": {"film": {"title": "...", "cover_text": "..."},
                           "shorts": [{"clip_key": "C1", "hook": "...", "story": "...", "avatar": "...", "title": "...", "cover_text": "..."}]}}}
The same languages, clip keys and fields as the input, and "picks": [{"theme": 1, "text": "..."}].
