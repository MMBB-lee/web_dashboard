import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { unproject, worldPosition, extrudeFeature, borderPositions, hasCoordinates } from './map-geometry'
import type { MapOptions, MapController, MapBoundaries, BoundaryCollection, LocatedEntity } from './map-types'

const COLORS = { scenic: '#00d4ff', rail_station: '#00f2a9', airport: '#ffb84d' }
const KINDS = { scenic: '景区', rail_station: '铁路站', airport: '机场' }
const METRICS = { scenic: '接待人次', rail_station: '铁路发送人次', airport: '旅客吞吐量' }
const CITY_COLORS = ['#10395e', '#123d63', '#14395f', '#123452', '#173f65', '#123858']
const DEPTH = 1.4
// 减少全省适配时的留白，使默认地图更大，同时保留完整轮廓。
const FIT_PADDING = 1.06

interface ScreenPosition { x: number; y: number; inside: boolean }
interface ScreenBox { left: number; right: number; top: number; bottom: number }
interface MarkerItem {
  entity: LocatedEntity
  node: HTMLButtonElement
  position: THREE.Vector3
  screen: ScreenPosition | null
}
interface AdminItem { node: HTMLSpanElement; kind: 'city' | 'county'; text: string; position: THREE.Vector3 }
interface Flow {
  curve: THREE.QuadraticBezierCurve3
  head: THREE.Sprite
  second: THREE.Sprite
  tailGeometry: THREE.BufferGeometry
  tailLength: number
  duration: number
  offset: number
}

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text != null) node.textContent = text
  return node
}

function releaseGroup(group: THREE.Object3D) {
  const geometries = new Set<THREE.BufferGeometry>()
  const materials = new Set<THREE.Material>()
  group.traverse(object => {
    const renderable = object as THREE.Object3D & { geometry?: THREE.BufferGeometry; material?: THREE.Material | THREE.Material[] }
    if (renderable.geometry) geometries.add(renderable.geometry)
    for (const material of Array.isArray(renderable.material) ? renderable.material : [renderable.material]) {
      if (material) materials.add(material)
    }
  })
  geometries.forEach(geometry => geometry.dispose())
  materials.forEach(material => material.dispose())
  group.clear()
}

function glowTexture() {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 64
  const context = canvas.getContext('2d')
  if (!context) throw new Error('无法创建地图动画纹理。')
  const gradient = context.createRadialGradient(32, 32, 0, 32, 32, 31)
  gradient.addColorStop(0, 'rgba(255,255,255,1)')
  gradient.addColorStop(.12, 'rgba(255,255,255,1)')
  gradient.addColorStop(.32, 'rgba(255,255,255,.6)')
  gradient.addColorStop(1, 'rgba(255,255,255,0)')
  context.fillStyle = gradient
  context.fillRect(0, 0, 64, 64)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}

