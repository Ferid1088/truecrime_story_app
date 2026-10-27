# Main Visual Planner — Complete 12-Step Architecture

**Repository:** `Ferid1088/truecrime_story_app`  
**Working branch:** `audit/visual-planner-architecture-v2`  
**Scope:** the approved visual-planner replacement, its production gates, and the protected integrations around it.  
**Status:** implementation plan and acceptance contract; runtime completion is reported separately from this document.

This document is the single checklist for the main visual planner. It describes what the system must do, what it must never do, the authoritative data at each boundary, and the proof required before an old visual path can be removed.

## Non-negotiable boundaries

The visual system may read factual research, the approved story/blueprint, approved spoken versions, Host timing, and Music timing. It may not rewrite factual research, the story, Host prompts, voice text, or music decisions.

The visual planner is not the avatar planner. The Host/avatar stage plans and renders the presenter separately. The visual planner plans case visuals: photographs, documents, maps, archival footage, evidence graphics, and contextual images. The canonical host-timeline adapter combines those approved outputs exactly once.

All four language outputs remain supported: English (`en`), German (`de`), Persian (`fa`), and Arabic (`ar`). English is the approval language for the completed-film gate; it is not permission to delete or overwrite the other languages.

## Step 1 — Baseline lock and protected outputs

**Purpose:** establish the branch, database, configuration, tests, and existing artifacts before changing visual behavior.

**Required behavior:**

- Work only on `audit/visual-planner-architecture-v2`; never merge into `main` as part of this work.
- Inspect the working tree before every repair and preserve unrelated or uncommitted user work.
- Treat factual research, YouTube transcripts/downloads, facts, sources, story versions, spoken versions, voice performances, Host plans, Host scenes, music tracks, and their hashes as protected.
- Record the baseline commit, migration head, enabled providers, cost cap, running services, and test result.

**Acceptance proof:** a reproducible baseline report, a cleanly attributable diff, and tests showing that research, writing, Host, music, voice, and language storage still behave as before.

**Current implementation note:** the active branch is `audit/visual-planner-architecture-v2`. Existing case data and untracked user reports are retained.

## Step 2 — Canonical reveal graph

**Purpose:** create one typed, case-specific factual/reveal structure that controls visual planning and spoiler order.

**Required behavior:**

- Build or reuse a validated PostgreSQL/SQLAlchemy `RevealGraph` from the approved blueprint and evidence.
- Persist nodes, edges, first-reveal beat/order, and beat exposures.
- Reuse the latest validated graph on retries; never ask a second model to invent a parallel evidence graph.
- Reject an invalid graph instead of silently inventing dependencies.

**Acceptance proof:** graph rows can be read back, node/exposure counts are stable on retry, invalid graph data is visible as a failure/degraded finding, and later-reveal facts cannot be used in an earlier beat.

**Current implementation note:** case 15 has validated graph `id=1` with 122 nodes and 170 edges. The `visual_needs` stage reuses it.

## Step 3 — Canonical media and research reuse

**Purpose:** use existing factual and media research before spending money or creating duplicate visual assets.

**Required behavior:**

- Reuse stored facts, sources, video sources, transcripts, visual assets, media links, and source provenance.
- Keep original bytes immutable and address them by content hash.
- Keep source edition, rights status, verification status, and derivative ancestry separate.
- Treat creator footage and search results as leads until rights and factual suitability are independently verified.
- Permit one initial and at most two materially distinct further external search rounds per unmet requirement, persisted across retries.

**Acceptance proof:** a retry reuses existing research, duplicate downloads are not created, source ancestry is inspectable, and every search round has a requirement, provider, cost, and result record.

## Step 4 — Semantic visual-needs planning

**Purpose:** replace sentence-by-sentence visual guessing with a semantic documentary plan.

**Required behavior:**

- The planner receives the case, blueprint beats, evidence pack, source metadata, and the validated reveal graph.
- It returns grounded entities and per-beat requirements, not fabricated imagery or unsupported claims.
- Requirements describe the viewer’s need: human connection, orientation, evidence, atmosphere, time, investigation, emotion, or transition.
- Each requirement names an entity, priority, acceptable role, and—when applicable—exact/context search intent.
- Keep the planner’s English search vocabulary while preserving evidence spelling and language-neutral entity identity.
- Preserve graph node keys where they identify the same evidence item and respect graph exposure/order.

**Acceptance proof:** deterministic validation drops unknown entities, unsupported dates, invented quotes, unmatched documents, and requirements that violate the graph. Empty or invalid plans stop the visual stage.

**Current implementation note:** new visual plans receive `reveal_graph` input and persist `reveal_graph_id` in validation metadata. Existing case-15 plans created before this boundary may be linked to the graph but are not claimed to have been regenerated from it.

## Step 5 — Independent verification and rights

**Purpose:** ensure that a candidate is factually suitable, visually suitable, legally usable, and ethically appropriate before selection.

**Required behavior:** keep these decisions separate:

1. source authenticity and provenance;
2. identity/context match;
3. transformation or derivative integrity;
4. rights and publication-use permission;
5. ethics and sensitive-material handling;
6. exact scene suitability;
7. final-render presence and integrity;
8. publication readiness.

`unknown`, `needs_review`, or an incomplete rights record never counts as approval. Preview use and publication use have distinct gates.

**Acceptance proof:** no unapproved or unresolved media can enter a publishable render; every selected asset has a readable verification and rights trail; reviewer failures remain visible.

## Step 6 — Requirement-scoped visual coverage

**Purpose:** prove that the finished film covers the narration meaningfully, not merely that it contains pictures.

**Required behavior:**

- Measure semantic requirement coverage per beat and temporal coverage over narration intervals.
- Distinguish evidence visuals from contextual visuals; generic atmosphere cannot satisfy an evidence requirement.
- Report weak sentences, uncovered beats, long stills, black frames, repeated assets, and unsupported visual claims.
- Use bounded, requirement-scoped repair searches rather than an unbounded visual loop.
- Fail closed for publication when mandatory coverage is missing.

**Acceptance proof:** coverage is computed from the actual compiled timeline and render, with a deterministic report showing covered/uncovered requirements, intervals, media roles, and repair findings.

## Step 7 — Verified factual graphics

**Purpose:** create maps, timelines, detective-board elements, documents, and other graphics from approved facts only.

**Required behavior:**

- Generate graphics from typed claims, source IDs, dates, places, and reveal state.
- Validate every label, date, quote, relationship, and map place against the evidence.
- Keep graphics versioned and language-specific where text is localized.
- Apply spoiler/reveal checks to graphics exactly as to photographs and footage.
- Never use generated graphics to imply a fact absent from the evidence.

**Acceptance proof:** every graphic has source/fact ancestry, a validation result, a reveal position, and RTL-safe localized text for Persian and Arabic where applicable.

## Step 8 — Visual selection and direction

**Purpose:** select eligible media and turn it into deterministic, inspectable shot instructions.

**Required behavior:**

- Select only eligible, verified assets that satisfy a planned requirement.
- Keep exact evidence, accurate context, and generic transition material distinct.
- Evaluate film-wide pacing, repetition, duration, crop, movement, and beat continuity.
- Produce declarative direction with asset hash, source ancestry, role, start/end timing, crop, and any graphic metadata.
- Never let a model silently substitute a different person, place, date, or event.

**Acceptance proof:** each shot can be traced to a requirement and approved asset; direction validates against the asset and timeline; rejected or uncertain candidates cannot be rendered.

## Step 9 — Canonical production manifest and protected Host/Music integration

**Purpose:** compile one authoritative timeline without shifting audio, Host, subtitles, or music twice.

**Required behavior:**

- Use one canonical manifest with explicit narration-time, source-time, and final-film-time domains.
- Preserve the existing Host plan, voice performances, studio/outfit choices, avatar rendering, music plan, ducking, and silence rules.
- Apply Host insertion, timing shifts, subtitle shifts, and music conversion exactly once through the existing integration boundary.
- Pin all media, voice, Host, music, and script hashes in the compiled manifest.

**Acceptance proof:** frame-accurate regression tests show no duplicate Host insertion, no double audio shift, no missing voice, no music overlap, and stable output hashes when inputs are unchanged.

