# Decision flow and demo

## Promise

Help a renter choose three Vladivostok addresses to visit, understanding monthly and move-in cash, house evidence and route uncertainty. The design combines existing listing information with a bounded evidence-backed advisor; it does not establish a unique market claim. FarPost already offers cost filters, and Vladivostok sources already publish outages and managing-company information.

## Four stages

| Stage | Purpose and next action | Unknown or error state |
| --- | --- | --- |
| 1. «Что для вас важно?» | Set budget, requirements and priority; find candidates. | Missing requirements remain unset. If no matches, adjust conditions; do not silently weaken them. |
| 2. «Выберите три адреса» | Review eligible candidates, offer costs and map; select candidates to check. | Fewer than three candidates is a valid result; explain missing offer terms and allow changing conditions. |
| 3. «Проверьте дом и сравните» | Compare offer-specific costs, source-backed house evidence and uncertainty; inspect each source and date. | Address not verified is distinct from offer eligibility. An index or dated snapshot is not live status; missing house records and route time remain unknown. Source failure must not become a clean bill of health. A route link opens Yandex with the destination; the user chooses origin and mode there. The app does not predict minutes or slope. |
| 4. «Подготовьтесь к просмотру» | Produce a concise viewing brief and questions grounded in selected offers and missing facts. | Show missing answers as questions; do not invent details. If advisor is unavailable, retain the comparison and evidence-based retrieval path. |

The advisor retrieves at most five actual candidates using existing eligibility, costs and lexical matching. With a configured provider, it can select only supplied candidate, fact and question IDs; the server renders the facts and rejects unknown or cross-offer IDs. Without a provider it must identify its response as retrieval, not a model-generated answer. Candidate eligibility, evidence and source dates remain visible to the user.

## Three-minute hackathon demo

1. **0:00–0:30:** State the decision: choose three addresses with a realistic cash picture and clear unknowns. Enter a budget and priority.
2. **0:30–1:15:** Show the candidate list and map; select three, or explain if fewer are available. Point to monthly versus move-in costs.
3. **1:15–2:15:** Compare the selected offers. Open a source-backed house record when available, identify its source/date, and distinguish a missing record or unavailable live status from a verified finding. Explain that route time is not calculated.
4. **2:15–3:00:** Ask the advisor for a recommendation and open the viewing questions. Identify whether the response uses a configured model or retrieval fallback; show evidence and an unresolved question.

Use a prepared local snapshot and a prechecked example. If the provider or map tiles fail, demonstrate the explicit fallback and retain the source links; do not imply a live check succeeded.

## Acceptance targets and scope

Targets for a later observed demo, not measured results: complete the four-stage path within three minutes; show three candidates when available; trace every factual recommendation to its offer or dated source; surface unknown costs, address verification, house and route status; keep the viewing brief usable when the model provider is unavailable. Test rejection of unknown and cross-offer IDs and the no-provider fallback. Validate clarity with a person and record actual timing and failures.

For this slice, omit route calculation, live provider integrations for house data, accounts, scraping, autonomous browsing and long reports. Do not add a vector database or schema merely for the advisor.
