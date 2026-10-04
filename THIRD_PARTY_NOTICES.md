# Third-party license scope

GUIDE-IEI's own application code is licensed under MIT, as stated in `LICENSE`.
That license does not relicense independently supplied annotation databases,
prediction scores, model files, container components, or other third-party
materials. Each retains its applicable upstream license and notices.

A distribution containing separately licensed resources is a multi-license
package, not an MIT-only distribution. A noncommercial restriction on a dataset
does not itself change the license of GUIDE-IEI's independent application code.
Use of restricted data must comply with that resource's terms even when the
application is free of charge. An acknowledgment cannot confer missing rights.

## Starter annotation resources under development

The starter bundle is not yet part of the released installer. Preparation outputs
are explicitly marked `development-not-for-distribution`; they are not release
artifacts. Public delivery must include resource-specific license copies,
attribution, original notices, and a record of the transformations performed.

The offline catalog in `config/starter-licenses.json` contains the applicable
license texts and retained source notices. The workbench's **Data sources &
licenses** page displays them without a consent checkbox or installation gate.
New subset preparations include a checksummed `LICENSE-NOTICES.json` copied
from that catalog. Older development outputs must be rebuilt before delivery.
This informational approach does not waive any upstream restrictions or change
the access requirements for unrelated datasets.

| Resource | Upstream terms and provenance |
|---|---|
| AlphaMissense predictions | DeepMind's current [README](https://github.com/google-deepmind/alphamissense#license-and-disclaimer) states CC BY 4.0 for predictions, separately from its Apache-licensed code. The downloaded canonical and isoform GRCh38 tables still have a CC BY-NC-SA 4.0 header. Preserve and document both statements during release review rather than silently deleting the source notice. Copyright 2023 DeepMind Technologies Limited. |
| CADD scores | [CADD terms](https://cadd.kircherlab.bihealth.org/impressum) permit noncommercial score use. Preserve its copyright, permission, and warranty notices. The proposed coding-SNV/Phred-only subset does not acquire an MIT license through conversion. |
| SpliceAI MANE v1.5/D=500/M=1 scores | The [independently generated dataset](https://huggingface.co/datasets/luoyiming1991/spliceai-mane-v1.5-d500-m1-snv) carries CC BY-NC 4.0 terms to the extent the publisher can license it, plus upstream notices. Model and generation-code licenses are separate. This bundle does not include model weights. |

These summaries identify the sources; they do not replace their license texts or
constitute a new blanket license for the collection. Preserve the model/dataset
citations, source release identifiers, and change notices for every prepared
subset. Do not imply endorsement by upstream authors.

## Engine and other annotation resources

The packaged VEP engine has its own component notices and corresponding-source
delivery requirements. This document does not replace those artifacts. Other
datasets (including resources installed after application installation) retain
their own terms, shown in their dataset documentation and installation metadata.

An installer without starter data would still contain separately licensed engine
and runtime components. It should be described as “without starter datasets,”
not “MIT-only.”
