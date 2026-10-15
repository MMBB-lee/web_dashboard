import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'
import vm from 'node:vm'
import { transformWithEsbuild } from 'vite'

const require = createRequire(import.meta.url)
const vue = require('vue')
const { parse, compileScript } = require('vue/compiler-sfc')
const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const mapDirectory = path.join(project, 'src/components/map')
const boundaries = JSON.parse(await readFile(path.join(project, 'public/geo/anhui-boundaries.json'), 'utf8'))
const sfcSource = await readFile(path.join(mapDirectory, 'AnhuiMap.vue'), 'utf8')
const compiled = compileScript(parse(sfcSource).descriptor, { id: 'map-contract-test', inlineTemplate: true })
const componentCode = (await transformWithEsbuild(compiled.content, 'AnhuiMap.ts', {
  loader: 'ts', format: 'cjs', define: { 'import.meta.env.BASE_URL': '"/"' },
})).code

function memoryNode(type, text = '') {
  return {
    type, text, children: [], props: {}, parent: null,
    replaceChildren() { this.children = []; this.text = '' },
  }
}

function mountMap({ load = async () => boundaries, create } = {}) {
  const mounts = []
  const updates = []
  const selections = []
  const errors = []
  let disposals = 0
  const factory = create ?? ((host, options, data) => {
    mounts.push({ host, options, data })
    return { setData(value) { updates.push(value) }, dispose() { disposals++ } }
  })
  const module = { exports: {} }
  vm.runInNewContext(componentCode, {
    module, exports: module.exports, Error, console,
    require(name) {
      if (name === 'vue') return vue
      if (name === './three-map') return { createAnhuiMap: factory }
      if (name === './map-data') return { loadMapBoundaries: load }
      if (name === './map.css') return {}
      throw new Error(`Unexpected module: ${name}`)
    },
  })
  const renderer = vue.createRenderer({
    createElement: type => memoryNode(type),
    createText: text => memoryNode('#text', text),
    createComment: text => memoryNode('#comment', text),
    setText(node, text) { node.text = text },
    setElementText(node, text) { node.text = text; node.children = [] },
    patchProp(node, key, previous, next) { node.props[key] = next },
    parentNode: node => node.parent,
    nextSibling(node) { const peers = node.parent?.children ?? []; return peers[peers.indexOf(node) + 1] ?? null },
    insert(node, parent, anchor) {
      if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
      node.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(node); else parent.children.splice(index, 0, node)
    },
    remove(node) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1) },
  })
  const entity = { id: 'scenic-1', name: '黄山风景区', type: 'scenic', longitude: 118.17, latitude: 30.13, status: 'verified', latest_value: 322000 }
  const state = vue.reactive({ entities: [entity], scenicId: entity.id, railId: 'rail-1', airportId: 'airport-1', demo: false })
  const app = renderer.createApp({ render: () => vue.h(module.exports.default, { ...state, onSelect: value => selections.push(value) }) })
  app.config.errorHandler = error => errors.push(error)
  const root = memoryNode('root')
  app.mount(root)
  const visit = node => [node, ...node.children.flatMap(visit)]
  return { app, root, state, mounts, updates, selections, errors, get disposals() { return disposals }, nodes: () => visit(root), text: () => visit(root).map(node => node.text).join(' ') }
}

async function flush() {
  await new Promise(resolve => setImmediate(resolve))
  await vue.nextTick()
}

test('Vue 地图挂载、选中项更新、真实数据与演示状态、点击事件及卸载', async () => {
  const map = mountMap()
  await flush()
  assert.deepEqual(map.errors, [])
  assert.equal(map.mounts.length, 1)
  assert.equal(map.mounts[0].host.props.class, 'three-map-host')
  assert.equal(map.mounts[0].data, boundaries)
  assert.equal(map.mounts[0].options.demo, false)
  assert.equal(map.mounts[0].options.entities[0].longitude, 118.17)
  assert.ok(!map.text().includes('正在加载'))
  map.mounts[0].options.onSelect(map.state.entities[0])
  assert.equal(map.selections[0], map.state.entities[0])
  map.state.scenicId = 'scenic-2'
  map.state.demo = true
  await flush()
  assert.equal(map.updates.at(-1).scenicId, 'scenic-2')
  assert.equal(map.updates.at(-1).demo, true)
  map.app.unmount()
  assert.equal(map.disposals, 1)
})

test('边界加载失败显示错误并可重试，避免空白地图', async () => {
  let attempts = 0
  const map = mountMap({ load: async () => {
    attempts++
    if (attempts === 1) throw new Error('测试：边界暂时不可用')
    return boundaries
  } })
  await flush()
  assert.ok(map.text().includes('地图加载失败'))
  assert.ok(map.text().includes('测试：边界暂时不可用'))
  const retry = map.nodes().find(node => node.type === 'button')
  assert.ok(retry)
  await retry.props.onClick()
  await flush()
  assert.equal(map.mounts.length, 1)
  assert.ok(!map.text().includes('地图加载失败'))
  map.app.unmount()
})

