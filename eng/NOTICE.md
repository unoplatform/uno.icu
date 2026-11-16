# Uno ICU package notices

This package combines distinct works; the Uno license does not relicense ICU.

* ICU native libraries and ICU/CLDR data: the **complete upstream LICENSE**,
  including its third-party notices, is reproduced verbatim in
  `licenses/ICU-LICENSE.txt`. It comes from ICU 77.1 commit
  `457157a92aa053e632cc7fcfd0e12f8a943b2d11`, not an inferred upstream
  version matching the NuGet package number.
* Uno build/packaging code and the WebAssembly shim: see
  `licenses/Uno-LICENSE.md`, copied from the identified Uno ICU source.
* `LICENSE.txt` combines both complete texts for NuGet's file-license display.
  Keeping all upstream notices is intentional even when a platform build or
  data filter excludes some components. This is not a license waiver or an
  assertion that every optional ICU component is shipped.

`provenance/source.json` identifies the source archive, its SHA-256, the Uno
commit, filter/shim/build input hashes and the build run. The adjacent payload
inventory binds those declarations to individual package entries. These are
build metadata, not themselves a cryptographic attestation or an assertion of
byte-for-byte reproducibility.

Release evidence must also contain a verified GitHub build-provenance
attestation for the **final signed nupkg bytes** and the external signed-package
inventory. NuGet repository signing can change the nupkg archive on ingestion:
compare downloaded bytes and retain their signature verification and payload
comparison separately. A package tag, version string or aggregate artifact
digest alone does not prove exact native provenance.
