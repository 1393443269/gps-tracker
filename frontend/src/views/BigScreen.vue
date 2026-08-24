<template>
  <div class="bigscreen" ref="bsEl">

    <!-- ── 顶部标题栏（半透明浮层） ── -->
    <div class="bs-header">
      <div class="bs-header-left">
        <span class="bs-logo-icon">🛰</span>
        <span class="bs-logo-text">资产管理平台</span>
      </div>
      <div class="bs-header-center">
        <div class="bs-title">设备分布数据大屏</div>
      </div>
      <div class="bs-header-right">
        <span class="bs-time">{{ currentTime }}</span>
        <button class="bs-btn" @click="toggleFullscreen">{{ isFullscreen ? '⊠ 退出全屏' : '⛶ 全屏' }}</button>
        <button class="bs-btn" @click="$router.back()">✕ 退出</button>
      </div>
    </div>

    <!-- ── 地图：铺满全屏底层 ── -->
    <div ref="mapEl" class="map-fullscreen" />

    <!-- 下钻时显示「返回全国」按钮 -->
    <button v-if="currentProvince" class="back-btn" @click="backToChina">← 返回全国</button>

    <!-- ── 左侧面板（毛玻璃浮层） ── -->
    <div class="panel-left">

      <!-- 设备数量概况 -->
      <div class="section-title">
        <span class="section-bar"></span>设备数量概况
      </div>
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-val">{{ deviceTotal }}</div>
          <div class="kpi-lbl">设备总数</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val" style="color:#00ff88;">{{ deviceOnline }}</div>
          <div class="kpi-lbl">在线</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val" style="color:#5a7a9a;">{{ deviceOffline }}</div>
          <div class="kpi-lbl">离线</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val" style="color:#ff6b6b;">{{ deviceAlarm }}</div>
          <div class="kpi-lbl">告警</div>
        </div>
      </div>

      <!-- 设备基础情况 -->
      <div class="section-title">
        <span class="section-bar"></span>设备基础情况
      </div>
      <div class="gauges-row">
        <div class="gauge-cell">
          <div class="gauge-ring" :style="activeRingStyle">
            <div class="gauge-inner">
              <div class="gauge-pct">{{ activePct.toFixed(1) }}%</div>
              <div class="gauge-label">报警设备占比</div>
            </div>
          </div>
        </div>
        <div class="gauge-cell">
          <div class="gauge-ring" :style="onlineRingStyle">
            <div class="gauge-inner">
              <div class="gauge-pct">{{ onlinePct.toFixed(1) }}%</div>
              <div class="gauge-label">设备在线率</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 本月告警 TOP5 -->
      <div class="section-title" style="margin-top:18px;">
        <span class="section-bar"></span>本月告警TOP5
      </div>
      <div class="alarm-total-line">
        <span class="alarm-dot">●</span>
        <span class="alarm-total-lbl">本月告警总数</span>
        <span class="alarm-total-val">{{ alarmTotal }}</span>
      </div>
      <div class="alarm-list">
        <div v-for="(item, idx) in alarmTypes" :key="idx" class="alarm-item">
          <div class="alarm-item-header">
            <span class="alarm-name">{{ item.alarm_desc || item.name || '未知' }}</span>
            <span class="alarm-count">{{ item.cnt }}</span>
          </div>
          <div class="alarm-bar-bg">
            <div class="alarm-bar-fill" :style="{ width: alarmBarWidth(item.cnt) + '%' }" />
          </div>
        </div>
        <div v-if="!alarmTypes.length" class="alarm-empty">暂无报警记录</div>
      </div>

    </div>

    <!-- ── 右下：图例 + 折线图（浮层） ── -->
    <div class="panel-bottom-right">
      <div class="legend-row">
        <span class="legend-txt">设备分布情况</span>
        <span class="legend-lbl">低</span>
        <div class="legend-bar" />
        <span class="legend-lbl">高</span>
      </div>
      <div class="section-title" style="margin-top:10px;">
        <span class="section-bar"></span>设备激活统计
      </div>
      <div ref="lineEl" class="line-chart" />
    </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { deviceApi, alarmApi, reportApi } from '@/api'

