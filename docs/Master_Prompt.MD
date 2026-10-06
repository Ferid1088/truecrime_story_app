MASTER TASK
YOUTUBE TRANSCRIPT INTELLIGENCE PIPELINE
+
MULTILINGUAL TRANSCRIPT RESEARCH
+
CANONICAL EVIDENCE MERGING
+
ORIGINAL LONG-FORM STORY CREATION
+
ITERATIVE IMPLEMENT → TEST → CRITIQUE → FIX LOOP


PROJECT:

/Users/feri/truecrime_story_app


============================================================
MISSION
============================================================

The current main limitation of long-form story generation is evidence depth.

Articles and source summaries often provide enough facts to understand a case,
but not enough grounded detail to create a strong 45–60 minute narration.

We will therefore add a new major research layer:

MULTILINGUAL YOUTUBE TRANSCRIPT INTELLIGENCE.

For every case, the system should search for relevant YouTube coverage in:

English
German
Persian
Arabic

and ingest available transcripts where access/use is permitted.

These transcripts are NOT final-story templates.

They are research material.

The system must extract:

- factual claims
- timeline information
- human details
- scene-supporting details
- investigation details
- quotes and reported statements
- contradictions
- hypotheses
- narrative topics
- questions raised by different creators
- details omitted by other sources

Then merge those extracted insights with:

- official sources
- articles
- books/archive material
- existing Devin research
- other source types

into ONE canonical evidence system.

The final story must be independently structured and independently written.

============================================================
NON-NEGOTIABLE PRINCIPLE
============================================================

DO NOT:

Transcript A
+
Transcript B
+
Transcript C
→ concatenate into giant text
→ ask Writer to rewrite it

That architecture is forbidden.

Instead:

Transcript A
Transcript B
Transcript C

remain separately stored with provenance.

Then:

A/B/C
↓
claim/detail extraction
↓
semantic deduplication
↓
verification
↓
cross-source comparison
↓
canonical evidence
↓
independent narrative planning
↓
original Master Story

============================================================
WHY
============================================================

We need to preserve:

- provenance
- language
- creator/source
- timestamps
- uncertainty
- contradictions
- attribution
- independence of sources

And we must prevent the Writer from reproducing:

- another video's wording
- another creator's paragraph structure
- distinctive transitions
- distinctive metaphors
- sequence of narration
- unique storytelling structure

============================================================
COPYRIGHT / ACCESS RULE
============================================================

Use only transcript/content access methods that are legitimately available.

Allowed examples:

- publicly exposed transcript/captions
- official/public transcript
- transcript made available by creator/platform
- API-supported access
- user-provided transcript
- public-domain material
- material legitimately returned by the configured research provider

Do NOT:

- bypass authentication
- bypass paywalls
- bypass platform access controls
- circumvent technical restrictions

If transcript text cannot legitimately be obtained:

store metadata only.

============================================================
NEW PIPELINE
============================================================

Implement this pipeline:

CASE
 ↓
MULTILINGUAL VIDEO DISCOVERY
 ↓
VIDEO CANDIDATE RANKING
 ↓
TRANSCRIPT ACQUISITION
 ↓
TRANSCRIPT NORMALIZATION
 ↓
TRANSCRIPT SEGMENTATION
 ↓
TRANSCRIPT INTELLIGENCE EXTRACTION
 ↓
CLAIM / DETAIL DEDUPLICATION
 ↓
CROSS-LANGUAGE NORMALIZATION TO ENGLISH
 ↓
SOURCE INDEPENDENCE ANALYSIS
 ↓
FACT / CLAIM VERIFICATION
 ↓
CONTRADICTION ANALYSIS
 ↓
CANONICAL NARRATIVE RESEARCH DOSSIER
 ↓
STORY EVIDENCE PACK
 ↓
INDEPENDENT ENGLISH STORY DIRECTOR
 ↓
SECTION-BASED MASTER WRITER
 ↓
GROUNDING
 ↓
SIMILARITY / SOURCE-DEPENDENCE CHECK
 ↓
ENGAGEMENT
 ↓
MASTER QUALITY GATE
 ↓
LOCALIZATION PIPELINE


