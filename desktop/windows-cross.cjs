const base = require("../package.json").build;
module.exports = {
  ...base,
  extraResources: [
    { from: "build/windows-backend", to: "backend", filter: ["**/*"] },
  ],
  win: { ...base.win, target: ["zip", "nsis"] },
};
