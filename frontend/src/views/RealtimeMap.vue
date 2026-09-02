<template>
  <div style="position:relative; height:calc(100vh - 120px);">
    <!-- 地图容器 -->
    <div id="map-container" style="width:100%; height:100%; border-radius:8px;"></div>

    <!-- 围栏 2D 画布叠加：绕过 MapLibre v6 + 天地图矢量底图下 GL fill 层不渲染的问题 -->
    <!-- pointer-events:none 让鼠标穿透到地图与左侧面板/右上按钮，不拦截任何交互 -->
    <canvas ref="fenceCanvas"
      style="position:absolute; inset:0; width:100%; height:100%; pointer-events:none; z-index:2;"></canvas>

    <!-- 左侧一整列面板：账号列表(客户树) + 搜索 + 设备列表(标签页) -->
    <div class="left-panel">
      <!-- 1. 账号列表(客户层级树) 可折叠 -->
      <div class="lp-section">
        <div class="lp-header" @click="treeCollapsed = !treeCollapsed">
          <span>账号列表</span>
          <el-icon class="lp-arrow" :class="{ open: !treeCollapsed }"><ArrowRight /></el-icon>
        </div>
        <div v-show="!treeCollapsed" class="lp-tree-wrap">
          <el-tree
            v-if="customerTree.length"
            :data="customerTree"
            node-key="id"
            :props="treeProps"
            :expand-on-click-node="false"
            :highlight-current="true"
            :current-node-key="currentCustomerId"
            @node-click="onCustomerClick"
          >
            <template #default="{ data }">
              <span class="tree-node-label">
                {{ data.name }}
                <span class="tree-node-count">【{{ data.deviceCount ?? 0 }}】</span>
              </span>
            </template>
          </el-tree>
          <div v-else class="lp-empty">暂无账号数据</div>
          <div v-if="currentCustomerId != null" class="lp-clear" @click="clearCustomerFilter">
            <el-icon><Close /></el-icon><span>清除筛选</span>
          </div>
        </div>
      </div>

      <!-- 2. 搜索框 -->
      <div class="lp-section">
        <el-input
          v-model="keyword"
          size="small"
          clearable
          placeholder="按设备号或姓名搜索"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </div>

      <!-- 3. 设备列表 + 标签页(全部/在线/离线) -->
      <div class="lp-section lp-devices">
        <el-tabs v-model="activeTab" class="lp-tabs">
          <el-tab-pane :label="`全部(${counts.all})`" name="all" />
          <el-tab-pane :label="`在线(${counts.online})`" name="online" />
          <el-tab-pane :label="`离线(${counts.offline})`" name="offline" />
        </el-tabs>
        <div class="lp-list">
          <div
            v-for="item in filteredDevices"
            :key="item.phone"
            class="device-item"
            :class="{ 'no-loc': !item.hasLoc }"
            @click="item.hasLoc ? flyTo(item.phone) : null"
          >
            <span class="dot" :style="{ background: dotColor(item) }"></span>
            <span class="devname">{{ item.name || item.phone || '—' }}</span>
            <span v-if="!item.hasLoc" class="no-loc-tip">无坐标</span>
          </div>
          <div v-if="!filteredDevices.length" class="lp-empty">暂无设备</div>
        </div>
      </div>
    </div>

    <!-- 地图控件：围栏显示切换 + 设备名称显示切换 -->
    <div class="map-controls">
      <el-button
        size="small"
        :type="nameVisible ? 'primary' : 'default'"
        @click="toggleNames"
      >
        {{ nameVisible ? '隐藏设备名称' : '显示设备名称' }}
      </el-button>
      <el-button
        size="small"
        :type="fenceVisible ? 'primary' : 'default'"
        :loading="fenceLoading"
        @click="toggleFences"
      >
        {{ fenceVisible ? '隐藏电子围栏' : '显示电子围栏' }}
      </el-button>
    </div>

    <!-- 连接状态 -->
    <div class="ws-status" :class="wsConnected ? 'connected' : 'disconnected'">
      {{ wsConnected ? '● 实时连接' : '○ 断线重连中…' }}
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, computed, watch } from 'vue'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { io } from 'socket.io-client'
import { deviceApi, portalApi, fenceApi, customerApi, isAdmin } from '@/api'
import { ElNotification, ElMessage } from 'element-plus'
import { Search, ArrowRight, Close } from '@element-plus/icons-vue'
import { TDT_MAP_STYLE, circleToPolygon } from '@/utils/mapStyle'

