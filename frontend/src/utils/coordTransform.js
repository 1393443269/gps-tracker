// WGS-84 ↔ GCJ-02 坐标转换
// ============================================================
// 设备上报的 GPS 坐标是 WGS-84（国际标准）；国内地图底图（高德/天地图/
// DataV GeoJSON）用 GCJ-02（国测局加密偏移）。二者直接叠加会偏移几百米~公里，
// 故渲染到地图前需把 WGS-84 转成 GCJ-02。
// 数据库保持原始 WGS-84 不动，仅在前端显示层转换。
// 算法为业界通用实现（纯数学，不依赖任何第三方服务）。

const PI = Math.PI
const A = 6378245.0            // 长半轴
const EE = 0.00669342162296594323  // 偏心率平方

function _transformLat(lng, lat) {
  let ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat +
    0.1 * lng * lat + 0.2 * Math.sqrt(Math.abs(lng))
  ret += (20.0 * Math.sin(6.0 * lng * PI) + 20.0 * Math.sin(2.0 * lng * PI)) * 2.0 / 3.0
  ret += (20.0 * Math.sin(lat * PI) + 40.0 * Math.sin(lat / 3.0 * PI)) * 2.0 / 3.0
  ret += (160.0 * Math.sin(lat / 12.0 * PI) + 320 * Math.sin(lat * PI / 30.0)) * 2.0 / 3.0
  return ret
}

function _transformLng(lng, lat) {
  let ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng +
    0.1 * lng * lat + 0.1 * Math.sqrt(Math.abs(lng))
  ret += (20.0 * Math.sin(6.0 * lng * PI) + 20.0 * Math.sin(2.0 * lng * PI)) * 2.0 / 3.0
  ret += (20.0 * Math.sin(lng * PI) + 40.0 * Math.sin(lng / 3.0 * PI)) * 2.0 / 3.0
  ret += (150.0 * Math.sin(lng / 12.0 * PI) + 300.0 * Math.sin(lng / 30.0 * PI)) * 2.0 / 3.0
  return ret
}

// 判断是否在中国境外——境外不做偏移（GCJ-02 只对中国大陆加密）
function _outOfChina(lng, lat) {
  return lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271
}

/**
 * WGS-84 → GCJ-02
 * @param {number} lng 经度（WGS-84）
 * @param {number} lat 纬度（WGS-84）
 * @returns {[number, number]} [gcjLng, gcjLat]
 */
export function wgs84ToGcj02(lng, lat) {
  lng = Number(lng); lat = Number(lat)
  if (!isFinite(lng) || !isFinite(lat)) return [lng, lat]
  if (_outOfChina(lng, lat)) return [lng, lat]
  let dLat = _transformLat(lng - 105.0, lat - 35.0)
  let dLng = _transformLng(lng - 105.0, lat - 35.0)
  const radLat = lat / 180.0 * PI
  let magic = Math.sin(radLat)
  magic = 1 - EE * magic * magic
  const sqrtMagic = Math.sqrt(magic)
  dLat = (dLat * 180.0) / ((A * (1 - EE)) / (magic * sqrtMagic) * PI)
  dLng = (dLng * 180.0) / (A / sqrtMagic * Math.cos(radLat) * PI)
  return [lng + dLng, lat + dLat]
}

/**
 * 便捷版：传入含 lng/lat 的对象或 [lng,lat]，返回转换后的 [lng,lat]。
 * 容错：空值/非法值原样返回，避免地图渲染因个别坏点崩溃。
 */
export function toGcj02(lng, lat) {
  return wgs84ToGcj02(lng, lat)
}
