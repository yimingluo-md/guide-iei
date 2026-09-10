---
title: "Security & privacy model"
parent: Reference
nav_order: 14
---

# Security and privacy model

This page is for the person who has to decide whether GUIDE-IEI may run on a
particular computer — an IT or compliance reader as much as a clinician. It
says, in one place, what the software assumes about the machine it runs on,
what it protects against, what it does not, where patient-derived data ends
up, and what deleting it means. Every statement here describes the current
code; where a limit is a design choice rather than an oversight, it says so.

## The deployment it is built for

GUIDE-IEI is a **single-user workstation application** — a local service on
the loopback address with no login, not a shared server. Its port must never
be forwarded or shared. The sections below explain what this design does
and does not protect.

It assumes one
person's operating-system account on one computer that they control, with
the software, the annotation datasets, and the patient files all local to
that machine. It is not a server, not a multi-user system, and not designed
to be reached from another computer.

Concretely:

- The local service listens on the loopback address only (`127.0.0.1`,
  default port 43117) and **refuses to start on any other address** — the
  `--host` option accepts loopback names only, with no override. The
  interface is a local web page served the same way.
- There is **no login, no user accounts, and no API token**. Anything that
  can open a TCP connection to the loopback port on this computer can use
  the service.
- On macOS, Linux and WSL, the data root that holds patient-derived data
  (the Sample Library, cohort index, review projections, logs, migration
  targets, and the upload workspace) is set to owner-only permissions
  (`0700` on the root and its private directories, `0600` on the databases)
  at every start, so other operating-system accounts on the same computer
  cannot read them from the filesystem. On native Windows the data root
  keeps the account's ordinary permissions.

What that adds up to, by situation:

| Where it runs | What the current design gives you |
|---|---|
| Your own workstation, one account, no port forwarding | The intended case. Other people would need your login to reach the data. |
| A shared computer with separate operating-system accounts | The files are protected by their permissions, **but the running service is not**: any account on that computer can reach the API over loopback while GUIDE-IEI is open and read, import, or delete library data through it. Close GUIDE-IEI when you leave the machine, or do not use a shared computer. |
| Malware or an untrusted program running as your own account | Such a program can read your files directly; the service adds no protection beyond what your account already has. |
| A shared server, a remote desktop host with other users, or any port forwarding / tunnelling of the service port | **Not supported.** The loopback binding and the checks below are not a substitute for authentication. Do not forward, proxy, or expose the port. |

If shared-machine use ever becomes necessary, the appropriate addition is a
per-session token provisioned by the launcher and handed to the interface at
start — not a user-facing login, and not a token readable from an endpoint.
That is a possible future feature, not something the current release offers.

## What the browser-facing checks do, and do not do

Because the service is reachable at `http://127.0.0.1:43117`, a web page
open in your browser could in principle make requests to it. Two checks on
every request close that path:

- the request's `Host` header must name this workstation (`127.0.0.1`,
  `localhost`, or `[::1]`), which defeats DNS-rebinding attacks that make a
  remote site look same-origin; and
- a request that carries an `Origin` header must come from a loopback origin,
  which stops cross-site requests from any other web page.

