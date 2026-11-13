# Product brief: choose a rental worth visiting

Help a person select 3–5 long-term rentals in Vladivostok for a viewing, based on their conditions, interior taste, known offer-specific monthly and move-in costs, house evidence and uncertainty, and location. The outcome is a shortlist with understandable tradeoffs and viewing questions, not just a catalog.

The flow is: describe a scenario and constraints → filter candidate offers → inspect each apartment and its house evidence → compare and shortlist → take targeted questions to viewings. Keep offer, apartment and house identities separate; costs come from one offer and unknowns never become zero or false certainty. Link observations to their source and date, show source conflicts and missing coverage, and avoid inferring resident problems from missing records.

A map helps inspect location. Journey, slopes, stairs, wind exposure, parking and daylight are checks or questions unless supported by specific evidence. Do not invent travel times, geographic facts or a score. Interior similarity and swipes remain supporting tools. A concise recommendation assistant may later use stored offer and house evidence in shortlist/comparison after deterministic filters and cost calculation, preserving sources and unknowns; it does not require a vector database or external data upload.

## Current gap and next slice

The v1 baseline and boundaries are documented in [architecture](architecture.md). The open [draft PR 38](https://github.com/Sacraer-group/farposton/pull/38) redesigns list, map, flat, favorites, comparison and swipe screens, including unknown house sections and questions; it is not merged or deployed as part of this decision. The collected [house dataset](../research/house-passports/README.md) is not integrated into the application. The useful next slice connects the frozen evidence to the correct house and offers, exposes provenance and uncertainty on the flat and comparison views, and turns gaps into viewing questions. See the [house passport proposal](house-passport/README.md). Its SQL and import descriptions are proposals, not completed application work.
