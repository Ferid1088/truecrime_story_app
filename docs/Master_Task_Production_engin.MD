MASTER TASK — BUILD THE TRUECRIME AUDIO-FIRST DOCUMENTARY PRODUCTION ENGINE

Project:

/Users/feri/truecrime_story_app


============================================================
MISSION
============================================================

Extend the existing TrueCrime application from:

research
→ evidence
→ story

into:

research
→ evidence
→ professional story strategy
→ native multilingual story versions
→ voice performance
→ visual intelligence
→ documentary direction
→ audio/video timeline
→ automated render
→ editorial critique
→ final documentary

The product goal is NOT:

"generate an AI video."

The product goal is:

Produce four highly professional documentary versions:

ENGLISH
GERMAN
PERSIAN
ARABIC

that:

- tell the same verified case
- preserve factual truth
- feel natively written in each language
- sound professionally narrated
- use real photos, documents, maps, footage and contextual visuals
- intelligently use silence
- intelligently use original audio
- are paced for listening first
- do not feel automatically assembled
- do not feel translated
- do not look like an AI slideshow


============================================================
CORE PRODUCT PHILOSOPHY
============================================================

Store this as an explicit product principle:

"If the listener closes their eyes, the documentary must still work.
If they open them, the visuals should deepen understanding,
not compete for attention."


============================================================
V1 GENERATIVE VIDEO POLICY
============================================================

For V1:

AI_VIDEO_ENABLED = false

Do NOT integrate:

Runway
Kling
Sora
or any generative-video workflow.

Use:

real photos
real footage
archive material
documents
maps
timelines
contextual B-roll
Ken Burns motion
crop/pan
subtle 2.5D parallax
typography
black frames
sound design
music
original audio

Generative video may become an optional future capability.


============================================================
TOP-LEVEL ARCHITECTURE
============================================================

CANONICAL CASE TRUTH
        ↓
CANONICAL RESEARCH DOSSIER
        ↓
EDITORIAL MASTER BLUEPRINT
        ↓

    ┌────────────┬────────────┬────────────┬────────────┐
    │ EN STORY   │ DE STORY   │ FA STORY   │ AR STORY   │
    │ native     │ native     │ native     │ native     │
    └────────────┴────────────┴────────────┴────────────┘

        ↓
LANGUAGE-SPECIFIC PERFORMANCE SCRIPT
        ↓
ELEVENLABS VOICE BLOCKS
        ↓
ACTUAL AUDIO TIMINGS / ALIGNMENT
        ↓
DOCUMENTARY DIRECTOR
        ↓
PRODUCTION SCRIPT PER LANGUAGE
        ↓
VISUAL / AUDIO / MUSIC / ORIGINAL MEDIA
        ↓
CRITIC LOOP
        ↓
FFMPEG / OPENCV RENDER
        ↓

FINAL_EN
FINAL_DE
FINAL_FA
FINAL_AR


============================================================
CRITICAL SEPARATION
============================================================

There are two distinct systems.

SYSTEM A — TRUTH SYSTEM

Responsible for:

facts
sources
evidence
timeline
contradictions
quotes
original media provenance

SYSTEM B — DOCUMENTARY SYSTEM

Responsible for:

story
voice
pacing
silence
visuals
music
sound
original clips
editing

DOCUMENTARY SYSTEM may NEVER override TRUTH SYSTEM.


============================================================
PART 1 — EDITORIAL MASTER BLUEPRINT
============================================================

Create a new central entity:

EditorialBlueprint

This is language-independent.

It represents the documentary strategy,
not the final prose.

Suggested structure:

EditorialBlueprint

id
case_id
research_snapshot_id
master_story_version_id

central_question
editorial_thesis
human_thread
mystery_arc
investigation_arc
emotional_arc

beats_json
reveal_graph_json
visual_intents_json
audio_intents_json
music_intents_json

created_at
version


============================================================
BEAT MODEL
============================================================

Every documentary is structured as beats.

Example:

{
  "beat_id": "B031",

  "purpose": "first_major_contradiction",

  "viewer_knows": [
    "F012",
    "F018"
  ],

  "viewer_does_not_know": [
    "F033"
  ],

  "open_questions": [
    "Why did the witness change his account?"
  ],

  "answers_allowed": [],

  "answers_forbidden": [
    "C009"
  ],

  "human_focus": "victim_family",

  "required_evidence_ids": [
    "F018",
    "C004",
    "Q007"
  ],

  "narrative_attention": 0.92,

  "visual_attention": 0.18,

  "emotional_load": 0.71,

  "mystery_intensity": 0.84,

  "visual_intent": "hold_or_document",

  "audio_intent": "controlled_tension"
}