/** 保持原 Entity 类型与 select 事件；不写入、不修改业务数据。 */
export function createAnhuiMap(host: HTMLElement, initial: MapOptions, data: MapBoundaries): MapController {
  let current = initial
  let disposed = false
  let inView = true
  let frame = 0
  let previousPaint = -Infinity
  let width = 1
  let height = 1
  let labelsDirty = true
  let showCities = true
  let showCounties = true
  let showAllNames = false
  let topView = false
  let tooltipEntity: MarkerItem | null = null
  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)')
  let reducedMotion = motionPreference.matches
  const listeners: Array<() => void> = []
  const markerItems: MarkerItem[] = []
  const adminItems: AdminItem[] = []
  const flows: Flow[] = []
  const pulseRings: THREE.Mesh<THREE.RingGeometry, THREE.MeshBasicMaterial>[] = []
  const textures = new Set<THREE.Texture>()

  const canvasWrap = element('div', 'three-map-canvas')
  const labels = element('div', 'three-map-labels')
  const pointLayer = element('div', 'three-map-points')
  const legend = element('div', 'three-map-legend')
  ;(Object.entries(KINDS) as Array<[keyof typeof KINDS, string]>).forEach(([kind, name]) => {
    const item = element('span', '', name)
    item.style.setProperty('--point-color', COLORS[kind])
    item.prepend(element('i', `three-legend-dot ${kind}`))
    legend.append(item)
  })
  const details = element('div', 'three-map-detail', `${data.counts.cities} 个地级市 · ${data.counts.counties} 个区县`)
  const labelControls = element('div', 'three-map-label-controls')
  const viewControls = element('div', 'three-map-view-controls')
  const footer = element('div', 'three-map-footer')
  const cursorPosition = element('span', 'three-map-coordinate', 'GCJ-02 · 墨卡托投影')
  footer.append(element('span', '', '滚轮缩放 · 拖拽旋转 · 右键平移'), cursorPosition)
  const attribution = element('div', 'three-map-attribution')
  const sourceLink = element('a', '', '边界：DataV.GeoAtlas')
  sourceLink.href = data.source_url
  sourceLink.target = '_blank'
  sourceLink.rel = 'noopener noreferrer'
  sourceLink.title = `${data.boundary_version}；源边界未抽稀。`
  attribution.append(sourceLink, element('span', '', '地名避让显示，放大可查看区县 · 立体高度仅为示意'))
  const tooltip = element('div', 'three-map-tooltip')
  tooltip.hidden = true
  tooltip.setAttribute('role', 'tooltip')
  const tooltipName = element('strong')
  const tooltipKind = element('span', 'three-tooltip-kind')
  const tooltipValue = element('span', 'three-tooltip-value')
  const tooltipCoordinate = element('span', 'three-tooltip-coordinate')
  const tooltipNote = element('span', 'three-tooltip-note')
  tooltip.append(tooltipName, tooltipKind, tooltipValue, tooltipCoordinate, tooltipNote)
  host.replaceChildren(canvasWrap, labels, pointLayer, legend, details, labelControls, viewControls, footer, attribution, tooltip)
  host.setAttribute('aria-label', '安徽省三维行政区地图，显示地级市、区县、景区、铁路站及机场')

  const scene = new THREE.Scene()
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
  renderer.setClearColor('#091d3b', 0)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.NoToneMapping
  renderer.domElement.setAttribute('aria-hidden', 'true')
  canvasWrap.append(renderer.domElement)
  const camera = new THREE.OrthographicCamera(-40, 40, 40, -40, .1, 1000)
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = !reducedMotion
  controls.dampingFactor = .11
  controls.rotateSpeed = .55
  controls.zoomSpeed = .85
  controls.panSpeed = .75
  controls.minPolarAngle = .001
  controls.maxPolarAngle = Math.PI * .34
  controls.minZoom = .8
  controls.maxZoom = 8
  controls.screenSpacePanning = true
  controls.zoomToCursor = true
  const geometryGroup = new THREE.Group()
  const flowGroup = new THREE.Group()
  const borderGroup = new THREE.Group()
  scene.add(geometryGroup, borderGroup, flowGroup)
  scene.add(new THREE.AmbientLight('#b3d8ff', 1.2))
  const mainLight = new THREE.DirectionalLight('#d8f3ff', 1.6)
  mainLight.position.set(-30, 90, 45)
  scene.add(mainLight)
  const sideLight = new THREE.DirectionalLight('#318bdd', .75)
  sideLight.position.set(45, 20, -50)
  scene.add(sideLight)
  const globeBounds = new THREE.Box3()
  const sideMaterial = new THREE.MeshPhongMaterial({ color: '#082849', shininess: 25, specular: '#154d75' })
  for (let index = 0; index < data.detailedCities.features.length; index++) {
    const feature = data.detailedCities.features[index]
    const geometry = extrudeFeature(feature, DEPTH)
    const topMaterial = new THREE.MeshPhongMaterial({
      color: CITY_COLORS[index % CITY_COLORS.length], shininess: 18,
      specular: '#1d4e73', emissive: '#05162b', emissiveIntensity: .28,
    })
    const mesh = new THREE.Mesh(geometry, [topMaterial, sideMaterial])
    geometryGroup.add(mesh)
    globeBounds.expandByObject(mesh)
  }
  const mapCenter = globeBounds.getCenter(new THREE.Vector3())
  mapCenter.y = 0
  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
  const raycaster = new THREE.Raycaster()
  const cursor = new THREE.Vector2()
  const hit = new THREE.Vector3()

  function addBorder(collection: BoundaryCollection, color: THREE.ColorRepresentation, opacity: number, level: number) {
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(borderPositions(collection, level), 3))
    const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false })
    const line = new THREE.LineSegments(geometry, material)
    line.renderOrder = level > 0 ? 3 : 0
    borderGroup.add(line)
    return material
  }
  const countyBorder = addBorder(data.counties, '#4d7b9f', .26, .045)
  addBorder(data.detailedCities, '#62accd', .63, .065)
  addBorder(data.detailedProvince, '#00d4ff', .94, .085)
  addBorder(data.detailedProvince, '#1486cb', .7, -DEPTH + .015)
  const grid = new THREE.GridHelper(140, 28, '#15446a', '#133652')
  grid.position.set(mapCenter.x, -DEPTH - .14, mapCenter.z)
  grid.material.transparent = true
  grid.material.opacity = .12
  grid.material.depthWrite = false
  scene.add(grid)

  function listen<E extends Event = Event>(node: EventTarget, name: string, callback: (event: E) => void, options?: AddEventListenerOptions) {
    const listener = callback as EventListener
    node.addEventListener(name, listener, options)
    listeners.push(() => node.removeEventListener(name, listener, options))
  }

  function screenPosition(position: THREE.Vector3): ScreenPosition {
    const screen = position.clone().project(camera)
    return { x: (screen.x + 1) * width / 2, y: (1 - screen.y) * height / 2,
      inside: screen.z >= -1 && screen.z <= 1 && screen.x > -1.05 && screen.x < 1.05 && screen.y > -1.05 && screen.y < 1.05 }
  }

  function makeAdminLabels(collection: BoundaryCollection, kind: 'city' | 'county') {
    for (const feature of collection.features) {
      const coordinate = feature.properties.label_position || feature.properties.centroid || feature.properties.center
      if (!coordinate) continue
      const node = element('span', `three-admin-label ${kind}`, feature.properties.name)
      node.dataset.adcode = String(feature.properties.adcode)
      labels.append(node)
      adminItems.push({ node, kind, text: feature.properties.name, position: worldPosition(...coordinate, .14) })
    }
  }
  makeAdminLabels(data.detailedCities, 'city')
  makeAdminLabels(data.counties, 'county')

  function overlap(a: ScreenBox, b: ScreenBox) {
    return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
  }

  function updateLabels() {
    camera.updateMatrixWorld()
    const occupied: ScreenBox[] = []
    for (const item of markerItems) {
      const point = screenPosition(item.position)
      item.screen = point
      item.node.hidden = !point.inside
      if (!point.inside) continue
      item.node.style.left = `${point.x}px`
      item.node.style.top = `${point.y}px`
      occupied.push({ left: point.x - 9, right: point.x + 9, top: point.y - 9, bottom: point.y + 9 })
    }
    // DOM 标签不受透视缩放影响，保持清晰的小字号；不挪动其地理锚点。
    for (const item of adminItems) {
      if ((item.kind === 'city' && !showCities) || (item.kind === 'county' && !showCounties)) {
        item.node.hidden = true
        continue
      }
      const point = screenPosition(item.position)
      const fontSize = item.kind === 'city' ? 10 : 8.5
      const textWidth = fontSize * Array.from(item.text).length + 5
      const box = { left: point.x - textWidth / 2, right: point.x + textWidth / 2, top: point.y - 7, bottom: point.y + 7 }
      const outside = !point.inside || box.left < 4 || box.right > width - 4 || box.top < 4 || box.bottom > height - 36
      const blocked = !showAllNames && occupied.some(existing => overlap(box, existing))
      item.node.hidden = outside || blocked
      if (outside || blocked) continue
      item.node.style.left = `${point.x}px`
      item.node.style.top = `${point.y}px`
      occupied.push(box)
    }
    if (tooltipEntity) placeTooltip(tooltipEntity)
    labelsDirty = false
  }

  function placeTooltip(item: MarkerItem) {
    const point = item.screen || screenPosition(item.position)
    const boxWidth = Math.min(244, width - 16)
    tooltip.style.width = `${boxWidth}px`
    tooltip.style.left = `${Math.max(8, Math.min(width - boxWidth - 8, point.x + 14))}px`
    tooltip.style.top = `${Math.max(8, Math.min(height - 139, point.y - 117))}px`
  }

  function showTooltip(item: MarkerItem) {
    tooltipEntity = item
    const entity = item.entity
    tooltipName.textContent = entity.name
    tooltipKind.textContent = KINDS[entity.type] || '地点'
    tooltipKind.style.color = COLORS[entity.type] || COLORS.scenic
    tooltipValue.textContent = `${METRICS[entity.type] || '客流'}：${Number.isFinite(entity.latest_value) ? Number(entity.latest_value).toLocaleString('zh-CN') + ' 人次' : '暂无数值'}`
    tooltipCoordinate.textContent = `经度 ${entity.longitude.toFixed(5)}° · 纬度 ${entity.latitude.toFixed(5)}°`
    tooltipNote.textContent = `${current.demo ? '静态样例 · ' : ''}连线仅示意地点关联，非实际出行轨迹`
    tooltip.hidden = false
    placeTooltip(item)
  }

  function hideTooltip() {
    tooltipEntity = null
    tooltip.hidden = true
  }

  const texture = glowTexture()
  textures.add(texture)
  function pointSprite(color: THREE.ColorRepresentation, size: number) {
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      color, map: texture, transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending,
    }))
    sprite.scale.set(size, size, 1)
    return sprite
  }

  function buildRoute(from: LocatedEntity, to: LocatedEntity, kind: 'rail_station' | 'airport', side: number, duration: number) {
    const start = worldPosition(from.longitude, from.latitude, .43)
    const end = worldPosition(to.longitude, to.latitude, .43)
    const distance = start.distanceTo(end)
    if (distance < .03) return
    const bend = Math.min(4, distance * .13) * side
    const control = start.clone().add(end).multiplyScalar(.5)
    control.x += (end.z - start.z) / distance * bend
    control.z -= (end.x - start.x) / distance * bend
    control.y = Math.max(2, Math.min(7, distance * .24))
    const curve = new THREE.QuadraticBezierCurve3(start, control, end)
    const color = COLORS[kind]
    const lineGeometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(100))
    flowGroup.add(new THREE.Line(lineGeometry, new THREE.LineBasicMaterial({ color, transparent: true, opacity: .48, depthWrite: false })))
    const tailGeometry = new THREE.BufferGeometry()
    const tailLength = 28
    tailGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(tailLength * 3), 3))
    const tailColors = new Float32Array(tailLength * 3)
    const tone = new THREE.Color(color)
    for (let i = 0; i < tailLength; i++) {
      const intensity = Math.pow(1 - i / tailLength, 1.3)
      tailColors.set([tone.r * intensity, tone.g * intensity, tone.b * intensity], i * 3)
    }
    tailGeometry.setAttribute('color', new THREE.BufferAttribute(tailColors, 3))
    const tail = new THREE.Line(tailGeometry, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: .96, depthWrite: false, blending: THREE.AdditiveBlending }))
    tail.frustumCulled = false
    const head = pointSprite(color, 1.1)
    const second = pointSprite(color, .72)
    flowGroup.add(tail, head, second)
    flows.push({ curve, head, second, tailGeometry, tailLength, duration, offset: kind === 'airport' ? .43 : 0 })
  }

  function setData(next: MapOptions) {
    if (disposed) return
    current = { ...current, ...next }
    hideTooltip()
    pointLayer.replaceChildren()
    markerItems.length = flows.length = pulseRings.length = 0
    releaseGroup(flowGroup)
    const valid = current.entities.filter(hasCoordinates)
    const selected = new Set([current.scenicId, current.railId, current.airportId])
    for (const entity of valid) {
      const isSelected = selected.has(entity.id)
      const node = element('button', `three-map-marker ${entity.type}${isSelected ? ' selected' : ''}`)
      node.type = 'button'
      node.dataset.entityId = entity.id
      node.style.setProperty('--point-color', COLORS[entity.type] || COLORS.scenic)
      const value = Number.isFinite(entity.latest_value) ? Number(entity.latest_value).toLocaleString('zh-CN') + ' 人次' : '暂无数值'
      node.setAttribute('aria-label', `${entity.name}，${KINDS[entity.type] || '地点'}，${value}`)
      node.append(element('span', 'three-point-core'))
      const item: MarkerItem = { entity, node, position: worldPosition(entity.longitude, entity.latitude, .48), screen: null }
      node.addEventListener('pointerenter', () => showTooltip(item))
      node.addEventListener('pointerleave', hideTooltip)
      node.addEventListener('focus', () => showTooltip(item))
      node.addEventListener('blur', hideTooltip)
      node.addEventListener('click', event => { event.stopPropagation(); current.onSelect?.(entity) })
      pointLayer.append(node)
      markerItems.push(item)
      if (isSelected) {
        const geometry = new THREE.RingGeometry(.32, .37, 48)
        geometry.rotateX(-Math.PI / 2)
        const material = new THREE.MeshBasicMaterial({ color: COLORS[entity.type], side: THREE.DoubleSide, transparent: true, opacity: .7, depthWrite: false })
        const ring = new THREE.Mesh(geometry, material)
        ring.position.copy(worldPosition(entity.longitude, entity.latitude, .11))
        flowGroup.add(ring)
        pulseRings.push(ring)
      }
    }
    const scenic = valid.find(entity => entity.id === current.scenicId)
    if (scenic) {
      for (const [id, kind, side, duration] of [[current.railId, 'rail_station', 1, 3900], [current.airportId, 'airport', -1, 4700]] as const) {
        const origin = valid.find(entity => entity.id === id)
        if (origin) buildRoute(origin, scenic, kind, side, duration)
      }
    }
    labelsDirty = true
    wake()
  }

  function paintFlows(time: number) {
    for (const flow of flows) {
      const t = reducedMotion ? .58 : (time / flow.duration + flow.offset) % 1
      flow.head.position.copy(flow.curve.getPoint(t))
      flow.second.position.copy(flow.curve.getPoint(reducedMotion ? .23 : (t + .5) % 1))
      const position = flow.tailGeometry.getAttribute('position')
      for (let i = 0; i < flow.tailLength; i++) {
        const point = flow.curve.getPoint(Math.max(0, t - i * .0045))
        position.setXYZ(i, point.x, point.y, point.z)
      }
      position.needsUpdate = true
    }
    for (let i = 0; i < pulseRings.length; i++) {
      const pulse = reducedMotion ? .2 : (time / 2100 + i * .26) % 1
      pulseRings[i].scale.setScalar(1 + pulse * 2.3)
      pulseRings[i].material.opacity = .65 * (1 - pulse)
    }
  }

  function animate(time: number) {
    frame = 0
    if (disposed || !inView || document.hidden) return
    if (time - previousPaint >= 30 || reducedMotion) {
      previousPaint = time
      controls.update()
      if (labelsDirty) updateLabels()
      paintFlows(time)
      renderer.render(scene, camera)
    }
    if (!reducedMotion && !frame) frame = requestAnimationFrame(animate)
  }

  function wake() {
    if (!disposed && !frame && inView && !document.hidden) frame = requestAnimationFrame(animate)
  }

  function fitCamera(resetOrientation = false) {
    if (resetOrientation) {
      controls.target.copy(mapCenter)
      camera.position.copy(mapCenter).add(topView ? new THREE.Vector3(0, 120, .12) : new THREE.Vector3(4, 110, 55))
      camera.up.set(0, 1, 0)
      camera.lookAt(mapCenter)
      camera.zoom = 1
      controls.update()
    }
    camera.updateMatrixWorld(true)
    let halfWidth = 0
    let halfHeight = 0
    for (const x of [globeBounds.min.x, globeBounds.max.x]) {
      for (const y of [globeBounds.min.y, globeBounds.max.y + 1]) {
        for (const z of [globeBounds.min.z, globeBounds.max.z]) {
          const point = new THREE.Vector3(x, y, z).applyMatrix4(camera.matrixWorldInverse)
          halfWidth = Math.max(halfWidth, Math.abs(point.x))
          halfHeight = Math.max(halfHeight, Math.abs(point.y))
        }
      }
    }
    const aspect = width / height
    const vertical = Math.max(halfHeight * FIT_PADDING, halfWidth / aspect * FIT_PADDING)
    camera.left = -vertical * aspect
    camera.right = vertical * aspect
    camera.top = vertical
    camera.bottom = -vertical
    camera.updateProjectionMatrix()
    labelsDirty = true
    wake()
  }

  function resize() {
    const bounds = host.getBoundingClientRect()
    width = Math.max(1, bounds.width)
    height = Math.max(1, bounds.height)
    renderer.setSize(width, height, false)
    fitCamera(false)
  }

  function toggle(text: string, initialValue: boolean, changed: (value: boolean) => void) {
    const label = element('label')
    const input = element('input')
    input.type = 'checkbox'
    input.checked = initialValue
    label.append(input, element('span', '', text))
    listen(input, 'change', () => { changed(input.checked); labelsDirty = true; wake() })
    labelControls.append(label)
  }
  toggle('地级市', true, value => { showCities = value })
  toggle('区县', true, value => { showCounties = value; countyBorder.opacity = value ? .26 : .08 })
  toggle('全部地名', false, value => { showAllNames = value })
  function button(text: string, title: string, callback: () => void) {
    const node = element('button', '', text)
    node.type = 'button'
    node.title = title
    node.setAttribute('aria-label', title)
    listen(node, 'click', callback)
    viewControls.append(node)
    return node
  }
  button('返回全省', '返回全省并恢复默认倾斜视角', () => { topView = false; viewButton.textContent = '俯视'; fitCamera(true) })
  const viewButton = button('俯视', '切换俯视与立体视角', () => { topView = !topView; viewButton.textContent = topView ? '立体' : '俯视'; fitCamera(true) })
  button('+', '放大地图', () => { camera.zoom = Math.min(controls.maxZoom, camera.zoom * 1.35); camera.updateProjectionMatrix(); labelsDirty = true; wake() })
  button('−', '缩小地图', () => { camera.zoom = Math.max(controls.minZoom, camera.zoom / 1.35); camera.updateProjectionMatrix(); labelsDirty = true; wake() })
  controls.addEventListener('change', () => { labelsDirty = true; wake() })
  controls.addEventListener('start', hideTooltip)
  let lastPointer = 0
  listen<PointerEvent>(renderer.domElement, 'pointermove', event => {
    if (event.timeStamp - lastPointer < 80) return
    lastPointer = event.timeStamp
    const bounds = renderer.domElement.getBoundingClientRect()
    cursor.set((event.clientX - bounds.left) / bounds.width * 2 - 1, -((event.clientY - bounds.top) / bounds.height * 2 - 1))
    raycaster.setFromCamera(cursor, camera)
    if (raycaster.ray.intersectPlane(plane, hit)) {
      const [lon, lat] = unproject(hit.x, hit.z)
      cursorPosition.textContent = `${lon.toFixed(4)}°E / ${lat.toFixed(4)}°N`
    }
  })
  listen(renderer.domElement, 'pointerleave', () => { cursorPosition.textContent = 'GCJ-02 · 墨卡托投影' })
  listen(renderer.domElement, 'webglcontextlost', event => {
    event.preventDefault()
    if (disposed) return
    const message = element('div', 'three-map-error', '3D 图形上下文已中断，请刷新页面重新加载地图。')
    host.append(message)
  })
  const observer = new ResizeObserver(resize)
  observer.observe(host)
  const visibilityObserver = new IntersectionObserver(entries => {
    inView = entries[0]?.isIntersecting ?? true
    if (inView) { labelsDirty = true; wake() }
    else { cancelAnimationFrame(frame); frame = 0 }
  })
  visibilityObserver.observe(host)
  listen(document, 'visibilitychange', () => {
    if (document.hidden) { cancelAnimationFrame(frame); frame = 0 }
    else { labelsDirty = true; wake() }
  })
  listen(motionPreference, 'change', () => {
    reducedMotion = motionPreference.matches
    controls.enableDamping = !reducedMotion
    host.classList.toggle('reduce-motion', reducedMotion)
    wake()
  })
  host.classList.toggle('reduce-motion', reducedMotion)
  const bounds = host.getBoundingClientRect()
  width = Math.max(1, bounds.width)
  height = Math.max(1, bounds.height)
  renderer.setSize(width, height, false)
  fitCamera(true)
  setData(initial)

  return {
    setData,
    dispose() {
      if (disposed) return
      disposed = true
      cancelAnimationFrame(frame)
      observer.disconnect()
      visibilityObserver.disconnect()
      listeners.forEach(remove => remove())
      controls.dispose()
      releaseGroup(scene)
      textures.forEach(texture => texture.dispose())
      renderer.dispose()
      renderer.forceContextLoss()
      host.replaceChildren()
    },
  }
}
