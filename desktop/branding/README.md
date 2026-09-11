# GUIDE-IEI icon

Approved direction C: an ivory DNA helix inside a cell membrane, with a muted
gold Y-shaped immune-receptor motif. Teal tile, no letters, pedestal, bevel or
heavy shadow. The motif is symbolic, not an anatomical diagram.

`GUIDE-IEI.svg` is the editable master. Its outer margin is truly transparent.
`GUIDE-IEI.png` is the 1024-pixel preview; `GUIDE-IEI-sizes.png` shows Dock/tab
scale examples. The native app uses
`desktop/macos/GUIDE-IEI.app/Contents/Resources/GUIDE-IEI.icns`, including
16–1024-pixel representations. `webui/public/favicon.svg` is an exact copy of
the master. Both macOS packaging scripts already consume that ICNS file.

Regenerate on macOS after installing the webui dependencies:

```sh
node scripts/build_app_icon.mjs
```

The generator uses the webui's installed Sharp and Apple's `iconutil`, checks
transparency and dimensions, and validates an ICNS round trip. It modifies
repository assets only, not an installed/signed application. App releases must
be rebuilt, signed and notarized to carry the new icon. Do not patch the icon
inside a distributed signed app.

## Design provenance

The imagegen skill's built-in image generation was used for concept C and its
refinement. The raster refinements baked a checkerboard into the background and
were not shipped. The final production artwork is the code-native SVG above,
with clean curves and deliberate gaps at alternating helix crossings.

Refinement prompt (built-in tool, no API/CLI fallback):

> Convert approved concept C into one production macOS application icon.
> Preserve the ivory upright double helix with four short rungs, ivory cell
> membrane interrupted at upper right by one gold Y-shaped receptor. Deep
> forest teal tile, ivory strokes, muted gold receptor. Remove small examples,
> all text, white pedestal, heavy shadow, metallic border, bevel and tilt.
> Crisp flat shapes, subtle teal shading, no grain. Square icon with an 84%
> rounded tile and equal transparent margins; no added background or watermark.

The final SVG uses teal `#145B52` to `#0B443F`, ivory `#F4F2E9`, and gold
`#C7A667`. Its transparent margin is 80 units in a 1024-unit canvas.