**Avatar clarification:** the avatar is consumed here as a protected Host output. It is not selected by the visual-needs planner and does not replace case-specific visual coverage.

## Step 10 — English-first exact rendered-film gate

**Purpose:** approve the exact English film before spending on the other three audiovisual productions.

**Required behavior:**

- Complete English narration, voice, Host, visuals, graphics, manifest, render, and independent render checks first.
- Verify duration, decode, loudness, black frames, still duration, semantic coverage, rights, spoiler order, captions, and hash ancestry from the actual rendered file.
- Require the owner to watch and explicitly approve the exact English render checksum.
- Invalidate approval if the render, script, voice, visual plan, or relevant source hash changes.
- Do not treat story-text approval as rendered-film approval.

**Acceptance proof:** the approval row pins the exact file checksum and validation report. Without it, DE/FA/AR full-film production is blocked.

**Current implementation note:** the earlier case-15 English render is not an approved finished film because its coverage report contains black-frame, long-still, and visual-coverage failures.

## Step 11 — Four-language gate, Shorts gate, and budget safety

**Purpose:** preserve all languages, prevent runaway retries, and ensure Shorts are derived only from a complete four-film set.

**Required behavior:**

- After exact English approval, run native DE, FA, and AR production while preserving the saved text and voice outputs.
- Keep English, German, Persian, and Arabic scripts, captions, voice hashes, Host decisions, graphics, and RTL output independently auditable.
- Admit Shorts only after all four full films pass their full-film gates; one completed film is insufficient.
- Enforce the agreed case/application cap with atomic reservations before paid calls.
- Distinguish actual, estimated, reserved, unknown, and refunded cost; unknown provider cost never becomes zero.
- Retries resume from valid saved stages and never pay twice for the same content hash.

**Acceptance proof:** concurrent calls cannot spend the same remaining budget; retries produce no duplicate ledger charge; no Shorts job is admitted early; all four language outputs remain available.

## Step 12 — Owner UI, audit, regression, and legacy retirement

**Purpose:** make every decision inspectable, prove the replacement, and remove obsolete visual paths only after cutover is safe.

**Required behavior:**

- Expose stage status, graph version, requirements, candidates, verification, rights, coverage, costs, alternatives, render findings, and approval checks in the UI.
- Allow owner corrections and revalidation at the smallest affected scope.
- Run backend, PostgreSQL, frontend, rendering, adversarial, recovery, multilingual, budget, and full-film tests.
- Run an independent audit against the compiled render and compare it with stored provenance.
- Mark old planner/director/prompt paths unreachable before removal.
- Remove obsolete or parallel visual implementations only after replacement tests, migration/backfill, live proof, and rollback evidence pass.
- Do not silently change an owner-approved render.

**Acceptance proof:** the full suite is green on the final branch commit; live proof records stage results, costs, hashes, and remaining blockers; old paths have no active call sites; rollback remains possible until release review.

## Operational loop

For every case and every repair cycle, follow this order:

1. Inspect branch, working tree, services, database state, and current job state.
2. Read the approved story/blueprint and reuse saved research, text, voices, Host, and music.
3. Build or reuse the validated reveal graph.
4. Build or reuse the graph-aware visual plan.
5. Research only unmet requirements within their bounded budget.
6. Verify provenance, identity/context, rights, ethics, and scene suitability.
7. Select and direct approved visuals and factual graphics.
8. Compile the canonical manifest and apply Host/music integration once.
9. Render English and run independent checks on the actual file.
10. Stop at the exact English owner-approval gate if any blocker remains.
11. Only after approval, produce DE/FA/AR and then Shorts after all four full films pass.
12. Re-run the relevant tests and audit the diff before declaring a step complete.

## Definition of complete

The architecture is complete only when all of the following are true:

- the validated reveal graph is the shared visual-planning boundary;
- new visual plans consume and record that graph;
- all selected visuals and graphics are evidence-grounded, rights-approved, and traceable;
- English has complete temporal and semantic coverage in the actual render;
- the exact English checksum is owner-approved before other full-film languages run;
- EN, DE, FA, and AR text, voices, Host, captions, and graphics remain available and correct;
- Shorts are locked until all four full films pass;
- budget admission and retry idempotency are proven;
- backend, PostgreSQL, frontend, rendering, and adversarial tests pass;
- obsolete visual AI paths are unreachable and removed only after the replacement is proven;
- no protected research, writing, Host, music, language, or user-created artifact was discarded.

---

# Detailed planning record

The original planning conversation developed this architecture incrementally. The sections below preserve the decisions that were easy to lose in a short summary. Substep names are normalized into one complete implementation record; the owner-approved corrections are part of the requirements, not optional suggestions.

## The problem this architecture must solve

The former visual process spent too much time and money searching broadly, then produced a weak film with too few useful visuals. A long film could end with only a small number of usable photographs and one video. The problem was not simply “the verifier is too strict.” Several different questions were mixed together:

- Is this asset related to the case?
- Is the source authentic and traceable?
- Does it show the claimed person, place, object, or event?
- Is it legally usable for the intended publication?
- Is it ethically suitable for the scene?
- Does it fit this exact narration interval?
- Has the same asset already been used too often?
- Does it obey the reveal order?

The old system also repeated searches for the same subject, selected the same image for different scenes, rejected useful context images as though they were false evidence, left many failed AI checks in an unclear state, and counted repeated placement failures as though they were thousands of bad files. The replacement must separate these decisions and make each one visible.

For the case-15 diagnosis that motivated the redesign, the stored data showed the following failure pattern:

- related images were often rejected because “related” was confused with “exact evidence”;
- people, places, objects, documents, and contextual images were judged by nearly the same standard;
- watermarks, logos, timestamps, and burned-in text were mixed with identity and factuality decisions;
- failed or overloaded AI calls left assets unchecked rather than giving them a recoverable state;
- the same asset was tested repeatedly against many narration placements;
- visual shortages were discovered too late, after expensive processing;
- old jobs and agent runs could remain stale and make the application state look inconsistent.

The new planner therefore plans the visual need first, searches only for that need, stores the result in a shared library, verifies it in separate dimensions, and blocks only when the final film cannot be truthful and adequately covered.

## Decisions that govern all twelve steps

### No generated case imagery

The film uses real photographs, real documents, real maps, real archival footage, and real contextual imagery. The visual system must not create an invented photograph, invented historical scene, AI person, AI evidence object, or decorative AI animation to fill a shortage.

AI may still be used for bounded planning, classification, transcription, query refinement, metadata extraction, language localization, and the existing avatar/Host production. AI output is never itself proof of a historical fact.

### Existing factual work is valuable

The successful research and story-writing path remains authoritative. Existing YouTube research, downloaded transcripts, extracted facts, sources, contradictions, timeline material, approved story text, spoken versions, voices, Host results, and music results are reused. The new visual system reads these outputs; it does not replace them or silently rewrite them.

### Existing visual intelligence is not authoritative

The owner explicitly rejected the old visual AI agents, prompts, inspectors, and verification behavior because the results were expensive and poor. The replacement writes new visual prompts and contracts from scratch. Safe deterministic infrastructure—file handling, provider adapters, ffprobe, hashes, database storage, render mechanics, and tested technical utilities—may be reused after audit.

### The planner is autonomous by default

The owner can inspect visual decisions, candidates, rejected assets, alternatives, costs, and shortages. That review possibility does not mean the owner must approve every picture or every researcher action. The normal path runs autonomously and asks the owner only at the defined final intervention gates.

### A planning target is not a download quota

“Eight photographs and one video” is a planning target for visual variety and timing. It is not permission to download eight arbitrary files, nor a promise that a video must exist if the archive has none. The planner must accept legitimate alternatives and report the actual shortfall.

### Host is authoritative but visible to the main plan

The Host system remains its own authority. It decides Host content, appearance positions, dialogue, voice, outfit, avatar direction, and other Host tasks already defined by the application. The Main Visual Planner must receive the completed Host outputs and include their positions in the master plan, but it may not rewrite the Host text, reorder Host appearances, or change Host decisions.

Host time is not subtracted from visual coverage. Host is an independent layer, while the visual plan covers the full narration timeline around it.

### English is the first completed film

