# Bundled annotation engine: redistribution review

Reviewed 2026-09-11 for the 0.6.2 Apple Silicon beta candidate. This is an
engineering compliance review, not a legal opinion or a blanket license grant.
**Review completed; public binary redistribution sign-off is still open.**
Public beta software is allowed. Beta/preview naming, a free price, open-source
application code and Apple notarization do not replace third-party obligations.

## Exact artifact and scope

- Image: `sha256:1b470d951fe3391cf1671a1310bd5b9e92f00130c7378dd0bb1bd4e4596f75e1`.
- Platform: Linux arm64, Ubuntu 22.04.5 LTS.
- GUIDE-IEI engine fingerprint:
  `8e9a5e7c37f0baa60bc620b2944efa7c355570453daf1bd8c1c35596b8d81c74`.
- Bundled gzip archive SHA-256:
  `cc544dfbc44fd0f33a10c095a57b093313be8801eef0fe83282b7cc33849a452`.
- Observed: **378 OS binary packages**, **243 distinct OS source/version pairs**,
  and **102 manually installed CPAN distributions**.

The [machine-readable inventory](releases/0.6.2-engine-inventory.json) records
versions, CPAN paths/license declarations and hashes of relevant evidence files.
Reproduce it with `python3 scripts/audit_bundled_engine.py --output /tmp/engine-inventory.json`.
The collector reads an immutable image without networking or patient-data mounts.
It does not download sources or certify licenses. The exported image includes
historical layers as well as the visible filesystem; a final-filesystem inventory
alone is not a complete inventory of everything distributed by `docker save`.

## Findings

| Component | Evidence and assessment | Required release action |
|---|---|---|
| Ensembl VEP and Bio-HTS | Apache-2.0 license texts retained in the image | Preserve full license and applicable notices. |
| LOFTEE | Apache-2.0 notice; commit `a46b502a68c812c8ae0c5a5721c0603fe81cae8d` retained | Preserve notices; review embedded third-party material separately. |
| Ensembl SpliceAI lookup plugin | Apache-2.0 header; GUIDE-IEI changes optional file handling | Preserve license and prominently identify modifications. This is not distribution of the SpliceAI neural-network software or score datasets. |
| HTSlib, samtools, bcftools and liftover plugin | Mixed source-built and OS-packaged components, not one single version of HTSlib | Retain each component's actual license/version and build inputs; account for linked libraries. |
| Ubuntu packages | Include bash, coreutils, GCC/binutils, glibc and other GPL/LGPL components | Prepare exact corresponding source and necessary build/patch materials, and provide the required access with binary downloads. The current app's notices are not a source distribution. |
| CPAN libraries | 102 distributions; most declare Perl terms, with additional Artistic, Apache, GPL and LGPL declarations; some declare `unknown` | Inspect actual POD/license texts for missing declarations. `unknown` metadata does not itself mean unlicensed. Account for native extensions and linked dependencies. |
| Legacy Kent `335_base` | Image retains `src/lib`, `src/inc` and **`src/jkOwnLib`**. Some headers contain noncommercial-use language; `src/lib/README` broadly permits use of that library | Resolve the precise rights for the pinned files, especially `jkOwnLib`; do not infer that every retained directory is MIT from current general UCSC guidance. Remove unnecessary material via a rebuilt/exported image or obtain appropriate permission if needed. |

The licensing review therefore does **not** support advertising the whole
bundled image as Apache-2.0 or unrestricted. In particular, current UCSC guidance
explicitly lists `jkOwnLib` among separately licensed exceptions. The older
retained library README and individual headers must be reconciled at the pinned
revision; this report does not infer that all legacy headers are current bans,
or that the general library permission necessarily covers `jkOwnLib`.

## Before publishing the binary

1. Produce a source-delivery package for the actual image, not merely links to
   moving upstream branches. Cover exact OS source versions, local patches/build
   scripts and native components. Offer equivalent download access with the
   binary wherever the applicable license requires it. A list of package names,
   Dockerfile, notices file or source for GUIDE-IEI alone is insufficient.
2. Resolve legacy Kent/embedded third-party permissions and any missing license
   texts. If removing unneeded files, remember that deleting them in a later
   Docker layer does not remove them from a saved layered image. Rebuild an
   appropriate runtime image and retest the real annotation pipeline.
3. Clearly identify GUIDE-IEI's changes to upstream files and retain applicable
   NOTICE/COPYING/copyright texts (not only files named LICENSE). The current
   notices collector is supporting evidence, not proof of completeness.
4. Re-inventory any changed image, attach source/notices materials to the release,
   and record explicit sign-off tied to its immutable digest. Do not silently
   mark this review approved because CI or notarization succeeded.

No source-offer commitment has been made on the maintainer's behalf, no third
party has been contacted, and no commercial license has been purchased. Those
would require the maintainer's decision. A lean runtime-image rebuild may reduce
both download size and the amount of unrelated legacy code requiring review;
it is a separate implementation step, not something this audit has performed.

## Primary references

- [GNU GPL FAQ: source availability and binary distribution](https://www.gnu.org/licenses/gpl-faq.en.html).
- [Apache License 2.0, redistribution conditions](https://www.apache.org/licenses/LICENSE-2.0.html).
- [Ensembl software licensing](https://www.ensembl.org/info/about/legal/code_licence.html).
- [UCSC licensing, including separately licensed directories](https://www.genome.ucsc.edu/license/).
- [Pinned LOFTEE source](https://github.com/konradjk/loftee/tree/a46b502a68c812c8ae0c5a5721c0603fe81cae8d).

The macOS Python runtime and UI dependencies are separate bundled components;
their retained licenses also remain applicable. Installed annotation datasets
have their own terms. This engine review does not waive either set of terms.
