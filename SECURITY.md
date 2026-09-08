# 安全问题

当前维护 `0.2.x`。请通过 [GitHub 私密漏洞报告](https://github.com/likeyeee/deliver-desk/security/advisories/new) 提交问题，说明受影响版本、复现步骤和影响范围；不要在公开 Issue 中上传可利用细节或账号数据。

## 数据与信任边界

- 登录状态、配置和 SQLite 历史保存在用户自己的机器上。项目没有账号托管服务。
- 远程网站窗口禁用 Node.js，不加载应用 preload；本地界面只能调用白名单 IPC 命令。
- 导航限定 BOSS 网站；发送前核对职位 ID、公司及聊天目标。
- 安装包和源码包不应包含 `.boss-cli/`、Cookie、聊天记录或个人配置。发布脚本检查产物内容。

当前安装包没有发行证书签名或 macOS 公证。仅从本仓库 Releases 获取，并对照 `SHA256SUMS.txt` 校验。