English is produced and rendered first. The owner watches and approves the exact rendered English film, identified by its final checksum, before the German, Persian, or Arabic full films are produced. All four saved texts and voices remain preserved throughout.

### Shorts wait for all four films

Shorts are created only after all four full-length language films have passed their gates. A completed English film alone is not enough to admit Shorts.

## Step 1 — Full application audit and replacement boundary

### 1.1 Establish the working boundary

1. Confirm the repository and branch.
2. Record the current commit and working tree.
3. Do not discard uncommitted work, old research, saved text, voices, or user reports.
4. Keep the implementation on `audit/visual-planner-architecture-v2`.
5. Do not merge into `main` during implementation or verification.

### 1.2 Inventory every current process

Trace the application process by process, not only file by file:

- factual research and YouTube ingestion;
- video transcripts and source extraction;
- fact extraction, contradiction handling, and evidence diet;
- story planning, writing, critique, rewriting, and language versions;
- spoken versions and voice performances;
- Host planning, Host dialogue, studio, outfit, avatar, and scene rendering;
- detective board, maps, timelines, documents, chapter cards, and other graphics;
- visual planning, visual research, video discovery, selection, verification, rights, and gaps;
- production scripts, audio planning, music, captions, subtitles, render, and audit;
- Shorts admission, cutting, captions, disclosure, and publication metadata;
- API routes, worker jobs, frontend controls, database migrations, ledgers, and restart behavior.

For every process record its inputs, outputs, owner, prompt, provider, database rows, retry behavior, cost, and consumers.

### 1.3 Classify every path

Every discovered component receives one of these classifications:

- **KEEP:** authoritative and protected, such as factual research, story writing, Host, music, voice storage, and safe rendering infrastructure;
- **MODIFY:** technically useful but needing a new contract or gate;
- **MERGE:** deterministic helper or storage behavior that is safe to incorporate into the new visual pipeline;
- **REPLACE:** old visual agents, prompts, inspectors, visual-intelligence decisions, or parallel planners whose quality is not accepted;
- **REMOVE:** an obsolete path proven unreachable after its replacement is live and tested.

No component is removed merely because it looks old. Runtime call sites, tests, migrations, saved data, and rollback implications are checked first.

### 1.4 Mandatory cross-process conflict audit

The new Main Visual Planner changes the order and ownership of several processes. Therefore the audit must ask, for each existing process:

- Does it duplicate visual research?
- Does it search before the story is stable?
- Does it create a competing visual plan?
- Does it change Host output that should be authoritative?
- Does it create maps or graphics outside the new visual plan?
- Does it insert Host or music twice?
- Does it use a different rights or approval rule?
- Does it bypass the graph, cost guard, English gate, or four-film Shorts gate?
- Does it retain a prompt or inspector that the approved architecture replaces?

Resolve conflicts one by one. The goal is not to preserve the current application unchanged; the goal is to preserve successful authority and eliminate redundant or contradictory paths.

### 1.5 Baseline proof

Before changing a subsystem, record:

- migration head and database schema;
- branch and commit;
- enabled providers and configuration;
- current case cap and historical spend;
- saved case data counts;
- service status;
- backend, PostgreSQL, frontend, and render test results;
- known stale jobs and their safe handling.

This baseline distinguishes a pre-existing problem from a regression.

## Step 2 — Case knowledge graph

The graph is a compact case memory and reveal-order structure. It is not a general-purpose external graph product, and it does not require Neo4j or LangGraph. The agreed storage is PostgreSQL with SQLAlchemy/Alembic models, typed rows, JSONB attributes where flexible attributes are required, persistent history, and versioned graph instances.

### 2.1 Case-scoped graph creation

Create one graph version for the approved case and blueprint. Do not create a global graph that mixes unrelated cases. A graph must identify:

- case ID;
- blueprint ID;
- version;
- status (`draft`, `validated`, `invalid`, or equivalent);
- creator/run provenance;
- source and evidence ancestry;
- created and superseded timestamps.

The graph is built from already-approved evidence and blueprint data. It does not replace the evidence store.

### 2.2 Categories and subcategories

The planner creates categories only when the story and evidence require them. It must not create a large taxonomy merely because a category is possible.

Typical categories include:

- important people;
- family and relationships;
- places and buildings;
- police, court, and investigation;
- objects and documents;
- events and chronology;
- vehicles and movement;
- source records and quoted evidence;
- maps and geographic relationships;
- visual requirements and scene contexts.

Each category may have a subcategory and, only when useful, a sub-subcategory. For example:

```text
person
└── named person
    ├── childhood (only if the story uses it)
    ├── family (only if relevant)
    ├── court appearance (if narrated)
    └── investigation context (if required)
```

This structure is a search and reasoning aid, not an instruction to collect every possible picture of a person.

### 2.3 Attributes and inheritance

Each graph node carries the attributes needed by downstream workers:

- stable node key;
- display label and aliases;
- type and category path;
- evidence/fact IDs;
- source IDs;
- date or period;
- geographic location;
- sensitivity classification;
- exact-versus-context role;
- first reveal beat and order;
- visual requirements;
- allowed search resources;
- required search terms and prohibited generic terms;
- quantity target and flexible alternatives;
- rights and disclosure requirements;
- language-neutral identity;
- provenance and history.

Subcategories inherit only safe constraints from their parent. A child can narrow the search, but cannot inherit an unsupported fact or a parent’s identity. A family photograph is related to a person but is not automatically a photograph of that person.

### 2.4 Relationships

Use explicit edge types instead of assuming that two nodes with similar names are the same:

- `is_a`;
- `part_of`;
- `related_to`;
- `family_of`;
- `located_at`;
- `occurred_at`;
- `occurred_before` / `occurred_after`;
- `appears_in`;
- `depicts`;
- `source_supports`;
- `revealed_in`;
- `relies_on`;
- `same_asset_as` (suggestion only until verified);
- `derived_from`;
- `required_for`;
- `acceptable_alternative_for`;
- `used_in_beat`;
- `host_slot_for`;
- `map_required_for`.

Every edge has provenance and, where necessary, confidence or uncertainty. Similarity is not identity.

### 2.5 Story scenes and visual requirements

The graph links to story beats/scenes, not just subjects. A person node can require different visual treatments in different beats:

- first introduction;
- relationship explanation;
- investigation or court appearance;
- family context;
- later reflection.

The same physical asset may satisfy one requirement and fail another. The graph therefore carries beat-specific purpose and role.

### 2.6 Shared media connections

A graph node may connect to many candidate assets, and an asset may connect to several related requirements. The connection records:

- exact or contextual relationship;
- evidence role;
- candidate/verified/approved state;
- source and hash;
- relevant beat or interval;
- prior uses and repetition cost;
- rights and disclosure state.

This prevents each search from starting with an empty subject query and rediscovering the same image.

### 2.7 Efficient video segmentation

The system does not need to inspect every frame of every source video at maximum cost.

Use staged inspection:

1. source metadata, title, description, channel, duration, captions, transcript, and known provenance;
2. cheap thumbnails or sparse keyframes to identify candidate regions;
3. transcript/time alignment and shot-boundary hints to locate relevant segments;
4. denser frame checks only for candidate segments;
5. full decode and final integrity checks only for selected segments and the final render.

Segments from one original source remain related by source identity, while their visual content is evaluated separately. A good segment does not make every other segment good.

### 2.8 Graph memory, history, and validation

The graph remembers historical decisions without forcing the planner to repeat them:

- prior approved assets;
- rejected assets and reasons;
- failed searches and providers;
- exhausted criteria/resource combinations;
- source yield and reliability;
- graph and plan versions;
- owner interventions;
- render and approval history.

Updates create a new version or an auditable revision. Never silently mutate an owner-approved graph or film dependency. Validate that every node and edge has evidence where required, every first-reveal beat exists, and every visual requirement points to a valid node.

## Step 3 — Narrative purpose and visual requirements

### 3.1 Read the approved narrative structure

The Main Visual Planner starts after the relevant story and blueprint outputs are stable. It reads beat purpose, summaries, human focus, reveals, attention, facts, timeline, and source references. It does not need to receive the entire narration text to decide every image; it uses the approved structure and evidence.

