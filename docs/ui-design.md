# UI design

## Journey

The primary task is to choose three Vladivostok addresses worth visiting. The four-stage decision flow and demo are specified in [decision-flow.md](decision-flow.md). Lead with location and address, then show the house context, costs and offer details; carry saved candidates into a consistent comparison and viewing questions. Swipe and visual similarity are secondary discovery tools.

A house passport is the intended place for building characteristics, management and utilities, outages and repairs. The current offer snapshot does not provide those facts. Show missing fields as unavailable and retain the source and date when such data becomes available. An offer's match to search conditions does not verify its house address.

## Presentation

The light interface uses a near-neutral `#f5f6f4` background, white surfaces, text `#23352d`, secondary text `#4e5a52`, deep green accent `#35664e`, muted text `#606c64` and borders `#dde2dd`. Unknown states use ochre `#92661f` on `#fbf3e3`; rejected states use `#a33c35`. Use VL.ru's observed font stack: locally hosted InterGlyphs for U+20BD, Arial for text, then the browser's sans-serif fallback. Arial is not bundled; the Inter subset includes its OFL license. Keep tabular price numerals. Page headings and prices use weight 500; captions and source notes are at least 12px (map-provider attribution is 11px). Controls have 8px corners and panels 16px. Keep controls labeled and usable with a keyboard; do not encode status by color alone.

The header's pressed-state «Тёмная тема» button selects light/dark mode. Apply the saved `bereg.theme` preference before the stylesheet loads; otherwise use and follow the system preference. Browser storage failures must not prevent toggling. The dark palette uses `#141715` for the page, `#1c201e` for surfaces, `#edf0ed` for primary text and `#a8d5b6` for the accent, with separate readable secondary and semantic colors. Print always uses the light palette.

The header retains journeys, advisor, catalog, favorites and secondary swipe navigation. Tablet navigation occupies a separate row; mobile links use two columns so every destination remains visible without horizontal scrolling. On wide screens the catalog defaults to cards alongside a sticky map, with list-and-map, list-only and map-only controls. On narrow screens, switch between list and map. Detail pages lead with address and photos, followed by offer-specific costs and house sections; address verification and unavailable utility, outage and repair data remain explicit. Favorites supports selecting up to five offers and a sticky comparison action; comparison groups fields and includes a map.

Maps retain Leaflet markers, grouping, popups, filtering and keyboard controls, but use 2GIS Raster Tiles API (`online_sd`) with 2GIS attribution. Both catalog/detail/comparison maps and the journey point picker use `flats/static/map-tiles.js`. `FLATS_2GIS_MAP_KEY` is intentionally exposed to the browser; do not expose the independent server routing key. Missing keys or tile failures retain address links or manual coordinates rather than silently switching providers. Tiles need network access and consume the provider's quota. The templates, `flats/static/app.css` and `flats/static/houses.css` are the implementation source of truth.

## Unslop pass

The pass starts from the current Bereg interface, not the earlier Flats redesign. Direction: a considered local rental shortlist, grounded in addresses, weekly journeys and verifiable sources. Keep the name, green accent, system typeface, section order and features. Photos and the map carry visual richness; chrome stays quiet. Pair important figures with unknown conditions and source context.

- Spacing: regularized shared panels, cards, forms and section gaps around the 4px rhythm.
- Color: quieter navigation, callouts and auxiliary surfaces; darker secondary text.
- Icons: retained the existing Lucide sprite and removed redundant heading decoration.
- Copy: shorter headings and captions, no decorative heading periods or numbered comparison categories. Actual workflow steps retain their numbers.
- Type: reduced heavy headings and figures; enlarged small labels across catalog, journeys, advisor, comparison and house passport.
- Layout: retained app proportions and map/list modes; repaired clipped tablet/mobile navigation.
- Details: consistent control corners, styled journey scope selector, visible photo-search labels on mobile and a focusable skip-link destination.
- Visuals: retained real listing photos, maps and explicit unavailable-data states; no generated images or invented product data.
- Motion: removed photo zoom and hover lift; retained purposeful swipe motion and consolidated reduced-motion handling.

Methodology: [unslop](https://github.com/heydamir/unslop) by Damir Sokolovsky ([@heydamir](https://x.com/heydamir)), CC BY 4.0.

Verification: 448 Python tests and 21 Node tests passed. Local Chromium checks covered catalog, flat, favorites, comparison, swipes, journeys, advisor and an imported house passport at 320, 390, 768 and 1440px. No page overflow, clipped main navigation, interface captions below 12px or uncaught JavaScript errors were observed. Browser flows passed for save/reload, comparison, manual place entry, schedule saving, quota preflight, source-based recommendations, swipe/undo, empty states and reduced motion. Screenshots were reviewed for desktop and mobile. Paid route execution, configured LLM synthesis and production deployment were not checked.

Subsequent map/font/theme verification: 450 Python tests and 28 Node tests passed.
Live 2GIS tiles loaded in all four map locations, and the local InterGlyphs font
loaded for the ruble sign. Both themes were checked on all eight screens at 320,
390, 768, 1024 and 1440px, without page overflow, clipped main navigation or
uncaught JavaScript errors. Keyboard toggling, reload persistence, system dark
preference, light print styling and text/accent contrast were checked in Chromium.
No OpenStreetMap tile requests were observed. Production deployment was not run.
The wheel build passed and includes the font, its license and both new scripts;
the demo map key is absent from the wheel and Git diff.
