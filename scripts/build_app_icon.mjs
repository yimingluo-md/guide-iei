#!/usr/bin/env node
// Reproduce macOS icon representations and the browser favicon from one SVG.
// Uses sharp already installed with the webui dependencies; no network access.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";
import os from "node:os";
import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "webui/package.json"));
const sharp = require("sharp");
const source = path.join(root, "desktop/branding/GUIDE-IEI.svg");
const destination = path.join(root, "desktop/macos/GUIDE-IEI.app/Contents/Resources/GUIDE-IEI.icns");
const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "guide-iei-icon-"));
const iconset = path.join(temporary, "GUIDE-IEI.iconset");
await fs.mkdir(iconset);
try {
  const svg = await fs.readFile(source);
  const master = await sharp(svg, { density: 144 }).resize(1024, 1024).png().toBuffer();
  const metadata = await sharp(master).metadata();
  assert.equal(metadata.width, 1024);
  assert.equal(metadata.height, 1024);
  assert.equal(metadata.hasAlpha, true);
  const { data, info } = await sharp(master).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  assert.equal(data[3], 0, "Icon corner must be genuinely transparent");
  assert.equal(data[(512 * info.width + 512) * 4 + 3], 255, "Tile center must be opaque");
  for (const size of [16, 32, 128, 256, 512]) {
    for (const scale of [1, 2]) {
      await sharp(master).resize(size * scale, size * scale).png().toFile(
        path.join(iconset, `icon_${size}x${size}${scale === 2 ? "@2x" : ""}.png`));
    }
  }
  const icns = path.join(temporary, "GUIDE-IEI.icns");
  execFileSync("iconutil", ["-c", "icns", iconset, "-o", icns]);
  const bytes = await fs.readFile(icns);
  assert.equal(bytes.toString("ascii", 0, 4), "icns");
  assert.equal(bytes.readUInt32BE(4), bytes.length);
  // Round trip verifies that iconutil accepts all encoded representations.
  execFileSync("iconutil", ["-c", "iconset", icns, "-o", path.join(temporary, "roundtrip.iconset")]);
  await fs.copyFile(icns, destination);
  await fs.writeFile(path.join(root, "desktop/branding/GUIDE-IEI.png"), master);
  await fs.copyFile(source, path.join(root, "webui/public/favicon.svg"));
  const previewSizes = [256, 128, 64, 32, 16];
  let x = 24;
  const previewIcons = previewSizes.map(size => {
    const markup = `<image x="${x}" y="${24 + (256 - size) / 2}" width="${size}" height="${size}" href="data:image/png;base64,${master.toString("base64")}"/><text x="${x + size / 2}" y="305" text-anchor="middle" font-family="sans-serif" font-size="13" fill="#244541">${size} px</text>`;
    x += size + 28;
    return markup;
  }).join("");
  await sharp(Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${x}" height="330"><rect width="100%" height="100%" fill="#EFF3F0"/>${previewIcons}</svg>`)).png()
    .toFile(path.join(root, "desktop/branding/GUIDE-IEI-sizes.png"));
  console.log("Updated macOS .icns (16–1024 px), PNG preview and browser favicon; alpha/round-trip checks passed.");
} finally {
  await fs.rm(temporary, { recursive: true, force: true });
}