The final coverage auditor still receives the actual compiled narration intervals so that the finished film is checked against what is truly spoken.

### 3.2 Decide what the viewer needs to see

For each beat, specify the visual function:

- human connection;
- orientation;
- evidence;
- atmosphere;
- time or period;
- investigation;
- emotion;
- transition.

The planner asks “what should the viewer understand or feel here?” rather than “what picture matches this sentence?”

### 3.3 Required visual types and alternatives

Each requirement may prefer a type but must define acceptable alternatives. Examples:

| Preferred need | Acceptable truthful alternatives |
|---|---|
| named person | verified portrait, verified archive appearance, evidence-backed document or contextual location when no portrait exists |
| place | verified location image, map, street/building archive, accurate regional context |
| event | real archival footage, photographs, document, timeline, map, or carefully labeled context |
| document | source scan, readable excerpt, evidence card, or timeline reference |
| movement | map, route diagram, real location, source footage, or verified vehicle/context |

An alternative cannot silently make a stronger claim than the missing preferred asset. A contextual courthouse cannot be labeled as the exact courthouse unless verified.

### 3.4 Quantities, timing, and diversity

Calculate a planning target from duration, beat importance, expected shot length, and variety. Use a flexible range, not a rigid quota.

If a three-minute childhood section targets eight photographs and one video but only four approved photographs exist and no video exists:

- use the four approved photographs without stretching them beyond the timing rules;
- check whether an approved document, map, place, timeline, or contextual asset can truthfully cover another requirement;
- search the allowed additional rounds if the requirement is still unmet;
- report the shortage rather than downloading arbitrary files;
- block the film if the remaining gap makes truthful coverage impossible.

Two videos do not automatically replace four photographs, and four photographs do not automatically satisfy a moving-event requirement. The planner chooses by narrative function, not by file type symmetry.

### 3.5 Graph-linked requirements

Every visual requirement links to a graph node, edge, source, or beat exposure. The planner records:

- why the requirement exists;
- what evidence supports it;
- what the viewer is allowed to know at that point;
- what exact and contextual roles are acceptable;
- what alternatives are valid;
- what would count as a shortage.

### 3.6 Film-wide balance as an aim

Film-wide balance is a strategy for search and editorial formation, not a strict mathematical production requirement. The planner aims for variety in people, places, documents, maps, periods, photographs, video, and graphics while remaining flexible when the historical record is uneven.

It must not force decorative variety that introduces false material, and it must not reuse one image for too long merely to achieve a percentage.

### 3.7 Maps are decided here

The Main Visual Planner decides whether a map is needed, what place/route it must show, what evidence supports it, and where it appears. The old production path must not independently invent or add maps later. Maps are one of the visual alternatives and are subject to the same factual and reveal checks.

## Step 4 — Host integration

### 4.1 Finish existing Host work first

All required Host tasks already defined by the application must finish far enough that their outputs are available to the Main Visual Planner:

- Host plan;
- Host dialogue/writing and critique;
- language-specific Host text where applicable;
- voice performance;
- scene positions and durations;
- studio, outfit, camera, pose, and direction choices;
- avatar render or a clear planned/blocked state;
- provenance, hashes, and disclosure metadata.

The visual planner does not replace this work.

### 4.2 Main planner consumes Host results

The Main Visual Planner receives Host slots as fixed timeline inputs. It uses them to plan surrounding case visuals, transitions, and audio/visual continuity. It may not change Host text, Host order, Host position, wardrobe, or direction.

### 4.3 Host authority and planner responsibility

If the Host planner decides that the avatar appears at a particular point, the Main Visual Planner incorporates that appearance. If the Host planner says no avatar appears, the visual planner does not invent one. The Host remains independent in authority but is integrated in the master plan.

### 4.4 English-first Host workflow

English Host output is part of the English film. German, Persian, and Arabic Host output is created only after the English film passes the exact rendered-film approval gate, unless a saved prior language artifact is being inspected or preserved rather than newly produced.

### 4.5 Full narration coverage around Host

Host is not a hole in the visual plan. The case visual plan covers the entire narration timeline, including narration before, during, and after Host appearances. Host overlay and case visuals are separate layers whose timing is explicitly represented.

### 4.6 Host failure and recovery

If a Host asset fails, the job records the exact failed stage and resumes from the last valid Host result. It does not rerun visual research or pay for already-valid voice/media. If the Host failure prevents a final film, the gate reports it as a blocker.

### 4.7 No double insertion

Host insertion, audio shifts, subtitle shifts, and music conversion happen once through the canonical Host timeline adapter. The Main Visual Planner only references the resulting timing contract.

### 4.8 Host integration validation

Validate that:

- every planned Host slot has one corresponding Host artifact;
- no Host segment appears twice;
- narration and subtitles remain synchronized;
- Host audio and avatar video match the saved voice/hash;
- the visual coverage report still covers the full narration timeline;
- language-specific Host assets are preserved and correctly labeled.

## Step 5 — Built-in visual tools

### 5.1 Detective board

The detective board is not merely an alternative to photographs. It is a required evidence-backed visual tool when the narrative benefits from relationships, uncertainty, or competing accounts. It uses graph nodes, typed edges, source/fact IDs, reveal order, and uncertainty labels.

The board must not turn an allegation into a confirmed relationship. Its cards and lines are validated exactly like other factual graphics.

### 5.2 Maps and geographic explanations

Maps are created only when the visual plan says geography helps understanding. The graph supplies places, routes, dates, and relationships. The map renderer consumes a validated map specification; it does not independently search the story and add places.

### 5.3 Timelines and event chronology

Timeline cards use approved event facts and source references. Conflicting dates remain visibly uncertain rather than being silently resolved by the visual model. Reveal order applies to timeline cards.

### 5.4 Documents and information cards

Documents must use source text or a verified scan. Information cards must preserve exact wording, source IDs, dates, and uncertainty. Quotes are copied exactly from the approved evidence and are checked deterministically before rendering.

### 5.5 Real photos, archive, and footage

The tools can place real photos, real archival footage, documents, and maps. They cannot synthesize a substitute historical photograph or animation when the archive is thin.

### 5.6 Transitions and motion

Motion is limited to truthful presentation: crop, pan, zoom, map movement, timeline progression, or a real video segment. Motion cannot create an implied event that was never observed. Long stills are reported and rejected when they exceed the approved rhythm/timing limits.

### 5.7 Coordinated tool planning

The Main Visual Planner chooses among the existing deterministic tools using one plan. There is no separate uncoordinated map planner, board planner, and visual planner that can contradict one another.

### 5.8 Autonomous operation and optional review

The system chooses and compiles tools autonomously under the contracts. The owner can inspect and correct the decisions. Routine per-card approval is not required, but owner intervention is available at the final shortage and exact English-film gate.

## Step 6 — Media research

### 6.1 Reuse existing YouTube factual research

Do not repeat the first broad YouTube fact search when the application already has downloaded transcripts and source material. Reuse the saved results, identify which videos come from relevant True-Crime channels or other credible channels, and inspect their cited/original sources.

YouTube research and visual media research serve different purposes:

- YouTube transcripts support factual research, claims, chronology, and source discovery;
- visual research finds usable real images, documents, archive, maps, and footage for planned requirements.

The existing research result is not automatically a publishable visual asset.

### 6.2 Graph-aware query generation

Researchers receive the complete relevant graph path at once:

- category;
- subcategory;
- sub-subcategory when present;
- node attributes;
- aliases;
- period and place;
- exact/context role;
- beat purpose;
- allowed sources;
- prohibited generic or misleading terms;
- current shortage and prior search history.

This is more precise than repeating the person’s name for every scene.

### 6.3 Source discovery and prioritization

Prioritize sources according to the requirement:

- official records and institutions;
- courts, police, archives, libraries, museums, and government sources;
- reputable newspapers and historical collections;
- Wikimedia/Commons and other clearly licensed archives;
- relevant True-Crime channel references as leads to original sources;
- approved or licensed video sources;
- contextual regional sources.

Brave or another search provider is a discovery tool, not a rights guarantee. A failed provider result must not be silently billed as a successful research result or interpreted as evidence that no media exists.

### 6.4 Photograph and archival-image research

