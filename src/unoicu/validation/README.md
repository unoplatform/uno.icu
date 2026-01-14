# unoicu validation assets

This folder contains the small C and JavaScript harnesses the CI pipeline uses to validate the generated `unoicu.a` archive. The sources are copied into a temporary build directory inside the `validate_unoicu` GitHub Actions job, compiled with Emscripten, and exercised in headless Chromium via Puppeteer.
