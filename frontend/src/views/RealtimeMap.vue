<template>
  <div style="position:relative; height:calc(100vh - 120px);">
    <!-- 地图容器 -->
    <div id="map-container" style="width:100%; height:100%; border-radius:8px;"></div>

    <!-- 设备面板 -->
    <div class="device-panel">
      <div class="panel-title">在线设备 ({{ onlineDevices.length }})</div>
      <div
        v-for="item in onlineDevices"
        :key="item.phone"
        class="device-item"
        @click="item.hasLoc ? flyTo(item.phone) : null"
        :style="item.hasLoc ? 'cursor:pointer' : 'cursor:default;opacity:.7'"
      >
        <el-icon :color="item.alarm ? '#f56c6c' : (item.roleColor || '#67c23a')" style="flex-shrink:0;"><Location /></el-icon>
        <span class="phone">{{ item.phone }}</span>
        <span class="devname">{{ item.name || '—' }}</span>
        <span v-if="!item.hasLoc" style="font-size:10px;color:#aaa;margin-left:4px">无坐标</span>
      </div>
    </div>

    <!-- 连接状态 -->
    <div class="ws-status" :class="wsConnected ? 'connected' : 'disconnected'">
      {{ wsConnected ? '● 实时连接' : '○ 断线重连中…' }}
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, computed } from 'vue'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { io } from 'socket.io-client'
import { deviceApi, portalApi, isAdmin } from '@/api'
import { ElNotification, ElMessage } from 'element-plus'
import { TDT_MAP_STYLE } from '@/utils/mapStyle'

// ── 常量：GeoJSON source / layer 标识 ──────────────────────────────────────────
const SRC_ID   = 'devices'
const LAYER_ID = 'devices-circle'

// ── 响应式状态 ────────────────────────────────────────────────────────────────
const wsConnected  = ref(false)
// phone → GeoJSON Point feature（替代原 markerStore，无 DOM Marker）
// feature.properties 承载渲染 + 面板所需的全部字段
const featureStore = reactive({})
const allOnlineDevices = ref([])   // status=1 的全量设备列表

// 合并：有坐标的来自 featureStore(feature.properties)，无坐标的来自 allOnlineDevices
const onlineDevices = computed(() => {
  const withLoc = Object.values(featureStore).map(f => ({ ...f.properties, hasLoc: true }))
  const phones  = new Set(withLoc.map(d => d.phone))
  const noLoc   = allOnlineDevices.value
    .filter(d => !phones.has(String(d.phone)))
    .map(d => ({ phone: String(d.phone), name: d.name || '', alarm: d.status === 2, hasLoc: false,
                 roleName: d.role_name, roleColor: d.role_color, roleIcon: d.role_icon }))
  return [...withLoc, ...noLoc]
})

const deviceList = computed(() => Object.values(featureStore).map(f => f.properties))

let map
let socket
let popup           // 单个可复用 popup，点击设备点时按需展示
let onlineTimer = null

// ── 颜色/半径：data-driven paint 会读取 properties.color / properties.radius ─────
// 报警时统一红色高亮并放大；否则用角色颜色，无角色回落默认蓝
function markerColor(info) {
  return info.alarm ? '#f56c6c' : (info.roleColor || '#409eff')
}
function markerRadius(info) {
  return info.alarm ? 9 : 7
}

// HTML 转义，防止 XSS（phone/time 等字段来自服务端）
function _esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;')
}

function popupHtml(info) {
  const roleLine = info.roleName
    ? `角色: <span style="color:${_esc(info.roleColor || '#409eff')}">${_esc(info.roleName)}</span><br>`
    : ''
  return `<b>${_esc(info.phone)}</b><br>
    ${roleLine}纬度: ${Number(info.lat).toFixed(6)}<br>
    经度: ${Number(info.lng).toFixed(6)}<br>
    速度: ${_esc(info.speed)} km/h<br>
    时间: ${_esc(info.time)}<br>
    ${info.alarm ? '<span style="color:red;">⚠ 报警中</span>' : '正常'}`
}

// ── 构建单个设备的 GeoJSON feature（properties 含渲染 + 面板字段） ───────────────
function makeFeature(info) {
  const props = {
    ...info,
    phone: String(info.phone),
    color:  markerColor(info),
    radius: markerRadius(info),
    // 报警标志，供 paint 表达式做描边/动画区分（circle 无法做形状，用颜色+大小近似）
    alarmFlag: info.alarm ? 1 : 0,
  }
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [info.lng, info.lat] },
    properties: props,
  }
}

