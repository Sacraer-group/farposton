# UI design

## Journey

The primary task is to choose three Vladivostok addresses worth visiting. The four-stage decision flow and demo are specified in [decision-flow.md](decision-flow.md). Lead with location and address, then show the house context, costs and offer details; carry saved candidates into a consistent comparison and viewing questions. Swipe and visual similarity are secondary discovery tools.

House passports show imported, dated evidence about building characteristics, management, outages and repairs when available. Show missing fields as unavailable and retain the source and date. An offer's match to search conditions does not verify its house address.

The advisor starts with a free-text task and an editable structured form. Suggested filters are reviewed and explicitly applied; the local draft remains in the browser.

## Presentation

The redesign uses a warm `#f4f5f1` background, white surfaces, text `#23352d`, deep green accent `#35664e`, muted text `#6d7972` and borders `#e0e4dd`. Unknown states use ochre `#92661f` on `#fbf3e3`; rejected states use `#a33c35`. Use the system UI font stack, tabular price numerals, 14–18px corners and restrained shadows. Keep controls labeled and usable with a keyboard; do not encode status by color alone.

The compact header prioritizes search and favorites, with swipe secondary. On wide screens the catalog defaults to cards alongside a sticky map, with list-and-map, list-only and map-only controls. On narrow screens, switch between list and map. Detail pages lead with address and photos, followed by offer-specific costs and house sections; address verification and unavailable utility, outage and repair data remain explicit. Favorites supports selecting up to five offers and a sticky comparison action; comparison groups fields and includes a map.

The map uses Leaflet with OpenStreetMap tiles and attribution; tiles need a network connection. The templates and `flats/static/app.css` are the implementation source of truth. For current verification and remaining browser review see [state.md](state.md).