// ── 常量：GeoJSON source / layer 标识 ──────────────────────────────────────────
const SRC_ID   = 'devices'
const LAYER_ID = 'devices-circle'

// ── 响应式状态 ────────────────────────────────────────────────────────────────
const wsConnected  = ref(false)
// phone → GeoJSON Point feature（承载渲染 + 面板所需字段，供 onlineDevices 列表用）
const featureStore = reactive({})
// phone → DOM Marker（地图上的可视点，与设备查询页一致的可靠渲染方式，
// 替代原 circle 图层——data-driven circle 在天地图矢量样式下存在不渲染问题）
const markerStore = {}
const allOnlineDevices = ref([])   // status=1 的全量设备列表

// ── 左侧面板状态 ────────────────────────────────────────────────────────────────
const treeCollapsed    = ref(false)   // 账号列表是否折叠
const customerTree     = ref([])      // 客户层级树数据
const currentCustomerId = ref(null)   // 当前选中的客户节点 id（null=不筛选）
const keyword          = ref('')      // 搜索关键字（IMEI / 姓名）
const activeTab        = ref('all')   // 设备列表标签页：all / online / offline
const nameVisible      = ref(false)   // 是否在 marker 旁显示设备名称
const treeProps        = { children: 'children', label: 'name' }

// 全量设备原始记录（用于本地过滤设备列表 + 客户树数量统计）
const allDevices = ref([])

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

// ── 设备列表：基于 allDevices 本地渲染（含在线/离线全量），带坐标标记 ──────────────
// 统一成面板列表项结构：phone/name/status/customerId/hasLoc/alarm/online/roleColor
function normalizeDevice(d) {
  const phone = String(d.phone)
  const hasLoc = !!(d.last_lat && d.last_lng)
  return {
    phone,
    name:       d.name || '',
    status:     d.status,                 // 0 离线 / 1 在线 / 2 报警
    customerId: d.customer_id != null ? String(d.customer_id) : null,
    hasLoc,
    alarm:      d.status === 2,
    online:     d.status === 1,
    roleColor:  d.role_color,
  }
}

const normalizedDevices = computed(() => allDevices.value.map(normalizeDevice))

// 选中客户后需要匹配的客户 id 集合（该客户 + 其所有下级）
const selectedCustomerIds = computed(() => {
  if (currentCustomerId.value == null) return null
  const ids = new Set()
  const walk = (nodes) => {
    for (const n of nodes) {
      if (String(n.id) === String(currentCustomerId.value)) {
        collect(n)
        return true
      }
      if (n.children && walk(n.children)) return true
    }
    return false
  }
  const collect = (node) => {
    ids.add(String(node.id))
    if (node.children) node.children.forEach(collect)
  }
  walk(customerTree.value)
  // 兜底：树里找不到就至少匹配自身 id
  if (!ids.size) ids.add(String(currentCustomerId.value))
  return ids
})

// phone(设备号) → customer_id(String) 映射，供围栏归属客户计算用
// 基于 allDevices 全量设备（含 customer_id）构建，随 allDevices 变化自动更新
const phoneToCustomerId = computed(() => {
  const m = {}
  for (const d of allDevices.value) {
    if (d.phone == null) continue
    const cid = d.customer_id != null ? String(d.customer_id) : null
    if (cid != null) m[String(d.phone)] = cid
  }
  return m
})

