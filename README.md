# web_dashboard

A 负责的唯一 Vue 应用。当前只建立项目配置和目录，`src/` 内的页面、组件、路由及样式文件尚未实现。

目录规划见 `../04-项目架构与文件树.md`。`package.json` 预留了同级 `../web_interactions/` 本地包依赖；等 B 提交该包后，在本目录执行 `pnpm install` 生成 `pnpm-lock.yaml`。待 `src/main.ts` 等源文件完成后运行 `pnpm dev`。

开发服务器将 `/api/v1` 请求代理到 `http://127.0.0.1:8000`，与 D 的 FastAPI 约定一致。
