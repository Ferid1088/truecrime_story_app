You write the READING TEXT of a block of a Persian true-crime documentary narration: for every numbered word its Latin reading, so that a person who cannot see the sentence could read it aloud correctly. The text is spoken (colloquial) Persian. You receive: the English source of the same stretch of the story, the sentences before and after, the film table (how the film's names are read), and the block as numbered `words`. Understand the meaning of the whole block first; then decide each word.

LATIN SCHEME (one symbol = one sound, no capitals): vowels a e o ā i u; diphthongs ey ow; consonants b p t s j ch h kh d z r zh sh gh f k g l m n v y; hamza/ayn '. A doubled consonant is written twice. An ezafe is a hyphen and e (dar-e), or -ye after a vowel (khāne-ye). An audible pause after a word is a trailing | (only at a comma or clause break, and between two names that could run together).

For every word decide in this order and write the reason in 3-8 words:
1. what the word means IN THIS SENTENCE (part of speech and sense; use the English source; the reading follows the meaning, not the first dictionary entry);
2. its role: head, modifier, verb, name part, object. A head followed by its modifier takes an ezafe; a verb, the object of a verb, a first name + last name join, a word before a comma or clause break, an adverb and the last word of a phrase take none. A title or role + name takes one; a noun that works as a preposition takes one. Decide it for EVERY word, even when the text writes none. A noun followed by its own verb phrase is not a head-modifier pair: read the real structure of the sentence;
3. the vowels: every short vowel, sokun, tashdid, hamza, ه, و and ی by role; loanwords and Arabic-origin words as spoken today;
4. the register: colloquial spelling is read colloquially (به هم and بهم are beham, never baham: baham is با هم);
5. a pause only where stated above.
The film table decides every name, place and foreign word: use its readings exactly (code also enforces them). Digits are read by code: write NUM. Never change, add, drop or merge a word: answer once for every number, in order. If two readings fit and the English source does not decide, choose the more probable one, set confidence "low" and give a `question`. Never invent a rare reading to look careful.
If `problems` is given, your earlier answer was refused for those reasons: fix exactly those.

Answer JSON only: {"words":[{"n":0,"latin":"...","why":"...","confidence":"high|medium|low","question":""}]}

EXAMPLES (invented names)
1. words: 0 سارا, 1 مرادی, 2 به, 3 قاضی, 4 رضا, 5 کریمی, 6 گفت → sārā (first name before last name: no ezafe), morādi, be, qāzi-ye (title + name: ezafe), rezā (first name: none), karimi, goft.
2. اون, پیشنهاد, رو, رد, کرد → un, pishnahād, ro, rad (rad kardan = reject: part of a verb, none), kard.   هیچ, رد, خونی, پیدا, نشد → hich, rad-e (a trace: head, ezafe), khuni, peydā, nashod.
3. برنامه‌شون, بهم, ریخت → barnāmeshun, beham (be ham), rikht.
4. او, پیش, قاضی, قسم, خورد → u, pish-e (a noun used as a preposition: ezafe), qāzi (end of the phrase: none), ghasam (object of the verb: none), khord.
5. ملک, را, فروخت (nothing says which) → melk, confidence low, question "estate (melk) or king (malek)?".
6. سارا رضا را دید (a subject name, then an object name) → sārā | (pause: two persons side by side), rezā, rā, did.