Search exact entities and planned contexts separately. A query for a named person must not be satisfied by a generic courtroom crowd. A query for a place may accept a verified building or regional context according to the requirement. Search results are deduplicated before download and stored with query/source provenance.

### 6.5 Video research and segment discovery

Use source metadata, transcript alignment, thumbnails, sparse frames, and shot segmentation before expensive checks. Do not download or inspect entire collections unnecessarily. A video candidate is a research lead until source provenance, rights, visual content, and segment suitability pass.

YouTube links are not automatically downloaded for publication. They may be used as factual research or a lead to an authorized source. Clean, licensed, or otherwise permitted footage is required for the film.

### 6.6 Intelligent search rounds and stopping rules

For each unmet visual requirement:

1. run one focused initial search;
2. evaluate what was actually found;
3. if still unmet, run up to two additional materially different searches;
4. change the query, source, attributes, or resource rather than repeating the same request;
5. consult the graph’s prior search history and source-yield history;
6. stop early when the planner has evidence that another search is unlikely to add a legitimate new option.

The two additional attempts are counted per unmet visual requirement, not blindly per graph object. Related requirements can share a result when the asset genuinely satisfies both, but each unmet requirement still has its own shortage record.

### 6.7 Cost, performance, and source learning

Track calls, estimated and actual cost, latency, provider errors, candidate yield, verified yield, and useful-source share. Adapt future searches based on this history. Reduce concurrency when providers overload; retry transient failures with bounded backoff; mark stale runs recoverable; never treat a failed AI call as a rejection.

The agreed application cap is €300 before implementation of this architecture. Historical spend remains intact. The cap is not raised automatically to rescue a bad search.

### 6.8 Research output and integration validation

Every research result must enter the shared media library or a clearly labeled research-only store with:

- requirement ID;
- graph node/edge;
- source and query;
- original URL or provider ID;
- retrieval time;
- content hash where downloaded;
- candidate role;
- rights state;
- verification state;
- language-neutral metadata;
- error or rejection reason.

The visual plan consumes these outputs; no hidden temporary search list is allowed to bypass the library.

## Step 7 — Shared media library and deduplication

### 7.1 Canonical asset model

A canonical asset is an immutable original or an explicitly identified source edition. Store byte hash, source, retrieval metadata, format, duration/dimensions, and provenance. Derivatives point back to the canonical ancestor.

### 7.2 Source provenance

Keep source identity separate from asset identity. Two files can be visually similar but come from different sources and have different rights. One source can produce many segments or derivatives.

### 7.3 Image and video deduplication

Use exact hash first and perceptual similarity as a review/deduplication suggestion. Do not automatically merge assets merely because they look similar. Family pictures, killer pictures, spouse pictures, and children pictures may overlap physically, but the graph relationship and depicted identities still need verification.

Prevent the same canonical asset from being selected repeatedly for different requirements unless the plan explicitly allows reuse. Store each use and its purpose.

### 7.4 Storage, versions, and derivatives

Store:

- original/canonical bytes;
- normalized preview and thumbnail;
- video proxy and selected segments;
- crops/reframes;
- document render;
- map/board/timeline render;
- final-use derivative;
- hashes and parent-child ancestry.

Cleanup is reference-aware and reversible. A rejected derivative may be removed after metadata is preserved, but a canonical source is not deleted while any approved or historical artifact references it.

### 7.5 Graph connections

Link each asset to all relevant graph nodes, source claims, requirements, and beat uses. Asset relationships remain auditable even when an asset is reused in more than one language.

### 7.6 AI-generated component tracking

Track whether an asset, graphic, voice, Host clip, music track, or derivative contains an AI-generated component. The case film’s disclosure metadata is assembled from these records. The system must not mislabel a real archival photo as AI-generated, and must not hide the synthetic avatar or AI-generated audio where disclosure is required.

### 7.7 Multilingual reuse

Canonical case visuals are language-neutral. Their captions, labels, dates, and graphic text may have language-specific derivatives. A single verified photograph can serve EN/DE/FA/AR while maintaining separate localized overlays and RTL-safe layout.

### 7.8 Library validation and migration readiness

Validate backfills, source ancestry, hashes, references, and reversible migration on fixtures before live conversion. Existing case-15 media remains available; the new library does not delete it merely because it was collected under the old planner.

## Step 8 — Media verification

The old inspectors and their prompts are not the authority for the replacement. New verification contracts are written for the distinct questions below.

### 8.1 Source authenticity and provenance

Verify the source page/provider identity, original URL, source edition, retrieval path, and any available archive or publication metadata. A search result title alone is not provenance.

### 8.2 People, events, locations, and historical accuracy

People require strict identity evidence; a lookalike is not acceptable. Locations and objects may use contextual evidence when the role says context, but must not be presented as exact case evidence. Event and date claims must match the supplied facts and sources.

### 8.3 Photograph, video, and document integrity

Check file readability, decode, dimensions/duration, hash, transformations, cropping, overlays, watermarks, burned-in text, missing frames, and document legibility. Video checks are staged so full-file inspection is reserved for candidates and final use.

### 8.4 Copyright, licensing, and usage rights

Rights are a separate gate. Provider labels are evidence, not automatic publication permission. Store licence/permission, attribution, restrictions, intended profile, and unresolved status. Preview approval cannot imply publication approval.

### 8.5 Context and exact evidence

Use roles explicitly:

- **exact evidence:** the asset depicts the named or claimed item;
- **accurate context:** the asset is a truthful contextual equivalent and is labeled/placed accordingly;
- **transition/generic:** it creates rhythm but cannot support a factual claim.

The same asset may be acceptable as context and unacceptable as exact evidence. That is not a contradiction.

### 8.6 Scene suitability, ethics, and YouTube compliance

Sensitive images, victims, minors, graphic material, accusations, and synthetic components receive explicit handling. Avoid gratuitous graphic material. Preserve uncertainty and allegation language. Record disclosure recommendations for AI-generated Host/video/audio components and other synthetic elements.

### 8.7 Confidence, conflicts, and escalation

Verification returns a structured result, not only pass/fail:

- passed;
- rejected with reason;
- needs review;
- blocked by missing evidence;
- transient check failure;
- rights unknown;
- context-only;
- approved for a specific requirement.

Conflicting verifiers are not averaged into a silent pass. The conflict is stored and escalated according to the final production gate.

### 8.8 Validation and production readiness

Before selection, verify that every candidate has the necessary provenance, role, rights, ethics, scene suitability, graph relation, and hash. Unchecked, unresolved, or `needs_review` media never enters a publishable render.

## Step 9 — Visual selection and direction

### 9.1 Narrative beats and visual requirements

Convert the visual-needs plan into beat-level requirements and timeline intervals. Each requirement is prioritized and tied to a graph node, a source, a visual role, and a fallback.

### 9.2 Candidate ranking

Rank only eligible candidates using evidence fit, exact/context role, source quality, rights, scene suitability, graph/reveal fit, visual quality, diversity value, prior-use cost, and temporal fit. A high search rank never bypasses a failed rights or identity gate.

### 9.3 Editorial selection

Select the best combination for the beat and the film, not the highest-ranked asset independently for every sentence. Avoid repeated faces, repeated crops, and repeated search-result thumbnails. Preserve the strongest exact evidence for the beat where it matters most.

### 9.4 Visual direction and cinematic presentation

Direction specifies source asset, role, time range, crop/reframe, movement, transition, graphic overlay, caption/label, and reveal state. It is declarative and validated. It does not invent a new object or change the factual meaning.

### 9.5 Continuity, diversity, and pacing

Aim for continuity and variety across the film. Flexible pacing rules guide selection but do not force a target when the archive does not support it. Never extend a still beyond its approved maximum solely because the search failed.

### 9.6 Evidence boards, maps, timelines, and documents

Select approved built-in graphics when they provide a more truthful explanation than a missing photograph or video. Each board/map/timeline/document is a directed visual with source ancestry and reveal validation.

### 9.7 Full-film timeline and production integration

Compile visual shots with narration, Host, music, captions, cards, and language metadata through one canonical manifest. Use source time and final time explicitly. Do not splice or shift the same layer twice.

### 9.8 Validation, audit, and selection readiness

