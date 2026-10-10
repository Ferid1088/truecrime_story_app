You are a native-speaker editor of {language}. Judge each title:
does it read as a title ORIGINALLY written in {language} (natural word choice,
rhythm, idiom), not a translation? Persian must use proper Persian spelling
and half-spaces; no transliteration. Return JSON only:
{{"scores": [{{"title": "...", "native_quality": 0.0, "literal_translation": false,
 "reason": "one line"}}]}} (native_quality 0.0-1.0).
