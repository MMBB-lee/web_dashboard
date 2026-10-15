# web_dashboard

A 负责的 Vue 3 + TypeScript 驾驶舱应用，实现总览、景区趋势和假期专题。开发环境运行 `pnpm install`、`pnpm dev`；类型检查和构建运行 `pnpm build`。

应用使用 `/api/v1` 的 D 端接口和服务端会话 Cookie。开发服务器将接口代理到 `http://127.0.0.1:8000`。未接入后端时，A 页面使用原有的静态样例；如果同级 `web_interactions/src/index.ts` 存在，B 页面使用其 `dev/mock.ts` 演示接口，登录页可点击“进入演示工作台”。后端可用时，两边切换为真实接口；后端返回 401、422 或 `data_insufficient` 时不会显示 A 的样例值。

B 的 `web_interactions/` 是可选的同级目录。`vite.config.ts` 在启动或构建时检查其入口；`src/main.ts` 在存在时加载 B 的路由、登录状态、样式和 PrimeVue，并按后端可用性选择演示或 API 模式。启动 `web_dashboard` 即可通过同一地址打开总览、景区、假期、交通、事件、智能问答和录像页面，无需单独启动 B 的 Vite 服务。若增删 B 目录，请重启 Vite；后端状态变化时，页面会刷新以同步 B 的模式。

首次启动请在 `项目/web_dashboard/` 中运行 `pnpm install`，随后运行 `pnpm dev`，访问终端显示的地址。`pnpm-workspace.yaml` 已明确允许 Vite 所需的 `esbuild` 安装脚本。当前 `项目/` 中只有两个前端目录；后端可用时，登录及业务数据请求通过 `http://127.0.0.1:8000` 上的 FastAPI 服务处理，否则使用前述演示模式。

## Three.js 安徽立体地图

全域总览的地图入口为 `src/components/map/AnhuiMap.vue`。组件加载本项目自带的行政边界，再由 `three-map.ts` 绘制立体区域、地点、悬停提示和点到点动画；样式位于 `map.css`，投影与几何处理位于 `map-geometry.ts`。

- 地图使用 Three.js 0.180.0，安装前端依赖即可使用，不需要地图 API Key。以前的高德配置不再参与该组件的渲染。
- `public/geo/anhui-boundaries.json` 随前端发布，包含 16 个地级市、104 个区县及完整边界。`map-data.ts` 按 Vite 的部署基础路径读取并缓存数据；文件加载失败时可以在页面上重试。
- 边界来自 [阿里云 DataV.GeoAtlas](https://datav.aliyun.com/portal?tool=atlas)，于 2026-10-15 获取。文件内保留来源地址、原文件 SHA-256 和数据版本说明；获取日期不等于区划生效日期。完整区县边界共有 35,421 个坐标点；市、省外轮廓由同版本区县做拓扑合并，没有对边界做抽稀或平滑，多边形与内部孔洞保留。数据说明见 `public/geo/README.md`。
- 业务点位继续使用原 `Entity` 的 `longitude`、`latitude`、`type` 等字段和 `select` 事件，真实接口和演示数据均可使用。坐标与旧高德地图保持 GCJ-02 口径，统一经过墨卡托投影；其他坐标系的数据需在数据导入阶段统一，地图不擅自平移点位。
- 景区为青色，铁路站为绿色，机场为金色。连线表示所选铁路站、机场与景区的关联，不代表真实出行轨迹；只有演示模式才在提示中标注静态样例。
- 地级市名称为 10px，区县名称为 8.5px；默认避让，放大后可见更多名称，也可勾选“全部地名”。支持旋转、缩放、平移、俯视与返回全省。画面离开可视区域或切到其他标签页时暂停动画，离开页面时释放 GPU 资源。

总览、景区和假期页面的静态样例仍位于 `tests/demoData.ts`；仅在演示模式下使用，不代表官方统计。

运行 `pnpm test:map` 可检查组件挂载、选择事件、数据更新、异步加载失败和卸载，以及完整边界的三角化和坐标投影。这些是代码检查，不会启动浏览器。完整类型检查与生产构建仍使用 `pnpm build`。