============================================================
PART 2 — PROFESSIONAL STORY STRATEGY
============================================================

Create:

NarrativeDirectorAgent

Purpose:

Convert CanonicalResearchDossier into EditorialBlueprint.

It must reason about:

what viewer knows

what viewer does not know

what question is currently active

what information should be delayed

what information should be revealed

where emotional connection is needed

where factual density is high

where listener needs recovery

where story accelerates

where story slows

where a chapter ends

where silence matters

where original media may be stronger than narration


============================================================
NARRATIVE RULES
============================================================

The story must NOT become:

Wikipedia chronology

fact dump

generic AI suspense

constant cliffhangers

TikTok pacing

formulaic "what happened next" writing


The story SHOULD:

create questions naturally

resolve questions progressively

build human connection

preserve uncertainty

use contradictions strategically

use documentary evidence as narrative material

maintain an investigative arc

maintain a human arc

maintain mystery without artificially hiding obvious facts


============================================================
PART 3 — MYSTERY / REVEAL GRAPH
============================================================

Create:

MysteryDirectorAgent

and:

RevealGraph

Example:

MYSTERY M001

question:
Where did Sarah go?

introduced_at:
B005

clues:
B008
B012
B017

false_or_weak_lead:
B020

major_reveal:
B028

resolved_at:
B031


The director must prevent:

too many unanswered loops

reveals too early

reveals too late

repeating the same mystery

artificial mystery where evidence is already clear


============================================================
PART 4 — REVEAL FIREWALL
============================================================

This is mandatory.

Every Beat has:

allowed_reveals
forbidden_reveals

Every visual/media asset may have:

reveals

Example:

asset:

{
  "asset_id": "VIS_412",
  "reveals": [
    "vehicle_identity",
    "vehicle_discovery_location"
  ]
}

beat:

{
  "forbidden_reveals": [
    "vehicle_identity"
  ]
}

Result:

VIS_412 MUST NOT be shown.


The same applies to:

documents
maps
videos
captions
headlines
audio clips


============================================================
PART 5 — FOUR NATIVE LANGUAGE STORIES
============================================================

From EditorialBlueprint generate:

Story_EN
Story_DE
Story_FA
Story_AR

These are NOT literal translations.

Use:

NATIVE TRANSCREATION


All four versions must preserve:

facts
dates
names
uncertainty
reveal order
mystery structure
evidence meaning
quote meaning
legal status
chronology

But may adapt:

sentence rhythm
paragraph structure
idioms
oral phrasing
sentence length
transitions
rhetorical form
pause placement
storytelling cadence


============================================================
LANGUAGE-SPECIFIC QUALITY
============================================================

Each language gets a dedicated native critic.

EN:
native English documentary narration

DE:
natural German documentary narration

FA:
native Persian oral storytelling

AR:
native Arabic documentary narration


The critic must ask:

Does this sound translated?

Does this sound AI-written?

Is syntax unnatural?

Is suspense native to this language?

Would a professional narrator speak this naturally?

Are phrases too literary?

Are phrases too robotic?

Are rhetorical patterns repetitive?


============================================================
PART 6 — PERFORMANCE SCRIPT
============================================================

Do NOT send raw story text directly to ElevenLabs.

Create:

PerformanceScript

per language.

Example:

{
  "language": "fa",

  "blocks": [
    {
      "block_id": "FA_VOICE_031",

      "beat_id": "B031",

      "text": "...",

      "style": "controlled_tension",

      "pace": 0.94,

      "intensity": 0.42,

      "energy": "low-medium",

      "pause_before_ms": 650,

      "pause_after_ms": 1400,

      "pronunciation_overrides": {
        "Nannup": "..."
      }
    }
  ]
}


============================================================
PART 7 — VOICE DIRECTOR
============================================================

Create:

VoiceDirectorAgent

per language.

Do NOT control every sentence mechanically.

Use performance blocks.

Styles:

neutral_documentary

controlled_tension

factual

emotional_restraint

investigative

reveal

urgent_but_controlled

reflective


Avoid theatrical exaggeration.


============================================================
VOICE PERFORMANCE PRINCIPLE
============================================================

