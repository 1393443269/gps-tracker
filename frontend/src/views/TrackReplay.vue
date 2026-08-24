<template>
  <div style="display:flex;gap:12px;height:calc(100vh - 140px);">
    <!-- 左侧控制面板 -->
    <div style="width:260px;min-width:260px;overflow-y:auto;">
      <el-card>
        <template #header>轨迹回放</template>
        <el-form label-width="72px">
          <el-form-item label="设备">
            <el-select v-model="selectedPhone" placeholder="选择设备" @change="onDeviceChange" style="width:100%;">
              <el-option v-for="d in devices" :key="d.phone" :label="`${d.phone}${d.plate_no ? ' '+d.plate_no : ''}`" :value="d.phone" />
            </el-select>
          </el-form-item>
          <el-form-item label="开始时间">
            <el-date-picker v-model="startTime" type="datetime" value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="开始" style="width:100%;" />
          </el-form-item>
          <el-form-item label="结束时间">
            <el-date-picker v-model="endTime" type="datetime" value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="结束" style="width:100%;" />
          </el-form-item>
          <el-form-item label="回放速度">
            <el-slider v-model="speed" :min="1" :max="10" :step="1" show-stops />
          </el-form-item>
        </el-form>

        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
          <el-button type="primary" @click="loadTrack" :loading="trackLoading">加载轨迹</el-button>
          <el-button @click="play"  :disabled="!trackPoints.length || playing">播放</el-button>
          <el-button @click="pause" :disabled="!playing">暂停</el-button>
          <el-button @click="reset">重置</el-button>
        </div>

        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="轨迹点数">{{ trackPoints.length }}</el-descriptions-item>
          <el-descriptions-item label="当前进度">{{ currentIdx + 1 }} / {{ trackPoints.length || 1 }}</el-descriptions-item>
          <el-descriptions-item label="当前速度" v-if="currentPoint">{{ (currentPoint.speed / 10).toFixed(1) }} km/h</el-descriptions-item>
          <el-descriptions-item label="当前时间" v-if="currentPoint">{{ currentPoint.gps_time }}</el-descriptions-item>
        </el-descriptions>

        <el-progress
          v-if="trackPoints.length"
          :percentage="progress"
          style="margin-top:12px;"
          :status="playing ? '' : 'success'"
        />
      </el-card>
    </div>

    <!-- 右侧地图 -->
    <div style="flex:1;min-width:0;height:100%;">
      <el-card style="height:100%;" body-style="padding:0;height:calc(100% - 48px);">
        <template #header>
          <span>轨迹地图</span>
          <span v-if="selectedPhone" style="font-size:12px;color:#909399;margin-left:12px;">{{ selectedPhone }}</span>
        </template>
        <div id="track-map" style="width:100%;height:100%;border-radius:0 0 4px 4px;" />
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { deviceApi, locationApi, portalApi, isAdmin } from '@/api'
import { TDT_MAP_STYLE } from '@/utils/mapStyle'

const devices       = ref([])
const selectedPhone = ref('')
const startTime     = ref('')
const endTime       = ref('')
const speed         = ref(3)
const trackPoints   = ref([])
const currentIdx    = ref(-1)
const playing       = ref(false)
const trackLoading  = ref(false)

const currentPoint = computed(() => trackPoints.value[currentIdx.value] || null)
const progress = computed(() => {
  if (!trackPoints.value.length) return 0
  return Math.round(((currentIdx.value + 1) / trackPoints.value.length) * 100)
})

let map         = null
let animTimer   = null
let startMarker = null
let endMarker   = null
let carMarker   = null
let trackAdded  = false   // 轨迹线是否已加入地图

// ── 辅助：生成标记元素 ─────────────────────────────────────────────────────────
function makeCircleEl(color, size = 16) {
  const el = document.createElement('div')
  el.style.cssText = [
    `width:${size}px`, `height:${size}px`, 'border-radius:50%',
    `background:${color}`, 'border:2px solid #fff',
    'box-shadow:0 0 4px rgba(0,0,0,.3)',
  ].join(';')
  return el
}

// ── 设备切换 ──────────────────────────────────────────────────────────────────
function onDeviceChange() { reset() }