// ── refs ──────────────────────────────────────────────────────────────────────
const bsEl   = ref(null)
const mapEl  = ref(null)
const lineEl = ref(null)

// ── state ──────────────────────────────────────────────────────────────────────
const currentTime    = ref('')
const isFullscreen   = ref(false)
const activePct      = ref(0)
const onlinePct      = ref(0)
const alarmTotal     = ref(0)
const alarmTypes     = ref([])
const currentProvince = ref('')   // 当前下钻的省份名，空=全国

// 设备数量
const deviceTotal   = ref(0)
const deviceOnline  = ref(0)
const deviceAlarm   = ref(0)
const deviceOffline = ref(0)

let charts = {}
let clockTimer = null
let dataTimer  = null
let provinceData = {}          // 保存省份设备数，下钻时复用
let devicePoints = []          // 有 GPS 坐标的设备，下钻返回时复用
let provinceFeaturesCache = {} // adcode → GeoJSON features，避免重复拉取

// ── 省份名 → adcode 映射 ──────────────────────────────────────────────────────
const PROVINCE_ADCODE = {
  '北京市':110000,'天津市':120000,'河北省':130000,'山西省':140000,
  '内蒙古自治区':150000,'辽宁省':210000,'吉林省':220000,'黑龙江省':230000,
  '上海市':310000,'江苏省':320000,'浙江省':330000,'安徽省':340000,
  '福建省':350000,'江西省':360000,'山东省':370000,'河南省':410000,
  '湖北省':420000,'湖南省':430000,'广东省':440000,'广西壮族自治区':450000,
  '海南省':460000,'重庆市':500000,'四川省':510000,'贵州省':520000,
  '云南省':530000,'西藏自治区':540000,'陕西省':610000,'甘肃省':620000,
  '青海省':630000,'宁夏回族自治区':640000,'新疆维吾尔自治区':650000,
  '台湾省':710000,'香港特别行政区':810000,'澳门特别行政区':820000,
}

// ── GPS → 省份（质心最近邻） ───────────────────────────────────────────────────
const PROVINCE_CENTROIDS = [
  { l:'北京市',           lat:39.90, lng:116.41 },
  { l:'天津市',           lat:39.13, lng:117.20 },
  { l:'河北省',           lat:38.04, lng:114.51 },
  { l:'山西省',           lat:37.87, lng:112.55 },
  { l:'内蒙古自治区',     lat:44.00, lng:113.00 },
  { l:'辽宁省',           lat:41.80, lng:123.43 },
  { l:'吉林省',           lat:43.90, lng:125.32 },
  { l:'黑龙江省',         lat:47.86, lng:127.74 },
  { l:'上海市',           lat:31.23, lng:121.47 },
  { l:'江苏省',           lat:32.06, lng:118.76 },
  { l:'浙江省',           lat:30.27, lng:120.15 },
  { l:'安徽省',           lat:31.86, lng:117.29 },
  { l:'福建省',           lat:26.07, lng:119.30 },
  { l:'江西省',           lat:28.68, lng:115.89 },
  { l:'山东省',           lat:36.67, lng:117.00 },
  { l:'河南省',           lat:34.76, lng:113.75 },
  { l:'湖北省',           lat:30.54, lng:114.30 },
  { l:'湖南省',           lat:28.23, lng:112.93 },
  { l:'广东省',           lat:23.13, lng:113.27 },
  { l:'广西壮族自治区',   lat:22.82, lng:108.37 },
  { l:'海南省',           lat:20.02, lng:110.35 },
  { l:'重庆市',           lat:29.57, lng:106.55 },
  { l:'四川省',           lat:30.65, lng:104.07 },
  { l:'贵州省',           lat:26.60, lng:106.71 },
  { l:'云南省',           lat:25.04, lng:102.71 },
  { l:'西藏自治区',       lat:29.65, lng:91.13  },
  { l:'陕西省',           lat:34.27, lng:108.95 },
  { l:'甘肃省',           lat:36.06, lng:103.83 },
  { l:'青海省',           lat:36.62, lng:101.78 },
  { l:'宁夏回族自治区',   lat:38.47, lng:106.27 },
  { l:'新疆维吾尔自治区', lat:43.79, lng:87.63  },
]