============================================================
PART 1 — YOUTUBE VIDEO DISCOVERY
============================================================

Create:

YouTubeResearchAgent

or an equivalent dedicated research component.

Search relevant YouTube videos for the case in:

en
de
fa
ar

Search terms must be language-native.

Do not simply translate one English query literally.

Discover videos covering:

- complete case narration
- documentary coverage
- interviews
- investigative analysis
- historical coverage
- local-language reporting
- archival material
- legal/investigative commentary

============================================================
VIDEO CANDIDATE MODEL
============================================================

Create or extend a model such as:

VideoSource

Fields conceptually:

id
case_id
platform
video_id
url
title
channel_name
channel_url
language
published_at
duration_seconds
view_count_if_available
description
discovery_query
research_language

transcript_status

creator_source_family

duplicate_group

relevance_score

created_at

Do not duplicate fields already represented cleanly by Source.

Prefer extending Source if that preserves architecture.

============================================================
PART 2 — VIDEO SOURCE CLASSIFICATION
============================================================

Each video should be classified.

Examples:

documentary
news
creator_narration
interview
official
archive
analysis
unknown

Also classify:

source_independence

Possible values:

primary
independent_secondary
likely_derivative
unknown

IMPORTANT:

Ten YouTube creators repeating the same Wikipedia/article account
must NOT count as ten independent sources.

============================================================
PART 3 — TRANSCRIPT ACQUISITION
============================================================

Create a dedicated abstraction:

TranscriptProvider

The rest of the code must NOT depend directly on a specific transcript service.

Concept:

TranscriptProvider.get_transcript(video)

returns:

{
    language,
    transcript_type,
    segments,
    retrieval_method,
    retrieved_at,
    available
}

Transcript types:

manual
auto_caption
creator_supplied
external_authorized
user_supplied
unavailable

============================================================
TRANSCRIPT SEGMENTS
============================================================

Do not immediately flatten timestamps.

Store segments.

Conceptually:

TranscriptSegment

id
video_source_id

start_seconds
end_seconds

text_original
language

canonical_text_en

segment_index

content_hash

created_at

============================================================
FULL TRANSCRIPT
============================================================

It is okay to reconstruct a transcript internally from segments if permitted.

But provenance must remain attached.

Never lose:

video_source_id
timestamps
original language

============================================================
PART 4 — TRANSCRIPT CLEANING
============================================================

Create deterministic cleanup first.

Remove or normalize:

- repeated caption fragments
- caption overlap
- excessive whitespace
- obvious caption timing duplicates
- speaker-label noise where appropriate
- meaningless filler generated by captioning

Do NOT stylistically rewrite transcript text at this stage.

Preserve original meaning.

============================================================
PART 5 — LANGUAGE NORMALIZATION
============================================================

Original transcript remains stored.

Create canonical English normalization for analysis.

For:

German
Persian
Arabic

produce:

text_original

AND

canonical_text_en

English remains:

canonical internal analysis language.

Do not discard original text.

============================================================
PART 6 — TRANSCRIPT INTELLIGENCE EXTRACTION
============================================================

This is the key component.

Create:

TranscriptIntelligenceAgent

Input:

transcript segments/chunks

Output structured information.

Extract:

FACTS

TIMELINE EVENTS

HUMAN DETAILS

SCENE DETAILS

INVESTIGATION DETAILS

PHYSICAL EVIDENCE

LEGAL DETAILS

LOCATIONS

PEOPLE

RELATIONSHIPS

QUOTES

REPORTED STATEMENTS

THEORIES

DISPUTED CLAIMS

UNVERIFIED CLAIMS

QUESTIONS RAISED

CONTRADICTIONS

HISTORICAL CONTEXT

============================================================
PART 7 — NARRATIVE INTELLIGENCE
============================================================

Also extract narrative intelligence separately from factual evidence.

Create:

NarrativeInsight

Examples:

important_question
mystery_element
emotional_context
turning_point
reveal_candidate
frequently_emphasized_detail
commonly_omitted_detail
viewer_context
narrative_transition_topic

IMPORTANT:

NarrativeInsight is NOT automatically factual evidence.

Keep it separate.

============================================================
VERY IMPORTANT
============================================================

Do NOT store:

"Creator X's hook"

and later reproduce it.

Instead extract abstract information such as:

The disappearance was first discovered through an unanswered relief arrival.

NOT:

copying the original creator's exact hook wording.

============================================================
PART 8 — CLAIM MODEL
============================================================

Every factual claim extracted from a transcript must retain provenance.

Concept:

TranscriptClaim

id

video_source_id
transcript_segment_ids

original_language

original_claim
canonical_claim_en

claim_type

confidence

certainty

speaker_if_applicable

timestamp_start
timestamp_end

verification_status

supporting_sources

created_at

============================================================
VERIFICATION STATUS
============================================================

Possible:

unverified
supported
strongly_supported
contradicted
disputed
false_or_unreliable
unknown

A YouTube narrator saying something does NOT automatically make it a fact.

============================================================
PART 9 — CROSS-VIDEO DEDUPLICATION
============================================================

This is crucial.

Example:

Video A:
"The clock had stopped."

Video B:
"The lighthouse clock was found stopped."

Video C German:
"Die Uhr war stehen geblieben."

These may represent ONE underlying claim.

Create semantic claim clustering.

Canonical result:

ClaimCluster

canonical_claim_en

member_claim_ids

independent_source_family_count

languages

support_status

confidence

============================================================
PART 10 — SOURCE DEPENDENCE DETECTION
============================================================

Try to detect when videos likely derive from the same upstream material.

Signals:

- same unusual claims
- same unusual sequence
- same error
- same wording after translation
- same article cited
- same documentary cited
- publication chronology

Do not treat dependent retellings as independent verification.

Store:

source_family

and:

likely_derivative_of

where confidence is sufficient.

============================================================
PART 11 — CROSS-LANGUAGE ADVANTAGE
============================================================

Use multilingual transcripts specifically to find information missed elsewhere.

For each language calculate:

video_count

transcript_count

unique_claims

unique_human_details

unique_scene_details

unique_investigation_details

contradictions_added

Example:

EN:
43 claims
9 unique

DE:
21 claims
3 unique

FA:
18 claims
2 unique

AR:
27 claims
4 unique

============================================================
PART 12 — MERGE ENGINE
============================================================

Now create the "merge" the user wants.

BUT:

Do NOT merge raw prose.

Merge KNOWLEDGE.

Create:

CanonicalResearchDossier

The dossier should combine information from:

official sources
web sources
articles
archives
books/material already available
YouTube transcripts
Devin research

Structure:

case_identity

verified_facts

timeline

people

human_details

scene_details

investigation_details

physical_evidence

locations

legal_details

historical_context

quotes

contradictions

disputed_claims

unverified_claims

theories

narrative_questions

research_gaps

============================================================
PART 13 — PROVENANCE
============================================================

Every dossier entry MUST contain provenance.

Example:

{
  "evidence_id": "E193",

  "canonical_claim_en": "...",

  "source_refs": [
      {...},
      {...}
  ],

  "independent_source_count": 2,

  "languages": ["en", "de"],

  "confidence": 0.91,

  "status": "verified",

  "narrative_value": "scene_detail"
}

============================================================
PART 14 — DATABASE STORAGE
============================================================

Database should therefore contain separate layers:

SOURCE

VIDEO SOURCE

TRANSCRIPT

TRANSCRIPT SEGMENT

TRANSCRIPT CLAIM

CLAIM CLUSTER

NARRATIVE INSIGHT

CANONICAL EVIDENCE

STORY VERSION

Do NOT store only a giant merged transcript.

============================================================
PART 15 — RAW TRANSCRIPT ACCESS
============================================================

The Writer should NOT normally receive all raw transcripts.

This is extremely important.

Writer receives:

CanonicalResearchDossier

+
StoryEvidencePack

+
ActEvidencePack

NOT:

20 complete creator transcripts.

This prevents stylistic copying and massively reduces tokens.

============================================================
PART 16 — ORIGINALITY / SOURCE-SEPARATION GATE
============================================================

Add a new:

SourceSimilarityValidator

Purpose:

detect whether generated narration is too close to any individual transcript.

Check:

- unusually similar phrases
- long matching sequences
- paragraph-level similarity
- distinctive sequence similarity
- repeated creator-specific metaphors

