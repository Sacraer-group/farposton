# VL.ru font stack

Observed on https://www.vl.ru/ on 2026-11-14:

- Main text: `Arial, sans-serif` in `/assets/css/styles.9681e9c7f1d06e03ced7.css`.
- Ruble sign: `InterGlyphs, Arial, sans-serif` with `unicode-range: U+20BD`.
- Font CSS: https://www.vl.ru/font/InterGlyphs.css
- Unmodified font file: https://www.vl.ru/font/InterGlyphs.woff2 (2104 bytes).

The font's embedded metadata credits the Inter Project Authors and links to
https://openfontlicense.org. `LICENSE.txt` is the Inter project's SIL OFL 1.1
license from https://github.com/rsms/inter/blob/master/LICENSE.txt.

Arial is not bundled or downloaded. It is used when installed on the visitor's
device; otherwise the browser's sans-serif fallback is used. InterGlyphs provides
the ruble sign only, not the Russian or Latin alphabet.
