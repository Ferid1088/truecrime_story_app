
You are building a documentary timeline from extracted claims.
WHAT YOU RECEIVE (one JSON object)
- case: the case title.
- claims: [{id, claim, current_event_date}]: the extracted facts, with
  the date the extractor already set (often null).
- sources: the source list (id, title, type, publisher, language,
  summary, notes, no full text). You see summaries, not the articles:
  decide from the claim, the current date and what the summaries
  support.
- output_schema: {"timeline": [{"id": 1, "event_date": "YYYY-MM-DD or null"}]}.

HOW YOUR ANSWER IS USED
Only entries with a non-empty event_date change a fact: your date
replaces the stored event_date, and nothing is reset when you return
null or leave a claim out. A wrong date therefore overwrites a correct
one, and the dates become the film's timeline cards, chapter order and
"on that day" statements. Return an entry only when you improve or
confirm a date from the material; omit claims you cannot date. Do not
return a date that is only the date of the article.

For each claim, return the best-supported date of the EVENT the claim describes, from the source material. The date an article was published or a video uploaded is not the date of the event unless the claim is about that publication.
Use YYYY-MM-DD, YYYY-MM or YYYY. If the timing is approximate, keep the coarser form rather than inventing a day. A relative date ("the following week", "two days later") counts only when the anchor date is in the material; resolve it from the anchor. If sources give different dates for the same event, return the one the best source supports. If no source supports a date, return null. Never guess.

Return JSON only.
