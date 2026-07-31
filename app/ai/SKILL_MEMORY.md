# Orderly skill memory (internal)

**Not visible to vault owners.** No sidebar / dashboard UI.

## What runs today

On each successful document fill (`AI_LEARNING_ENABLED=true`):

1. OCR/PDF text + section patch + prompts/decisions → `ai_skill_examples` (MongoDB)
2. Similar past examples may be injected as few-shot hints (invisible to user)
3. gpt-4o-mini remains the production fill brain

## Mongo collections

| Collection | Purpose |
|------------|---------|
| `ai_skill_examples` | Training corpus (`orderly_skill_v2`) |
| `ai_brain_settings` | Reserved for future admin toggles |

## Admin panel (later)

- Mount `app.ai.ai_brain_routes` in `main.py` with **admin auth only**
- Routes already exist: `GET/PUT /ai/brain/settings`, `GET /ai/brain/skill-export`
- Do **not** expose skill stats or export to owner JWT

## Own model (later)

Fine-tune on exported `train.messages`, then:

```env
AI_PROVIDER=own
OWN_MODEL_BASE_URL=...
OWN_MODEL_NAME=orderly-fill-v1
```