function lngLatToProvince(lng, lat) {
  let minDist = Infinity, result = null
  for (const p of PROVINCE_CENTROIDS) {
    const d = (lng - p.lng) ** 2 + (lat - p.lat) ** 2
    if (d < minDist) { minDist = d; result = p.l }
  }
  return result
}

/** 从 GeoJSON features 的 center/centroid 属性中找最近的城市名 */
function nearestCityFromFeatures(lng, lat, features) {
  let minDist = Infinity, result = null
  for (const feat of features) {
    const center = feat.properties?.center || feat.properties?.centroid
    if (!center) continue
    const d = (lng - center[0]) ** 2 + (lat - center[1]) ** 2
    if (d < minDist) { minDist = d; result = feat.properties?.name }
  }
  return result
}

// ── 仪表盘 CSS 圆环（conic-gradient） ─────────────────────────────────────────
const activeRingStyle = computed(() => ringStyle(activePct.value, '#ff6633'))
const onlineRingStyle = computed(() => ringStyle(onlinePct.value, '#00e5ff'))

function ringStyle(pct, color) {
  const deg = Math.round(pct * 3.6)
  return {
    background: `conic-gradient(${color} 0deg ${deg}deg, rgba(0,50,80,0.4) ${deg}deg 360deg)`,
    boxShadow: `0 0 18px ${color}55, 0 0 4px ${color}`,
  }
}

function alarmBarWidth(cnt) {
  const max = Math.max(...alarmTypes.value.map(a => Number(a.cnt)), 1)
  return Math.round((Number(cnt) / max) * 100)
}

