# 构建与发布

Node.js 24，Python 3.12，依赖分别由 `package-lock.json` 和 `uv.lock` 锁定。

## 原生构建

```sh
uv sync --locked
npm ci
npm run lint
npm run test:python
npm run build:ui
npm run test:desktop
npm run build:backend
npm run test:desktop -- --packaged-backend
```

macOS arm64 上执行 `npm run pack:mac`；Windows x64 上执行 `npm run pack:win`。产物在 `release/`。Python 服务由 PyInstaller 打包，安装用户不需要 Python 或 Node.js。

`desktop/after-pack.cjs` 校验核心与 Electron 的系统和架构必须一致。Intel Mac 需要对应的 Intel 构建机和 x64 打包目标。

## Windows 交叉构建

在其他系统准备 Windows 包：

```sh
npm run build:ui
uv run python scripts/build_windows_backend.py
npx electron-builder --config desktop/windows-cross.cjs --win --x64 --publish never
```

该流程核对 Python 官网嵌入式包 SHA-256，安装锁定的 Windows wheels。它可以生成安装包，不能替代 Windows 原生运行和安装验收。

## GitHub Actions

- **CI**：每次 push / pull request 执行格式检查、三平台 Python 测试，以及 macOS / Windows Electron 隔离集成测试。
- **Desktop installers**：手动运行或推送 `v*` 标签，在 macOS / Windows 原生构建，验证打包后的 Python 服务，上传安装产物和校验值。
- **Publish release**：填写已成功的原生构建运行 ID，检查当前 CI 与应用源码一致性，审计产物后发布测试版本。安装包在 GitHub 内直接转入 Releases。

## 发布清单

1. 同步 `package.json`、`pyproject.toml`、`src/boss_cli/__init__.py` 及锁文件中的版本。
2. 合并当天的 [发布记录](releases/release-notes.md)，更新 README 最新更新。
3. 等待 CI 与原生构建通过，检查实际启动、登录恢复和只读预览。
4. 提交全部改动，将两个平台产物放在 `release/`，运行 `npm run release:prepare`。脚本从当前 Git 提交生成源码包，直接检查 ZIP 内的运行环境与应用归档，输出 `SHA256SUMS.txt` 和内容审计。
5. 把 DMG / EXE / ZIP、校验文件及版本说明上传至 Releases。源码仓库不存安装包和用户运行数据。

首次发行是未签名测试版。正式签名需由维护者配置自己的证书及 macOS 公证凭据；不要提交证书或凭据到仓库。