test('离开页面后才返回的边界数据不得创建已卸载的地图', async () => {
  let resolveData
  const map = mountMap({ load: () => new Promise(resolve => { resolveData = resolve }) })
  assert.ok(map.text().includes('正在加载'))
  map.app.unmount()
  resolveData(boundaries)
  await flush()
  assert.equal(map.mounts.length, 0)
  assert.deepEqual(map.errors, [])
})

test('WebGL 初始化错误显示可读错误和重试按钮', async () => {
  const map = mountMap({ create: () => { throw new Error('测试：无法创建 WebGL 上下文') } })
  await flush()
  assert.ok(map.text().includes('无法创建 WebGL 上下文'))
  assert.ok(map.nodes().some(node => node.type === 'button' && node.text === '重新加载地图'))
  assert.deepEqual(map.errors, [])
  map.app.unmount()
})

test('本地边界支持子路径部署、请求复用和失败后重新请求', async () => {
  const loaderSource = await readFile(path.join(mapDirectory, 'map-data.ts'), 'utf8')
  const loaderCode = (await transformWithEsbuild(loaderSource, 'map-data.ts', { loader: 'ts', format: 'cjs' })).code
  const urls = []
  let fail = false
  const module = { exports: {} }
  vm.runInNewContext(loaderCode, {
    module, exports: module.exports, Error,
    fetch: async url => {
      urls.push(url)
      return { ok: !fail, status: fail ? 503 : 200, json: async () => boundaries }
    },
  })
  const { loadMapBoundaries } = module.exports
  const first = loadMapBoundaries('/contest/dashboard/')
  assert.equal(first, loadMapBoundaries('/contest/dashboard/'))
  assert.equal(await first, boundaries)
  assert.equal(urls[0], '/contest/dashboard/geo/anhui-boundaries.json')
  fail = true
  await assert.rejects(loadMapBoundaries('/retry/'), /HTTP 503/)
  fail = false
  assert.equal(await loadMapBoundaries('/retry/'), boundaries)
  assert.equal(urls.length, 3)
})

test('完整边界三角化保留区域面积与孔洞，原点位投影可逆并过滤无效坐标', async () => {
  const geometrySource = await readFile(path.join(mapDirectory, 'map-geometry.ts'), 'utf8')
  const threeUrl = pathToFileURL(path.join(path.dirname(require.resolve('three')), 'three.module.js')).href
  const geometryCode = (await transformWithEsbuild(geometrySource, 'map-geometry.ts', { loader: 'ts', format: 'esm' })).code
    .replace(/from (["'])three\1/g, `from '${threeUrl}'`)
  const geometry = await import('data:text/javascript;base64,' + Buffer.from(geometryCode).toString('base64'))
  assert.equal(boundaries.detailedCities.features.length, 16)
  assert.equal(boundaries.counties.features.length, 104)
  assert.equal(boundaries.counts.county_vertices, 35421)
  function area(ring) {
    return Math.abs(ring.reduce((sum, coordinate, i) => {
      const a = geometry.project(...coordinate)
      const b = geometry.project(...ring[(i + 1) % ring.length])
      return sum + a.x * b.y - b.x * a.y
    }, 0)) / 2
  }
  for (const city of boundaries.detailedCities.features) {
    const mesh = geometry.extrudeFeature(city)
    const points = mesh.getAttribute('position')
    const normals = mesh.getAttribute('normal')
    assert.ok(Array.from(points.array).every(Number.isFinite))
    let actualArea = 0
    for (let i = 0; i < points.count; i += 3) {
      if (normals.getY(i) < .99) continue
      for (let j = 0; j < 3; j++) assert.ok(Math.abs(points.getY(i + j)) < 1e-5)
      const ax = points.getX(i), az = points.getZ(i)
      const bx = points.getX(i + 1), bz = points.getZ(i + 1)
      const cx = points.getX(i + 2), cz = points.getZ(i + 2)
      actualArea += Math.abs((bx - ax) * (cz - az) - (cx - ax) * (bz - az)) / 2
    }
    const expectedArea = geometry.polygonsOf(city).reduce((sum, polygon) => sum + area(polygon[0]) - polygon.slice(1).reduce((holes, ring) => holes + area(ring), 0), 0)
    assert.ok(Math.abs(actualArea - expectedArea) / expectedArea < 1e-5, city.properties.name)
    mesh.dispose()
  }
  const fixtureCode = (await transformWithEsbuild(await readFile(path.join(project, 'tests/demoData.ts'), 'utf8'), 'demoData.ts', { loader: 'ts', format: 'esm' })).code
  const { demoEntities } = await import('data:text/javascript;base64,' + Buffer.from(fixtureCode).toString('base64'))
  for (const entity of demoEntities) {
    assert.ok(geometry.hasCoordinates(entity))
    const point = geometry.worldPosition(entity.longitude, entity.latitude)
    const [longitude, latitude] = geometry.unproject(point.x, point.z)
    assert.ok(Math.abs(longitude - entity.longitude) < 1e-9 && Math.abs(latitude - entity.latitude) < 1e-9)
  }
  for (const [longitude, latitude] of [[null, 31], [118, null], [NaN, 31], [Infinity, 31], [118, 90]]) {
    assert.equal(geometry.hasCoordinates({ longitude, latitude }), false)
  }
})
