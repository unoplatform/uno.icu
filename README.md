# Uno.icu

ICU ([International Components for Unicode](https://icu.unicode.org/)) builds used by Uno Platform's text layout for bidirectional text and line, word and character breaking (`ubidi_*`, `ubrk_*`).

| Package | Contents |
|---|---|
| `Uno.icu-wasm` | `unoicu.a`, linked into the WebAssembly runtime, and the ICU data |
| `Uno.icu-macos` | `libicuuc`/`libicudata` dylibs and the ICU data |
| `Uno.icu-win` | ICU DLLs for x64 and arm64, and the ICU data |
| `Uno.icu-ios`, `Uno.icu-tvos` | static ICU libraries |

## ICU data

The data is filtered (`src/cldr_data/filters.json`), then the locale tree and converter aliases the ICU build tools need are removed, leaving what text layout uses: the break iterator rules and dictionaries, the emoji and layout properties. The WebAssembly, macOS and Windows packages embed it in the app head.

| File | Contents |
|---|---|
| `icudt.dat` | All of the filtered data (embedded by default) |
| `icudt.core.dat` | Everything but the break iterator dictionaries |
| `icudt.dictionaries.dat` | Only the dictionaries |

The dictionaries are most of the data: they hold the words of Chinese, Japanese, Thai, Lao, Khmer and Burmese, which are written without spaces between words. An app that doesn't display those languages can leave them out:

```xml
<PropertyGroup>
    <UnoIcuDictionaries>None</UnoIcuDictionaries>
</PropertyGroup>
```

Line breaking then still follows the Unicode rules for every script. Chinese and Japanese lines break as before, but Thai, Lao, Khmer and Burmese lines break at arbitrary characters instead of between words, and word selection in all six languages works per character.

## Building

The CI workflow (`.github/workflows/main.yml`) builds everything. The ICU data alone can be built with Docker:

```bash
cd src/cldr_data
docker build --build-arg ICU_SOURCE_ZIP_URL=https://github.com/unicode-org/icu/archive/refs/tags/release-77-1.zip --output type=local,dest=. .
```