// ── 把 featureStore 全量刷进 source（setData 整个 FeatureCollection） ────────────
function refreshSource() {
  const src = map && map.getSource(SRC_ID)
  if (!src) return
  src.setData({
    type: 'FeatureCollection',
    features: Object.values(featureStore),
  })
}

// ── 更新/新建设备点（替代原 updateMarker，写内存映射后刷 source） ────────────────
function updateFeature(info, { refresh = true } = {}) {
  const { lat, lng } = info
  if (!lat || !lng) return
  const phone = String(info.phone)

  if (featureStore[phone]) {
    const prev = featureStore[phone].properties || {}
    // 合并：新推送可能不含角色字段（如报警事件），沿用已有的
    const merged = {
      ...prev, ...info, phone,
      name:      info.name      ?? prev.name      ?? '',
      roleName:  info.roleName  ?? prev.roleName,
      roleColor: info.roleColor ?? prev.roleColor,
      roleIcon:  info.roleIcon  ?? prev.roleIcon,
    }
    featureStore[phone] = makeFeature(merged)
  } else {
    featureStore[phone] = makeFeature({ ...info, phone })
  }

  if (refresh) refreshSource()
}

// ── 清理离线设备（对比在线 phone 集合，移除不在集合内的 feature，保留原清理语义） ──
function pruneFeatures(onlinePhones) {
  const keep = new Set([...onlinePhones].map(p => String(p)))
  for (const ph of Object.keys(featureStore)) {
    if (!keep.has(String(ph))) {
      delete featureStore[ph]
    }
  }
}

function flyTo(phone) {
  const f = featureStore[String(phone)]
  if (f) {
    map.flyTo({ center: f.geometry.coordinates, zoom: 15 })
  }
}

// ── 初始位置加载 ──────────────────────────────────────────────────────────────
async function loadInitialPositions() {
  try {
    const res = isAdmin() ? await deviceApi.list({ size: 500 }) : await portalApi.deviceList({ size: 500 })
    const records = res.data?.records || []
    // 更新在线设备列表（含无坐标）
    allOnlineDevices.value = records.filter(d => d.status === 1 || d.status === 2)
    // 有坐标的显示在地图上
    const locatedPhones = new Set()
    for (const d of records) {
      if (d.last_lat && d.last_lng) {
        locatedPhones.add(String(d.phone))
        updateFeature({
          phone: d.phone,
          name:  d.name || '',
          lat:   d.last_lat,
          lng:   d.last_lng,
          speed: d.last_speed != null ? (d.last_speed / 10).toFixed(1) : '0.0',
          alarm: d.status === 2,
          time:  d.last_location_time || '',
          roleName:  d.role_name,
          roleColor: d.role_color,
          roleIcon:  d.role_icon,
        }, { refresh: false })
      }
    }
    // 移除已离线（不再返回坐标）设备的点，避免 featureStore 无限增长
    pruneFeatures(locatedPhones)
    // 全量刷一次 source（批量加载时避免逐点 setData）
    refreshSource()
  } catch (e) {
    console.warn('[RealtimeMap] 初始位置加载失败:', e)
  }
}

// ── 在线设备列表刷新（不更新地图点，只刷新面板） ─────────────────────────────────
async function loadOnlineDevices() {
  try {
    const res = isAdmin()
      ? await deviceApi.list({ status: 1, size: 500 })
      : await portalApi.deviceList({ status: 1, size: 500 })
    allOnlineDevices.value = res.data?.records || []
  } catch (e) {
    console.warn('[RealtimeMap] 在线设备列表刷新失败:', e)
  }
}

// ── 地图 source / layer / 交互初始化 ────────────────────────────────────────────
function initDeviceLayer() {
  // 空的 FeatureCollection source，后续 setData 增量刷新
  map.addSource(SRC_ID, {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
  })

  // circle layer：颜色/半径用 data-driven 表达式按设备属性区分（在线/报警/角色色）
  map.addLayer({
    id: LAYER_ID,
    type: 'circle',
    source: SRC_ID,
    paint: {
      'circle-color': ['get', 'color'],
      'circle-radius': ['coalesce', ['get', 'radius'], 7],
      'circle-stroke-width': 2,
      // 报警点白描边加粗高亮（circle 做不了形状，用颜色+大小+描边近似原形状区分）
      'circle-stroke-color': ['case', ['==', ['get', 'alarmFlag'], 1], '#ffffff', '#ffffff'],
      'circle-opacity': 0.95,
    },
  })

  // 可复用 popup（点击时按需展示，不再每设备常驻 DOM popup）
  popup = new maplibregl.Popup({ closeButton: true, maxWidth: '240px' })

  // 点击设备点 → 弹 popup 显示详情
  map.on('click', LAYER_ID, (e) => {
    const feat = e.features && e.features[0]
    if (!feat) return
    const info = feat.properties || {}
    popup
      .setLngLat(feat.geometry.coordinates)
      .setHTML(popupHtml(info))
      .addTo(map)
  })

  // hover：鼠标样式区分可点击的设备点
  map.on('mouseenter', LAYER_ID, () => { map.getCanvas().style.cursor = 'pointer' })
  map.on('mouseleave', LAYER_ID, () => { map.getCanvas().style.cursor = '' })
}