Do not require zero semantic similarity because facts must remain the same.

We want:

same facts
different expression and independent structure.

============================================================
PART 17 — STRUCTURAL ORIGINALITY
============================================================

Text similarity alone is insufficient.

Create:

NarrativeStructureSimilarityChecker

Compare Master Story against major transcript structures.

Example:

Creator A:

discovery
→ diary
→ weather
→ theories
→ conclusion

Our story should not simply reproduce that exact structure unless independently justified.

Story Director receives aggregated evidence,
not another creator's story outline.

============================================================
PART 18 — SAFE QUOTE HANDLING
============================================================

If transcript contains a quote:

determine whether it is:

actual documented quote

or

creator narration.

Never quote creator narration as historical quotation.

Quote classification:

source_document_quote

interview_quote

reported_quote

creator_narration

uncertain

Only appropriately sourced quotations may become approved_quotes.

============================================================
PART 19 — NARRATIVE CAPACITY V3
============================================================

Transcript intelligence should strongly improve narrative-capacity estimation.

New capacity inputs:

verified transcript claims

unique human details

unique scene details

investigation details

documented quotes

timeline density

contradictions

primary evidence

source depth

independent source families

transcript coverage duration

BUT:

do NOT award capacity merely because transcripts are long.

A 60-minute video repeating known information should add little capacity.

============================================================
PART 20 — NOVEL INFORMATION RATIO
============================================================

For each transcript calculate:

novel_information_ratio

Concept:

unique useful claims
÷
total useful claims

Examples:

Video A:
0.45

Video B:
0.06

Video B may not need further processing.

Use config thresholds.

============================================================
PART 21 — TRANSCRIPT VALUE SCORE
============================================================

Create:

TranscriptValueScore

based on:

unique claims
credible details
human details
scene details
quotes
contradictions
source independence
source quality

Not views.

Views do not equal evidence quality.

============================================================
PART 22 — RESEARCH STOP CONDITION
============================================================

Do not download/process endless videos.

Stop when:

new transcript value becomes marginal

AND

narrative capacity is sufficient

AND

major research gaps are filled.

Config examples:

max_videos_per_language

max_transcripts_per_case

minimum_novel_information_ratio

consecutive_low_value_stop

============================================================
PART 23 — TARGETED VIDEO RESEARCH
============================================================

If ResearchGapPlan says:

human_detail missing

search specifically for:

interviews
documentaries
local reporting
family commentary

If:

investigation_detail missing

search for:

documentary
investigator interview
official reconstruction
news investigation

Do not blindly add more creator narration.

============================================================
PART 24 — QUALITY HIERARCHY
============================================================

Evidence hierarchy remains important.

Example conceptual priority:

official / primary
↓
credible independent reporting
↓
documentary / expert analysis
↓
creator narration
↓
uncorroborated retelling

YouTube creator transcripts are excellent for:

finding details

finding research leads

finding contradictions

finding missing questions

But not automatically for establishing truth.

============================================================
PART 25 — STORY DIRECTOR
============================================================

Story Director receives the canonical dossier.

It must create a NEW narrative design.

The Director should explicitly answer:

What is our central question?

What does our version contribute?

Which details are truly verified?

Which myths should be challenged?

What order best serves THIS evidence?

What human thread should carry the story?

Which revelations must be delayed?

============================================================
PART 26 — LONG-FORM TARGET
============================================================

Goal:

minimum approximately 45 minutes
when evidence supports it.

Do NOT pad.

If transcript research raises supported capacity from:

30 min

to:

52 min

then generate 45-minute Master.

If after transcript ingestion capacity remains:

32 min

do NOT fabricate 45 minutes.

============================================================
PART 27 — STORY WRITING
============================================================

Continue using:

structured Master Draft

act budgets

per-act writing

ActEvidencePack

targeted editing

grounding

No return to monolithic full-story rewriting.

============================================================
PART 28 — TRANSCRIPT-SPECIFIC GROUNDING
============================================================

When story claim came from transcript-derived evidence:

validator must be able to trace:

Story sentence
↓
Evidence
↓
TranscriptClaim
↓
TranscriptSegment
↓
VideoSource
↓
timestamp