// 计算某个围栏归属的客户 id 集合（String 集合）
// 链条：围栏.devices(逗号分隔的设备phone串) → 各设备的 customer_id → 客户集合
// devices 兼容三种形态：逗号串 / 数组 / 空(null/undefined/'')
// 返回可能为空集合（围栏未关联设备，或关联设备均无 customer_id）
function fenceCustomerIds(fence) {
  const ids = new Set()
  const raw = fence && fence.devices
  let phones = []
  if (Array.isArray(raw)) {
    phones = raw
  } else if (typeof raw === 'string') {
    phones = raw.split(',')
  }
  const map = phoneToCustomerId.value
  for (const p of phones) {
    const phone = String(p).trim()
    if (!phone) continue
    const cid = map[phone]
    if (cid != null) ids.add(cid)
  }
  return ids
}

// 客户筛选后的设备（供各标签页计数与列表复用）
const customerFilteredDevices = computed(() => {
  const ids = selectedCustomerIds.value
  if (!ids) return normalizedDevices.value
  return normalizedDevices.value.filter(d => d.customerId != null && ids.has(d.customerId))
})

// 三个标签页数量（在客户筛选 + 关键字筛选基础上统计）
const keywordFilteredDevices = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  let list = customerFilteredDevices.value
  if (kw) {
    list = list.filter(d =>
      d.phone.toLowerCase().includes(kw) ||
      (d.name && d.name.toLowerCase().includes(kw))
    )
  }
  return list
})

const counts = computed(() => {
  const list = keywordFilteredDevices.value
  return {
    all:     list.length,
    online:  list.filter(d => d.status === 1 || d.status === 2).length,
    offline: list.filter(d => d.status !== 1 && d.status !== 2).length,
  }
})

// 最终渲染的设备列表（标签页过滤）
const filteredDevices = computed(() => {
  const list = keywordFilteredDevices.value
  if (activeTab.value === 'online')  return list.filter(d => d.status === 1 || d.status === 2)
  if (activeTab.value === 'offline') return list.filter(d => d.status !== 1 && d.status !== 2)
  return list
})

// 列表项圆点颜色：报警红 > 在线绿 > 离线灰
function dotColor(item) {
  if (item.alarm || item.status === 2) return '#f56c6c'
  if (item.status === 1) return item.roleColor || '#67c23a'
  return '#b0b3b8'
}

let map
let socket
let popup           // 单个可复用 popup，点击设备点时按需展示
let onlineTimer = null

// ── 客户层级树 ────────────────────────────────────────────────────────────────
// 用扁平客户列表(id/name/parent_id)拼成树，并按设备 customer_id 统计每个节点(含下级)设备数
function buildCustomerTree(customers, devices) {
  // 每个客户直接持有的设备数
  const directCount = {}
  for (const d of devices) {
    const cid = d.customer_id != null ? String(d.customer_id) : null
    if (cid != null) directCount[cid] = (directCount[cid] || 0) + 1
  }
  const nodeMap = {}
  customers.forEach(c => {
    nodeMap[String(c.id)] = {
      id: c.id,
      name: c.name,
      parentId: c.parent_id != null ? String(c.parent_id) : null,
      children: [],
      deviceCount: 0,
    }
  })
  const roots = []
  Object.values(nodeMap).forEach(n => {
    if (n.parentId != null && nodeMap[n.parentId]) {
      nodeMap[n.parentId].children.push(n)
    } else {
      roots.push(n)
    }
  })
  // 递归汇总设备数（自身直接数 + 所有下级）
  const sum = (node) => {
    let total = directCount[String(node.id)] || 0
    node.children.forEach(ch => { total += sum(ch) })
    node.deviceCount = total
    if (!node.children.length) delete node.children  // 叶子去掉空 children，避免展开箭头
    return total
  }
  roots.forEach(sum)
  return roots
}

async function loadCustomerTree() {
  if (!isAdmin()) return   // 门户端无客户树权限，跳过
  try {
    const res = await customerApi.list({ size: 500 })
    const records = res.data?.records || res.data || []
    customerTree.value = buildCustomerTree(records, allDevices.value)
  } catch (e) {
    console.warn('[RealtimeMap] 客户树加载失败:', e)
  }
}

// 点击客户节点 → 设置筛选并把地图移到该客户设备范围
function onCustomerClick(data) {
  currentCustomerId.value = data.id
  fitToDevices(customerFilteredDevices.value)
}

