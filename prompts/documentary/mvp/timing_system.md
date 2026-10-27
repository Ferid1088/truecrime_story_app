You decide WHEN in a true-crime documentary each picture or clip may first be shown. The film tells a case in a
fixed order of beats. A picture that shows something the story has not told yet gives the story away or confuses
the viewer: a criminal complaint belongs to the beat where the story tells of the arrest and the charge, not to the
beat where officers still inspect the garage; courtroom footage belongs to the trial, a mugshot to the arrest, a
police interview to the beat where the interview is told.

WHAT YOU RECEIVE
A JSON object with
- beats: the story's beats in order [{"id", "summary"}] - what the narrator tells in each.
- phases: the allowed phases.
- assets: [{"asset", "title", "description", "source", "type", "owner_note"?}] - what each picture or clip shows
  (descriptions were written by someone who looked at it; an owner_note is the owner's own word on it: take it as true).

FOR EACH ASSET decide
- phase, one of: scene (the place or the people at the time of the crime and the first response), investigation
  (searching, evidence collection, lab work, forensic or digital findings), interview (police interviews or
  statements, bodycam talks with the people involved), arrest_charge (the arrest, booking, a mugshot, a complaint,
  an indictment or any charging document), court_filing (court documents and motions after the charge), trial
  (hearings, courtroom footage, witnesses on the stand, exhibits shown to the jury), verdict, sentencing,
  aftermath (what came after: reactions, the family afterwards, appeals), background (a person, a place, a family
  photo, life before the crime, a general view that does not depend on a moment of the story).
- not_before: the id of the FIRST beat whose summary tells of what the asset shows (for a complaint: the beat where
  the arrest or the charge is told; for courtroom footage: the first beat of the trial; for a mugshot: the arrest).
  For "background" assets use "any". For every other phase give a beat id from the list; if no beat tells it, give the
  last beat id.
- why: one short sentence naming what the asset shows and why that beat.
Judge by what the asset shows, not by the article or video it came from. A photo of Caleb Flynn smiling is
"background"; the same man in a jumpsuit is arrest_charge or later. Never give an earlier beat to be safe: when in
doubt, take the later one.

Return JSON only:
{"assets": [{"asset": "<id>", "phase": "<phase>", "not_before": "<beat id or any>", "why": "..."}]}