This should be visible internally.

============================================================
PART 29 — UI
============================================================

Add a new section inside case research:

VIDEO RESEARCH

Display per language:

EN
DE
FA
AR

For each:

videos found
transcripts available
transcripts processed
unique claims
unique details

============================================================
PART 30 — VIDEO TABLE
============================================================

Show:

Title

Channel

Language

Duration

Transcript Status

Transcript Value

Unique Claims

Source Independence

Processed

============================================================
PART 31 — VIDEO DETAIL
============================================================

Click video.

Show:

metadata

transcript availability

transcript language

segments

claims extracted

unique information

duplicates

contradictions

human details

scene details

investigation details

Do not dump entire copyrighted transcript into normal story UI by default.

Make transcript inspection an internal research view.

============================================================
PART 32 — TRANSCRIPT INTELLIGENCE UI
============================================================

Show:

"WHAT THIS VIDEO ADDED"

Example:

4 new claims

2 new scene details

1 contradiction

0 unique quotes

likely derivative source

This is much more useful than merely showing transcript length.

============================================================
PART 33 — RESEARCH DOSSIER UI
============================================================

Create a canonical Research Dossier view.

Sections:

Case facts

Timeline

People

Human details

Scene details

Investigation

Evidence

Contradictions

Quotes

Theories

Research gaps

Sources

============================================================
PART 34 — SOURCE TRACE UI
============================================================

Click any evidence item.

Show:

Supported by:

Official record

English YouTube Video A @ 13:21

German documentary @ 21:04

Article X

This gives complete editorial traceability.

============================================================
PART 35 — AGENTS
============================================================

All AI roles must be explicit.

Research provider:

video_discovery_agent
→ Devin

youtube_research_agent
→ Devin

transcript_acquisition
→ deterministic/provider integration where possible

Generation roles:

transcript_normalizer
→ cheap

transcript_intelligence_extractor
→ cheap

claim_clusterer
→ cheap

source_dependence_analyzer
→ cheap

evidence_verifier
→ cheap

narrative_research_synthesizer
→ cheap

master_story_director
→ writer

master_writer
→ writer

master_rewriter
→ writer

source_similarity_critic
→ premium

structure_originality_critic
→ premium

master_engagement_critic
→ premium

master_final_editor
→ premium

Keep existing:

grounding_validator

consistency_checker

section_critic

etc.

============================================================
CURRENT MODEL ROUTING
============================================================

Do not hardcode model IDs in agent code.

Current aliases remain:

cheap
→ openai/gpt-5.6-luna

writer
→ google/gemini-3.8-flash

premium
→ anthropic/claude-sonnet-5.5

Devin remains research provider.

============================================================
PART 36 — CONFIG
============================================================

Everything configurable through:

config/ai_config.json

Add conceptual sections:

youtube_research

transcript_ingestion

transcript_chunking

transcript_value

claim_clustering

source_independence

similarity

research_stop_conditions

No magic thresholds.

============================================================
PART 37 — COST CONTROL
============================================================

Transcripts may be huge.

Do NOT send entire 60-minute transcript repeatedly to LLMs.

Process once:

Transcript
↓
chunks
↓
structured intelligence

Then downstream uses intelligence.

Cache all results by:

video_id

transcript hash

chunk hash

model/config version

Do not re-extract unchanged transcripts.

============================================================
PART 38 — TOKEN EFFICIENCY
============================================================

Track:

transcript tokens ingested

normalization tokens

intelligence extraction tokens

dedupe tokens

verification tokens

story tokens

critique tokens

cost per video

cost per case

============================================================
PART 39 — DUPLICATE TRANSCRIPT CACHE
============================================================

If same video is rediscovered through:

English query

German query

another search round

do not process it again.

Canonical video identity prevents duplicate cost.

============================================================
PART 40 — TESTS
============================================================

Add tests for at least:

1. YouTube video discovery result storage

2. same video deduplicated

3. transcript unavailable

4. transcript retrieved

5. transcript segment timestamps preserved

6. caption duplicate cleanup

7. transcript language preserved

8. canonical English normalization

9. transcript claim extraction

10. claim provenance

11. multilingual claim clustering

12. duplicate claim detected

