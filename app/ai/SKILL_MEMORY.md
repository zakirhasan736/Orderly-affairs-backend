# Orderly skill memory (internal)

**Not visible to vault owners.** No sidebar / dashboard UI.

## What runs today

On each live document run (`AI_LEARNING_ENABLED=true`) we store **three skills** in MongoDB `ai_skill_examples` (`orderly_skill_v3`):

| Task | What the future own model learns |
|---|---|
| `document_ocr_prepare` | OCR is primary. Quality good → Sol text. Quality bad → Terra vision text → Sol. |
| `section_classify` | Topic + correct vault section / additional sections from prepared text. |
| `section_field_fill` | Semantic label matching onto exact catalog keys, subsections, structured patch. |

Each row includes:

* prepared document text (SSN/card redacted)
* field catalog keys + labels
* OCR quality / Terra vs Sol path
* requested vs detected section
* filled subsections and field keys
* confidence band (high auto-fill after validation / medium review / low do not silent-commit)
* `train.messages` chat triples for fine-tuning

Similar past **fill** examples may be injected as few-shot hints (invisible to the owner).

GPT-5.6 Sol is the production fill brain (Terra vision only when OCR is bad).

## Mongo collections

| Collection | Purpose |
|------------|---------|
| `ai_skill_examples` | Training corpus (`orderly_skill_v3`) |
| `ai_brain_settings` | Reserved for future admin toggles |

## Admin panel (later)

- Mount `app.ai.ai_brain_routes` in `main.py` with **admin auth only**
- Routes already exist: `GET/PUT /ai/brain/settings`, `GET /ai/brain/skill-export?task=section_field_fill`
- Do **not** expose skill stats or export to owner JWT

## Own model (later)

Fine-tune on exported `train.messages`, then:

```env
AI_PROVIDER=own
OWN_MODEL_BASE_URL=...
OWN_MODEL_NAME=orderly-fill-v1
```
