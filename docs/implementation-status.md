# Bereg implementation status

The current working tree combines the merged interface, house import, weekly trips and judging-readiness work with a decision assistant. Users can enter a Russian-language task or edit the structured form, including districts. Text parsing proposes explicit filters with snippets and notes for review; it is deterministic and does not call a model. The advisor uses imported house passports, available completed weekly trip results and offer-specific costs. Its optional model may select only supplied IDs; without a provider it identifies the retrieval fallback. The local form draft and browser decisions are separate on each browser or device.

Import both offers and the house snapshot as shown in [README](../README.md). House evidence is dated, incomplete and not live service status. The trip flow offers a non-mutating quota estimate and an explicitly selected saved shortlist before calculation. Route jobs are memory-only and subject to budgets. Public visitor isolation and CSRF protections remain in place.

Earlier upstream records report public release `8983ee9` through CI run `43532864183`, four live public routing results and cancellation, browser isolation and restart preservation. They separately report a real-provider run on an isolated database copy: five places, six legs, five weekdays and three flats produced 90 successful dated legs with 50 unique queries. [Recorded evidence](judging/live-weekly-check.json) specifies its scope; it was not repeated in this session. Places returned meta 403 with the tested key, so pin selection remains available; quota and retention permissions remain unconfirmed.

The [judging materials](judging/README.ru.md) include the rubric, source checks, seven-minute defense, AI evidence and an unfilled user-validation protocol. Upstream reported that its own Slides deck was not authored because reauthentication was required. No user-study outcome is claimed.

Current verification and pending checks are in [state](state.md). No live LLM provider has been configured or verified. Earlier production inspection recorded a separate Forecast service and an older preview; preserve and back up the existing flats volume before deployment. These historical observations do not verify the current deployment.

## User-flow QA follow-up 2026-11-14

Scope: select 3–5 real apartments through catalog/map, swipes/photo similarity, comparison and a viewing brief, on desktop and 360/390px. Work isolated in fix/userflow-20261114: initial fixes on b883238, then merged current main9bdd76f preserving guided requests, gallery/contact and quick trips.

Confirmed fixes: popup geometry after MapGL paint/idle with accessible close and keyboard entry/escape; named mobile swipe navigation; focus to current successful advisor results. No data, credentials or route-provider mocks shipped.

Checks on integrated code:594Python tests passed,1optional browser module skipped locally;46JavaScript tests passed. Separate headless browsers exercise actual HTML/API. Public quick-trip checks confirmed duplicate-time validation, save/reload and anonymous isolation; full-catalog estimate stopped before paid routing. CI with the browser extra and the post-release public repeat are the remaining release gates.
