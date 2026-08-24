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
        <el-icon :color="item.alarm ? '#f56c6c' : '#67c23a'" style="flex-shrink:0;"><Location /></el-icon>
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
import { ElNotification } from 'element-plus'
import { TDT_MAP_STYLE } from '@/utils/mapStyle'

// ── 响应式状态 ────────────────────────────────────────────────────────────────
const wsConnected  = ref(false)
const markerStore  = reactive({})  // phone → { marker, popup, info }
const allOnlineDevices = ref([])   // status=1 的全量设备列表

// 合并：有坐标的来自 markerStore，无坐标的来自 allOnlineDevices
const onlineDevices = computed(() => {
  const withLoc = Object.values(markerStore).map(v => ({ ...v.info, hasLoc: true }))
  const phones  = new Set(withLoc.map(d => d.phone))
  const noLoc   = allOnlineDevices.value
    .filter(d => !phones.has(String(d.phone)))
    .map(d => ({ phone: String(d.phone), name: d.name || '', alarm: d.status === 2, hasLoc: false }))
  return [...withLoc, ...noLoc]
})

const deviceList = computed(() => Object.values(markerStore).map(v => v.info))

let map
let socket
let onlineTimer = null

// ── 标记元素 ──────────────────────────────────────────────────────────────────
function makeMarkerEl(alarm) {
  const el = document.createElement('div')
  const color = alarm ? '#f56c6c' : '#409eff'
  el.className = alarm ? 'dev-marker dev-marker-alarm' : 'dev-marker'
  el.style.cssText = [
    'width:14px', 'height:14px', 'border-radius:50%',
    `background:${color}`, 'border:2px solid #fff',
    'box-shadow:0 0 6px rgba(0,0,0,.4)', 'cursor:pointer',
  ].join(';')
  return el
}

// HTML 转义，防止 XSS（phone/time 等字段来自服务端）
function _esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;')
}

function popupHtml(info) {
  return `<b>${_esc(info.phone)}</b><br>
    纬度: ${Number(info.lat).toFixed(6)}<br>
    经度: ${Number(info.lng).toFixed(6)}<br>
    速度: ${_esc(info.speed)} km/h<br>
    时间: ${_esc(info.time)}<br>
    ${info.alarm ? '<span style="color:red;">⚠ 报警中</span>' : '正常'}`
}

// ── 更新/新建标记 ─────────────────────────────────────────────────────────────
function updateMarker(info) {
  const { phone, lat, lng } = info
  if (!lat || !lng) return

  if (markerStore[phone]) {
    const { marker, popup } = markerStore[phone]
    const merged = { ...info, name: info.name ?? markerStore[phone].info?.name ?? '' }

    marker.setLngLat([lng, lat])

    // 更新颜色和动画类
    const el = marker.getElement()
    el.style.background = merged.alarm ? '#f56c6c' : '#409eff'
    el.className = merged.alarm ? 'dev-marker dev-marker-alarm' : 'dev-marker'

    popup.setHTML(popupHtml(merged))
    markerStore[phone].info = merged
  } else {
    const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '240px' })
      .setHTML(popupHtml(info))

    const marker = new maplibregl.Marker({ element: makeMarkerEl(info.alarm) })
      .setLngLat([lng, lat])
      .setPopup(popup)
      .addTo(map)

    markerStore[phone] = { marker, popup, info: { ...info } }
  }
}

function flyTo(phone) {
  const item = markerStore[phone]
  if (item) {
    map.flyTo({ center: [item.info.lng, item.info.lat], zoom: 15 })
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
    for (const d of records) {
      if (d.last_lat && d.last_lng) {
        updateMarker({
          phone: d.phone,
          name:  d.name || '',
          lat:   d.last_lat,
          lng:   d.last_lng,
          speed: d.last_speed != null ? (d.last_speed / 10).toFixed(1) : '0.0',
          alarm: d.status === 2,
          time:  d.last_location_time || '',
        })
      }
    }
  } catch {}
}

// ── 在线设备列表刷新（不更新地图标记，只刷新面板） ───────────────────────────────
async function loadOnlineDevices() {
  try {
    const res = isAdmin()
      ? await deviceApi.list({ status: 1, size: 500 })
      : await portalApi.deviceList({ status: 1, size: 500 })
    allOnlineDevices.value = res.data?.records || []
  } catch {}
}

// ── Socket.IO ─────────────────────────────────────────────────────────────────
function connectSocket() {
  // 使用当前页面域名（支持任意部署环境），携带 token 供服务端验证身份和加入组织房间
  const token = localStorage.getItem('admin_token') || localStorage.getItem('customer_token') || ''
  socket = io(window.location.origin, {
    transports: ['websocket', 'polling'],
    query: { token },
  })

  socket.on('connect',    () => { wsConnected.value = true  })
  socket.on('disconnect', () => { wsConnected.value = false })

  socket.on('location_update', (data) => {
    updateMarker({
      phone: data.phone,
      lat:   data.lat,
      lng:   data.lng,
      speed: String(data.speed),
      alarm: data.alarm,
      time:  data.time,
    })
  })

  socket.on('alarm', (data) => {
    ElNotification({
      title:   `⚠ 报警: ${data.phone}`,
      message: `${data.alarmDesc} | ${data.time}`,
      type:    'error',
      duration: 6000,
    })
  })
}

// ── 生命周期 ──────────────────────────────────────────────────────────────────
onMounted(async () => {
  map = new maplibregl.Map({
    container: 'map-container',
    style: TDT_MAP_STYLE,
    center: [114.3, 30.5],
    zoom: 5,
  })
  map.addControl(new maplibregl.NavigationControl(), 'top-left')

  await new Promise(r => map.once('load', r))

  await loadInitialPositions()
  connectSocket()
  // 每 15 秒刷新在线设备列表（面板实时显示无坐标设备）
  onlineTimer = setInterval(loadOnlineDevices, 15000)
})

onUnmounted(() => {
  if (onlineTimer) clearInterval(onlineTimer)
  socket?.disconnect()
  map?.remove()
})
</script>

<!-- 非 scoped：marker 元素是 JS 动态创建的 DOM，scoped 选择器匹配不到 -->
<style>
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 4px rgba(245,108,108,.5); }
  50%       { box-shadow: 0 0 14px rgba(245,108,108,.9); }
}
.dev-marker-alarm { animation: pulse 1s infinite; }
</style>

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