function clearCustomerFilter() {
  currentCustomerId.value = null
}

// 把地图视野移动到给定设备(有坐标者)范围
function fitToDevices(devices) {
  if (!map) return
  const located = devices.filter(d => d.hasLoc && featureStore[d.phone])
  if (!located.length) return
  if (located.length === 1) {
    const rec = featureStore[located[0].phone]
    map.flyTo({ center: [rec.lng, rec.lat], zoom: 14 })
    return
  }
  let minLng = 180, minLat = 90, maxLng = -180, maxLat = -90
  located.forEach(d => {
    const rec = featureStore[d.phone]
    if (rec.lng < minLng) minLng = rec.lng; if (rec.lng > maxLng) maxLng = rec.lng
    if (rec.lat < minLat) minLat = rec.lat; if (rec.lat > maxLat) maxLat = rec.lat
  })
  try { map.fitBounds([[minLng, minLat], [maxLng, maxLat]], { padding: 80, maxZoom: 14, duration: 800 }) } catch {}
}

// ── 电子围栏显示（2D canvas 方案，绕过 MapLibre v6 GL fill 不渲染问题）──────────────
const fenceVisible = ref(false)   // 围栏是否显示
const fenceLoading = ref(false)   // 加载中
const fenceCanvas  = ref(null)    // 2D canvas 叠加层引用
let fenceCache = null              // 围栏数据缓存，避免重复请求
let renderBound = null             // map render 事件的绑定引用（用于精确解绑）

// hex 颜色转 rgba（支持 3 位 / 6 位 hex，默认回退红色）
function hexToRgba(hex, alpha) {
  const clean = (hex || '#FF4444').replace('#', '')
  const full = clean.length === 3
    ? clean.split('').map(c => c + c).join('')
    : clean
  const r = parseInt(full.slice(0, 2), 16) || 0
  const g = parseInt(full.slice(2, 4), 16) || 0
  const b = parseInt(full.slice(4, 6), 16) || 0
  return `rgba(${r},${g},${b},${alpha})`
}

// 把围栏画到 2D canvas 叠加层上（经纬度 → 屏幕像素，处理 DPR）
function drawFencesOnCanvas() {
  const cvs = fenceCanvas.value
  if (!cvs || !map) return

  const container = map.getContainer()
  const w = container.offsetWidth
  const h = container.offsetHeight
  if (!w || !h) return

  const dpr = window.devicePixelRatio || 1
  // 只在尺寸变化时才重置 canvas 大小（避免每帧重分配内存）
  if (cvs.width !== w * dpr || cvs.height !== h * dpr) {
    cvs.width  = w * dpr
    cvs.height = h * dpr
  }

  const ctx = cvs.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  // 围栏未显示或无数据时，仅清空画布后返回
  if (!fenceVisible.value || !fenceCache || !fenceCache.length) {
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    return
  }

  // 客户筛选：选中某客户时，只画"归属该客户(及其下级)"的围栏
  // selectedCustomerIds 为 null(未选客户) → 画全部围栏
  // 选中客户时：围栏归属客户集合与 selectedCustomerIds 有交集才画；
  //   围栏未关联设备(归属集合为空) → 选中客户时一律不画
  const selIds = selectedCustomerIds.value
  const fencesToDraw = selIds
    ? fenceCache.filter(f => {
        const own = fenceCustomerIds(f)
        for (const cid of own) { if (selIds.has(cid)) return true }
        return false
      })
    : fenceCache

  fencesToDraw.forEach(f => {
    // 1. 获取围栏顶点列表（[lng, lat] 格式）
    let ring = null
    if (f.fence_type === 'circle') {
      if (!f.lat || !f.lng) return
      const lat = f.lat, lng = f.lng, radius = f.radius || 1000
      ring = []
      for (let i = 0; i < 64; i++) {
        const angle = (i / 64) * 2 * Math.PI
        const dLat = (radius * Math.sin(angle) / 6371000) * (180 / Math.PI)
        const dLng = (radius * Math.cos(angle) / (6371000 * Math.cos(lat * Math.PI / 180))) * (180 / Math.PI)
        ring.push([lng + dLng, lat + dLat])
      }
    } else if ((f.fence_type === 'polygon' || f.fence_type === 'administrative') && f.coordinates) {
      try { ring = typeof f.coordinates === 'string' ? JSON.parse(f.coordinates) : f.coordinates } catch { return }
    }
    if (!ring || ring.length < 3) return

    // 2. 经纬度 → 屏幕像素
    const pts = ring.map(([lng, lat]) => {
      const p = map.project([lng, lat])
      return [p.x, p.y]
    })

    const fc = f.color || '#FF4444'

    // 3. 绘制半透明填充
    ctx.beginPath()
    ctx.moveTo(pts[0][0], pts[0][1])
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1])
    ctx.closePath()
    ctx.fillStyle = hexToRgba(fc, 0.25)
    ctx.fill()

    // 4. 绘制实线边框
    ctx.beginPath()
    ctx.moveTo(pts[0][0], pts[0][1])
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1])
    ctx.closePath()
    ctx.strokeStyle = fc
    ctx.lineWidth   = 2.5
    ctx.stroke()

    // 5. 外发光效果
    ctx.beginPath()
    ctx.moveTo(pts[0][0], pts[0][1])
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1])
    ctx.closePath()
    ctx.strokeStyle = hexToRgba(fc, 0.2)
    ctx.lineWidth   = 8
    ctx.stroke()
  })

  ctx.setTransform(1, 0, 0, 1, 0, 0)
}