Small variation is better than dramatic acting.

Examples:

normal:
pace 1.00

tension:
~0.94

reveal:
~0.90

dense factual section:
~1.03

These are conceptual.

Actual values belong in config.


============================================================
PART 8 — PAUSE SYSTEM
============================================================

Create:

PauseDirector

Separate three classes.


1. MICRO SPEECH PAUSE

Purpose:
natural language

Typical:
~0.1–0.7 sec

Handled mainly by:
voice performance / ElevenLabs


2. DRAMATIC PAUSE

Purpose:
tension / reflection / reveal

Typical:
~0.7–2.5 sec

Handled by:
application timeline


3. DOCUMENTARY SILENCE

Purpose:
let moment land

Possible:

no narrator
no music
room tone only

or:

black screen

Typical:
~1–4 sec

Handled by:
application


============================================================
PART 9 — SILENCE DIRECTOR
============================================================

Create:

SilenceDirectorAgent

Silence is a first-class editorial tool.

It decides:

where silence increases impact

where silence becomes repetitive

where no sound is stronger than music

where black screen is appropriate

where room tone is enough


Example:

{
  "silence_id": "S014",
  "beat_id": "B031",
  "duration": 1.8,
  "type": "reveal_after",
  "voice": false,
  "music": false,
  "room_tone": true
}


============================================================
PART 10 — ELEVENLABS INTEGRATION
============================================================

Create a provider abstraction:

VoiceProvider

Implementation:

ElevenLabsVoiceProvider


Use:

TrueCrime_ELEVENLABS_API_KEY

in .env


Do not hardcode voice IDs.


============================================================
VOICE BLOCK GENERATION
============================================================

Generate audio in blocks.

Do NOT generate an entire 45–60 minute documentary in one call.

Typical conceptual block duration:

30–90 seconds

but config-driven.


Benefits:

re-render one block

different performance

better timing

easier original clip insertion

lower regeneration cost


============================================================
PART 11 — ACTUAL AUDIO ALIGNMENT
============================================================

After ElevenLabs generates audio:

DO NOT assume planned duration.

Use actual audio duration.


Preferred approach:

known narration text
+
generated audio
↓
forced alignment
↓
word-level timestamps


Whisper should be:

fallback
or
verification tool

not primary alignment when original text is already known.


============================================================
WORD TIMESTAMPS
============================================================

Store:

word
start
end
confidence

Example:

{
  "word": "disappeared",
  "start": 183.42,
  "end": 184.07
}


============================================================
PART 12 — AUDIO TIMELINE
============================================================

Create:

AudioTimeline

Contains:

voice blocks
dramatic pauses
documentary silence
original media audio
music
ambience
SFX


This timeline becomes authoritative for visual timing.


============================================================
PART 13 — AUDIO FOCUS MANAGER
============================================================

Create:

AudioFocusManager

Possible modes:

NARRATION
ORIGINAL_AUDIO
MUSIC_FOCUS
SILENCE
TRANSITION


Example narration:

narrator = 1.0
music = 0.14
ambience = 0.10


Original interview:

original_audio = 1.0
narrator = 0.0
music = 0.03
ambience = 0.0


Reveal:

narrator = 1.0
music fades to 0


After reveal:

narrator = 0
music = 0
room tone = subtle


============================================================
PART 14 — ORIGINAL AUDIO DIRECTOR
============================================================

Create:

OriginalAudioDirectorAgent


It evaluates real audio/video clips.

Examples:

police interview
family interview
press conference
news report
court statement
archival interview
911 call
documentary interview


Use original audio only when it contributes:

AUTHENTICITY
EVIDENCE
HUMAN CONNECTION
CONTRADICTION
REVEAL
ACTUAL EVENT ATMOSPHERE


============================================================
ORIGINAL AUDIO RULE
============================================================

If original clip audio is used:

Narrator should normally STOP.

Do NOT speak narrator over important original speech.


Pattern:

Narrator
↓
pause
↓
original clip
↓
pause
↓
Narrator resumes


============================================================
PART 15 — FOREIGN-LANGUAGE ORIGINAL AUDIO
============================================================

If original clip language differs from documentary language:

Possible strategy:

original clip audible briefly

then:

duck original audio

then:

native voice-over translation

or:

native subtitles


Store:

original_audio_language

target_language

translation_strategy

ducking_level

subtitle_strategy