// ── 中国地图 ──────────────────────────────────────────────────────────────────
async function buildMap(el, provinceData) {
  let geoJson
  try {
    const r = await fetch('https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json')
    geoJson = await r.json()
  } catch {
    return null
  }
  echarts.registerMap('china', geoJson)

  const mapData = Object.entries(provinceData).map(([name, value]) => ({ name, value }))
  const maxVal  = Math.max(...mapData.map(d => d.value), 1)

  const c = echarts.init(el, null, { renderer: 'canvas' })

  function applyMapOption(mapName, data, max, scatter = []) {
    const scatterSeries = scatter.length ? [{
      type: 'effectScatter',
      coordinateSystem: 'geo',
      data: scatter,
      symbolSize: 10,
      rippleEffect: { period: 1.5, scale: 3.5, brushType: 'stroke' },
      itemStyle: { color: '#00ff88', shadowBlur: 10, shadowColor: '#00ff8888' },
      label: {
        show: true,
        formatter: p => p.data.label || '',
        position: 'right',
        fontSize: 10,
        color: '#b0d8ff',
      },
      tooltip: {
        trigger: 'item',
        formatter: p => `${p.data.label || p.data.value[2] || '设备'}<br/>` +
          `${(+p.data.value[1]).toFixed(4)}°N  ${(+p.data.value[0]).toFixed(4)}°E`,
      },
      zlevel: 2,
    }] : []

    c.setOption({
      backgroundColor: 'transparent',
      geo: [{
        map: mapName,
        roam: mapName !== 'china',
        silent: true,
        itemStyle: { areaColor: 'transparent', borderColor: 'transparent' },
        label: { show: false },
      }],
      tooltip: {
        trigger: 'item',
        formatter: p => p.seriesType === 'map'
          ? `${p.name}<br/>设备数：${Number.isFinite(+p.value) ? +p.value : 0}`
          : undefined,
        backgroundColor: 'rgba(0,15,40,0.88)',
        borderColor: '#00e5ff',
        textStyle: { color: '#b0d8ff', fontSize: 13 },
      },
      visualMap: {
        show: false, min: 0, max: Math.max(max, 1),
        inRange: { color: ['#0a1a3a', '#0a3a6a', '#0070c0', '#00a0e0', '#00e5ff'] },
        seriesIndex: 0,
      },
      series: [{
        type: 'map', map: mapName,
        roam: mapName !== 'china',
        label: {
          show: true,
          fontSize: mapName === 'china' ? 9 : 10,
          color: 'rgba(160,210,255,0.75)',
        },
        emphasis: {
          label: { show: true, color: '#fff', fontSize: 12 },
          itemStyle: { areaColor: 'rgba(0,229,200,0.35)' },
        },
        itemStyle: {
          borderColor: '#00e5ff',
          borderWidth: mapName === 'china' ? 0.8 : 1,
          areaColor: 'rgba(4,18,50,0.5)',
        },
        data: data,
      }, ...scatterSeries],
    }, true)
  }

  const scatter = devicePoints.map(d => ({
    value: [d.last_lng, d.last_lat, d.phone],
    label: d.name || d.phone || '',
  }))
  applyMapOption('china', mapData, maxVal, scatter)

  // ── 省份下钻 ──
  c.on('click', async params => {
    if (params.componentType !== 'series') return
    const provinceName = params.name
    const adcode = PROVINCE_ADCODE[provinceName]
    if (!adcode) return

    currentProvince.value = provinceName

    // 拉取或复用省级 GeoJSON（同时缓存 features 用于城市归类）
    const mapKey = `province_${adcode}`
    let features = provinceFeaturesCache[adcode]
    if (!features) {
      try {
        const r = await fetch(`https://geo.datav.aliyun.com/areas_v3/bound/${adcode}_full.json`)
        const geo = await r.json()
        echarts.registerMap(mapKey, geo)
        features = geo.features || []
        provinceFeaturesCache[adcode] = features
      } catch {
        currentProvince.value = ''
        return
      }
    }

    // 该省内有 GPS 的设备
    const inProvince = devicePoints.filter(
      d => lngLatToProvince(d.last_lng, d.last_lat) === provinceName
    )

    // 按城市质心最近邻归类，统计各市设备数
    const cityCounts = {}
    for (const d of inProvince) {
      const city = nearestCityFromFeatures(d.last_lng, d.last_lat, features)
      if (city) cityCounts[city] = (cityCounts[city] || 0) + 1
    }
    const cityData = Object.entries(cityCounts).map(([name, value]) => ({ name, value }))
    const maxCity  = Math.max(...cityData.map(d => d.value), 1)

    // 散点（保留设备名称标注）
    const provinceScatter = inProvince.map(d => ({
      value: [d.last_lng, d.last_lat, d.phone],
      label: d.name || d.phone || '',
    }))

    applyMapOption(mapKey, cityData, maxCity, provinceScatter)
  })

  return c
}

// ── 返回全国地图 ─────────────────────────────────────────────────────────────
async function backToChina() {
  currentProvince.value = ''
  const c = charts.map
  if (!c) return
  const mapData = Object.entries(provinceData).map(([name, value]) => ({ name, value }))
  const maxVal  = Math.max(...mapData.map(d => d.value), 1)
  const scatter = devicePoints.map(d => ({
    value: [d.last_lng, d.last_lat, d.phone],
    label: d.name || d.phone || '',
  }))
  const scatterSeries = scatter.length ? [{
    type: 'effectScatter', coordinateSystem: 'geo',
    data: scatter, symbolSize: 10,
    rippleEffect: { period: 1.5, scale: 3.5, brushType: 'stroke' },
    itemStyle: { color: '#00ff88' },
    label: { show: true, formatter: p => p.data.label, position: 'right', fontSize: 10, color: '#b0d8ff' },
    zlevel: 2,
  }] : []
  c.setOption({
    geo: [{ map: 'china', roam: false, silent: true,
      itemStyle: { areaColor: 'transparent', borderColor: 'transparent' }, label: { show: false } }],
    series: [{
      type: 'map', map: 'china', roam: false,
      label: { fontSize: 9, color: 'rgba(160,210,255,0.75)' },
      itemStyle: { borderColor: '#00e5ff', borderWidth: 0.8, areaColor: 'rgba(4,18,50,0.5)' },
      data: mapData,
    }, ...scatterSeries],
    visualMap: { min: 0, max: maxVal },
  }, true)
}