These checks are aimed at **web pages in your browser**. They do not
authenticate local programs, and they do not tell one operating-system user
from another; a local tool that sends no `Origin` header passes them by
design (the pipeline's own tools do exactly that).

## What leaves the computer

All annotation, review, and cohort analysis runs locally. The service contacts
the network only in these cases, none of which sends patient, sample, variant,
phenotype, or analysis data unless stated:

- **Dataset and reference downloads** that you start from the dataset screen
  (Ensembl, UCSC, NCBI ClinVar, Zenodo, ENCODE, the project's reference
  mirror, and licensed sources you supply a link for). The ClinVar refresh at
  the start of an annotation run is on by default and is labelled as such
  ("Refresh ClinVar before run"); it downloads a public file and sends
  nothing.
- **Update checks and installs**, only when you click them (GitHub receives
  ordinary request metadata such as your IP address).
- **The optional per-variant SpliceAI lookup**, which sends only the genomic
  coordinates and alleles of the one variant you ask about, after you have
  agreed to it once; the agreement is remembered in the browser.
- **First-launch runtime downloads** (Python, Node.js, the container tooling)
  and the container image build, which fetch published software.

The interface loads no fonts, analytics, or scripts from the internet, and no
telemetry is collected.

## Where patient-derived data lives

- **Your input VCFs and the annotated outputs** stay where you put them; the
  annotated VCF, its QC report, and the run manifest are written beside the
  input by default. Every such output carries the research-use notice.
- **The Sample Library, cohort index, phenotype records, and WGS review
  files** live under the storage roots shown on the **Storage** page (by
  default in your user profile; movable to another local disk). Per-sample
  review projections and cohort staging files are under the same roots.
- **Job logs** (`logs/` and `resource-logs/` under the data root) record the
  commands run, the pipeline's own messages, and file paths. They can contain
  the input file's path and sample names, and pipeline warnings may quote
  individual records; treat them as patient-derived. The launcher window
  prints one line per request, including query strings such as a gene
  symbol or a variant locus.
- **The browser's local storage** (per browser profile, on this computer)
  holds display preferences, custom gene lists, the SpliceAI-lookup
  agreement. **Candidate stars are kept only in memory**, not in localStorage
  or sessionStorage. They clear when a different dataset is loaded or the
  page is refreshed or closed; export the Saved view to retain a record.
  On startup, the workbench removes the legacy saved-candidates entry from
  the current browser origin without reading or migrating its contents.
  If browser permissions prevent removal, a visible warning asks you to
  clear the site's browser data. Earlier copies under other browser profiles
  or ports require clearing those sites' data separately. The Storage page
  does not reach into browser storage.
- **Nothing is written outside these places**, except the temporary
  scratch directory of a running annotation (removed at the end of the run)
  and the container runtime's own working space during an annotation.

## Deleting data

- **Removing a dataset** from the Sample Library deletes its managed copy,
  its review projections, and its cohort-index rows. The original VCF you
  imported is never deleted.
- **Cohort Search removal** deletes that sample's index rows and clears the
  library's link to them.
- **Storage cleanup** (Storage page) removes caches, partial downloads, and
  leftover staging directories, and reports what it removed.
- **Deleting a phenotype record** is not yet available from the interface;
  the record can be replaced by re-saving the individual, and the whole
  phenotype table is removed together with the data root.
- Deleted files are unlinked, not securely erased. On an encrypted disk
  (FileVault, BitLocker) that is the appropriate level; on an unencrypted
  disk, treat "deleted" as "recoverable by forensic tools".
- Deleting the data root itself (after closing GUIDE-IEI) removes everything
  the service stores; the browser's local storage and your own input/output
  files remain.

Backups are your responsibility; see
[backup and restore](SAMPLE_LIBRARY_AND_STORAGE.md#backup-and-restore).

## What a software update can do

Installing an update from the About page downloads the release archive and
its checksum file from the project's GitHub releases, verifies the archive
against the checksum, replaces only the files the release manifests, keeps
the previous version for rollback, and never touches datasets, the library,
or your edited configuration.

Be clear about what that verifies and what it does not. The checksum
detects a corrupted or mismatched download. It **cannot** detect a release
in which both the archive and its checksum file were replaced by someone
who controlled the publishing account or its automation — the two share one
trust root. **Installing an update means trusting the project's published
code**: the update replaces shell scripts, Python, and interface code that
then run with your account's permissions on a computer holding patient
data. If that is not acceptable in your setting, do not use the in-app
updater; obtain releases through a channel you can verify.

The extraction step is bounded (per-file and total uncompressed size, file
count, and path containment are enforced before and while writing), and an
update that changed the interface or its dependencies tells you to close and
relaunch rather than offering an in-app restart that would keep serving the
previous interface.

## Reporting a problem

If you find a way in which the software departs from this page, treat it as
a defect and report it through the project's issue tracker; do not include
patient data in a report.
