# UI design

## Journey

The primary task is to narrow Vladivostok rentals to 3–5 candidates worth visiting. Lead with location and address, then show the house context, costs and offer details; carry saved candidates into a consistent comparison and viewing questions. Swipe and visual similarity are secondary discovery tools.

A house passport is the intended place for building characteristics, management and utilities, outages and repairs. The current offer snapshot does not provide those facts. Show missing fields as unavailable and retain the source and date when such data becomes available. An offer's match to search conditions does not verify its house address.

## Presentation

The redesign uses a warm `#f4f5f1` background, white surfaces, text `#23352d`, deep green accent `#35664e`, muted text `#6d7972` and borders `#e0e4dd`. Unknown states use ochre `#92661f` on `#fbf3e3`; rejected states use `#a33c35`. Use the system UI font stack, tabular price numerals, 14–18px corners and restrained shadows. Keep controls labeled and usable with a keyboard; do not encode status by color alone.

The compact header prioritizes search and favorites, with swipe secondary. On wide screens the catalog defaults to cards alongside a sticky map, with list-and-map, list-only and map-only controls. On narrow screens, switch between list and map. Detail pages lead with address and photos, followed by offer-specific costs and house sections; address verification and unavailable utility, outage and repair data remain explicit. Favorites supports selecting up to five offers and a sticky comparison action; comparison groups fields and includes a map.

The map uses Leaflet with OpenStreetMap tiles and attribution; tiles need a network connection. The templates and `flats/static/app.css` are the implementation source of truth. Automated checks passed; visual and responsive browser verification remains outstanding (see [state.md](state.md)).
