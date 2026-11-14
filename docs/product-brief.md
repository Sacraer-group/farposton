# Product brief: choose a rental worth visiting

Help a person select 3–5 long-term rentals in Vladivostok for a viewing, based on conditions, interior taste, offer-specific monthly and move-in costs, house evidence and uncertainty, and location. The outcome is a shortlist with understandable tradeoffs and viewing questions, not just a catalog.

The flow is: describe a scenario and constraints → filter candidate offers → inspect each apartment and its house evidence → compare and shortlist → take targeted questions to viewings. Keep offer, apartment and house identities separate; costs come from one offer and unknowns never become zero or false certainty. Link observations to their source and date, show source conflicts and missing coverage, and avoid inferring resident problems from missing records.

A map helps inspect location. Journey, slopes, stairs, wind exposure, parking and daylight are checks or questions unless supported by specific evidence. Do not invent travel times, geographic facts or a score. Interior similarity and swipes remain supporting tools. The bounded advisor uses offer and imported house evidence after deterministic filters and cost calculation, preserving sources and unknowns.

## Current implementation and next validation

The user can describe a task in Russian and review deterministic filter suggestions before applying them to the editable advisor form, or enter conditions directly. The advisor uses repository house passports and any available previous weekly trip results. The snapshot is imported separately from offers; missing records remain unknown. The earlier [house passport proposal](house-passport/README.md) is historical design material. See [implementation status](implementation-status.md) and [judging evidence](judging/README.ru.md) for checks, unresolved gaps and the seven-minute defense. Usefulness with real renters remains to be measured.