13. independent claim count

14. derivative source does not increase verification strength improperly

15. creator narration not treated as historical quote

16. verified quote keeps timestamp

17. narrative insight separated from evidence

18. novel information ratio

19. transcript value score

20. low-value transcript stop condition

21. ResearchGapPlan triggers targeted video research

22. canonical dossier includes transcript-derived evidence

23. Writer does NOT receive raw transcripts

24. Master claims trace back to evidence

25. transcript-derived claim traces to timestamp

26. similarity checker catches copied phrase

27. similarity checker permits ordinary shared factual language

28. structural similarity checker identifies excessive story-shape copying

29. cache prevents repeated transcript extraction

30. unavailable transcript does not break research

31. raw transcript is never fabricated

32. provider outage fails safely

============================================================
CRITICAL TEST
============================================================

Add an explicit regression test:

Given:

three transcripts describing the same case

the system must NOT construct the Writer input by concatenating the transcripts.

Assert:

Writer input contains:

CanonicalResearchDossier
StoryEvidencePack
ActEvidencePack

and does NOT contain:

full Transcript A
full Transcript B
full Transcript C

============================================================
PART 41 — LOOP IMPLEMENTATION PROCESS
============================================================

DO NOT implement everything once and stop.

Work in iterative loops.

============================================================
LOOP 1 — ARCHITECTURE AUDIT
============================================================

Before code:

inspect existing:

Source

SourceChunk

Fact

EvidencePack

ResearchJob

Devin provider

StoryPipeline

StoryVersion

UI

API

ai_config.json

Identify reusable components.

Do not create duplicate architecture.

Write a short implementation map.

Then implement.

============================================================
LOOP 2 — DATA MODEL
============================================================

Implement transcript/video storage.

Run migrations/schema upgrade.

Run tests.

Inspect database manually.

If provenance can be lost:

fix it.

Repeat until stable.

============================================================
LOOP 3 — TRANSCRIPT INGESTION
============================================================

Use ONE video fixture.

Test:

video
→ transcript
→ segments
→ cleanup
→ storage

Inspect actual DB rows.

Fix.

Repeat.

============================================================
LOOP 4 — TRANSCRIPT INTELLIGENCE
============================================================

Run one transcript through extraction.

Inspect:

facts

human details

scene details

investigation details

quotes

claims

narrative insights

Verify against transcript.

If extractor invents information:

fix prompt/schema.

Repeat.

============================================================
LOOP 5 — CROSS-VIDEO MERGE
============================================================

Use at least 3 transcript fixtures.

Test:

duplicate claims

unique claims

contradictions

source families

Verify no raw prose concatenation.

Fix.

Repeat.

============================================================
LOOP 6 — MULTILINGUAL
============================================================

Test:

EN

DE

FA

AR

Original text must remain.

Canonical English representations must be correct.

Cross-language duplicate claims should merge.

Fix.

Repeat.

============================================================
LOOP 7 — DOSSIER
============================================================

Build CanonicalResearchDossier.

Check:

facts

details

timeline

contradictions

theories

quotes

source provenance

No transcript should dominate simply because it is long.

Fix.

Repeat.

============================================================
LOOP 8 — STORY INPUT
============================================================

Inspect actual prompt sent to Story Director and Writer.

Verify:

NO raw transcript dump.

Verify:

evidence IDs

source diversity

human details

scene details

investigation details

Fix.

Repeat.

============================================================
LOOP 9 — ORIGINALITY
============================================================

Generate a test Master with mocked providers.

Compare with transcript fixtures.

Ensure:

no copied paragraphs

no copied long phrases

no single-source story structure replication

Fix similarity/originality gates.

Repeat.

============================================================
LOOP 10 — UI
============================================================

Open real UI.

Check:

Video Research

Transcripts

Transcript Intelligence

Canonical Dossier

Evidence provenance

Research Depth

Use the same UI debugging approach already established:

OPEN
→ TEST
→ OBSERVE
→ FIX
→ RELOAD
→ RETEST

No infinite skeleton.

============================================================
LOOP 11 — COST
============================================================

Measure transcript processing cost.

Look for:

same transcript processed twice

full transcripts repeatedly sent

unnecessary normalization calls

