# Bundled annotation engine: redistribution review

Updated 2026-09-11 for the 0.6.2 Apple Silicon beta candidate.
**The identified Kent and source-delivery gaps have been addressed in the
revised engine and its local source companion.** The companion must accompany
the binary when published. This is an engineering compliance review, not a
blanket license grant or legal opinion; no public release has been published.

Public beta software is allowed. Beta naming, a free price, open-source
application code and Apple notarization do not replace third-party obligations.

## Revised artifact

- Image: `sha256:75a4f6d5fd846476c2ce1b057cf33f206a3b0109520e02067f91afee76779ae2`.
- Platform: Linux arm64, Ubuntu 22.04.5 LTS.
- Engine fingerprint: `6fa3d0c4f809cd01871e9f453f84363f3f719751fdb942924a17e005c590a4d0`.
- Inventory: **378 OS binary packages**, **243 OS source/version pairs**,
  **101 CPAN distributions** and **5 Python distributions**.
- Companion: `GUIDE-IEI-engine-sources-0.6.2-linux-arm64.tar.gz`,
  1,379,552,910 bytes (approximately 1.38 GB); 349 package source downloads,
  plus native source trees, restored build scripts and local modifications.
- Companion SHA-256: `8a15c825ba7d5cae7a145fcf747abc65f8a41b9eb186d868bf55c0e7990e51f6`.
- Full runtime inventory, source-file index, download receipts and layer
  verification are inside the companion. Its
  [identity manifest](releases/0.6.2-engine-source-manifest.json) is tracked here.

The [earlier inventory](releases/0.6.2-engine-inventory.json) is retained as
**baseline evidence for the old image**, not the current release inventory.

## What was changed and verified

### Kent / BigFile

The original image retained Kent `335_base`'s `jkOwnLib`, object archives and
unused graphics/alignment material. Its `jkOwnLib/README` permits personal,
academic and nonprofit use but requires agreement for commercial use.

The cleanup checks retained native ELF files against the original
`jkOwnLib` object symbols and fails if it finds a match. No such linked symbols
were found. It preserves the **60 general-library objects' source files** used
by Bio::DB::BigFile, their compiler-resolved header dependencies and the original
`src/lib/README` permission notice. That notice permits public, private and
commercial use. The selected source subset contains none of the flagged
noncommercial/explicit-commercial-agreement text from unused headers.

The original Kent tree is removed. The final Docker stage starts from scratch
and copies the cleaned filesystem, so a saved image cannot carry the removed
tree in an older layer. The saved-layer verifier confirmed absence of the
removed components in every layer.

The BigFile runtime binary is unchanged. The preserved source subset compiled
successfully, and a real GERP BigWig query returned the same interval and score
before and after cleanup. The full public annotation regression passed on the
final engine: **8 checks passed, 0 failed, 4 optional-resource checks skipped**.
The skipped checks concern disabled LoGoFunc/PromoterAI resources, not Kent.

This narrowly resolves the retained `jkOwnLib` problem for this image; it does
not declare all UCSC/Kent software MIT or grant rights to separately downloaded
UCSC data.

### Additional unused dependency

Source inspection found that Math::CDF 0.1 includes DCDFLIB/ACM material with
noncommercial conditions. Its only identified upstream plugin consumer was
Carol, which GUIDE-IEI does not expose. Both are removed from the runtime and
excluded from the published source companion. The build fails if another Perl
consumer imports Math::CDF. Precomputed dbNSFP score lookups are unaffected.

### Corresponding source and notices

The source companion includes exact Ubuntu source versions (.dsc descriptors,
original archives and packaging patches), CPAN/Python source distributions,
native source trees, BCFtools/HTSlib/HTScodecs pins and build instructions.
Ubuntu source components and PyPI archives were checked against upstream
SHA-256 declarations. Additional archives have HTTPS provenance and recorded
SHA-256 receipts. Recovered BioPerl, Bio-HTS, ensembl-xs and HTSlib source files
were compared with surviving image files; differences fail collection.

The original upstream image removed some source/build files after compilation.
Those are restored separately; the companion does not mistake installed
binaries for complete source. The original image history and upstream build
scripts document inherited commands and the BioPerl -fPIC modification.

Vague CPAN metadata was checked against actual license/POD text. Most
`unknown` entries grant Perl's terms; String::Format specifies GPLv2,
ExtUtils::PkgConfig provides LGPL terms, and Mozilla::CA specifies MPL 2.0.
Full originals are retained, rather than replaced with guessed SPDX labels.
Math::CDF was handled by removal as described above.

The image notices collector now includes NOTICE files and explicit GUIDE-IEI
modification notices. The latter identify the SpliceAI lookup-plugin change,
pinned LOFTEE replacement, BCFtools liftover addition and runtime cleanup.
Ensembl VEP/Bio-HTS and LOFTEE retain their Apache-2.0 texts. Original licenses
continue to apply to each component; the whole image is not relicensed Apache.

## Repeat for each engine revision

1. Build a separate candidate tag with `docker/build.sh`; do not replace a
   user's running production image during review.
2. Run `scripts/audit_bundled_engine.py` and
   `scripts/verify_engine_layers.py` against the exact candidate.
3. Collect package sources with `scripts/collect_engine_sources.py`, using the
   audit's Python package list, then native sources with
   `scripts/collect_engine_native_sources.py`. Inspect vague declarations with
   `scripts/review_engine_source_licenses.py`.
4. Run real annotation regression and native-source rebuild/query checks.
5. Create the companion with `scripts/package_engine_sources.py`. It rejects
   incomplete coverage, changed downloads and mismatched image identities.
6. Build/sign/notarize from clean committed source. Pass `--engine-image` to
   the Mac builder when using an isolated candidate tag.
7. Assemble with `scripts/make_release.sh --macos-artifacts DIR --engine-sources DIR`.
   The assembler verifies that the source companion matches the app's engine
   and includes it in the release assets/checksums. Keep equivalent, no-charge
   source access alongside the binary. Users need not download the sources.

Source availability must be maintained with distributed binaries. No paid
license was purchased, no rights holder was contacted, and no written
source-offer commitment was made on the maintainer's behalf.

## Scope and references

- [GNU GPL FAQ: source access and binary distribution](https://www.gnu.org/licenses/gpl-faq.en.html).
- [Apache License 2.0 redistribution conditions](https://www.apache.org/licenses/LICENSE-2.0.html).
- [UCSC licensing and separately licensed directories](https://www.genome.ucsc.edu/license/).
- [Pinned LOFTEE source](https://github.com/konradjk/loftee/tree/a46b502a68c812c8ae0c5a5721c0603fe81cae8d).

The macOS Python runtime, UI dependencies and downloaded container/VM tools
are separate components with their own notices. Annotation datasets also have
separate terms. This review does not replace the remaining clean-machine,
security or clinical/research-use checks in the release checklist.
