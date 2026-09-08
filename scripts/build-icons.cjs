const { Resvg } = require("@resvg/resvg-js");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const asset = path.resolve(__dirname, "../desktop/assets");
const svg = fs.readFileSync(path.join(asset, "icon.svg"), "utf8");
const render = (size) =>
  new Resvg(svg, { fitTo: { mode: "width", value: size } }).render().asPng();
fs.writeFileSync(path.join(asset, "icon.png"), render(1024));
const sizes = [16, 24, 32, 48, 64, 128, 256];
const pngs = sizes.map(render);
const head = Buffer.alloc(6 + 16 * sizes.length);
head.writeUInt16LE(1, 2);
head.writeUInt16LE(sizes.length, 4);
let offset = head.length;
pngs.forEach((png, i) => {
  const n = 6 + 16 * i;
  head[n] = sizes[i] === 256 ? 0 : sizes[i];
  head[n + 1] = head[n];
  head.writeUInt16LE(1, n + 4);
  head.writeUInt16LE(32, n + 6);
  head.writeUInt32LE(png.length, n + 8);
  head.writeUInt32LE(offset, n + 12);
  offset += png.length;
});
fs.writeFileSync(path.join(asset, "icon.ico"), Buffer.concat([head, ...pngs]));
if (process.platform === "darwin") {
  const set = path.resolve(__dirname, "../build/icon.iconset");
  fs.mkdirSync(set, { recursive: true });
  for (const size of [16, 32, 128, 256, 512]) {
    fs.writeFileSync(path.join(set, `icon_${size}x${size}.png`), render(size));
    fs.writeFileSync(
      path.join(set, `icon_${size}x${size}@2x.png`),
      render(size * 2),
    );
  }
  execFileSync("iconutil", [
    "-c",
    "icns",
    set,
    "-o",
    path.join(asset, "icon.icns"),
  ]);
}
console.log("Application icons generated");
