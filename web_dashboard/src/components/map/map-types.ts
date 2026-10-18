import type { Entity } from '../../app/types'

export type MapEntity = Entity & {
  latest_value?: number | null
  latest_metric?: string
  latest_period_end?: string
  latest_grain?: string
  unit?: string
}

export type LocatedEntity = MapEntity & { longitude: number; latitude: number }
export type Coordinate = [number, number]
export type PolygonCoordinates = Coordinate[][]

export interface BoundaryFeature {
  type: 'Feature'
  properties: {
    adcode: number
    name: string
    label_position?: Coordinate
    center?: Coordinate
    centroid?: Coordinate
  }
  geometry:
    | { type: 'Polygon'; coordinates: PolygonCoordinates }
    | { type: 'MultiPolygon'; coordinates: PolygonCoordinates[] }
}

export interface BoundaryCollection {
  type: 'FeatureCollection'
  features: BoundaryFeature[]
}

export interface MapBoundaries {
  source: string
  source_url: string
  retrieved_at: string
  boundary_version: string
  geometry_processing: string
  counts: { cities: number; counties: number; county_vertices: number }
  detailedCities: BoundaryCollection
  detailedProvince: BoundaryCollection
  counties: BoundaryCollection
}

export interface MapOptions {
  entities: readonly MapEntity[]
  scenicId?: string
  railId?: string
  airportId?: string
  demo?: boolean
  onSelect?: (entity: MapEntity) => void
}

export interface MapController {
  setData(options: MapOptions): void
  dispose(): void
}