// ── 折线图 ────────────────────────────────────────────────────────────────────
function buildLine(el, trend) {
  const c = echarts.init(el, null, { renderer: 'canvas' })
  const days = [], counts = []
  for (let i = 6; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i)
    const key = d.toISOString().slice(0, 10)
    days.push(key.slice(5))
    const found = trend.find(t => t.day === key)
    counts.push(found ? Number(found.cnt) : 0)
  }
  c.setOption({
    backgroundColor: 'transparent',
    grid: { left: 32, right: 12, top: 8, bottom: 22 },
    xAxis: {
      type: 'category', data: days,
      axisLine: { lineStyle: { color: '#1a3355' } },
      axisTick: { show: false },
      axisLabel: { color: '#4a6080', fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: '#4a6080', fontSize: 10 },
      splitLine: { lineStyle: { color: '#1a3355', type: 'dashed' } },
      minInterval: 1,
    },
    series: [{
      type: 'line', data: counts, smooth: true,
      symbol: 'circle', symbolSize: 5,
      lineStyle: { color: '#00e5ff', width: 2 },
      itemStyle: { color: '#00e5ff' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(0,229,255,0.30)' },
            { offset: 1, color: 'rgba(0,229,255,0)' },
          ],
        },
      },
    }],
  })
  return c
}

