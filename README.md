# web_dashboard

A 负责的 Vue 3 + TypeScript 驾驶舱应用，实现总览、景区趋势和假期专题。开发环境运行 `pnpm install`、`pnpm dev`；类型检查和构建运行 `pnpm build`。

应用使用 `/api/v1` 的 D 端接口和服务端会话 Cookie。开发服务器将接口代理到 `http://127.0.0.1:8000`。当后端不可用时，页面展示标注来源的静态样例；当后端可用且登录有效时，改用真实接口。后端返回 401、422 或 `data_insufficient` 时不会显示样例值。

B 的 `web_interactions/` 包尚未创建，因此暂未写入本地依赖。A 已在 `src/app/router.ts` 预留 `registerInteractionRoutes`；B 交付后在 `package.json` 加入 `"web-interactions": "file:../web_interactions"`，并在应用入口装配 B 导出的路由和认证状态。

`public/geo/anhui.geojson` 是空的授权占位文件。地图当前只表示对象的大致位置，明确标为示意图；取得可使用的边界数据后再替换。所有静态样例数值集中在 `src/app/demo.ts`，不代表官方统计。