// 点击"显示/隐藏电子围栏"：切换 canvas 绘制
async function toggleFences() {
  if (fenceVisible.value) {        // 当前显示 → 隐藏
    clearFences()
    fenceVisible.value = false
    return
  }
  // 当前隐藏 → 拉数据并渲染
  fenceLoading.value = true
  try {
    if (!fenceCache) {
      const res = isAdmin() ? await fenceApi.list() : await portalApi.fences()
      // 兼容 {data:{records}} 与 {data:[]} 两种返回结构
      const d = res.data
      fenceCache = Array.isArray(d) ? d : (d?.records || d?.list || [])
    }
    fenceVisible.value = true
    renderFences(fenceCache)
    if (!fenceCache.length) ElMessage.info('暂无电子围栏')
  } catch (e) {
    console.warn('[RealtimeMap] 围栏加载失败:', e)
    ElMessage.error('围栏加载失败')
  } finally {
    fenceLoading.value = false
  }
}

// 把围栏画到 canvas（圆形→多边形；多边形/行政区用 coordinates），并 fit 视野 + 注册重绘监听
function renderFences(fences) {
  if (!map) return
  // 计算边界(用于自动 fit 视野)
  let minLng=180, minLat=90, maxLng=-180, maxLat=-90, hasBounds=false
  fences.forEach(f => {
    if (f.fence_type === 'circle' && f.lat && f.lng) {
      // 圆形：用半径换算的经纬度粗略外接框即可
      const deg = (f.radius || 1000) / 111000
      const lo = [f.lng - deg, f.lng + deg], la = [f.lat - deg, f.lat + deg]
      lo.forEach(lng => { if (lng < minLng) minLng = lng; if (lng > maxLng) maxLng = lng })
      la.forEach(lat => { if (lat < minLat) minLat = lat; if (lat > maxLat) maxLat = lat })
      hasBounds = true
    } else if ((f.fence_type === 'polygon' || f.fence_type === 'administrative') && f.coordinates) {
      try {
        const coords = typeof f.coordinates === 'string' ? JSON.parse(f.coordinates) : f.coordinates
        if (Array.isArray(coords) && coords.length >= 3) {
          coords.forEach(([lng, lat]) => {
            if (lng < minLng) minLng = lng; if (lng > maxLng) maxLng = lng
            if (lat < minLat) minLat = lat; if (lat > maxLat) maxLat = lat
            hasBounds = true
          })
        }
      } catch {}
    }
  })

  // 注册地图移动/缩放/重绘监听：地图移动时同步重绘围栏 canvas（跟随平移缩放）
  // render 事件在每帧触发，覆盖 move/zoom/resize 各种场景，绑定前先解绑避免重复
  if (renderBound) { try { map.off('render', renderBound) } catch {} }
  renderBound = drawFencesOnCanvas
  map.on('render', renderBound)

  // 先立即画一次
  drawFencesOnCanvas()

  // 渲染后把地图视野移到围栏范围(否则围栏在桂林、地图停在武汉，看着像没反应)
  // fitBounds 会触发 move/render，canvas 会随之重绘
  if (hasBounds) {
    try { map.fitBounds([[minLng, minLat], [maxLng, maxLat]], { padding: 60, maxZoom: 15, duration: 800 }) } catch {}
  }
}

