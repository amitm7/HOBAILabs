# Caption fonts

Fonts here are baked into the Docker image (`COPY deploy/fonts → /usr/share/fonts/...`
+ `fc-cache`) so libass can render them by family name in captions.

## Bundled (free / OFL — safe to ship)
- **Montserrat** (`Montserrat-Regular.ttf`, `Montserrat-Italic.ttf`) — SIL Open
  Font License, see `OFL-Montserrat.txt`. Family name libass uses: `Montserrat`.

## Drop-in (licensed — NOT bundled)
- **Satoshi** — by the Indian Type Foundry. Free for personal use; **commercial /
  hosted use needs a license**. When you have it, drop `Satoshi-Regular.ttf` (and
  any weights) into this folder and rebuild the image. The "Satoshi" dropdown
  option then renders correctly; until then it falls back to the system default.

## Local (macOS) dev
libass looks up fonts by the family name the UI sends (e.g. `Montserrat`). If a
font isn't installed locally, install it (e.g. `brew install --cask font-montserrat`)
or it will fall back. The Docker image is the source of truth for hosted renders.

To add another font: drop its `.ttf` here, rebuild, and add an `<option>` to the
caption-font dropdown in `web/templates/index.html` whose value is the font's
exact family name.
