# Prompt 9E Part 0 — configuration boundary

Inspected parent: `b881844fe05b1e3fa53af6b59bd85299e6f4ae40`.
Production does not send `production_policies` (or its `incremental` section)
as a provider request. The concrete boundary builders are:

| Provider/effect | Builder and call | Config transmitted |
| --- | --- | --- |
| Generation | `app/providers/generation/openai_compat.py:169-195` | selected model, temperature, max_tokens, seed; system/user/images, provider extra_body |
| TTS | `app/providers/voice/elevenlabs.py:44-73,100-113` | text/model/voice settings/context/seed/language; voice ID in URL, output_format query |
| ASR | `app/documentary/asr.py:313-333` | language-scoped Whisper model/compute type; audio, language, explicit transcription options |
| Music | `app/providers/voice/elevenlabs.py:171-193` | text, duration, influence, loop/model; output_format query |
| Image search | `app/agents/visual_research.py:31-37,88-90`; `app/documentary/visuals/research.py:142-155,199-201` | provider URL, query/limit and explicit search parameters; timeout/User-Agent |
| Geocoding/tiles | `app/documentary/visuals/maps.py:249-252,294,310` | URL, q/format/limit, tile coordinates, User-Agent |
| Render | `app/documentary/render/engine.py:559-586` | ffmpeg arguments from render dimensions/fps/preset/crf and actual media inputs |

No production change is needed at these boundaries. The test LLM hashes the
whole `ai_config` object, broader than the production body; its incremental
exclusion is retained with this distinction documented in `tests/mock_payload.py`.
`test_incremental_policy_is_not_a_fake_generation_input` checks that exclusion.

Correction to the previous evidence: there was **no** ASR-specific canonicalizer
in the snapshot helper. The prior test sorted complete request payloads, which
could hide swapped block associations. `saved_request_snapshot` now keys ASR
requests by provider/stage/language/block/retry seed, then hashes the payload.
The independent full-run test uses this function. It sorts only these logical
keys, never payload/output values. `test_asr_snapshot_orders_by_block_identity_not_payload`
proves both reorder tolerance and detection of exchanged payloads. This request
snapshot is diagnostic, separate from saved artifact snapshots; no diagnostic
request hash is used to promote artifact soundness.
