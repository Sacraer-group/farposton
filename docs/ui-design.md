# UI design

## Journey

The primary task is to choose three Vladivostok addresses worth visiting. The four-stage decision flow and demo are specified in [decision-flow.md](decision-flow.md). Lead with location and address, then show the house context, costs and offer details; carry saved candidates into a consistent comparison and viewing questions. Swipe and visual similarity are secondary discovery tools.

A house passport is the intended place for building characteristics, management and utilities, outages and repairs. The current offer snapshot does not provide those facts. Show missing fields as unavailable and retain the source and date when such data becomes available. An offer's match to search conditions does not verify its house address.

## Presentation

The light interface uses a near-neutral `#f5f6f4` background, white surfaces, text `#23352d`, secondary text `#4e5a52`, deep green accent `#35664e`, muted text `#606c64` and borders `#dde2dd`. Unknown states use ochre `#92661f` on `#fbf3e3`; rejected states use `#a33c35`. Use VL.ru's observed font stack: locally hosted InterGlyphs for U+20BD, Arial for text, then the browser's sans-serif fallback. Arial is not bundled; the Inter subset includes its OFL license. Keep tabular price numerals. Page headings and prices use weight 500; captions and source notes are at least 12px (map-provider attribution is 11px). Controls have 8px corners and panels 16px. Keep controls labeled and usable with a keyboard; do not encode status by color alone.

The header's pressed-state «Тёмная тема» button selects light/dark mode. Apply the saved `bereg.theme` preference before the stylesheet loads; otherwise use and follow the system preference. Browser storage failures must not prevent toggling. The dark palette uses `#141715` for the page, `#1c201e` for surfaces, `#edf0ed` for primary text and `#a8d5b6` for the accent, with separate readable secondary and semantic colors. Print always uses the light palette.

The header retains journeys, advisor, catalog, favorites and secondary swipe navigation. Tablet navigation occupies a separate row; mobile links use two columns so every destination remains visible without horizontal scrolling. On wide screens the catalog defaults to cards alongside a sticky map, with list-and-map, list-only and map-only controls. On narrow screens, switch between list and map. Detail pages lead with address and photos, followed by offer-specific costs and house sections; address verification and unavailable utility, outage and repair data remain explicit. Favorites supports selecting up to five offers and a sticky comparison action; comparison groups fields and includes a map. Favorites and swipe cards provide photo arrows that do not change save or skip decisions. A confirmed house passport appears inside the flat page with the same source links and dates: house characteristics and management are visible, while long outage and repair sections can be expanded. Outage pagination stays on the flat page.

Maps use only the official 2GIS MapGL JS API with native vector maps, zoom controls and provider attribution. Leaflet, its vendored assets, raster tiles and related styles are removed. Both catalog/detail/comparison maps and the journey point picker use `flats/static/map-sdk.js`; templates share SDK loading through the `map_scripts` macro. `FLATS_2GIS_MAP_KEY` needs Map Tiles API access and is intentionally exposed to the browser; do not expose the independent server routing key.

Preserve house grouping, filtering, reset-to-bounds, list/map switching, listing-to-marker focus and safe DOM-built links. Price markers are keyboard-accessible buttons. Hover or focus opens a compact preview with the offer’s cover, monthly price, characteristics, address and detail link; grouped house markers keep offers separate in a bounded scroll area, and missing or failed photos show a placeholder. Popups can be dismissed with Escape or their close button, returning focus to the marker. Map coordinates use longitude/latitude order; the manual form remains latitude/longitude. Selecting a new point clears confirmation. Missing keys, unsupported WebGL, failed SDK/style loading or a 15-second initial loading timeout retain address links or manual coordinates rather than switching providers. A later successful idle event restores map readiness. SDK and vector tiles need network access and consume the provider's quota. The templates, `flats/static/app.css` and `flats/static/houses.css` are the implementation source of truth.

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

Verification: 461 Python tests and 32 Node tests passed. Native 2GIS vector maps
loaded with the existing demo key in the catalog, flat, comparison and journey
point picker. Chromium checks covered grouping, skip/undo synchronization,
keyboard popup opening/closing, return of focus, listing-to-marker focus, manual
coordinate selection and confirmation reset. Catalog, flat and comparison maps
were checked in both themes at 320, 390 and 1440px, including list/map switching.
With the SDK blocked, address lists and manual coordinate entry remained usable.
No Leaflet, legacy raster tile requests or uncaught JavaScript errors were
observed. Native-map production rollout and paid routing were not run.
The wheel build passed with `map-sdk.js`, without vendored Leaflet or the old
raster helper; the demo map key is absent from the wheel and Git diff.