// ── 加载轨迹 ──────────────────────────────────────────────────────────────────
async function loadTrack() {
  if (!selectedPhone.value) { ElMessage.error('请先选择设备'); return }
  trackLoading.value = true
  reset()

  try {
    const params = { size: 1000 }
    if (startTime.value && endTime.value) {
      params.start = startTime.value
      params.end   = endTime.value
    }
    const res = isAdmin()
      ? await locationApi.history(selectedPhone.value, params)
      : await portalApi.history(selectedPhone.value, params)
    const pts = (res.data?.records || []).filter(p => p.lat && p.lng)
    if (!pts.length) { ElMessage.warning('该时段无轨迹数据'); return }

    trackPoints.value = pts.reverse()  // 时间正序

    // GeoJSON 坐标：[lng, lat]
    const coords = pts.map(p => [p.lng, p.lat])

    // 添加轨迹线 source + layer
    map.addSource('track-line', {
      type: 'geojson',
      data: { type: 'Feature', geometry: { type: 'LineString', coordinates: coords } },
    })
    map.addLayer({
      id: 'track-line-layer',
      type: 'line',
      source: 'track-line',
      paint: { 'line-color': '#409EFF', 'line-width': 3, 'line-opacity': 0.85 },
    })
    trackAdded = true

    // 起点（绿色）
    startMarker = new maplibregl.Marker({ element: makeCircleEl('#67c23a') })
      .setLngLat(coords[0])
      .setPopup(new maplibregl.Popup({ closeButton: false }).setHTML('起点'))
      .addTo(map)

    // 终点（红色）
    endMarker = new maplibregl.Marker({ element: makeCircleEl('#f56c6c') })
      .setLngLat(coords[coords.length - 1])
      .setPopup(new maplibregl.Popup({ closeButton: false }).setHTML('终点'))
      .addTo(map)

    // 自适应视野
    const lngs = coords.map(c => c[0])
    const lats  = coords.map(c => c[1])
    map.fitBounds(
      [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
      { padding: 50 }
    )

    currentIdx.value = 0
    ElMessage.success(`加载完成，共 ${pts.length} 个轨迹点`)
  } catch {
    ElMessage.error('加载失败')
  } finally {
    trackLoading.value = false
  }
}

// ── 播放 / 暂停 / 重置 ────────────────────────────────────────────────────────
function play() {
  if (!trackPoints.value.length) { ElMessage.error('请先加载轨迹'); return }
  if (currentIdx.value >= trackPoints.value.length - 1) currentIdx.value = 0
  playing.value = true

  const step = () => {
    if (currentIdx.value >= trackPoints.value.length - 1) {
      playing.value = false
      ElMessage.success('轨迹回放完成')
      return
    }
    currentIdx.value++
    const pt = trackPoints.value[currentIdx.value]
    const lngLat = [pt.lng, pt.lat]

    if (carMarker) {
      carMarker.setLngLat(lngLat)
    } else {
      carMarker = new maplibregl.Marker({ element: makeCircleEl('#e6a23c', 18) })
        .setLngLat(lngLat)
        .addTo(map)
    }
    map.panTo(lngLat, { animate: true, duration: 200 })
    animTimer = setTimeout(step, Math.max(50, 500 / speed.value))
  }
  animTimer = setTimeout(step, 0)
}

function pause() {
  playing.value = false
  clearTimeout(animTimer)
  animTimer = null
}

function reset() {
  pause()
  currentIdx.value  = -1
  trackPoints.value = []

  if (map && trackAdded) {
    if (map.getLayer('track-line-layer')) map.removeLayer('track-line-layer')
    if (map.getSource('track-line'))      map.removeSource('track-line')
    trackAdded = false
  }

  startMarker?.remove(); startMarker = null
  endMarker?.remove();   endMarker   = null
  carMarker?.remove();   carMarker   = null
}

// ── 生命周期 ──────────────────────────────────────────────────────────────────
onMounted(async () => {
  const res = isAdmin() ? await deviceApi.list({ size: 200 }) : await portalApi.deviceList({ size: 200 })
  devices.value = res.data?.records || []

  await new Promise(r => setTimeout(r, 300))
  map = new maplibregl.Map({
    container: 'track-map',
    style: TDT_MAP_STYLE,
    center: [114.3, 30.5],
    zoom: 5,
  })
  map.addControl(new maplibregl.NavigationControl(), 'top-left')
})

onUnmounted(() => {
  pause()
  map?.remove()
})
</script>
