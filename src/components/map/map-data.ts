import type { MapBoundaries } from './map-types'

let pending: Promise<MapBoundaries> | null = null
let pendingUrl = ''

/** 边界随前端发布，兼容 Vite 的根目录或子路径部署，不调用第三方地图 API。 */
export function loadMapBoundaries(baseUrl: string): Promise<MapBoundaries> {
  const url = `${baseUrl.replace(/\/?$/, '/')}geo/anhui-boundaries.json`
  if (pending && pendingUrl === url) return pending
  pendingUrl = url
  const request = fetch(url)
    .then(async response => {
      if (!response.ok) throw new Error(`行政边界读取失败（HTTP ${response.status}）`)
      const data = await response.json() as MapBoundaries
      if (!data.detailedCities?.features.length || !data.detailedProvince?.features.length || !data.counties?.features.length) {
        throw new Error('行政边界文件不完整，请重新发布 public/geo 中的数据文件。')
      }
      return data
    })
    .catch(error => {
      if (pending === request) pending = null
      throw error
    })
  pending = request
  return request
}