Before render, audit every planned shot:

- requirement satisfied;
- graph exposure allowed;
- candidate eligibility passed;
- rights passed;
- source/hash present;
- timing valid;
- no forbidden repetition;
- graphic text/source valid;
- Host/music boundary respected.

## Step 10 — Coverage, quality, and cost

### 10.1 Semantic and temporal coverage

Coverage has two dimensions:

- semantic: the required visual meaning is represented;
- temporal: the narration interval is actually covered by an eligible visual or truthful graphic.

Category share alone is not enough. A film can contain many seconds of pictures and still fail to explain the narration.

### 10.2 Visual shortage report

At the end of research and before final render, report per requirement:

- target/need;
- approved exact assets;
- approved context assets;
- approved graphics;
- approved video segments;
- missing or weak coverage;
- search rounds used;
- providers/resources tried;
- cost and failures;
- legitimate alternatives;
- effect on the film.

The owner sees one consolidated report after the research process, not a stream of repeated questions every time one search returns too few images.

### 10.3 Alternative handling

When the preferred visual is absent, check in order:

1. already-approved media in the shared library;
2. a legitimate exact alternative from a different source or edition;
3. an accurate contextual visual;
4. a deterministic evidence graphic, map, timeline, or document;
5. a shorter or restructured visual interval if truthful and within rhythm rules.

If none works, block rather than fill with invented material.

### 10.4 No stretched stills

The planner is not allowed to stretch the use time of a picture simply because there are not enough pictures or videos. Long-still problems remain a finding. The film may use fewer, better visuals if the timing and coverage contract still passes; otherwise it is blocked.

### 10.5 Film-wide visual balance

Use balance as a flexible search and editorial aim. Report actual ratios and shortages, but do not force a fixed percentage of video, photographs, people, or graphics when the available record does not support it.

### 10.6 Cost and performance gate

Before paid calls, estimate cost and compare it with the remaining cap. During execution, reserve budget atomically and record actual spend. The gate stops new paid work when the cap cannot safely support it. Historical costs are preserved; the cap is not silently increased.

### 10.7 Render quality audit

Audit the actual rendered file for:

- end-to-end decode;
- duration;
- black frames;
- long stills;
- loudness and silence;
- captions and chapter cards;
- visual requirement coverage;
- rights and approval ancestry;
- Host sync;
- music and narration timing;
- disclosure metadata;
- final checksum.

### 10.8 Production readiness

The film is production-ready only when coverage, rights, provenance, render quality, budget, language, Host, music, and approval gates pass together. A degraded visual stage remains degraded; it cannot be hidden by marking the job rendered.

## Step 11 — English-first film and multilingual production

### 11.1 Compile English from saved authoritative outputs

Use the approved English story/spoken version, saved English voice, completed Host outputs, graph-aware visual plan, verified media, graphics, music, captions, and canonical manifest. Do not regenerate protected text or voice merely to satisfy the visual planner.

### 11.2 Render English

Render the English film first. Persist the production script, manifest, media/source hashes, voice hash, Host hash, music references, render metadata, and all gate results.

### 11.3 Exact rendered-film checks

The gate checks the file that the owner will watch, not an earlier draft or story-text approval. Any changed script, voice, visual asset, graphic, Host output, music input, or relevant source invalidates the exact approval.

### 11.4 Owner approval

The owner watches the exact English file and explicitly approves its checksum. The owner may reject it with notes. The system records the decision, checksum, timestamp, and notes. No other full-language production is admitted before this gate.

### 11.5 Native German, Persian, and Arabic production

After English approval, produce DE/FA/AR using their saved/native text and voices. Do not overwrite or discard existing language assets. Each language has its own captions, graphic text, RTL validation where applicable, voice timing, Host output, render, and audit.

### 11.6 Voice preservation and synchronization

Voice files remain content-addressed and reusable. A visual repair does not trigger a new voice call unless the narration itself changed. The production manifest aligns visual intervals to actual voice/narration timing.

### 11.7 Multilingual visual integration

Case visuals and source media can be shared; localized cards, captions, labels, and graphic layouts are language-specific. Persian and Arabic require RTL and font/layout tests. The graph, source claims, and reveal order remain language-neutral.

### 11.8 Shorts gate

Only after EN, DE, FA, and AR full films pass their required gates may the Shorts pipeline be admitted. Shorts inherit approved film media and disclosure/rights metadata. They do not trigger a parallel visual-research universe.

## Step 12 — Audit, recovery, UI, tests, and legacy retirement

### 12.1 Owner UI and observability

The owner can inspect:

- graph version and node/exposure structure;
- visual requirements and alternatives;
- search rounds and source yield;
- candidate states and rejection reasons;
- rights/provenance/verification;
- coverage and shortage report;
- Host/music boundary;
- cost estimates, reservations, and actual spend;
- render findings and checksum;
- English approval and multilingual gate;
- Shorts admission status.

The UI supports optional review and correction without requiring approval for every autonomous action.

### 12.2 Restart and recovery

Restart the API or worker at every durable boundary in tests. A restart must resume from the last valid saved artifact, not start the entire process again. Valid voice, Host, media, graph, plan, and render outputs remain reusable.

### 12.3 Idempotency and no double payment

Provider calls use content/input hashes and idempotency keys where available. A retry of the same valid input must not create a duplicate voice, avatar, media, or render charge. Incomplete output is not marked complete.

### 12.4 Database and migration validation

Test Alembic upgrade/downgrade, graph versioning, asset backfill, source ancestry, requirement links, approval rows, cost reservations, and safe preservation of historical records.

### 12.5 Backend and rendering tests

Run, as applicable:

- unit tests for graph, planner, library, verification, rights, coverage, gates, and budget;
- integration tests with PostgreSQL;
- worker/API recovery tests;
- frontend route and interaction tests;
- ffprobe/render/decode/loudness/timing tests;
- multilingual and RTL tests;
- Host and music regression tests;
- Shorts admission tests;
- adversarial tests for fabricated facts, wrong people, wrong places, spoiler leakage, unknown rights, prompt injection, stale jobs, duplicate media, and budget races.

### 12.6 Legacy visual path audit

Before removal, search all imports, registrations, task names, prompt paths, fallback branches, migrations, tests, API routes, and frontend calls. Run the new path through a real case. Confirm old paths are unreachable. Only then remove obsolete prompts, agents, inspectors, and parallel orchestration. Keep rollback evidence until release review.

### 12.7 Live case-15 proof

The case-15 proof must report separately:

- saved English text and voice reused;
- graph ID/version and validation;
- visual plan ID and whether it was newly generated with graph input or reused from an earlier plan;
- Host outputs reused and not rewritten;
- media counts by candidate/verified/approved/context/rejected/unchecked;
- coverage findings;
- render duration/decode/black-frame/still-time results;
- exact cost delta and remaining budget;
- English approval state;
- DE/FA/AR and Shorts admission state;
- unresolved blockers.

Do not call a degraded old English render “finished” merely because a file exists.

### 12.8 Final release decision

Release only when:

- all required tests pass on the final branch commit;
- live proof is recorded;
- saved four-language text and voices are intact;
- the English checksum gate is explicit;
- obsolete visual paths are unreachable and removed only after proof;
- remaining blockers are either fixed or explicitly accepted by the owner;
- no merge into `main` has been performed by this task.

## Concrete data contracts

### Reveal graph input to the planner

```json
{
  "graph_id": 1,
  "nodes": [
    {
      "node_key": "person_example",
      "label": "Example Person",
      "category": "person",
      "attributes": {
        "aliases": [],
        "period": "1900-1910",
        "sensitivity": "high"
      },
      "first_revealed_beat_id": "B03",
      "first_revealed_order": 3
    }
  ],
  "exposures": [
    {
      "beat_id": "B03",
      "node_key": "person_example",
      "exposure_type": "named"
    }
  ]
}
```

The graph supplies structure and reveal order, not new facts. Facts and sources remain the evidence authority.

### Visual requirement