// 清除围栏：清空 canvas 并移除重绘监听
function clearFences() {
  if (renderBound && map) { try { map.off('render', renderBound) } catch {} }
  renderBound = null
  const cvs = fenceCanvas.value
  if (cvs) {
    const ctx = cvs.getContext('2d')
    if (ctx) {
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, cvs.width, cvs.height)
    }
  }
}

// 选中客户变化时：若围栏正在显示，重绘 canvas（围栏归属集合变了，要画的围栏也变了）
// 仅重绘 canvas，不重新拉数据、不改动 render 监听机制
watch(currentCustomerId, () => {
  if (fenceVisible.value) drawFencesOnCanvas()
})

// ── 颜色/半径 ─────────────────────────────────────────────────────────────────
// 报警红 > 离线灰 > 角色色 > 默认蓝。离线设备(online===false)用灰色，
// 便于一眼区分在线/离线；在线设备保持彩色。
function markerColor(info) {
  if (info.alarm) return '#f56c6c'
  if (info.online === false) return '#b0b3b8'   // 离线：灰色
  return info.roleColor || '#409eff'
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
  return `<b>${_esc(info.terminal_id || info.phone)}</b><br>
    ${roleLine}纬度: ${Number(info.lat).toFixed(6)}<br>
    经度: ${Number(info.lng).toFixed(6)}<br>
    速度: ${_esc(info.speed)} km/h<br>
    电量: ${info.last_battery != null ? info.last_battery + '%' : '—'}<br>
    时间: ${_esc(info.time)}<br>
    ${info.alarm ? '<span style="color:red;">⚠ 报警中</span>' : '正常'}`
}

// ── 设备点数据记录（featureStore 存 properties+坐标，供面板列表 onlineDevices 用） ──
// 天地图底图与设备上报的 WGS-84 基本对齐（设备查询页同样直接用原始坐标，显示准确），
// 故不做坐标转换，直接用上报经纬度渲染。
function makeRecord(info) {
  const props = {
    ...info,
    phone: String(info.phone),
    color:  markerColor(info),
    alarm:  !!info.alarm,
  }
  return { properties: props, lng: Number(info.lng), lat: Number(info.lat) }
}

// 建一个 DOM Marker 元素（圆点 + 可选名称标签），报警红/角色色，与设备查询页风格一致
function makeMarkerEl(props) {
  const el = document.createElement('div')
  el.className = 'rt-marker'
  const size = props.alarm ? 18 : 14
  // 圆点
  const dot = document.createElement('div')
  dot.className = 'rt-marker-dot'
  dot.style.cssText =
    `width:${size}px;height:${size}px;border-radius:50%;` +
    `background:${props.color || '#409eff'};border:2px solid #fff;` +
    `box-shadow:0 0 6px rgba(0,0,0,.4);`
  el.appendChild(dot)
  // 名称标签（默认按 nameVisible 决定显隐）
  const label = document.createElement('span')
  label.className = 'rt-marker-label'
  label.textContent = props.name || props.phone || ''
  label.style.display = nameVisible.value ? 'inline-block' : 'none'
  el.appendChild(label)
  return el
}

// 切换 marker 名称标签显隐
function toggleNames() {
  nameVisible.value = !nameVisible.value
  applyNameVisibility()
}

