# Apple Silicon beta release checklist

Target: GUIDE-IEI 0.6.2, Apple Silicon, macOS 13 or newer. This is a checklist,
not a statement that unrecorded checks have passed. Keep results with the exact
source commit, build ID, host macOS version and artifact checksums. Use synthetic
or public regression variants only. Do not delete an existing library or its
databases to simulate a clean machine.

## Automated gates

- Clean committed source, matching VERSION and CHANGELOG; no private data in
  the tracked release payload. No subsequent source edits during packaging.
- Green clean-clone CI on macOS/Linux and Windows launcher checks for that
  commit; green Intel packaged smoke as a compatibility signal only.
- Python/shell/UI suites, type checking, lint (no errors), documentation checks.
- Packaged runtime/browser smoke with empty temporary state, host tools hidden,
  engine discovery disabled, duplicate launch and confirmed Quit/closed port.
- Native first-launch setup: named stages, existing-engine reuse, failed setup
  and Retry, safe Quit/cancellation, ignored old deferral settings and immediate
  Quit/reopen. No skip option. No large annotation-dataset downloads during
  engine preparation. Verify orphaned managed Colima recovery and actual app
  sharing, without changing a user's explicitly selected engine.
- Real container plugin verification and public annotation regression, then
  import its output into an isolated Sample Library/cohort and reopen it.
  Compare every indexed record with the sequential VCF, including chromosome X;
  a passing annotation report alone does not prove the index is complete.
  This tests a configured host; it does not substitute for clean-machine setup.
- Final clean-source app/ZIP/DMG signed, notarized and stapled; distribution
  verifier checks the actual contents of both archives against the commit.

## Human/clean-machine gates — must be recorded separately

1. On a clean supported Apple Silicon Mac or suitable VM, download the DMG
   through a browser so quarantine is real. Install from Finder. With networking
   enabled, launch without preinstalled Python, Node, Homebrew or Docker Desktop.
   Verify that first launch installs container tools/VM and loads the bundled
   image before opening the workbench. Verify the native window, Dock, Open Log,
   Retry and Quit. Then disable networking and verify that a ready installation
   reopens and reviews an annotated synthetic VCF. A genuinely offline first
   launch is expected to need networking, not bypass preparation.
2. Restore networking, choose Storage locations, confirm engine readiness,
   install required datasets (use the tester's own registered dbNSFP access),
   annotate the public regression VCF and review the result. Record downloads,
   free space, times and any warnings; confirm no unexplained missing required
   annotations. Do not claim this passed from mocked download/setup tests.
3. Exercise a failed/interrupted dataset transfer and Retry; confirm successful
   repair and no second simultaneous writer. Check the actual selected paths.
4. Repeat with an external SSD/path containing spaces. Reject missing container
   access with actionable guidance, then grant access and retry successfully.
5. Using only synthetic records, save to Sample Library and Cohort Search, quit,
   reopen, replace the app and confirm data/settings remain. Test Storage restart
   and active-work Quit behavior. Reopen via the new Dock icon, not the old shim.
6. Record which macOS versions were actually tested. macOS 13 is the build
   minimum, not evidence of testing every later OS. Intel needs its own clean
   machine/full annotation test and signed installer before supported release.

## Distribution sign-off

- Complete the [bundled-engine redistribution review](BUNDLED_ENGINE_REDISTRIBUTION.md).
  Publish corresponding source/materials where required, preserve licenses and
  resolve separately licensed retained components. A notices file, signed DMG,
  free price or beta label alone does not satisfy these requirements.
- Review the draft: signed `GUIDE-IEI-macOS-0.6.2-arm64.dmg` is the primary Mac
  download, optional matching ZIP, source/update archive separately labelled,
  combined `sha256sums.txt`, release notes and known limitations.
- Keep the release marked **prerelease** for the beta. The normal `/releases/latest`
  endpoint excludes prereleases; link users to `/releases` instead.
- A public beta is supported and encouraged once the applicable release gates
  are met. Internal `preview=true` means a development build may contain
  uncommitted code; it is distinct from a public GitHub prerelease, which is
  built from a clean commit with `preview=false` and labelled beta in release
  notes. This is a reproducibility policy, not a prohibition on public betas.
- Document single-user local operation and research-use-only scope. Signing and
  notarization are security/distribution checks, not clinical validation.
- Publish only after reviewing this evidence and obtaining explicit approval.
  A tag or successful build must not automatically publish the draft.
