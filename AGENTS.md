# Forecast development

- Work only in this repository; preserve concurrent edits. Never access client sources or deploy without a separate request.
- Start with README.md and Git status. Use codebase-memory for structural questions, check coverage; read source when this nested repository is not indexed. Do not build another index unnecessarily.
- Python 3.12, dependencies exclusively web/requirements.txt. Use `make setup`, `make test`; no new framework or service for local work.
- Local synthetic data: `make demo-prepare` once, then `make demo-check`, `make demo`. Everything generated belongs in ignored `.demo/`; never overwrite an existing fixture, use live paths, or commit secrets/runtime data.
- `make demo-ml` explicitly runs packaged HGB inference and a bounded synthetic standalone CatBoost. Missing stacked_guard weights must stay visible; never present fallback as the requested model.
- Preserve issue−1 information boundaries and lag 3/4/5 for D+2/D+3/D+4. Manual edits are not demand labels. Synthetic accuracy is not client accuracy.
- Verify changed behavior with existing tests and actual local scenarios. Keep one continuation/status record in README.md; distinguish source review, passing runtime checks, missing artifacts and untested integrations.
- Never read, print, copy or send the 2GIS API key. OpenCode configuration loads it from `~/.config/farposton/2gis_api_key`; avoid `opencode debug config`, which can expand secrets. Do not persist 2GIS responses or train on them before the applicable terms are confirmed.