// ── Socket.IO ─────────────────────────────────────────────────────────────────
function connectSocket() {
  // 使用当前页面域名（支持任意部署环境），携带 token 供服务端验证身份和加入组织房间
  const token = localStorage.getItem('admin_token') || localStorage.getItem('customer_token') || ''
  socket = io(window.location.origin, {
    transports: ['websocket', 'polling'],
    auth: { token },
  })

  socket.on('connect',    () => { wsConnected.value = true  })
  socket.on('disconnect', () => { wsConnected.value = false })
  socket.on('connect_error', (err) => {
    wsConnected.value = false
    console.warn('[RealtimeMap] 实时连接失败，自动重连中:', err?.message || err)
  })

  socket.on('location_update', (data) => {
    // 更新对应 feature 的坐标和属性，再 setData 整个 FeatureCollection
    updateFeature({
      phone: data.phone,
      lat:   data.lat,
      lng:   data.lng,
      speed: String(data.speed),
      alarm: data.alarm,
      time:  data.time,
      roleName:  data.roleName,
      roleColor: data.roleColor,
      roleIcon:  data.roleIcon,
    })
  })

  socket.on('alarm', (data) => {
    ElNotification({
      title:   `⚠ 报警: ${_esc(data.phone || '未知设备')}`,
      message: `${_esc(data.alarmDesc || '报警')} | ${_esc(data.time || '')}`,
      dangerouslyUseHTMLString: false,
      type:    'error',
      duration: 6000,
    })
  })
}

// ── 生命周期 ──────────────────────────────────────────────────────────────────
onMounted(async () => {
  try {
    map = new maplibregl.Map({
      container: 'map-container',
      style: TDT_MAP_STYLE,
      center: [114.3, 30.5],
      zoom: 5,
    })
    map.addControl(new maplibregl.NavigationControl(), 'top-left')

    await new Promise(r => map.once('load', r))

    initDeviceLayer()
    await loadInitialPositions()
    connectSocket()
    // 每 15 秒刷新在线设备列表（面板实时显示无坐标设备）
    onlineTimer = setInterval(loadOnlineDevices, 15000)
  } catch (e) {
    console.warn('[RealtimeMap] 地图初始化失败:', e)
    ElMessage.error('地图加载失败，请刷新页面重试')
  }
})

onUnmounted(() => {
  if (onlineTimer) clearInterval(onlineTimer)
  // 先解绑所有 socket 监听再断开，避免监听器/闭包残留导致内存泄漏
  if (socket) {
    socket.off()
    socket.disconnect()
    socket = null
  }
  // 清理 popup（GeoJSON 方案下无 DOM Marker，泄漏问题自然消失）
  if (popup) {
    popup.remove()
    popup = null
  }
  // map.remove() 会一并销毁 source/layer 及其绑定的事件监听
  map?.remove()
  map = null
})
</script>

<style scoped>
.device-panel {
  position: absolute; top: 10px; right: 10px;
  width: 230px; max-height: 60vh; overflow-y: auto;
  background: rgba(255,255,255,.93); border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0,0,0,.15);
  z-index: 1000; padding: 10px;
}
.panel-title { font-weight: 600; font-size: 13px; color: #303133; margin-bottom: 8px; }
.device-item {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 6px; border-radius: 4px; cursor: pointer; font-size: 12px;
}
.device-item:hover { background: #f0f2f5; }
.phone { flex: 1; color: #303133; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.devname { color: #909399; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 70px; }
.ws-status {
  position: absolute; bottom: 16px; left: 16px;
  padding: 4px 12px; border-radius: 12px; font-size: 12px; z-index: 1000;
}
.connected    { background: #f0f9eb; color: #67c23a; }
.disconnected { background: #fef0f0; color: #f56c6c; }
</style>