// ── 数据加载 ──────────────────────────────────────────────────────────────────
async function loadData() {
  const [devRes, sumRes] = await Promise.allSettled([
    deviceApi.list({ size: 500 }),
    reportApi.summary(),
  ])
  const devices = devRes.status === 'fulfilled' ? devRes.value.data?.records || [] : []
  const summary = sumRes.status === 'fulfilled' ? sumRes.value.data || {} : {}

  // 激活率 / 在线率 / 设备数量
  const total   = summary.device?.total  || 0
  const onlineCnt = summary.device?.online || 0
  const alarmCnt  = summary.device?.alarm  || 0
  const online  = onlineCnt + alarmCnt
  activePct.value  = total ? +(alarmCnt / total * 100).toFixed(2) : 0   // 报警设备占比
  onlinePct.value  = total ? +(onlineCnt / total * 100).toFixed(2) : 0  // 纯在线率
  deviceTotal.value   = total
  deviceOnline.value  = onlineCnt
  deviceAlarm.value   = alarmCnt
  deviceOffline.value = Math.max(0, total - online)

  // 告警
  const types = summary.alarm_types || []
  alarmTypes.value = types.slice(0, 5)
  alarmTotal.value = types.reduce((s, t) => s + Number(t.cnt), 0)

  // 省份统计：优先用 GPS 坐标，无 GPS 则按文字匹配
  const PROVINCE_SEARCH = [
    { s:'北京',  l:'北京市'  }, { s:'上海',  l:'上海市'  }, { s:'天津',  l:'天津市'  }, { s:'重庆',  l:'重庆市'  },
    { s:'广东',  l:'广东省'  }, { s:'浙江',  l:'浙江省'  }, { s:'江苏',  l:'江苏省'  }, { s:'山东',  l:'山东省'  },
    { s:'四川',  l:'四川省'  }, { s:'湖北',  l:'湖北省'  }, { s:'湖南',  l:'湖南省'  }, { s:'河南',  l:'河南省'  },
    { s:'河北',  l:'河北省'  }, { s:'安徽',  l:'安徽省'  }, { s:'福建',  l:'福建省'  }, { s:'江西',  l:'江西省'  },
    { s:'广西',  l:'广西壮族自治区' }, { s:'云南',  l:'云南省'  }, { s:'贵州',  l:'贵州省'  },
    { s:'陕西',  l:'陕西省'  }, { s:'山西',  l:'山西省'  }, { s:'甘肃',  l:'甘肃省'  }, { s:'辽宁',  l:'辽宁省'  },
    { s:'吉林',  l:'吉林省'  }, { s:'黑龙江',l:'黑龙江省'}, { s:'海南',  l:'海南省'  }, { s:'内蒙古',l:'内蒙古自治区'},
    { s:'新疆',  l:'新疆维吾尔自治区'}, { s:'西藏', l:'西藏自治区'}, { s:'宁夏',l:'宁夏回族自治区'},
    { s:'青海',  l:'青海省'  }, { s:'台湾',  l:'台湾省'  },
  ]
  const pCounts = {}
  devicePoints = []
  for (const d of devices) {
    if (d.last_lat && d.last_lng) {
      devicePoints.push(d)
      const province = lngLatToProvince(d.last_lng, d.last_lat)
      if (province) { pCounts[province] = (pCounts[province] || 0) + 1; continue }
    }
    // 无 GPS：按文字匹配省份
    const text = [d.plate_no, d.name, d.remark, d.phone].filter(Boolean).join('')
    for (const p of PROVINCE_SEARCH) {
      if (text.includes(p.s) || text.includes(p.l)) {
        pCounts[p.l] = (pCounts[p.l] || 0) + 1; break
      }
    }
  }
  if (!Object.keys(pCounts).length && devices.length) pCounts['广东省'] = devices.length
  provinceData = pCounts   // 存到模块级，backToChina 时复用

  // 初始化图表
  if (!charts.map) {
    charts.map  = await buildMap(mapEl.value, pCounts)
    charts.line = buildLine(lineEl.value, summary.loc_trend || [])
  } else {
    // 刷新折线图：同时更新 xAxis 日期，防止日期与数据错位
    const trend2 = summary.loc_trend || []
    const days2 = [], counts2 = []
    for (let i = 6; i >= 0; i--) {
      const d = new Date(); d.setDate(d.getDate() - i)
      const key = d.toISOString().slice(0, 10)
      days2.push(key.slice(5))
      const found2 = trend2.find(t => t.day === key)
      counts2.push(found2 ? Number(found2.cnt) : 0)
    }
    charts.line?.setOption({ xAxis: { data: days2 }, series: [{ data: counts2 }] })
  }
}

function updateTime() {
  currentTime.value = new Date().toLocaleString('zh-CN', { hour12: false })
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    bsEl.value?.requestFullscreen()
    isFullscreen.value = true
  } else {
    document.exitFullscreen()
    isFullscreen.value = false
  }
}

onMounted(async () => {
  updateTime()
  clockTimer = setInterval(updateTime, 1000)
  await loadData()
  dataTimer = setInterval(loadData, 30000)
  window.addEventListener('resize', () => Object.values(charts).forEach(c => c?.resize()))
  document.addEventListener('fullscreenchange', () => {
    isFullscreen.value = !!document.fullscreenElement
    setTimeout(() => Object.values(charts).forEach(c => c?.resize()), 200)
  })
})

onUnmounted(() => {
  clearInterval(clockTimer)
  clearInterval(dataTimer)
  Object.values(charts).forEach(c => c?.dispose())
})
</script>

<style scoped>
/* ── 根容器 ── */
.bigscreen {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background:
    radial-gradient(ellipse at 15% 40%, rgba(0,60,160,0.25) 0%, transparent 50%),
    radial-gradient(ellipse at 85% 20%, rgba(0,120,200,0.18) 0%, transparent 45%),
    radial-gradient(ellipse at 60% 80%, rgba(0,40,100,0.20) 0%, transparent 50%),
    #030d1c;
  color: #b0c4de;
  font-family: 'PingFang SC', 'Helvetica Neue', sans-serif;
  overflow: hidden;
}