// 把当前 nameVisible 应用到所有已存在的 marker，并刷新标签文字
function applyNameVisibility() {
  Object.entries(markerStore).forEach(([phone, m]) => {
    const el = m.getElement()
    const label = el && el.querySelector('.rt-marker-label')
    if (label) {
      const rec = featureStore[phone]
      label.textContent = (rec && (rec.properties.name || rec.properties.phone)) || ''
      label.style.display = nameVisible.value ? 'inline-block' : 'none'
    }
  })
}

// ── 更新/新建设备点：写 featureStore（列表）+ 建/移 DOM Marker（地图） ────────────
function updateFeature(info, { refresh = true } = {}) {
  const { lat, lng } = info
  if (!lat || !lng) return
  const phone = String(info.phone)

  let rec
  if (featureStore[phone]) {
    const prev = featureStore[phone].properties || {}
    const merged = {
      ...prev, ...info, phone,
      name:      info.name      ?? prev.name      ?? '',
      roleName:  info.roleName  ?? prev.roleName,
      roleColor: info.roleColor ?? prev.roleColor,
      roleIcon:  info.roleIcon  ?? prev.roleIcon,
    }
    rec = makeRecord(merged)
  } else {
    rec = makeRecord({ ...info, phone })
  }
  featureStore[phone] = rec

  // 地图 DOM Marker：已存在则移动位置，否则新建
  if (map) {
    const ll = [rec.lng, rec.lat]
    if (markerStore[phone]) {
      markerStore[phone].setLngLat(ll)
      const el = markerStore[phone].getElement()
      const dot = el.querySelector('.rt-marker-dot')
      if (dot) dot.style.background = rec.properties.color || '#409eff'
      // 同步名称标签文字（名称可能随推送更新）
      const label = el.querySelector('.rt-marker-label')
      if (label) {
        label.textContent = rec.properties.name || rec.properties.phone || ''
        label.style.display = nameVisible.value ? 'inline-block' : 'none'
      }
    } else {
      const el = makeMarkerEl(rec.properties)
      el.addEventListener('click', () => {
        if (!popup) popup = new maplibregl.Popup({ closeButton: true, maxWidth: '240px' })
        popup.setLngLat(ll).setHTML(popupHtml(rec.properties)).addTo(map)
      })
      markerStore[phone] = new maplibregl.Marker({ element: el }).setLngLat(ll).addTo(map)
    }
  }
}

// refreshSource 保留为空操作，兼容旧调用点（DOM Marker 无需批量 setData）
function refreshSource() {}

// ── 清理离线设备：移除不在集合内的 featureStore 记录与其 DOM Marker ──────────────
function pruneFeatures(onlinePhones) {
  const keep = new Set([...onlinePhones].map(p => String(p)))
  for (const ph of Object.keys(featureStore)) {
    if (!keep.has(String(ph))) {
      delete featureStore[ph]
      if (markerStore[ph]) { markerStore[ph].remove(); delete markerStore[ph] }
    }
  }
}

function flyTo(phone) {
  const rec = featureStore[String(phone)]
  if (rec) {
    map.flyTo({ center: [rec.lng, rec.lat], zoom: 15 })
  }
}