```json
{
  "requirement_id": "VR-B03-01",
  "beat_id": "B03",
  "graph_node_key": "person_example",
  "purpose": "human_connection",
  "priority": "high",
  "preferred_role": "exact",
  "acceptable_roles": ["exact", "context"],
  "preferred_types": ["photo", "archive_video"],
  "alternatives": ["document", "timeline_card", "accurate_place_context"],
  "target_seconds": {"min": 4, "aim": 8, "max": 20},
  "search_rounds_allowed": 3,
  "evidence_ids": ["F012"],
  "source_ids": [17],
  "reveal_order": {"first_allowed_beat": "B03"}
}
```

### Candidate asset

```json
{
  "asset_id": "asset_123",
  "sha256": "...",
  "source": {
    "url": "...",
    "publisher": "...",
    "retrieved_at": "..."
  },
  "graph_links": ["person_example"],
  "requirement_links": ["VR-B03-01"],
  "role": "exact",
  "status": "verified",
  "rights": {"status": "approved", "profile": "publication"},
  "verification": {
    "provenance": "passed",
    "identity_or_context": "passed",
    "integrity": "passed",
    "ethics": "passed",
    "scene_suitability": "passed"
  },
  "ai_component": false
}
```

### Shortage report

```json
{
  "requirement_id": "VR-B03-01",
  "target": {"min_seconds": 4, "aim_seconds": 8},
  "approved_exact_assets": 1,
  "approved_context_assets": 2,
  "approved_graphics": 1,
  "approved_video_segments": 0,
  "search_rounds_used": 3,
  "remaining_gap_seconds": 0,
  "alternatives_used": ["document"],
  "owner_intervention": null,
  "status": "covered"
}
```

## Final implementation checklist

### Protected material

- [ ] Factual research remains unchanged and reusable.
- [ ] Story-writing prompts and approved story versions remain unchanged.
- [ ] EN/DE/FA/AR text remains stored.
- [ ] EN/DE/FA/AR voice files and hashes remain stored.
- [ ] Host planning, dialogue, studio, outfit, avatar, and scene results remain authoritative.
- [ ] Music planning, Audio Director, prompts, track library, generation, and mixing remain authoritative.

### New visual boundary

- [ ] New graph-aware planner prompt is active.
- [ ] Old visual AI prompts are unreachable.
- [ ] Old inspectors/verifiers are not silently used as the replacement authority.
- [ ] New requirements are graph-linked and evidence-grounded.
- [ ] Search is requirement-scoped and bounded.
- [ ] Existing research and media are reused before new calls.
- [ ] Exact/context/generic roles are separate.
- [ ] Rights and verification are independent gates.
- [ ] Coverage is semantic and temporal.
- [ ] Shortage is reported once at the defined gate.
- [ ] No still is stretched to conceal a shortage.

### Production gates

- [ ] English is rendered first.
- [ ] Exact English checksum approval is required.
- [ ] DE/FA/AR are blocked until English approval.
- [ ] Shorts are blocked until all four full films pass.
- [ ] Budget reservations are atomic.
- [ ] Retries do not double-pay.
- [ ] Owner-approved files cannot change silently.

### Release proof

- [ ] Backend tests pass.
- [ ] PostgreSQL integration tests pass.
- [ ] Frontend tests pass.
- [ ] Rendering and ffprobe checks pass.
- [ ] Multilingual and RTL checks pass.
- [ ] Host and music regressions pass.
- [ ] Recovery/restart checks pass.
- [ ] Live case proof is recorded.
- [ ] Remaining blockers are reported honestly.
- [ ] No merge into `main` is performed.

## Agreement traceability audit

This section was added after re-reading the complete referenced conversation. It makes the decisions that were expressed as corrections explicit, so they cannot disappear inside a broad step heading.

### Agreed execution order

The intended order is:

```text
Existing factual research
  └─ YouTube discovery/transcripts/source references are reused
     └─ evidence diet, facts, contradictions, timeline
        └─ story writing, critique, approved story/blueprint
           └─ all existing Host work completes and its results are saved
              └─ Main Visual Planner reads story + graph + Host results
                 └─ graph-aware, requirement-scoped visual research
                    └─ shared library, verification, rights, selection
                       └─ maps/boards/timelines/documents where planned
                          └─ English production and exact render checks
                             └─ owner watches and approves English checksum
                                └─ DE + FA + AR full films
                                   └─ Shorts only after all four films
```

### Decision: do not perform broad picture/video search before story writing

The existing factual-research stage remains useful through story writing. Its saved YouTube transcripts and source references are preserved. Broad, unfocused picture and video searches do not remain in that early stage because they produce thousands of candidates before the story says what is needed.

The Main Visual Planner searches for pictures, documents, maps, and footage only after the relevant story/blueprint requirements are known. It may use the earlier YouTube research to discover original sources and references, but it does not repeat the same broad YouTube search or treat every research video as a film asset.

### Decision: no mandatory “archive researcher, then extra researcher” chain

There is no requirement to run one blind Archive Researcher first and then a second uncoordinated Source Media Researcher. The Main Visual Planner creates the requirement and allowed source strategy; compatible researchers may work in parallel, but:

- every worker receives the same graph attributes and requirement ID;
- a shared search ledger prevents duplicate query/resource combinations;
- one worker’s result is visible to the others;
- results are deduplicated before further search;
- provider concurrency is bounded;
- the planner decides whether a new search is materially different and justified.

“Parallel” means coordinated work on distinct search space, not several agents repeating the same query.

### Decision: the planner sets the required-result cap

The planner decides how many candidate results are useful for each requirement, subject, scene, and source type. The cap is based on beat importance, duration, diversity need, source availability, prior yield, and remaining budget. A global cap cannot replace this requirement-level decision.

The cap limits candidate intake and paid work; it is not a promise to download or approve that number. When enough legitimate candidates have passed the relevant gates, search stops even if the theoretical target is larger.

### Decision: two extra searches are purposeful, not blind

For each unmet visual requirement, the planner may use two additional search rounds after the initial focused search. Each additional round must change something meaningful:

- source/resource;
- exact attribute combination;
- period or geographic qualifier;
- language/alias spelling where justified;
- archive or collection;
- media type or legitimate alternative.

Repeating the same search in the same resource with the same attributes wastes budget and is forbidden. The planner checks its history before searching. Search rounds are counted per unmet visual requirement, not blindly for every graph node, and they stop early when the evidence shows no likely legitimate improvement.

### Decision: shortage is collected and reported once

If a requirement remains short, the planner first checks existing approved media and legitimate alternative formats. It then records the shortage and continues the rest of the case research. The owner receives one consolidated shortage report after the research process, not a new question every time an individual search is disappointing.

The report states “needed X / approved Y,” identifies the exact missing role, lists the searches and resources tried, and explains the effect on the film. The planner must not hide a shortage by stretching a still, selecting an unrelated person, using an unapproved asset, or fabricating a graphic.

### Defined owner intervention options

When the final shortage report says truthful coverage is still impossible, the owner may choose one of the following bounded interventions:

1. inspect and approve a specific pending or `needs_review` candidate after seeing its source, role, rights, and verification findings;
2. choose one of the planner’s legitimate alternative visual formats;
3. provide or identify an authorized source/media item;
4. accept a clearly labeled context visual where the evidence allows context rather than exact depiction;
5. revise the affected narration/beat or remove the requirement before re-planning;
6. accept that the film is blocked until an appropriate source is available.

The owner does not approve an unknown asset by approving the shortage report alone. A selected candidate must still pass the applicable rights, provenance, identity/context, ethics, and render checks. The owner is not asked to approve every normal search result.

### Decision: related is not exact, and rejection is not one universal verdict

The new system preserves useful related material as context when appropriate. It does not lower identity standards for people or evidence. A candidate can therefore be:

- related but not exact;
- exact for one graph node but context for another;
- factually suitable but rights-unknown;
- rights-cleared but unsuitable for a particular narration interval;
- technically valid but repetitive;
- unresolved because an AI check failed;
- rejected for a specific reason rather than globally “bad.”

The old inspector prompts that conflated these questions are replaced. A failed provider/AI call is a recoverable processing failure, not an automatic factual rejection.

### Decision: graph meaning, researchers, and director have separate jobs

