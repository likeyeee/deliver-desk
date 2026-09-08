const fs = require("node:fs/promises");
const path = require("node:path");
module.exports = async (context) => {
  const platform = context.electronPlatformName;
  const resources =
    platform === "darwin"
      ? path.join(
          context.appOutDir,
          context.packager.appInfo.productFilename + ".app",
          "Contents",
          "Resources",
        )
      : path.join(context.appOutDir, "resources");
  const metadata = JSON.parse(
    await fs.readFile(path.join(resources, "backend", "runtime.json"), "utf8"),
  );
  if (metadata.platform !== platform)
    throw Error(
      `Python runtime is ${metadata.platform}, but installer is ${platform}. Build the correct backend first.`,
    );
  const expected =
    context.arch === 1 ? "x86_64" : context.arch === 3 ? "arm64" : null;
  const actual =
    { AMD64: "x86_64", aarch64: "arm64" }[metadata.architecture] ||
    metadata.architecture;
  if (expected && actual !== expected)
    throw Error(
      `Python architecture ${actual} does not match Electron ${expected}.`,
    );
};
