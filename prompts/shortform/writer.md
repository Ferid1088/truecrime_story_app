#! The Shorts writer is called once per language and receives only that language's spoken text.
Write one short-form script in the requested language. Return JSON with hook, body, cta,
and captions, where captions is a list of {"start": number, "end": number, "text": string}.
Use only the supplied spoken text and already-told reveals. Do not translate another
language, add facts, or reveal anything reserved for later.
