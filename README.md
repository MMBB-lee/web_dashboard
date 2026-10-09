# web_dashboard

A 负责的 Vue 3 + TypeScript 驾驶舱应用，实现总览、景区趋势和假期专题。开发环境运行 `pnpm install`、`pnpm dev`；类型检查和构建运行 `pnpm build`。

应用使用 `/api/v1` 的 D 端接口和服务端会话 Cookie。开发服务器将接口代理到 `http://127.0.0.1:8000`。未接入后端时，A 页面使用原有的静态样例；如果同级 `web_interactions/src/index.ts` 存在，B 页面使用其 `dev/mock.ts` 演示接口，登录页可点击“进入演示工作台”。后端可用时，两边切换为真实接口；后端返回 401、422 或 `data_insufficient` 时不会显示 A 的样例值。

B 的 `web_interactions/` 是可选的同级目录。`vite.config.ts` 在启动或构建时检查其入口；`src/main.ts` 在存在时加载 B 的路由、登录状态、样式和 PrimeVue，并按后端可用性选择演示或 API 模式。启动 `web_dashboard` 即可通过同一地址打开总览、景区、假期、交通、事件、智能问答和录像页面，无需单独启动 B 的 Vite 服务。若增删 B 目录，请重启 Vite；后端状态变化时，页面会刷新以同步 B 的模式。

首次启动请在 `项目/web_dashboard/` 中安装依赖，随后运行 `pnpm --config.verifyDepsBeforeRun=false dev`，访问终端显示的地址。本机的 pnpm 11 将未获批的 `esbuild` 构建脚本当作安装错误；如遇此错误，可用 `pnpm --config.strictDepBuilds=false install` 完成安装。pnpm 11 可能自动生成 `pnpm-workspace.yaml` 记录待审批的脚本。当前 `项目/` 中只有两个前端目录；B 存在时，登录及业务数据请求需要 `http://127.0.0.1:8000` 上的 FastAPI 后端。

## 高德地图 JS API 2.0

总览页的安徽地图位于 `src/components/map/AnhuiMap.vue`，通过 `@amap/amap-jsapi-loader` 加载高德地图 JS API 2.0。该依赖已写入 `package.json`，克隆仓库后在 `web_dashboard/` 目录运行 `pnpm install` 即可安装；如果在旧版本项目中单独补装，运行：

```powershell
pnpm add @amap/amap-jsapi-loader
```

在[高德开放平台](https://lbs.amap.com/)创建 Web 端（JS API）Key，取得 Key 和对应的安全密钥。然后在 `web_dashboard/` 根目录（与 `package.json` 同级）新建不提交到 Git 的 `.env.local`：

```dotenv
VITE_AMAP_KEY=你的Web端Key
VITE_AMAP_SECURITY_CODE=你的安全密钥
```

保存后重启 `pnpm dev`。如果缺少任一变量，地图会提示配置 Key 和安全密钥。`.env.local` 已被 `.gitignore` 忽略；请勿把真实值写入 README 或提交到仓库。`VITE_` 变量会打包进前端代码，正式部署时应按高德平台的安全方案使用服务端代理，并配置适用的访问限制。

地图在线查询安徽省边界，只显示安徽境内的底图及业务点位，省外保留省级轮廓，不显示底图地名。`public/geo/anhui.geojson` 仍是未使用的授权占位文件。所有静态样例数值集中在 `src/app/demo.ts`，不代表官方统计。
