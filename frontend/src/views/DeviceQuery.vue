<template>
  <div style="display:flex;flex-direction:column;height:calc(100vh - 112px);background:#fff;border-radius:4px;overflow:hidden;">
    <!-- Tab 导航 -->
    <div style="display:flex;border-bottom:1px solid #e4e7ed;padding:0 16px;flex-shrink:0;">
      <div v-for="t in tabs" :key="t.key"
        class="dq-tab" :class="{ active: activeTab === t.key }"
        @click="switchTab(t.key)">
        {{ t.label }}
      </div>
    </div>

    <!-- ══ Tab 1：实时位置 ══ -->
    <div v-show="activeTab === 'realtime'" style="flex:1;display:flex;overflow:hidden;">
      <!-- 左侧设备列表 -->
      <div style="width:260px;border-right:1px solid #e4e7ed;display:flex;flex-direction:column;flex-shrink:0;">
        <div style="padding:10px 12px;border-bottom:1px solid #f0f0f0;display:flex;align-items:center;gap:8px;">
          <el-input v-model="devSearch" placeholder="搜索设备" size="small" clearable style="flex:1;" />
          <el-button size="small" text :icon="Refresh" @click="loadDevices" :loading="devLoading" />
        </div>
        <div style="flex:1;overflow-y:auto;">
          <div v-if="!filteredDevices.length && !devLoading"
            style="text-align:center;color:#ccc;padding:30px 0;font-size:13px;">暂无设备</div>
          <div v-for="d in filteredDevices" :key="d.phone"
            class="dq-device-item" :class="{ active: selected?.phone === d.phone }"
            @click="selectDevice(d)">
            <div style="display:flex;align-items:center;gap:8px;">
              <span :style="{
                width:'7px',height:'7px',borderRadius:'50%',flexShrink:0,
                background:d.status===1?'#67c23a':d.status===2?'#f56c6c':'#ccc'
              }" />
              <div style="flex:1;min-width:0;">
                <div style="font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                  {{ d.name || d.phone }}
                </div>
                <div style="font-size:11px;color:#909399;">{{ d.phone }}</div>
              </div>
              <el-tag size="small" :type="d.status===1?'success':d.status===2?'danger':'info'">
                {{ d.status===1?'在线':d.status===2?'报警':'离线' }}
              </el-tag>
            </div>
          </div>
        </div>
      </div>

      <!-- 地图 + 详情侧边栏 -->
      <div style="flex:1;display:flex;overflow:hidden;">
        <div style="flex:1;position:relative;">
          <div id="dq-map" style="position:absolute;inset:0;" />

        </div>
        <!-- 设备详情 -->
        <transition name="slide-detail">
          <div v-if="selected" style="width:260px;border-left:1px solid #e4e7ed;overflow-y:auto;flex-shrink:0;">
            <div style="padding:10px 12px;border-bottom:1px solid #f0f0f0;display:flex;align-items:center;justify-content:space-between;">
              <span style="font-size:13px;font-weight:600;">设备详情</span>
              <el-button text size="small" @click="selected=null">✕</el-button>
            </div>
            <div style="padding:12px;">
              <el-descriptions :column="1" size="small" border>
                <el-descriptions-item label="设备名称">{{ selected.name || '—' }}</el-descriptions-item>
                <el-descriptions-item label="设备号">{{ selected.phone }}</el-descriptions-item>
                <el-descriptions-item label="IMEI">{{ selected.imei || '—' }}</el-descriptions-item>
                <el-descriptions-item label="状态">
                  <el-tag size="small" :type="selected.status===1?'success':selected.status===2?'danger':'info'">
                    {{ selected.status===1?'在线':selected.status===2?'报警':'离线' }}
                  </el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="最后上报">{{ selected.last_location_time || '—' }}</el-descriptions-item>
                <el-descriptions-item label="纬度">{{ selected.last_lat?.toFixed(6) || '—' }}</el-descriptions-item>
                <el-descriptions-item label="经度">{{ selected.last_lng?.toFixed(6) || '—' }}</el-descriptions-item>
                <el-descriptions-item label="速度">
                  {{ selected.last_speed != null ? (selected.last_speed/10).toFixed(1)+' km/h' : '—' }}
                </el-descriptions-item>
                <el-descriptions-item label="归属客户">{{ selected.customer_name || '—' }}</el-descriptions-item>
                <el-descriptions-item label="电量">
                  <span v-if="selected.last_battery != null" :style="{ color: selected.last_battery <= 20 ? '#f56c6c' : '#67c23a' }">
                    {{ selected.last_battery }}%
                  </span>
                  <span v-else>—</span>
                </el-descriptions-item>
              </el-descriptions>
              <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">
                <el-button size="small" type="primary" @click="locateOnMap(selected)">地图定位</el-button>
                <el-button size="small" @click="goTrack(selected.phone)">查看轨迹</el-button>
                <el-button size="small" @click="goCmd(selected.phone)">发送指令</el-button>
              </div>
            </div>
          </div>
        </transition>
      </div>
    </div>

    <!-- ══ Tab 2：轨迹回放 ══ -->
    <div v-show="activeTab === 'track'" style="flex:1;display:flex;overflow:hidden;">
      <div style="width:260px;border-right:1px solid #e4e7ed;padding:14px;display:flex;flex-direction:column;gap:10px;overflow-y:auto;flex-shrink:0;">
        <div style="font-size:13px;font-weight:600;color:#303133;">轨迹回放</div>
        <el-form label-width="56px" size="small">
          <el-form-item label="设备">
            <el-select v-model="trkPhone" placeholder="选择设备" style="width:100%;" filterable @change="resetTrack">
              <el-option v-for="d in devices" :key="d.phone" :label="d.name||d.phone" :value="d.phone" />
            </el-select>
          </el-form-item>
          <el-form-item label="开始">
            <el-date-picker v-model="trkStart" type="datetime" value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="开始时间" style="width:100%;" />
          </el-form-item>
          <el-form-item label="结束">
            <el-date-picker v-model="trkEnd" type="datetime" value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="结束时间" style="width:100%;" />
          </el-form-item>
          <el-form-item label="速度">
            <el-slider v-model="trkSpeed" :min="1" :max="10" :step="1" show-stops />
          </el-form-item>
        </el-form>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <el-button type="primary" size="small" @click="loadTrack" :loading="trkLoading">加载轨迹</el-button>
          <el-button size="small" @click="playTrack"  :disabled="!trkPoints.length||trkPlaying">播放</el-button>
          <el-button size="small" @click="pauseTrack" :disabled="!trkPlaying">暂停</el-button>
          <el-button size="small" @click="resetTrack">重置</el-button>
        </div>
        <el-descriptions :column="1" size="small" border v-if="trkPoints.length">
          <el-descriptions-item label="轨迹点">{{ trkPoints.length }}</el-descriptions-item>
          <el-descriptions-item label="进度">{{ trkIdx+1 }} / {{ trkPoints.length }}</el-descriptions-item>
          <el-descriptions-item label="速度" v-if="trkCurrent">{{ (trkCurrent.speed/10).toFixed(1) }} km/h</el-descriptions-item>
          <el-descriptions-item label="时间"  v-if="trkCurrent">{{ trkCurrent.gps_time }}</el-descriptions-item>
        </el-descriptions>
        <el-progress v-if="trkPoints.length" :percentage="trkProgress" :status="trkPlaying?'':'success'" />
      </div>
      <div style="flex:1;position:relative;overflow:hidden;">
        <div id="dq-track-map" style="position:absolute;inset:0;" />
      </div>
    </div>

    <!-- ══ Tab 3：报警记录 ══ -->
    <div v-show="activeTab === 'alarms'" style="flex:1;display:flex;flex-direction:column;overflow:hidden;padding:14px;gap:10px;">
      <!-- 筛选栏 -->
      <div style="display:flex;align-items:center;gap:10px;flex-shrink:0;">
        <el-input v-model="alarmPhone" placeholder="设备号筛选" size="small" clearable style="width:180px;" @clear="loadAlarms(true)" />
        <el-button size="small" type="primary" @click="loadAlarms(true)">查询</el-button>
        <el-button size="small" :icon="Refresh" @click="loadAlarms(true)" :loading="alarmLoading">刷新</el-button>
        <div style="flex:1;" />
        <span style="font-size:13px;color:#909399;">共 {{ alarmTotal }} 条</span>
      </div>
      <el-table :data="alarms" size="small" style="flex:1;" height="100%" stripe border>
        <el-table-column label="设备号"   prop="phone"           width="140" />
        <el-table-column label="报警类型" prop="alarm_type_name" width="130" />
        <el-table-column label="报警时间" prop="alarm_time"      width="160" />
        <el-table-column label="位置" min-width="180">
          <template #default="{ row }">
            <span v-if="row.lat&&row.lng">{{ row.lat?.toFixed(5) }}, {{ row.lng?.toFixed(5) }}</span>
            <span v-else style="color:#ccc;">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status===0?'danger':'info'">
              {{ row.status===0?'未处理':'已处理' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination background layout="prev,pager,next,total"
        :total="alarmTotal" :page-size="alarmPageSize"
        v-model:current-page="alarmPage" @current-change="loadAlarms(false)"
        style="justify-content:flex-end;flex-shrink:0;" />
    </div>

    <!-- ══ Tab 4：操作指令 ══ -->
    <div v-show="activeTab === 'cmd'" style="flex:1;display:flex;overflow:hidden;">
      <!-- 左：发送 -->
      <div style="width:300px;border-right:1px solid #e4e7ed;padding:16px;display:flex;flex-direction:column;gap:14px;flex-shrink:0;overflow-y:auto;">
        <div style="font-size:13px;font-weight:600;">发送指令</div>
        <el-form label-width="56px" size="small">
          <el-form-item label="设备">
            <el-select v-model="cmdPhone" placeholder="选择设备" style="width:100%;" filterable>
              <el-option v-for="d in devices" :key="d.phone" :label="d.name||d.phone" :value="d.phone">
                <span>{{ d.name||d.phone }}</span>
                <el-tag size="small" :type="d.status===1?'success':'info'" style="float:right;margin-top:6px;">
                  {{ d.status===1?'在线':'离线' }}
                </el-tag>
              </el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="指令">
            <el-input v-model="cmdText" placeholder="输入指令内容" type="textarea" :rows="3" />
          </el-form-item>
        </el-form>
        <div>
          <div style="font-size:12px;color:#909399;margin-bottom:6px;">快捷指令</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            <el-tag v-for="q in quickCmds" :key="q.label" style="cursor:pointer;" @click="cmdText=q.cmd">
              {{ q.label }}
            </el-tag>
          </div>
        </div>
        <el-button type="primary" @click="sendCmd" :loading="cmdSending"
          :disabled="!cmdPhone||!cmdText" style="width:100%;">发送</el-button>
        <el-alert v-if="cmdResult" :title="cmdResult.msg"
          :type="cmdResult.ok?'success':'error'" show-icon :closable="false" />
      </div>

      <!-- 右：历史 -->
      <div style="flex:1;display:flex;flex-direction:column;padding:14px;gap:10px;overflow:hidden;">
        <div style="display:flex;align-items:center;gap:10px;">
          <span style="font-size:14px;font-weight:600;">指令历史</span>
          <el-button size="small" :icon="Refresh" @click="loadCmdHistory(true)" :loading="cmdHisLoading">刷新</el-button>
          <div style="flex:1;" />
          <span style="font-size:13px;color:#909399;">共 {{ cmdHisTotal }} 条</span>
        </div>
        <el-table :data="cmdHistory" size="small" style="flex:1;" height="100%" stripe border>
          <el-table-column label="设备"   prop="phone"       width="140" />
          <el-table-column label="设备名称" prop="device_name" width="110" />
          <el-table-column label="指令"   prop="command"     min-width="160" />
          <el-table-column label="结果"   prop="result"      width="80" />
          <el-table-column label="时间"   prop="created_at"  width="160" />
        </el-table>
        <el-pagination background layout="prev,pager,next,total"
          :total="cmdHisTotal" :page-size="cmdHisPageSize"
          v-model:current-page="cmdHisPage" @current-change="loadCmdHistory(false)"
          style="justify-content:flex-end;flex-shrink:0;" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { deviceApi, locationApi, alarmApi, commandApi, isAdmin, portalApi } from '@/api'
import { TDT_MAP_STYLE } from '@/utils/mapStyle'

// 角色自适应接口：管理员走管理端，客户走门户端
const api = {
  deviceList: (p)      => isAdmin() ? deviceApi.list(p)               : portalApi.deviceList(p),
  locHistory: (ph, p)  => isAdmin() ? locationApi.history(ph, p)      : portalApi.history(ph, p),
  alarmList:  (p)      => isAdmin() ? alarmApi.list(p)                : portalApi.alarms(p),
  sendCmd:    (ph, tx) => isAdmin() ? commandApi.sendText(ph, tx)     : portalApi.sendCommand({ phone: ph, text: tx }),
  addHistory: (d)      => isAdmin() ? commandApi.addHistory(d)        : Promise.resolve(), // 门户端后台自动记录
  cmdHistory: (p)      => isAdmin() ? commandApi.history(p)           : portalApi.cmdHistory(p),
}

const tabs = [
  { key: 'realtime', label: '实时位置' },
  { key: 'track',    label: '轨迹回放' },
  { key: 'alarms',   label: '报警记录' },
  { key: 'cmd',      label: '操作指令' },
]
const activeTab = ref('realtime')

async function switchTab(key) {
  activeTab.value = key
  await nextTick()
  if (key === 'realtime' && realtimeMap) realtimeMap.resize()
  if (key === 'track'    && trackMap)    trackMap.resize()
  if (key === 'alarms'   && !alarmsFetched.value)  loadAlarms(true)
  if (key === 'cmd'      && !cmdHisFetched.value)  loadCmdHistory(true)
}

// ══════════════════════════════════════════════════════════════════════════════
// Tab 1：实时位置
// ══════════════════════════════════════════════════════════════════════════════
const devices       = ref([])
const devLoading    = ref(false)
const devSearch     = ref('')
const selected      = ref(null)
const filteredDevices = computed(() => {
  if (!devSearch.value) return devices.value
  const q = devSearch.value.toLowerCase()
  return devices.value.filter(d =>
    (d.phone||'').includes(q) || (d.name||'').toLowerCase().includes(q)
  )
})

let realtimeMap = null
const markers   = {}
let pollTimer   = null

onMounted(async () => {
  await loadDevices()
  await nextTick()
  await new Promise(r => setTimeout(r, 200))

  realtimeMap = new maplibregl.Map({
    container: 'dq-map', style: TDT_MAP_STYLE,
    center: [104.19, 35.86], zoom: 5,
  })
  realtimeMap.addControl(new maplibregl.NavigationControl(), 'top-left')
  realtimeMap.on('load', () => {
    renderMarkers(devices.value)
    fitAll(devices.value)
    pollTimer = setInterval(async () => {
      await loadDevices()
    }, 30000)
  })

  trackMap = new maplibregl.Map({
    container: 'dq-track-map', style: TDT_MAP_STYLE,
    center: [104.19, 35.86], zoom: 5,
  })
  trackMap.addControl(new maplibregl.NavigationControl(), 'top-left')
})

onUnmounted(() => {
  clearInterval(pollTimer)
  clearTimeout(trkTimer)
  Object.values(markers).forEach(m => m.remove())
  if (realtimeMap) realtimeMap.remove()
  if (trackMap)    trackMap.remove()
})

async function loadDevices() {
  devLoading.value = true
  try {
    const res  = await api.deviceList({ size: 500 })
    const list = res.data?.records || []
    devices.value = list
    if (realtimeMap) renderMarkers(list)
  } catch {} finally { devLoading.value = false }
}

// 按设备状态/角色生成标记样式：报警红优先，否则用角色颜色+形状（圆/方/星/菱）
function markerCss(d) {
  const alarm = d.status === 2
  const color = alarm ? '#f56c6c' : (d.status === 0 ? '#ccc' : (d.role_color || '#67c23a'))
  const shape = d.role_icon || '圆形'
  let form = 'border-radius:50%;'
  if (shape === '方形')      form = 'border-radius:2px;'
  else if (shape === '菱形') form = 'clip-path:polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%);'
  else if (shape === '星形') form = 'clip-path:polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%);'
  return `width:12px;height:12px;background:${color};border:2px solid #fff;box-shadow:0 0 6px rgba(0,0,0,.4);cursor:pointer;${form}`
}
function renderMarkers(list) {
  const phones = new Set(list.map(d => d.phone))
  Object.keys(markers).forEach(ph => {
    if (!phones.has(ph)) { markers[ph].remove(); delete markers[ph] }
  })
  list.forEach(d => {
    if (!d.last_lat || !d.last_lng) return
    if (markers[d.phone]) {
      markers[d.phone].setLngLat([d.last_lng, d.last_lat])
      markers[d.phone].getElement().style.cssText = markerCss(d)
    } else {
      const el = document.createElement('div')
      el.style.cssText = markerCss(d)
      markers[d.phone] = new maplibregl.Marker({ element: el })
        .setLngLat([d.last_lng, d.last_lat]).addTo(realtimeMap)
      el.addEventListener('click', () => selectDevice(d))
    }
    if (selected.value?.phone === d.phone) selected.value = { ...d }
  })
}

function fitAll(list) {
  const pts = list.filter(d => d.last_lat && d.last_lng)
  if (!pts.length) return
  if (pts.length === 1) {
    realtimeMap.flyTo({ center: [pts[0].last_lng, pts[0].last_lat], zoom: 13 })
    return
  }
  const lngs = pts.map(d => d.last_lng), lats = pts.map(d => d.last_lat)
  realtimeMap.fitBounds(
    [[Math.min(...lngs), Math.min(...lats)],[Math.max(...lngs), Math.max(...lats)]],
    { padding: 60, maxZoom: 15 }
  )
}

function selectDevice(d) {
  selected.value = d
  locateOnMap(d)
}

function locateOnMap(d) {
  if (d.last_lat && d.last_lng && realtimeMap) {
    realtimeMap.flyTo({ center: [d.last_lng, d.last_lat], zoom: 14, duration: 600 })
  }
}

function goTrack(phone) { trkPhone.value = phone; switchTab('track') }
function goCmd(phone)   { cmdPhone.value  = phone; switchTab('cmd')   }

// ══════════════════════════════════════════════════════════════════════════════
// Tab 2：轨迹回放
// ══════════════════════════════════════════════════════════════════════════════
let trackMap     = null
let trkTimer     = null
let trkStartMark = null
let trkEndMark   = null
let trkCarMark   = null
let trkLineAdded = false

const trkPhone   = ref('')
const trkStart   = ref('')
const trkEnd     = ref('')
const trkSpeed   = ref(3)
const trkPoints  = ref([])
const trkIdx     = ref(-1)
const trkPlaying = ref(false)
const trkLoading = ref(false)

const trkCurrent  = computed(() => trkPoints.value[trkIdx.value] || null)
const trkProgress = computed(() => {
  if (!trkPoints.value.length) return 0
  return Math.round(((trkIdx.value+1)/trkPoints.value.length)*100)
})

function makeDot(color, size=14) {
  const el = document.createElement('div')
  el.style.cssText = `width:${size}px;height:${size}px;border-radius:50%;background:${color};border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.3);`
  return el
}

async function loadTrack() {
  if (!trkPhone.value) { ElMessage.warning('请先选择设备'); return }
  trkLoading.value = true; resetTrack()
  try {
    const params = { size: 1000 }
    if (trkStart.value && trkEnd.value) { params.start = trkStart.value; params.end = trkEnd.value }
    const res = await api.locHistory(trkPhone.value, params)
    const pts = (res.data?.records || []).filter(p => p.lat && p.lng)
    if (!pts.length) { ElMessage.warning('该时段无轨迹数据'); return }
    trkPoints.value = pts
    const coords = pts.map(p => [p.lng, p.lat])
    if (trackMap.getSource('trk')) {
      trackMap.getSource('trk').setData({ type:'Feature', geometry:{ type:'LineString', coordinates:coords } })
    } else {
      trackMap.addSource('trk', { type:'geojson', data:{ type:'Feature', geometry:{ type:'LineString', coordinates:coords } } })
      trackMap.addLayer({ id:'trk-line', type:'line', source:'trk', paint:{ 'line-color':'#409EFF','line-width':3,'line-opacity':0.85 } })
      trkLineAdded = true
    }
    trkStartMark = new maplibregl.Marker({ element:makeDot('#67c23a') }).setLngLat(coords[0]).setPopup(new maplibregl.Popup({ closeButton:false }).setHTML('起点')).addTo(trackMap)
    trkEndMark   = new maplibregl.Marker({ element:makeDot('#f56c6c') }).setLngLat(coords[coords.length-1]).setPopup(new maplibregl.Popup({ closeButton:false }).setHTML('终点')).addTo(trackMap)
    const lngs = coords.map(c=>c[0]), lats = coords.map(c=>c[1])
    trackMap.fitBounds([[Math.min(...lngs),Math.min(...lats)],[Math.max(...lngs),Math.max(...lats)]], { padding:50 })
    trkIdx.value = 0
    ElMessage.success(`加载完成，共 ${pts.length} 个轨迹点`)
  } catch { ElMessage.error('加载失败') }
  finally { trkLoading.value = false }
}

function playTrack() {
  if (!trkPoints.value.length) { ElMessage.warning('请先加载轨迹'); return }
  if (trkIdx.value >= trkPoints.value.length-1) trkIdx.value = 0
  trkPlaying.value = true
  const step = () => {
    if (trkIdx.value >= trkPoints.value.length-1) { trkPlaying.value=false; ElMessage.success('回放完成'); return }
    trkIdx.value++
    const pt = trkPoints.value[trkIdx.value], ll = [pt.lng, pt.lat]
    if (trkCarMark) trkCarMark.setLngLat(ll)
    else trkCarMark = new maplibregl.Marker({ element:makeDot('#e6a23c',18) }).setLngLat(ll).addTo(trackMap)
    trackMap.panTo(ll, { animate:true, duration:200 })
    trkTimer = setTimeout(step, Math.max(50, 500/trkSpeed.value))
  }
  trkTimer = setTimeout(step, 0)
}

function pauseTrack() { trkPlaying.value=false; clearTimeout(trkTimer) }

function resetTrack() {
  pauseTrack(); trkPoints.value=[]; trkIdx.value=-1
  trkStartMark?.remove(); trkStartMark=null
  trkEndMark?.remove();   trkEndMark=null
  trkCarMark?.remove();   trkCarMark=null
  if (trackMap && trkLineAdded) {
    try { trackMap.removeLayer('trk-line'); trackMap.removeSource('trk') } catch {}
    trkLineAdded = false
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Tab 3：报警记录
// ══════════════════════════════════════════════════════════════════════════════
const alarms        = ref([])
const alarmTotal    = ref(0)
const alarmPage     = ref(1)
const alarmPageSize = ref(20)
const alarmLoading  = ref(false)
const alarmsFetched = ref(false)
const alarmPhone    = ref('')

async function loadAlarms(reset=true) {
  if (reset) alarmPage.value = 1
  alarmLoading.value = true
  try {
    const params = { page: alarmPage.value, size: alarmPageSize.value }
    if (alarmPhone.value) params.phone = alarmPhone.value
    const res = await api.alarmList(params)
    alarms.value    = res.data?.records || []
    alarmTotal.value = res.data?.total  || 0
    alarmsFetched.value = true
  } catch {} finally { alarmLoading.value = false }
}

// ══════════════════════════════════════════════════════════════════════════════
// Tab 4：操作指令
// ══════════════════════════════════════════════════════════════════════════════
const cmdPhone   = ref('')
const cmdText    = ref('')
const cmdSending = ref(false)
const cmdResult  = ref(null)

const quickCmds = [
  { label:'查询位置', cmd:'WHERE'  },
  { label:'查询参数', cmd:'PARAMS' },
  { label:'设备重启', cmd:'RESET'  },
  { label:'消音',     cmd:'MUTE'   },
]

async function sendCmd() {
  if (!cmdPhone.value || !cmdText.value) return
  cmdSending.value = true; cmdResult.value = null
  try {
    const dev = devices.value.find(d => d.phone === cmdPhone.value)
    await api.sendCmd(cmdPhone.value, cmdText.value)
    // 写入历史（门户端后台自动记录，管理端需手动写入）
    await api.addHistory({
      phone: cmdPhone.value,
      device_name: dev?.name || cmdPhone.value,
      command: cmdText.value, result: '已发送'
    })
    cmdResult.value = { ok:true, msg:'指令已发送' }
    cmdText.value   = ''
    await loadCmdHistory(true)
  } catch (e) {
    cmdResult.value = { ok:false, msg: e.response?.data?.msg || '发送失败' }
  } finally { cmdSending.value = false }
}

const cmdHistory    = ref([])
const cmdHisTotal   = ref(0)
const cmdHisPage    = ref(1)
const cmdHisPageSize = ref(20)
const cmdHisLoading = ref(false)
const cmdHisFetched = ref(false)

async function loadCmdHistory(reset=true) {
  if (reset) cmdHisPage.value = 1
  cmdHisLoading.value = true
  try {
    const res = await api.cmdHistory({ page: cmdHisPage.value, size: cmdHisPageSize.value })
    cmdHistory.value  = res.data?.records || []
    cmdHisTotal.value = res.data?.total   || 0
    cmdHisFetched.value = true
  } catch {} finally { cmdHisLoading.value = false }
}
</script>

<style scoped>
.dq-tab {
  padding: 10px 16px;
  font-size: 13px;
  color: #606266;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: color .2s, border-color .2s;
  user-select: none;
  white-space: nowrap;
}
.dq-tab:hover  { color: #409EFF; }
.dq-tab.active { color: #409EFF; border-bottom-color: #409EFF; font-weight: 600; }

.dq-device-item {
  padding: 8px 12px;
  cursor: pointer;
  border-bottom: 1px solid #f5f5f5;
  transition: background .15s;
}
.dq-device-item:hover  { background: #f5f7fa; }
.dq-device-item.active { background: #ecf5ff; }

.slide-detail-enter-active, .slide-detail-leave-active { transition: width .2s ease, opacity .2s; }
.slide-detail-enter-from, .slide-detail-leave-to      { width:0; opacity:0; overflow:hidden; }
</style>