============================================================
PART 16 — ORIGINAL CLIP SEGMENT SELECTION
============================================================

Do NOT automatically use full clips.

Find the strongest relevant segment.

Store:

source_start

source_end

duration

reason

quote/transcript

dramatic_function


Example:

02:47.3–02:58.6

reason:
first-person statement of last confirmed contact


============================================================
PART 17 — VISUAL INTELLIGENCE ENGINE
============================================================

Create a dedicated module:

app/documentary/visuals/

Suggested:

visual_research.py
visual_verification.py
asset_index.py
asset_matching.py
visual_director.py
rights.py
maps.py
documents.py
motion.py


============================================================
PART 18 — VISUAL ASSET MODEL
============================================================

Create:

VisualAsset

Suggested fields:

id
case_id

asset_type

photo
video
document
map
graphic
broll

subject_type

person
place
organization
object
event
document
geography

entities_json

date_start
date_end

location

description

asset_role

evidence
context
illustration

story_functions_json

source_url

source_name

rights_status

verification_confidence

quality_score

historical_accuracy_score

reveals_json

local_path

thumbnail_path

duration_seconds

has_original_audio

original_audio_language

created_at


============================================================
PART 19 — ASSET NAMING
============================================================

Do not rely on filenames for intelligence.

Use stable IDs.

Example:

VIS_000184.jpg

Human-friendly filename may be:

alice_smith_college_2003_VIS_000184.jpg

But semantic meaning lives in DB.


============================================================
PART 20 — ASSET ROLE
============================================================

Every visual must be classified as:

EVIDENCE

CONTEXT

ILLUSTRATION


EVIDENCE:

real case-related material

CONTEXT:

real location/context but not evidence

ILLUSTRATION:

atmospheric generic material


The editor must not accidentally imply that CONTEXT or ILLUSTRATION is real
case evidence.


============================================================
PART 21 — VISUAL RESEARCH AGENT
============================================================

Create:

VisualResearchAgent


It searches for:

real person photos

locations

buildings

vehicles

objects

documents

news footage

archive footage

interviews

city footage

regional footage

aerial footage

maps

organizations

schools

companies

forests

roads

police stations

court buildings


============================================================
PART 22 — VISUAL VERIFICATION AGENT
============================================================

Create:

VisualVerificationAgent


Check:

Is this actually the correct person?

Correct location?

Correct building?

Correct date/period?

Correct vehicle?

Actual case material?

Archive footage or illustrative?

Does metadata match claim?


Do not trust filenames or webpage labels blindly.


============================================================
PART 23 — RIGHTS CLASSIFICATION
============================================================

Create:

RightsClassifier


Possible:

owned

licensed

public_domain

creative_commons

editorial_review_required

permission_required

unknown

do_not_use


Important:

FOUND
does NOT mean
USABLE.


============================================================
PART 24 — VISUAL REQUIREMENT PLANNER
============================================================

After story beats exist, create:

VisualRequirement

Example:

{
  "beat_id": "B031",

  "requirements": [
    {
      "entity_type": "person",
      "entity": "Sarah",
      "period": "college",
      "purpose": "human_connection",
      "priority": "high"
    },

    {
      "entity_type": "location",
      "entity": "Perth",
      "purpose": "orientation",
      "priority": "medium"
    }
  ]
}


Do NOT search visually sentence-by-sentence.

Search by semantic documentary need.


============================================================
PART 25 — VISUAL FALLBACK HIERARCHY
============================================================

When no exact visual exists:

1. exact authentic asset

2. authentic related asset

3. relevant real document

4. relevant map

5. timeline / date visualization

6. real contextual location

7. contextual B-roll

8. reuse current image

9. crop / push / pan current image

10. typography

11. black / near-black

12. NO_VISUAL_CHANGE


NO_VISUAL_CHANGE is a legitimate professional choice.


============================================================
PART 26 — HOLDING IMAGES
============================================================

Allow one image to remain for:

3 sec
8 sec
12 sec
15 sec

or longer when appropriate.

Do NOT enforce constant visual change.

The story determines rhythm.


============================================================
PART 27 — MOTION DIRECTOR
============================================================

Create:

MotionDirector


Actions:

NONE

SLOW_PUSH

SLOW_PULL

PAN_LEFT

PAN_RIGHT

CROP_FOCUS

SUBTLE_2_5D

DOCUMENT_SCROLL

DOCUMENT_HIGHLIGHT