// ── 初始位置加载 ──────────────────────────────────────────────────────────────
async function loadInitialPositions() {
  try {
    const res = isAdmin() ? await deviceApi.list({ size: 500 }) : await portalApi.deviceList({ size: 500 })
    const records = res.data?.records || []
    // 全量设备原始记录：供左侧列表(全部/在线/离线)本地过滤与客户树数量统计
    allDevices.value = records
    // 面板列表：在线 + 报警设备
    allOnlineDevices.value = records.filter(d => d.status === 1 || d.status === 2)
    // 地图：所有有坐标的设备都画点，在线彩色 / 离线灰色 / 报警红
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
          last_battery: d.last_battery,
          alarm: d.status === 2,
          online: d.status === 1,          // 在线状态 → marker 颜色区分
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

// ── 地图交互初始化 ──────────────────────────────────────────────────────────────
// 设备点改用 DOM Marker（见 updateFeature），无需 GeoJSON source/circle 图层。
// 此处仅预建可复用 popup。
function initDeviceLayer() {
  popup = new maplibregl.Popup({ closeButton: true, maxWidth: '240px' })
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
    // 收到实时位置推送=设备刚上报=在线，标为在线(点转为彩色)
    updateFeature({
      phone: data.phone,
      lat:   data.lat,
      lng:   data.lng,
      speed: String(data.speed),
      alarm: data.alarm,
      online: true,
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
    // 设备加载完后再拼客户树（需要设备的 customer_id 统计数量）
    await loadCustomerTree()
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
  // 清理围栏：清空 canvas 并解绑 render 重绘监听
  clearFences()
  // 清理所有 DOM Marker
  Object.values(markerStore).forEach(m => m.remove())
  for (const k of Object.keys(markerStore)) delete markerStore[k]
  if (popup) {
    popup.remove()
    popup = null
  }
  // map.remove() 会一并销毁底图及其绑定的事件监听
  map?.remove()
  map = null
})
</script>

<style scoped>
/* 左侧一整列面板 */
.left-panel {
  position: absolute; top: 10px; left: 50px;
  width: 260px; max-height: calc(100% - 20px);
  display: flex; flex-direction: column;
  background: rgba(255,255,255,.95); border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0,0,0,.15);
  z-index: 1000; padding: 10px; box-sizing: border-box;
  overflow: hidden;
}
.lp-section { margin-bottom: 10px; }
.lp-section:last-child { margin-bottom: 0; }
.lp-header {
  display: flex; align-items: center; justify-content: space-between;
  font-weight: 600; font-size: 13px; color: #303133;
  cursor: pointer; user-select: none; padding: 2px 0;
}
.lp-arrow { transition: transform .2s; }
.lp-arrow.open { transform: rotate(90deg); }
.lp-tree-wrap { max-height: 26vh; overflow-y: auto; margin-top: 6px; }
.tree-node-label { font-size: 12px; color: #303133; }
.tree-node-count { color: #909399; font-size: 11px; }
.lp-clear {
  display: flex; align-items: center; gap: 4px;
  font-size: 11px; color: #909399; cursor: pointer;
  padding: 4px 2px; margin-top: 2px;
}
.lp-clear:hover { color: #409eff; }
.lp-empty { font-size: 12px; color: #aaa; padding: 8px 4px; text-align: center; }

.lp-devices { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.lp-tabs { --el-tabs-header-height: 32px; }
.lp-tabs :deep(.el-tabs__item) { font-size: 12px; padding: 0 10px; }
.lp-tabs :deep(.el-tabs__header) { margin-bottom: 6px; }
.lp-list { flex: 1; overflow-y: auto; min-height: 60px; max-height: 40vh; }

.device-item {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 6px; border-radius: 4px; cursor: pointer; font-size: 12px;
}
.device-item:hover { background: #f0f2f5; }
.device-item.no-loc { cursor: default; opacity: .7; }
.dot {
  width: 10px; height: 10px; border-radius: 50%;
  flex-shrink: 0; border: 1px solid rgba(0,0,0,.1);
}
.devname {
  flex: 1; color: #303133; font-size: 12px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.no-loc-tip { font-size: 10px; color: #aaa; margin-left: 4px; flex-shrink: 0; }

/* 地图控件 */
.map-controls {
  position: absolute; top: 10px; right: 10px;
  z-index: 1000; display: flex; gap: 8px;
}

/* 连接状态 */
.ws-status {
  position: absolute; bottom: 16px; left: 16px;
  padding: 4px 12px; border-radius: 12px; font-size: 12px; z-index: 1000;
}
.connected    { background: #f0f9eb; color: #67c23a; }
.disconnected { background: #fef0f0; color: #f56c6c; }

/* 设备 marker 名称标签（非 scoped 元素，用 :deep 穿透以命中动态创建的 DOM） */
:deep(.rt-marker) { display: flex; align-items: center; }
:deep(.rt-marker-label) {
  margin-left: 4px; padding: 1px 5px;
  background: rgba(255,255,255,.9); border-radius: 3px;
  font-size: 11px; color: #303133; white-space: nowrap;
  box-shadow: 0 1px 3px rgba(0,0,0,.25); pointer-events: none;
}
</style>