over-large prompts

Fix caching/scoping.

Repeat.

============================================================
LOOP 12 — FULL REGRESSION
============================================================

Run:

backend pytest

frontend lint

frontend build

Playwright

Fix every regression.

Then rerun full suite.

============================================================
PART 42 — LIVE VALIDATION
============================================================

IMPORTANT:

OpenRouter is currently unauthorized.

Before paid generation:

provider preflight.

If:

authorized != true

DO NOT run OpenRouter operations.

The implementation/tests can proceed with mocks.

Once provider is healthy:

perform one controlled Flannan validation.

============================================================
LIVE FLANNAN TEST
============================================================

Research:

EN videos
DE videos
FA videos
AR videos

Report:

videos discovered

transcripts available

transcripts processed

total transcript minutes

claims

unique claims

human details

scene details

investigation details

quotes

contradictions

============================================================
COMPARE BEFORE / AFTER
============================================================

Current approximate state before transcript intelligence:

28 sources

1 full-text

16 partial-text

11 summary-only

11 chunks

supported capacity ~43.8 minutes

Compare after transcript ingestion.

But do NOT treat transcript duration itself as supported story duration.

============================================================
MASTER GENERATION
============================================================

Only after:

source_readiness = ready

evidence_readiness = ready

provider authorized

capacity sufficient

Generate ONE English Master.

Do not generate localization yet.

============================================================
MASTER SUCCESS CRITERIA
============================================================

Target:

at least configured 45-minute narration band

AND:

grounding passes

consistency passes

engagement passes

similarity passes

structural originality passes

language quality passes

purity passes

============================================================
PART 43 — FINAL CRITIQUE LOOP
============================================================

After implementation is apparently complete:

STOP.

Critique your own architecture.

Ask:

1. Can provenance ever be lost?

2. Can a transcript become treated as fact without corroboration?

3. Can ten derivative videos artificially increase confidence?

4. Can Writer see too much raw creator prose?

5. Can final text copy another creator's wording?

6. Can final structure simply mimic one source?

7. Can transcript processing costs explode?

8. Can multilingual duplicates inflate evidence?

9. Can a low-quality auto-caption create false evidence?

10. Can story length be artificially inflated by transcript repetition?

Fix every meaningful weakness.

Then rerun tests.

============================================================
PART 44 — SECOND CRITIQUE LOOP
============================================================

After fixes:

critique again from four perspectives:

DATA ENGINEER

INVESTIGATIVE RESEARCHER

TRUE-CRIME EDITOR

COPYRIGHT / ORIGINALITY REVIEWER

For every issue:

fix

test

retest.

============================================================
PART 45 — FINAL REPORT
============================================================

Return:

ARCHITECTURE

new flow

new models

new agents

config changes

TRANSCRIPT RESEARCH

EN videos
DE videos
FA videos
AR videos

transcripts available

transcripts processed

SOURCE INTELLIGENCE

claims extracted

unique claims

duplicate claims

contradictions

human details

scene details

investigation details

quotes

source families

CANONICAL DOSSIER

evidence count

independent-source count

research gaps

NARRATIVE CAPACITY

before

after

confidence

MASTER

only if provider authorized and capacity sufficient:

words

minutes

grounding

consistency

engagement

similarity

structural originality

status

EFFICIENCY

transcript processing tokens

evidence tokens

story tokens

critic tokens

total cost

TESTS

pytest

frontend lint

frontend build

Playwright

FINAL VERDICT

A
Transcript intelligence materially improved evidence depth and the system can
produce an original grounded 45+ minute Master.

B
Transcript intelligence works but evidence or story quality still needs work.

C
Pipeline or provider remains blocked.

============================================================
FINAL PRODUCT PRINCIPLE
============================================================

Other creators' videos help us answer:

"What information should we investigate?"

"What details might our existing research have missed?"

"What contradictions exist?"

"What parts of the case need deeper verification?"

They must NOT answer:

"How should we copy this creator's story?"

Our final product must be:

MORE RESEARCHED

MORE VERIFIED

MORE COMPLETE

and

NARRATIVELY INDEPENDENT.

Same historical case.

Shared underlying facts.

Completely original editorial synthesis and narration.