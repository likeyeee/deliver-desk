const { contextBridge, ipcRenderer } = require("electron");
const commands = [
  "snapshot",
  "saveConfig",
  "start",
  "control",
  "resolve",
  "openBrowser",
  "browserViewport",
  "browserTab",
  "browserNavigate",
  "openJob",
  "exportHistory",
  "exportConfig",
  "importConfig",
  "openData",
];
contextBridge.exposeInMainWorld(
  "desk",
  Object.fromEntries(
    commands.map((name) => [
      name,
      (params) => ipcRenderer.invoke("desk:" + name, params),
    ]),
  ),
);