/* ── 星点背景 ── */
.bigscreen::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    radial-gradient(1px 1px at 10% 15%, rgba(255,255,255,0.5) 0%, transparent 100%),
    radial-gradient(1px 1px at 25% 35%, rgba(255,255,255,0.35) 0%, transparent 100%),
    radial-gradient(1px 1px at 40% 10%, rgba(255,255,255,0.45) 0%, transparent 100%),
    radial-gradient(1px 1px at 55% 50%, rgba(255,255,255,0.3) 0%, transparent 100%),
    radial-gradient(1px 1px at 70% 25%, rgba(255,255,255,0.4) 0%, transparent 100%),
    radial-gradient(1px 1px at 80% 60%, rgba(255,255,255,0.35) 0%, transparent 100%),
    radial-gradient(1px 1px at 90% 10%, rgba(255,255,255,0.5) 0%, transparent 100%),
    radial-gradient(1px 1px at 15% 70%, rgba(255,255,255,0.3) 0%, transparent 100%),
    radial-gradient(1px 1px at 35% 80%, rgba(255,255,255,0.4) 0%, transparent 100%),
    radial-gradient(1px 1px at 65% 90%, rgba(255,255,255,0.35) 0%, transparent 100%);
  pointer-events: none;
  z-index: 0;
}

/* ── 顶部栏 ── */
.bs-header {
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 56px;
  z-index: 30;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: linear-gradient(180deg, rgba(3,15,40,0.90) 0%, rgba(3,15,40,0.60) 100%);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-bottom: 1px solid rgba(0,229,255,0.18);
}
.bs-header::after {
  content: '';
  position: absolute;
  bottom: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, #00e5ff88, transparent);
}
.bs-header-left  { display: flex; align-items: center; gap: 8px; }
.bs-header-center{ position: absolute; left: 50%; transform: translateX(-50%); }
.bs-header-right { display: flex; align-items: center; gap: 12px; }