MAP_ZOOM

MAP_PAN

CROSSFADE

FADE_BLACK


Motion must be subtle.

Avoid visible AI/slideshow feel.


============================================================
PART 28 — 2.5D PARALLAX
============================================================

Allow subtle 2.5D on still photos.

Goal:

extend visual interest

NOT:

create fake cinematic movement


Config:

small camera displacement

subtle depth separation

long duration

avoid warping faces/objects


============================================================
PART 29 — MAP DIRECTOR
============================================================

Create:

GeoVisualDirector


Do not rely on random Google Maps screen recordings.


Input:

location

coordinates

start_zoom

end_zoom

movement

duration

labels


Possible sequences:

world
→ country
→ state
→ city
→ road

or reverse.


Use legally suitable map tiles/data.

Map provider must be configurable.


============================================================
PART 30 — DOCUMENT DIRECTOR
============================================================

Create:

DocumentDirector


For:

court records

police reports

letters

newspaper articles

coroner reports

official statements


Actions:

show page

zoom to line

highlight passage

dim irrelevant content

pan document

show date/header


Example:

{
  "action": "DOCUMENT_FOCUS",
  "document_id": "DOC_042",
  "page": 17,
  "highlight": "The vehicle was located...",
  "duration": 9.2
}


============================================================
PART 31 — COGNITIVE LOAD DIRECTOR
============================================================

Create:

CognitiveLoadDirector


Attention modes:

LISTEN

LOOK

READ

ORIENT

FEEL

VERIFY


Every beat receives scores.

Example:

{
  "listen": 0.92,
  "look": 0.20,
  "read": 0.05,
  "orient": 0.10
}


Rule:

Do NOT overload user with high-complexity voice + document + map + text
simultaneously.


============================================================
PART 32 — ATTENTION DIRECTOR
============================================================

Create:

AttentionDirectorAgent


It decides:

Do we need a new visual?

Should image remain?

Should viewer read?

Should viewer listen?

Should visual become minimal?

Should music disappear?

Should silence happen?


Possible visual commands:

KEEP_CURRENT_IMAGE

NEW_IMAGE

CROP_EXISTING

ZOOM_EXISTING

SHOW_DOCUMENT

SHOW_MAP

SHOW_DATE

SHOW_QUOTE

BLACK_SCREEN

ATMOSPHERIC_BROLL

NO_VISUAL_CHANGE


============================================================
PART 33 — MUSIC DIRECTOR
============================================================

Create:

MusicDirector


Do NOT manage music sentence-by-sentence.

Use music arcs.

Example:

02:10 enter subtly

03:20 increase tension

04:05 thin texture

04:31 fade to silence

04:34 reveal

04:41 new texture


MusicIntent can be language-independent.

Exact MusicTimeline is language-specific.


============================================================
PART 34 — SOUND DIRECTOR
============================================================

Create:

SoundDirector


Allowed examples:

room tone

wind

distant traffic

paper movement

subtle electronic cue

low-frequency transition

rain ambience if real/contextually appropriate


Hard rule:

SFX must NOT imply undocumented events.


Do NOT add:

gunshots

screams

struggles

doors opening

phone dropping

unless the event is actually supported and editorial use is appropriate.


============================================================
PART 35 — PRODUCTION SCRIPT
============================================================

Create:

ProductionScript

one per language.

Example:

{
  "language": "de",

  "beat_id": "B031",

  "start": 874.22,

  "end": 903.18,

  "voice": {
    "block_id": "DE_VOICE_031",
    "start": 874.22,
    "end": 889.70
  },

  "silence": {
    "start": 889.70,
    "duration": 1.35
  },

  "visual": {
    "asset_id": "VIS_193",
    "action": "slow_push",
    "start": 874.22,
    "end": 891.05
  },

  "original_media": {
    "asset_id": "VID_028",
    "source_start": 142.4,
    "source_end": 151.8,
    "audio": true,
    "translation_strategy": "voiceover_de"
  },

  "music": {
    "cue": "fade_out_before_clip"
  }
}


============================================================
PART 36 — LANGUAGE-SPECIFIC TIMELINES
============================================================

Do NOT reuse one exact timeline for all languages.

Create:

ProductionScript_EN

ProductionScript_DE

ProductionScript_FA

ProductionScript_AR


because:

sentence duration differs

pause placement differs

voice cadence differs

translation of original audio differs

subtitle duration differs


Shared:

EditorialBlueprint


Different:

exact timing


============================================================
PART 37 — DOCUMENTARY TIMELINE COMPILER
============================================================

Create:

TimelineComposer


Input:

PerformanceScript

actual ElevenLabs audio

alignment

visual decisions

music cues

sound cues

original media

silence


Output:

render-ready timeline


============================================================
PART 38 — RENDER ENGINE
============================================================

Implement first with:

FFmpeg

OpenCV

Pillow

and existing Python tools if useful.


Support:

still images

video clips

crop

pan

zoom

crossfade

black

text overlays

map sequences

document sequences

audio mixing

ducking

music fades

original audio

subtitles

final mux


============================================================
PART 39 — RENDER DATA-FIRST
============================================================

Renderer should NOT contain editorial logic.

It receives instructions.

Example:

Timeline says:

SLOW_PUSH VIS_001
duration 8.2 sec


Renderer simply executes it.


============================================================
PART 40 — DOCUMENTARY CRITICS
============================================================

Create separate critics.


NarrativeCritic

Checks:

engagement

mystery

story progression

human connection

repetition

reveal timing


NativeLanguageCritic

Checks:

native quality

translation artifacts

spoken naturalness


VoicePerformanceCritic

Checks:

pace

overacting

monotony

pause naturalness


AttentionCritic

Checks:

visual overload

reading/listening conflict

too many cuts

too little change


VisualAccuracyCritic

Checks:

wrong identity

wrong place

misleading contextual footage

future reveal


OriginalAudioCritic

Checks:

clip relevance

duration

language handling

audio transition


ProductionCritic

Checks:

overall rhythm

music overuse

awkward silence

transitions


AutomationFeelCritic

Critical question:

"At which timestamps does this feel algorithmically assembled,
formulaic, repetitive, synthetic, or machine-generated?"


Return exact timestamps and fixes.


============================================================
PART 41 — CRITIC LOOP
============================================================

ProductionScript
↓
Critics
↓
Problems
↓
Targeted fixes
↓
Recheck


Do NOT regenerate entire documentary plan unnecessarily.

Fix specific:

beat

voice block

pause

visual

clip

music cue

timeline section


============================================================
PART 42 — FOUR-LANGUAGE FINAL QUALITY
============================================================

Each language must independently pass:

native language quality

story engagement

voice performance

pause quality

visual timing

original audio handling

attention balance

factual visual accuracy

automation-feel check


Do NOT inherit English scores.


============================================================
PART 43 — UI
============================================================

Add a top-level production section:

DOCUMENTARY


Suggested tabs:

Blueprint

Language Versions

Voice

Visual Library

Original Media

Timeline

Music & Sound

Production Script

Critique

Render


============================================================
PART 44 — BLUEPRINT UI
============================================================

Show:

beats

open questions

reveals

human thread

attention state

visual intent

audio intent


Allow internal reviewer inspection.


============================================================
PART 45 — VOICE UI
============================================================

For each language:

voice selection

voice blocks

performance style

duration

render state

playback

re-render block

pause before/after

alignment


Do not expose secret API keys.


============================================================
PART 46 — VISUAL LIBRARY UI
============================================================

Show assets grouped by:

People

Places

Organizations

Objects

Documents

Maps

Videos

B-roll


Filters:

evidence/context/illustration

verified/unverified

rights

quality

language

date


============================================================
PART 47 — ORIGINAL MEDIA UI
============================================================

Show:

clip

source

language

transcript

suggested segment

story use

rights

has audio

selected segment


Allow playback with in/out markers.


============================================================
PART 48 — TIMELINE UI
============================================================

Build a documentary timeline view.

Tracks:

Voice

Original Audio

Visual

Overlay

Music

SFX

Silence


At minimum:

read-only visualization first.

Editing may be incremental.


============================================================
PART 49 — RENDER UI
============================================================

Show:

language

estimated duration

render status

progress stage

output path

resolution

fps

audio status


Possible stages:

preparing

rendering_visuals

mixing_audio

encoding

finalizing

completed

failed


============================================================
PART 50 — CONFIG
============================================================

Centralize in config.

Suggested sections:

documentary

story_direction

performance

voice

elevenlabs

pause

silence

visuals

visual_search

visual_verification

rights

motion

maps

documents

music

sound

attention

alignment

render

critics


No magic thresholds.


============================================================
PART 51 — PROVIDER ROUTING
============================================================