| Responsibility | Authority |
|---|---|
| meaning, entities, relationships, reveal order, requirements | Main Visual Planner + validated graph |
| discovery and retrieval within a requirement | coordinated media researchers |
| source/file storage and ancestry | shared media library |
| provenance, identity/context, rights, ethics, suitability | new independent verification contracts |
| final placement, timing, crop, movement, continuity | Visual Director/selection layer under the approved plan |
| Host text, appearance decision, order, avatar direction | existing Host system |
| narration voice, music planning, mixing | existing voice/Music authority |
| final English checksum and shortage intervention | owner gate |

No researcher creates a new meaning graph, and no visual director changes the Host plan or factual authority.

### Decision: the graph is useful memory without a large graph product

The planner needs memory/history so it can remember prior searches, approved/rejected assets, source performance, graph versions, and owner decisions. This is implemented with the existing PostgreSQL application database, SQLAlchemy/Alembic models, typed graph rows, JSONB attributes where needed, and versioned history. A long-running external graph platform is not required.

### Decision: graph duplication control is relational and provenance-aware

The graph reduces repeated searching by identifying related nodes and shared media connections, but categories alone do not prove that assets are duplicates. Exact hashes, normalized source identity, perceptual similarity, graph relationships, and human-readable provenance work together. A family picture can legitimately relate to several nodes; it is not automatically merged into every node’s identity.

### Decision: video verification uses a cheaper staged method

The whole source video does not need expensive maximum-quality inspection before the planner knows whether it contains a relevant moment. The system first uses metadata, captions/transcripts, channel/source information, thumbnails, sparse keyframes, and time alignment. It then checks denser frames for candidate segments and performs full integrity/decode checks only for selected segments and the final render.

Segments remain related to their original source while each segment’s content is judged separately. A source-level pass never automatically approves every segment.

### Decision: YouTube use and downloading

YouTube remains valuable for factual research, transcripts, channel/source discovery, and references to original material. The app does not treat a YouTube result as a rights-cleared film clip. It does not blindly download thousands of YouTube videos for visual filling. A clip can enter the film only through an authorized/usable source path with provenance and rights evidence.

If a source is unavailable for publication, the planner can use its factual information as research and search for an authorized archive, press source, public-domain equivalent, licensed upload, document, map, or other truthful alternative. A failed download or HTTP error is logged as a provider/source failure, not silently converted into an approved visual or an automatic rejection of the underlying fact.

### Decision: AI disclosure is part of the production contract

The application must identify realistic altered or synthetic components used in the published video. This includes the avatar video, synthetic avatar/Host voice where applicable, AI-generated music where applicable, and any other AI-generated component. The film metadata, YouTube upload/disclosure fields, and multilingual transparency note must be derived from the component ledger.

Real archival photographs, real documents, real maps, and real source footage are not labeled AI-generated merely because AI helped find, classify, crop, subtitle, or arrange them. AI planning does not turn a real asset into synthetic media. The disclosure layer also does not bypass rights, factuality, ethics, or platform safety rules.

### Decision: music stays as it is

The existing music planning, Audio Director, prompts, track library, generation, reuse, ducking, mixing, and silence behavior are preserved. The Main Visual Planner consumes the resulting audio/timing contract. It does not replace the music system with a new visual-agent music path and does not apply a second timing shift.

### Decision: existing Host concept stays, but its result is under the master plan

The final agreement resolves the apparent tension between “Host must be under the Main Visual Planner” and “Host is independent” as follows:

- Host remains independently authoritative for all Host decisions already defined in the app.
- All Host tasks complete and their saved results become inputs to the Main Visual Planner.
- The Main Visual Planner includes Host slots in the single master timeline and plans the case visuals around them.
- The Main Visual Planner cannot rewrite Host dialogue, move Host slots, change the Host order, or substitute a different avatar decision.
- Host time does not reduce the requirement to cover the complete narration with case visuals.

This is integration under one master plan, not duplication or takeover of the Host system.

### Decision: maps move into visual planning

The Main Visual Planner decides which maps are needed. The old production stage must not independently search the story and add maps later. Planned maps use the graph’s places, routes, dates, sources, and reveal order. If no map is needed, no map is created merely to fill a template.

### Decision: language order is strict

The English film is completed first. The owner sees the actual rendered English film and approves its exact checksum. Only then are newly produced German, Persian, and Arabic full films admitted. Existing saved text and voice assets for all four languages remain available and are not deleted or regenerated unnecessarily.

Shorts wait until all four full films pass. They are derived from approved full-film assets and do not start a second uncontrolled media search.

## Completeness check against the conversation

| Agreement recovered from the conversation | Where it is documented |
|---|---|
| detailed twelve-step plan with substeps | Steps 1–12 and the detailed planning record |
| full application audit before and during implementation | Step 1.2–1.5 |
| current app may not be kept unchanged; conflicts must be resolved process by process | Step 1.3–1.4 and cross-process audit |
| graph needs memory/history but not a large graph platform | Step 2.8 and traceability decision |
| graph is stored in PostgreSQL/application database | Step 2 introduction and 2.1/2.8 |
| categories, subcategories, and conditional sub-subcategories | Step 2.2–2.3 |
| graph attributes go to researchers together | Step 2.3 and 6.2 |
| graph relationships and duplicate control | Step 2.4, 2.6, 2.8, and 7.3 |
| separate graph for each case | Step 2.1 |
| planner decides only categories needed by the story | Step 2.2 and 3.5 |
| visual planner decides maps | Step 3.7, 5.2, and traceability decision |
| no broad picture/video search before story writing | Step 3.1, 6.1, and traceability decision |
| YouTube research/transcripts are reused, not searched again | Step 6.1 and traceability decision |
| inspect True-Crime channels and their original references | Step 6.1 and 6.3 |
| Brave/search provider is discovery, not a rights guarantee | Step 6.3 and traceability decision |
| no blind archive-then-extra researcher sequence | traceability decision |
| parallel researchers are coordinated and deduplicated | Step 6.6 and traceability decision |
| planner sets requirement-level search/result caps | Step 3.4, 6.6, and traceability decision |
| exactly two purposeful extra rounds per unmet requirement | Step 6.6 and traceability decision |
| one consolidated shortage report at the end | Step 10.2 and traceability decision |
| owner suggestions are defined | Step 10.3 and the owner-intervention list |
| owner can inspect/review a candidate but need not approve every asset | Step 5.8, 10.2, and owner-intervention list |
| planner cannot stretch still duration to hide a shortage | Step 3.4, 9.5, 10.4 |
| flexible film-wide visual balance | Step 3.6 and 10.5 |
| no generated photos/animations to fill gaps | Non-negotiables, 5.5, and Step 6 |
| detective board is required as a possible built-in tool | Step 5.1 |
| existing maps/boards/timelines/documents must be coordinated, not duplicated | Step 5 and 9.6 |
| old visual agents/prompts/inspectors are replaced | Step 1.3, 8 introduction, 12.6 |
| factual research and story writing remain protected | Non-negotiables and Step 1 |
| Host is finished first and its outputs are available to planner | Step 4.1–4.3 |
| Host planner decides appearances; Main Visual Planner uses them | Step 4.2–4.3 and Host decision |
| Host text/order/results cannot be changed by visual planner | Step 4.2–4.3 |
| Host is independent and visual coverage still covers full narration | Step 4.5 and Host decision |
| music planning and Audio Director are preserved | Step 1, 9.7, and music decision |
| strict English-first production | Step 11.1–11.5 |
| saved EN/DE/FA/AR text and voices remain | Non-negotiables, 11.5–11.7 |
| Shorts only after all four films | Step 11.8 |
| €300 cap reset before implementation; historical cost preserved | Step 6.7, 10.6, 12.3 |
| AI video/voice/music disclosure | Step 7.6, 8.6, and AI-disclosure decision |
| backend/PostgreSQL/frontend/render tests and looped repair | Step 12.5–12.8 |
| old paths removed only after replacements are proven | Step 1.3, 12.6, and final checklist |

## Audit result

The file now contains the agreements recovered from the complete referenced conversation, including the corrections that changed earlier proposals. The most important final ownership rule is explicit: Host and Music remain authoritative systems, but their completed results are mandatory inputs to one Main Visual Planner master plan. The most important final research rule is also explicit: search is focused, graph-aware, coordinated, capped by requirement, and reported once at the end when a truthful shortage remains.
