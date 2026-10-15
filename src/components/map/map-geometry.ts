import { Shape, Path, Vector2, Vector3, ExtrudeGeometry } from 'three'
import type { BoundaryFeature, BoundaryCollection, MapEntity, LocatedEntity } from './map-types'

// 边界与业务点位沿用原地图的 GCJ-02 经纬度口径，统一投影后再绘制。
// 场景单位约等于墨卡托平面上的 10 km，不把经纬度当作线性平面坐标。
export const ORIGIN = [117.28, 31.83] as const
export const SCALE = 637.8137
const rad = Math.PI / 180
const originMercator = Math.log(Math.tan(Math.PI / 4 + ORIGIN[1] * rad / 2))

export function project(longitude: number, latitude: number): Vector2 {
  return new Vector2(
    (longitude - ORIGIN[0]) * rad * SCALE,
    (Math.log(Math.tan(Math.PI / 4 + latitude * rad / 2)) - originMercator) * SCALE,
  )
}

export function unproject(x: number, south: number): [number, number] {
  return [
    ORIGIN[0] + x / SCALE / rad,
    (2 * Math.atan(Math.exp(originMercator - south / SCALE)) - Math.PI / 2) / rad,
  ]
}

export function hasCoordinates(entity: MapEntity): entity is LocatedEntity {
  return typeof entity.longitude === 'number' && Number.isFinite(entity.longitude)
    && typeof entity.latitude === 'number' && Number.isFinite(entity.latitude)
    && Math.abs(entity.latitude) < 85 && Math.abs(entity.longitude) <= 180
}

export function worldPosition(longitude: number, latitude: number, height = .2): Vector3 {
  const projected = project(longitude, latitude)
  return new Vector3(projected.x, height, -projected.y)
}

export function polygonsOf(feature: BoundaryFeature) {
  return feature.geometry.type === 'Polygon' ? [feature.geometry.coordinates] : feature.geometry.coordinates
}

export function shapesOf(feature: BoundaryFeature): Shape[] {
  return polygonsOf(feature).map(polygon => {
    const shape = new Shape(polygon[0].map(([lon, lat]) => project(lon, lat)))
    for (const hole of polygon.slice(1)) shape.holes.push(new Path(hole.map(([lon, lat]) => project(lon, lat))))
    return shape
  })
}

export function extrudeFeature(feature: BoundaryFeature, depth = 1.4): ExtrudeGeometry {
  const geometry = new ExtrudeGeometry(shapesOf(feature), {
    depth, steps: 1, bevelEnabled: false, curveSegments: 1,
  })
  // 只向下挤出厚度；保留顶面原有轮廓、多边形及内部孔洞。
  geometry.rotateX(-Math.PI / 2)
  geometry.translate(0, -depth, 0)
  return geometry
}

export function borderPositions(collection: BoundaryCollection, height: number): Float32Array {
  const positions: number[] = []
  for (const feature of collection.features) {
    for (const polygon of polygonsOf(feature)) {
      for (const ring of polygon) {
        for (let i = 1; i < ring.length; i++) {
          const a = worldPosition(ring[i - 1][0], ring[i - 1][1], height)
          const b = worldPosition(ring[i][0], ring[i][1], height)
          positions.push(a.x, a.y, a.z, b.x, b.y, b.z)
        }
      }
    }
  }
  return new Float32Array(positions)
}
