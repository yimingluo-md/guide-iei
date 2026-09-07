---
title: Troubleshooting
nav_order: 6
---

# Troubleshooting

Start with the **first specific error**, not the final “exit code 1” summary.
Keep original VCFs and completed downloads. Do not reset the library, delete
the installation folder, or repeatedly start the same job to diagnose a failure.

## Find the right log

| Problem | Where to look |
|---|---|
| App does not open | Launcher terminal; Windows launcher logs under `%LOCALAPPDATA%\GUIDE-IEI\logs` |
| Direct WSL launch fails | The Ubuntu terminal used to start the workbench (not the Windows launcher log folder) |
| Dataset download or preparation fails | The affected dataset card's log and error message |
| Annotation fails | **Recent annotation jobs → Log**; read above the final exit-code line |
| Import or cohort intake fails | Import QC/error details; the Sample Library bulk queue has per-file outcomes |
| External storage missing | **Storage → Test location**, or the startup reconnect message |

The default service-data location is `~/.iei-variant-review/`; selected
Storage roots can move logs and caches. On WSL this is the Linux user's home,
not the Windows profile folder.

## Launch and setup

| Symptom | Safe next action |
|---|---|
| Mac source-tree app says it was separated from the repository | Keep the extracted repository together and open `desktop/macos/GUIDE-IEI-Workbench.command`; see [which file to open](guide/02-install.md). A standalone release is a different package. |
| Windows blocks the unsigned launcher | Follow [Start directly in WSL](guide/02-install.md#windows-start-directly-in-wsl). Do not disable Windows security; institutional policy may require IT approval. |
| WSL is missing or the wrong distribution starts | In PowerShell check `wsl --list --verbose`; use the actual Ubuntu WSL2 distribution, not Docker's internal distribution. |
| Browser cannot connect / local service unavailable | Keep the launch terminal open. Read startup errors and confirm the UI and service started; open the URL printed by the launcher. Do not start duplicate servers on occupied ports. |
| Missing managed `lima`/`limactl` or prerequisites | From the repository in Terminal/Ubuntu, run `bash scripts/setup_environment.sh --install`; [details](FAQ.md#setup-says-a-managed-colima-dependency-lima-or-limactl-is-missing). |
| Missing/outdated indexed-predictor plugin or container image | Rerun the current setup/launcher. If needed, run `bash docker/build.sh` from the updated repository with the container engine running; do not repeatedly re-download datasets. |
| A file exists on the host but is inaccessible in the container | Follow [container sharing guidance](FAQ.md#why-does-annotation-say-the-container-cannot-access-a-file-that-exists-on-my-mac). Share the named input/reference folders readably and work/output folders writably. Stop jobs before restarting the engine. |

## Downloads and annotation

| Symptom | What it means / next action |
|---|---|
| Download is complete but setup is still preparing | Check the named stage: checksum verification, decompression, sorting, indexing, or protein-catalog preparation can take additional time. Download progress is not total installation progress. |
| Interrupted download, timeout, or HTTP 429 | Keep completed parts. Wait before retrying; 429 is rate limiting, not proof of an expired account. Use the dataset's retry/resume action. For persistent 401/403 errors, check authorized access with the provider. Never post private links in an issue. |
| dbNSFP preparation fails | Confirm the supplied filename ends `_grch38.gz`, with companions from the same release. Direct and Outlook Safe Links are accepted. Read whether the failure is network, checksum, schema, or index related before changing files. |
| GenIA “Installed with validation notes” | Some source records were excluded (for example ambiguous `N` alleles or reference mismatches). Read the reason counts in installation details. This differs from total installation failure; do not infer evidence for excluded variants. |
| GenIA component missing after a partial installation | Components are optional and independent. Supply the missing component if needed; the GEI filter requires the GEI list and variant annotations require the variant VCF. Normal subset updates preserve omitted installed components. |
| Invalid VCF, malformed row, or truncated gzip | Obtain a complete valid export or re-copy the original. Shared validation runs before annotation in both UI and direct commands; it does not repair broken records. Do not disable validation or edit patient calls merely to pass it. |
| Disk-space error | Use **Storage** to inspect the relevant destination and preparation space. Move managed data with **Copy existing data**; do not drag a live database between drives. See [storage guidance](SAMPLE_LIBRARY_AND_STORAGE.md). |
| VEP count reaches the total but the job is not done | Post-processing, clinical-source matching, indexing, and QC may remain. Use the final completed-job result, not an intermediate VCF. |

## Import and interpretation

| Symptom | What to check |
|---|---|
| Exome import has hundreds of thousands of rows | A row may represent a transcript/sample consequence, not a unique variant. Keep Exome scope; retain in the Sample Library for a compact browser projection and use Cohort Search for large collections. |
| No variants / unexpectedly few rows | Check annotation scope, intake settings, selected samples/transcripts, and review filters separately. Clearing review filters cannot recover variants removed by an earlier stage. See [filter stages](guide/04-first-exome.md#three-different-places-where-filtering-happens). |
| AlphaMissense missing on MANE | A transcript score may be absent from the released table; this is not a benign result. See the [AlphaMissense FAQ](FAQ.md#why-is-alphamissense-blank-for-the-selected-mane-transcript). |
| Setting gnomAD to zero still shows unknown frequencies | Zero and missing are different. Missing annotations are retained by the missing-data rule and do not prove absence from gnomAD. See [frequency controls](guide/09-reading-a-variant.md). |
| A reimport does not create a second sample | Identical files reuse the managed version; changed annotations can create a new current version. See [duplicate imports](SAMPLE_LIBRARY_AND_STORAGE.md#duplicate-imports-and-updated-versions). |
| Sample Library entry exists but Cohort Search is missing | Use that entry's **Add to Cohort Search** or **Repair Cohort Search** action. The cohort index is derived; do not delete the managed VCF. |

## Ask for help safely

Provide the GUIDE-IEI version, OS/architecture (and WSL distribution if used),
the failing action/stage, and the first relevant error with a short redacted
log excerpt. State whether the data/working folders are internal, external,
or network/cloud-synced. A small **synthetic** reproducer is preferable to
patient data. Remove patient/sample identifiers, personal paths, credentials,
private OMIM/dbNSFP links, and email tokens before sharing on
[GitHub Issues](https://github.com/yimingluo-md/guide-iei/issues).