All agents use central routing.

Example conceptual routing:

narrative_director
→ writer

mystery_director
→ writer

language_story_writer
→ writer

performance_director
→ writer

visual_verification
→ cheap

attention_director
→ cheap

original_audio_director
→ cheap

music_director
→ cheap

sound_director
→ cheap

native_language_critic
→ premium

narrative_critic
→ premium

production_critic
→ premium

automation_feel_critic
→ premium


Do not hardcode model names in agents.


============================================================
PART 52 — STORAGE
============================================================

Create case-local storage structure.

Example:

case_0002/

research/

story/

audio/
  en/
  de/
  fa/
  ar/

visuals/
  photos/
  video/
  documents/
  maps/
  broll/

production/
  en/
  de/
  fa/
  ar/

renders/
  en/
  de/
  fa/
  ar/


DB remains source of truth for metadata.


============================================================
PART 53 — CACHE
============================================================

Cache:

voice block output

alignment

visual analysis

visual verification

motion render

map render

document render

music decisions

critic results


Use:

input hash
config version
model version


============================================================
PART 54 — TESTS
============================================================

Add comprehensive tests.


BLUEPRINT

1. beats created

2. reveal graph valid

3. forbidden reveal enforced

4. evidence preserved


LANGUAGE

5. EN version

6. DE version

7. FA version

8. AR version

9. all derive from same blueprint

10. facts unchanged


VOICE

11. performance blocks

12. ElevenLabs mock

13. block regeneration

14. pause storage

15. actual duration

16. alignment


SILENCE

17. dramatic pause

18. documentary silence

19. no repetitive silence pattern


VISUAL

20. asset stored

21. evidence/context classification

22. reveal metadata

23. fallback hierarchy

24. no_visual_change

25. hold current image


ORIGINAL MEDIA

26. original audio selected

27. narrator stops

28. foreign-language clip strategy

29. segment trim

30. inappropriate clip rejected


ATTENTION

31. high-listen prevents complex visual

32. map allowed during low voice density

33. read mode reduces narration density


SOUND

34. undocumented event SFX rejected

35. ambience allowed


TIMELINE

36. EN timeline

37. DE timeline differs

38. FA timeline differs

39. AR timeline differs


RENDER

40. still image render

41. video render

42. black frame

43. text overlay

44. audio ducking

45. crossfade

46. document highlight

47. map sequence


CRITICS

48. automation feel detected

49. reveal violation detected

50. visual overload detected

51. native language failure detected


============================================================
PART 55 — IMPLEMENTATION LOOP
============================================================

DO NOT implement everything in one pass.

Use loops.


LOOP 1 — DATA MODEL

Implement:

EditorialBlueprint

Beat

PerformanceScript

VoiceBlock

VisualAsset

OriginalMediaSegment

ProductionScript

Run tests.


LOOP 2 — NARRATIVE BLUEPRINT

Use one existing Master Story.

Generate blueprint.

Inspect manually.

Ask:

Does it genuinely improve story strategy?

Fix.

Repeat.


LOOP 3 — ONE LANGUAGE ONLY

Start with EN.

Generate:

Story
PerformanceScript
VoiceBlocks

Do NOT implement all languages yet.

Test audio.


LOOP 4 — ELEVENLABS

Generate a small real audio sample.

Inspect:

pronunciation

pace

tone

pause behavior

Fix.

Repeat.


LOOP 5 — ALIGNMENT

Align actual audio.

Check word timings.

Fix.


LOOP 6 — VISUAL LIBRARY

Use one case.

Ingest:

10–20 real photos/documents/videos.

Verify metadata.

Fix.


LOOP 7 — VISUAL MATCHING

Create VisualRequirements for several beats.

Inspect candidate ranking.

Fix.


LOOP 8 — REVEAL FIREWALL

Test intentionally forbidden visuals.

Ensure they cannot be selected.


LOOP 9 — ORIGINAL AUDIO

Use one real interview/news clip.

Test:

clip selection

narrator stop

pause

resume


LOOP 10 — ATTENTION

Inspect several beats manually.

Look for:

too much visual activity

too much reading

conflicts with voice

Fix.


LOOP 11 — PRODUCTION SCRIPT

Generate 3–5 minute documentary segment.

Do NOT render whole 45 min yet.


LOOP 12 — RENDER

Render the 3–5 minute sample.

Watch it.

Inspect actual:

voice

silence

visual timing