.bs-logo-icon { font-size: 20px; }
.bs-logo-text { font-size: 13px; color: #4a90c0; letter-spacing: 1px; }
.bs-title {
  font-size: 22px; font-weight: 700; color: #00e5ff;
  letter-spacing: 4px;
  text-shadow: 0 0 24px rgba(0,229,255,0.7), 0 0 8px rgba(0,229,255,0.4);
}
.bs-time { font-size: 13px; color: #4a6888; font-variant-numeric: tabular-nums; }
.bs-btn {
  background: rgba(0,80,160,0.25);
  color: #7ec8e3; border: 1px solid rgba(0,180,255,0.3);
  border-radius: 4px; padding: 4px 14px; cursor: pointer; font-size: 12px;
  transition: all .2s;
}
.bs-btn:hover { background: rgba(0,150,255,0.40); color: #00e5ff; }

/* ── 地图铺满全屏 ── */
.map-fullscreen {
  position: absolute;
  inset: 0;
  z-index: 1;
}

/* ── 返回全国按钮 ── */
.back-btn {
  position: absolute;
  top: 66px;
  right: 20px;
  z-index: 20;
  background: rgba(0,80,160,0.35);
  color: #00e5ff;
  border: 1px solid rgba(0,229,255,0.35);
  border-radius: 4px;
  padding: 5px 16px;
  font-size: 13px;
  cursor: pointer;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  transition: all .2s;
}
.back-btn:hover { background: rgba(0,150,255,0.50); }

/* ── 左侧面板 ── */
.panel-left {
  position: absolute;
  top: 56px; left: 0; bottom: 0;
  width: 300px;
  z-index: 10;
  padding: 20px 18px 16px;
  display: flex;
  flex-direction: column;
  background: linear-gradient(135deg, rgba(3,12,35,0.78) 0%, rgba(3,20,50,0.68) 100%);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border-right: 1px solid rgba(0,229,255,0.10);
  overflow: hidden;
}

/* ── 右下浮层 ── */
.panel-bottom-right {
  position: absolute;
  bottom: 20px; right: 20px;
  width: 300px;
  z-index: 10;
  padding: 14px 16px 12px;
  background: linear-gradient(135deg, rgba(3,12,35,0.78) 0%, rgba(3,20,50,0.68) 100%);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid rgba(0,229,255,0.10);
  border-radius: 6px;
}

/* ── Section 标题 ── */
.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #7ec8e3;
  letter-spacing: 1px;
  margin-bottom: 10px;
}
.section-bar {
  display: inline-block;
  width: 3px; height: 14px;
  background: #00e5ff;
  border-radius: 2px;
  box-shadow: 0 0 8px #00e5ff;
  flex-shrink: 0;
}

/* ── 设备数量卡片 ── */
.kpi-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 18px;
}
.kpi-card {
  background: rgba(0,40,100,0.28);
  border: 1px solid rgba(0,229,255,0.12);
  border-radius: 6px;
  padding: 10px 6px 8px;
  text-align: center;
}
.kpi-val {
  font-size: 26px;
  font-weight: 700;
  color: #00e5ff;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 0 12px currentColor;
}
.kpi-lbl {
  font-size: 10px;
  color: #4a6888;
  margin-top: 4px;
  letter-spacing: 1px;
}

/* ── CSS 圆环仪表盘 ── */
.gauges-row {
  display: flex;
  gap: 16px;
  justify-content: center;
  margin-bottom: 4px;
}
.gauge-cell { display: flex; flex-direction: column; align-items: center; }
.gauge-ring {
  width: 110px; height: 110px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.8s ease;
  flex-shrink: 0;
}
.gauge-inner {
  width: 82px; height: 82px;
  border-radius: 50%;
  background: rgba(3,12,35,0.90);
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
}
.gauge-pct {
  font-size: 18px; font-weight: 700; color: #00ff88;
  line-height: 1.1;
}
.gauge-label {
  font-size: 10px; color: #6a9ab8; margin-top: 2px;
  text-align: center; white-space: nowrap;
}

/* ── 告警 TOP5 ── */
.alarm-total-line {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: #4a6888;
  padding: 6px 0 8px;
  border-bottom: 1px solid rgba(0,120,180,0.15);
  margin-bottom: 8px;
}
.alarm-dot        { color: #00e5ff; font-size: 10px; }
.alarm-total-lbl  { flex: 1; }
.alarm-total-val  { font-size: 18px; font-weight: 700; color: #00e5ff; }

.alarm-list { display: flex; flex-direction: column; gap: 0; flex: 1; overflow: hidden; }
.alarm-item { padding: 6px 0; }
.alarm-item-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 5px;
}
.alarm-name  { font-size: 12px; color: #8ab0cc; }
.alarm-count { font-size: 14px; font-weight: 600; color: #b0d4ee; }
.alarm-bar-bg {
  height: 3px;
  background: rgba(0,80,150,0.25);
  border-radius: 2px;
  overflow: hidden;
}
.alarm-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #0060c0, #00e5ff);
  border-radius: 2px;
  transition: width 0.8s ease;
}
.alarm-empty { font-size: 12px; color: #3a5070; padding: 16px 0; text-align: center; }

/* ── 右下图例 + 折线图 ── */
.legend-row {
  display: flex; align-items: center; gap: 8px;
  font-size: 11px;
}
.legend-txt { color: #7ec8e3; font-size: 12px; margin-right: 4px; }
.legend-lbl { color: #4a6888; }
.legend-bar {
  flex: 1; height: 5px;
  background: linear-gradient(90deg, #061428, #0070c0, #00e5ff);
  border-radius: 3px;
}
.line-chart { height: 110px; }
</style>
