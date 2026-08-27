/**
 * 天地图 WMTS 底图配置（MapLibre GL JS 格式）
 * 申请 tk：https://console.tianditu.gov.cn/ → 创建应用 → 复制 key
 * 拿到 key 后把 YOUR_TDT_TOKEN 替换掉，整个项目只改这一处。
 */
export const TDT_TOKEN = import.meta.env.VITE_TDT_TOKEN || ''

/** 生成 t0~t7 多节点负载均衡 URL 数组 */
function tdtTiles(layer) {
  return Array.from({ length: 8 }, (_, i) =>
    `https://t${i}.tianditu.gov.cn/${layer}_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0` +
    `&LAYER=${layer}&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles` +
    `&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${TDT_TOKEN}`
  )
}

/**
 * MapLibre GL JS style：矢量底图 + 中文注记叠加
 * 直接传给 new maplibregl.Map({ style: TDT_MAP_STYLE })
 */
export const TDT_MAP_STYLE = {
  version: 8,
  sources: {
    'tdt-vec': {
      type: 'raster',
      tiles: tdtTiles('vec'),
      tileSize: 256,
      maxzoom: 18,
      attribution: '© <a href="https://www.tianditu.gov.cn/" target="_blank">天地图</a>',
    },
    'tdt-cva': {
      type: 'raster',
      tiles: tdtTiles('cva'),
      tileSize: 256,
      maxzoom: 18,
    },
  },
  layers: [
    { id: 'tdt-vec-layer', type: 'raster', source: 'tdt-vec' },
    { id: 'tdt-cva-layer', type: 'raster', source: 'tdt-cva' },
  ],
}

/**
 * 将「圆心 + 半径(米)」转换为 GeoJSON Polygon，用于地图绘制围栏。
 * MapLibre 不支持地理圆，需手动多边形近似。
 * @param {[number, number]} center  [lng, lat]
 * @param {number} radiusMeters      半径（米）
 * @param {number} steps             多边形顶点数，越大越圆，默认 64
 */
export function circleToPolygon(center, radiusMeters, steps = 64) {
  const [lng, lat] = center
  const latRad = lat * Math.PI / 180
  const coords = []
  for (let i = 0; i <= steps; i++) {
    const angle = (i / steps) * 2 * Math.PI
    const dLat = (radiusMeters * Math.sin(angle) / 6371000) * (180 / Math.PI)
    const dLng = (radiusMeters * Math.cos(angle) / (6371000 * Math.cos(latRad))) * (180 / Math.PI)
    coords.push([lng + dLng, lat + dLat])
  }
  return { type: 'Polygon', coordinates: [coords] }
}