music

original audio


LOOP 13 — CRITIQUE

Run all critics.

Then HUMAN REVIEW the rendered sample.

Fix.


LOOP 14 — MULTILINGUAL

Only when EN sample works:

create DE

then FA

then AR


Verify all independently.


LOOP 15 — 10–15 MINUTE PILOT

Produce one longer segment in all 4 languages.

Compare.


LOOP 16 — FULL DOCUMENTARY

Only after pilot quality passes:

render full 45–60 min.


LOOP 17 — REGRESSION

Run:

pytest

tsc

lint

build

Playwright


============================================================
PART 56 — INITIAL SUCCESS TARGET
============================================================

Do NOT define success as:

"video rendered successfully."


V1 success means:

1. Story remains compelling with eyes closed.

2. Native language sounds professional.

3. ElevenLabs voice sounds naturally directed.

4. Silence feels intentional.

5. Images do not change mechanically.

6. Real media improves authenticity.

7. Original audio is used selectively.

8. Visuals never reveal future information.

9. No misleading generic visual is presented as evidence.

10. Viewer is never overloaded by reading + listening + watching.

11. Production does not feel algorithmically assembled.

12. Same case feels native in all four languages.


============================================================
PART 57 — LIVE VALIDATION STRATEGY
============================================================

Use one existing well-researched case.

Do NOT start with entire documentary.

Create a 3–5 minute pilot containing:

human introduction

location orientation

one mystery setup

one evidence/document moment

one original audio clip if available

one meaningful silence

one reveal

one transition


Produce:

EN first.


Then review manually.


Only after EN passes:

DE
FA
AR


============================================================
PART 58 — FINAL CRITIQUE LOOP
============================================================

After the pilot is produced:

critique from five perspectives.


TRUE-CRIME EDITOR

Would I continue watching?


AUDIO DIRECTOR

Does the voice sound naturally performed?


DOCUMENTARY EDITOR

Do the visuals support the narration?


INVESTIGATIVE JOURNALIST

Is anything visually or audibly misleading?


NATIVE VIEWER

Does this feel originally produced in my language?


Then ask:

Does any part feel like automated content?

For each failure:

timestamp

reason

recommended fix


Fix.

Render again.

Repeat until stable.


============================================================
PART 59 — FINAL REPORT
============================================================

Report:

ARCHITECTURE

new entities
new agents
new services
new endpoints
new UI


BLUEPRINT

beats
mysteries
reveals
attention plan


VOICE

language
blocks
duration
rerenders
alignment quality


VISUALS

assets found
evidence
context
illustration
verified
rights status


ORIGINAL MEDIA

clips
audio clips
languages
selected durations


PRODUCTION

voice duration
silence duration
original audio duration
music duration
visual changes
black-screen moments


CRITICS

narrative
native language
voice
attention
visual accuracy
production
automation feel


TESTS

pytest
tsc
lint
build
Playwright


FINAL VERDICT

A.
Pilot feels professionally produced and not algorithmically assembled.

B.
Pipeline works but specific quality problems remain.

C.
Production still feels automated or technically unreliable.


============================================================
FINAL DESIGN RULES
============================================================

1. STORY FIRST.

2. AUDIO FIRST.

3. VISUALS SUPPORT THE STORY.

4. SILENCE IS A TOOL.

5. NO VISUAL CHANGE IS A VALID CHOICE.

6. ORIGINAL AUDIO BEATS RECREATED AUDIO WHEN IT ADDS AUTHENTICITY.

7. NARRATOR STOPS FOR IMPORTANT ORIGINAL SPEECH.

8. DO NOT USE SOUND EFFECTS TO INVENT EVENTS.

9. NEVER SHOW A FUTURE REVEAL EARLY.

10. DO NOT OVERLOAD LISTENER ATTENTION.

11. NEVER CONFUSE CONTEXTUAL B-ROLL WITH CASE EVIDENCE.

12. EACH LANGUAGE GETS ITS OWN STORY PERFORMANCE AND FINAL TIMELINE.

13. ELEVENLABS HANDLES VOICE PERFORMANCE.
    THE APP HANDLES DOCUMENTARY TIMING.

14. ACTUAL AUDIO DURATION CONTROLS FINAL VISUAL TIMING.

15. THE FINAL RESULT MUST FEEL EDITED BY A HUMAN DOCUMENTARY TEAM,
    NOT GENERATED BY A PIPELINE.