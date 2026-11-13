# Rules for executor agents

Read this file, `docs/contracts.md`, `docs/constraints.md`, and your task block. Nothing else is required.

## Doing a task

1. `git pull --rebase origin main`.
2. Touch **only** the files listed in your task's "Owns" line. If you need another file changed, stop and report it — do not edit it.
3. Follow signatures and rules in `docs/contracts.md` exactly. Do not rename, add parameters, or change return types.
4. No new dependencies except those in your task. No abstractions, options, or features not in the task.
5. Write the tests listed in the task. Use `pytest`. No network in tests: pass fake `http`/`sleep` objects or fixture dicts.
6. Run the acceptance command from the task. It must pass. Paste its real output in your report.
7. Commit with an English message `S<x>-T<y>: <what>`, then `git pull --rebase origin main` and `git push origin main`. On rebase conflict in a file you do not own: stop and report.

## Never

- Commit `METRAPI_KEY`, `data/`, `.env`, photos, model weights.
- Print the API key in logs or errors.
- Turn unknown values into `0`, `False`, or `True`.
- Mix conditions of different offers in one calculation.
- Use `| safe` or `innerHTML` on listing text (XSS).

## Report format

```
Task: S<x>-T<y>
Changed: <paths>
Check: <command> -> <real result>
Blockers / contract issues: <none or details>
```

## Setup (after S0-T1 is merged)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```
