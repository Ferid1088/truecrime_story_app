# TrueCrime Story Studio

یک اپلیکیشن پایتونی برای:

- پیدا کردن پرونده‌های جدید که قبلاً روی آن‌ها کار نشده
- ذخیره‌ی تاریخچه‌ی پرونده‌ها در دیتابیس
- جمع‌آوری و ثبت منابع جدا از متن داستان
- استخراج فکت‌ها و تناقض‌ها
- ساخت «پرونده‌ی تحقیق»
- طراحی زاویه‌ی روایی
- نوشتن داستان جدید و جذاب
- بازبینی جذابیت و شباهت
- خروجی نهایی: فقط متن داستان
- نگهداری منابع، فکت‌ها، تناقض‌ها و نسخه‌ها به‌صورت جداگانه در دیتابیس

## معماری

```text
OpenRouter Research Provider (web search + fetch, single gateway)
        ↓
Topic Discovery Agent / Deep Research
        ↓
Case Registry / Database
        ↓
Sources → Facts → Contradictions
        ↓
Story Director
        ↓
Writer Agent
        ↓
Engagement Critic
        ↓
Similarity Critic
        ↓
Final Story Text
```

## External research provider (OpenRouter)

OpenRouter is the single gateway for all web research — search, source
fetch, multilingual queries and YouTube discovery run through its
server tools (`openrouter:web_search`, `openrouter:web_fetch`). Devin
was removed from active workflows; historical ResearchJob rows with
provider="devin" remain readable data.

```text
app/providers/base.py    ResearchProvider interface + ProviderJob
app/providers/research_openrouter.py   OpenRouterResearchProvider
  (iterative loop: query plan → web_search → evaluate → web_fetch →
   refine → stop on low marginal value)
app/services/research_jobs.py   persistent ResearchJob lifecycle + ingestion
```

- `TrueCrime_OPENROUTER_API_KEY` in `.env` (backend-only; never sent to
  the frontend, logged, or stored in DB rows) — the only active AI secret.
- `GET /api/integrations/research/status` → `{provider, configured, reachable}`.
- `POST /api/topics/discover` and `POST /api/cases/{id}/research` return
  `{job_id, status}`; poll `GET /api/research-jobs/{job_id}` (or
  `GET /api/research-jobs?case_id=`).
- On completion, sources are stored as `Source` rows (provider metadata:
  `research_provider`, `external_reference`, `published_at`, `retrieved_at`,
  `summary`, `status`). `possible_facts`/`possible_contradictions` stay in the
  job result — unverified claims never enter the Fact layer directly; the
  Research Agent's extraction stage remains the fact path.
- If the key is absent the app falls back to the legacy LLM discovery /
  research path; provider failures return
  `503 "Research provider unavailable"` and leave stored data intact.

## نکته مهم

این پروژه عمداً «دانلود و بازنشر ویدئوی دیگران» را انجام نمی‌دهد.
برای یوتیوب، متادیتا و لینک‌ها را می‌توان جمع کرد، اما متن/ترنسکریپت باید از منبعی
استفاده شود که شما حق استفاده از آن را دارید یا به‌صورت مجاز در اختیار اپ قرار گرفته باشد.

## اجرا

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
uvicorn app.main:app --reload
```

سپس:

- API docs: http://127.0.0.1:8000/docs

## Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

The UI talks to the FastAPI backend at `http://127.0.0.1:8000` by default.
Override with `NEXT_PUBLIC_API_URL` in `frontend/.env.local` if needed.

Backend tests: `pytest tests/`

## Generation provider (OpenRouter)

OpenRouter is the generation/reasoning provider. Only the secret lives in
`.env`; every model and generation parameter lives in
`config/ai_config.json` — swapping a model means editing that one file.

```env
TrueCrime_OPENROUTER_API_KEY=...
```

`config/ai_config.json` defines three model aliases:

- `cheap` — `openai/gpt-5.6-luna`
  (structured extraction: facts, timeline, contradictions)
- `writer` — `google/gemini-3.8-flash`
  (story direction, long-form writing, rewriting)
- `premium` — `anthropic/claude-sonnet-5.5`
  (engagement critique, final editorial review)

Role routing (`fact_extractor`, `timeline_builder`, `contradiction_analyzer`,
`story_director`, `writer`, `rewriter`, `engagement_critic`, `final_editor`)
maps each agent role to an alias. If a routed model fails with a
provider-side error (rate limit, unavailable, timeout) the provider walks the
configured `fallbacks` chain for that alias and records `fallback_used` on the
AgentRun; the model that actually produced each output is recorded on the
AgentRun (provider/model/role/temperature) and StoryVersion
(`generation_provider`, `generation_model`).

- `GET /api/integrations/openrouter/status` → `{provider, configured,
  reachable, models, routing, fallbacks}`.

Agents never contain model IDs, temperatures, token limits or thresholds —
they ask `ai_config.model_for(role)` / `generation_for(role)`.

## Workflow پیشنهادی

1. `POST /api/topics/discover`
2. یکی از موضوع‌ها را انتخاب کنید.
3. `POST /api/cases`
4. منابع را با `POST /api/cases/{case_id}/sources` اضافه کنید.
5. `POST /api/cases/{case_id}/research`
6. `POST /api/cases/{case_id}/generate-story`
7. `GET /api/cases/{case_id}/story/latest`

خروجی آخر فقط متن داستان است.
