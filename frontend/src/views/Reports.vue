<template>
  <div v-loading="loading">

    <!-- 时间筛选栏 -->
    <el-row :gutter="12" style="margin-bottom:16px;" align="middle">
      <el-col :span="6">
        <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD"
          range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期"
          style="width:100%" @change="load" />
      </el-col>
      <el-col :span="3">
        <el-select v-model="quickDays" placeholder="快速选择" @change="onQuickDays" style="width:100%">
          <el-option label="近 7 天"  :value="7"  />
          <el-option label="近 30 天" :value="30" />
          <el-option label="近 90 天" :value="90" />
        </el-select>
      </el-col>
      <el-col :span="15" style="text-align:right">
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </el-col>
    </el-row>

    <!-- SIM 卡到期预警 -->
    <el-row :gutter="12" style="margin-bottom:16px;" v-if="data.sim?.expiring7 > 0 || data.sim?.expired > 0">
      <el-col :span="24">
        <el-alert v-if="data.sim?.expired > 0" type="error" :closable="false" show-icon>
          <template #title>
            <b>{{ data.sim.expired }}</b> 张 SIM 卡已过期，
            <b>{{ data.sim.expiring7 }}</b> 张将在 7 天内到期，请尽快处理！
          </template>
        </el-alert>
        <el-alert v-else type="warning" :closable="false" show-icon>
          <template #title>
            <b>{{ data.sim.expiring7 }}</b> 张 SIM 卡将在 7 天内到期，
            <b>{{ data.sim.expiring30 }}</b> 张将在 30 天内到期
          </template>
        </el-alert>
      </el-col>
    </el-row>

    <!-- KPI 卡片 -->
    <el-row :gutter="12" style="margin-bottom:16px;">
      <el-col :span="4" v-for="card in kpis" :key="card.label">
        <el-card shadow="never" :body-style="{ padding:'14px' }">
          <div style="font-size:11px;color:#909399">{{ card.label }}</div>
          <div :style="{ fontSize:'24px', fontWeight:700, color:card.color, marginTop:'4px' }">
            {{ card.prefix }}{{ card.val }}
          </div>
          <div v-if="card.sub" style="font-size:11px;color:#909399;margin-top:2px">{{ card.sub }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 图表行 -->
    <el-row :gutter="16" style="margin-bottom:16px;">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <span>报警趋势</span>
            <span style="font-size:12px;color:#909399;margin-left:8px">（近 {{ data.trend_days ?? 30 }} 天）</span>
          </template>
          <el-empty v-if="chartLoadFailed" description="图表库未加载" :image-size="50" />
          <canvas v-else ref="alarmTrendCanvas" height="180" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>报警类型分布</template>
          <el-empty v-if="chartLoadFailed" description="图表库未加载" :image-size="50" />
          <canvas v-else ref="alarmTypeCanvas" height="180" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-bottom:16px;">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <span>定位上报量</span>
            <span style="font-size:12px;color:#909399;margin-left:8px">（近 {{ data.trend_days ?? 30 }} 天）</span>
          </template>
          <el-empty v-if="chartLoadFailed" description="图表库未加载" :image-size="50" />
          <canvas v-else ref="locTrendCanvas" height="180" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>设备生命周期分布</template>
          <el-empty v-if="chartLoadFailed" description="图表库未加载" :image-size="50" />
          <canvas v-else ref="lcCanvas" height="180" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 客户设备排名 + 数据汇总 -->
    <el-row :gutter="16">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>客户设备数排名（Top 10）</template>
          <el-table :data="data.customer?.rank ?? []" size="small" border>
            <el-table-column type="index" label="#" width="40" />
            <el-table-column prop="name"         label="客户名称" />
            <el-table-column prop="device_count" label="设备数" width="80" align="right" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>数据汇总</template>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="设备总数">{{ data.device?.total }}</el-descriptions-item>
            <el-descriptions-item label="本月新增设备">{{ data.device?.new_this_month }}</el-descriptions-item>
            <el-descriptions-item label="已激活">{{ data.device?.active }}</el-descriptions-item>
            <el-descriptions-item label="已停用/报废">{{ data.device?.disabled }}</el-descriptions-item>
            <el-descriptions-item label="报警总数">{{ data.alarm?.total }}</el-descriptions-item>
            <el-descriptions-item label="未处理报警">{{ data.alarm?.unhandled }}</el-descriptions-item>
            <el-descriptions-item label="SIM卡总数">{{ data.sim?.total }}</el-descriptions-item>
            <el-descriptions-item label="30天内到期">
              <span :style="{ color: (data.sim?.expiring30 > 0) ? '#e6a23c' : '' }">
                {{ data.sim?.expiring30 }}
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="客户总数">{{ data.customer?.total }}</el-descriptions-item>
            <el-descriptions-item label="本月新增客户">{{ data.customer?.new_this_month }}</el-descriptions-item>
            <el-descriptions-item label="总充值">¥{{ data.recharge_total?.toFixed(2) }}</el-descriptions-item>
            <el-descriptions-item label="期间充值">¥{{ data.recharge_period?.toFixed(2) }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { reportApi, portalApi, isAdmin } from '@/api'

const loading         = ref(false)
const dateRange       = ref(null)
const quickDays       = ref(30)
const chartLoadFailed = ref(false)
let Chart = null

const data = ref({
  device: {}, alarm: {}, sim: {}, customer: { rank: [] },
  location: {}, recharge_total: 0, recharge_period: 0,
  alarm_trend: [], alarm_types: [], loc_trend: [], trend_days: 30
})

const alarmTrendCanvas = ref(null)
const alarmTypeCanvas  = ref(null)
const locTrendCanvas   = ref(null)
const lcCanvas         = ref(null)

// ── KPI 卡片 ─────────────────────────────────────────────────────────────────
const kpis = computed(() => [
  { label:'设备总数',   val: data.value.device?.total    ?? 0, color:'#409EFF' },
  { label:'在线设备',   val: data.value.device?.online   ?? 0, color:'#67c23a' },
  { label:'未处理报警', val: data.value.alarm?.unhandled ?? 0, color:'#f56c6c' },
  { label:'SIM卡数',   val: data.value.sim?.total       ?? 0, color:'#e6a23c' },
  { label:'7天内到期',  val: data.value.sim?.expiring7   ?? 0,
    color: (data.value.sim?.expiring7 ?? 0) > 0 ? '#f56c6c' : '#909399' },
  { label:'客户数',     val: data.value.customer?.total  ?? 0, color:'#909399',
    sub: `本月新增 ${data.value.customer?.new_this_month ?? 0}` },
])

// ── 时间选择辅助 ──────────────────────────────────────────────────────────────
function onQuickDays(d) {
  dateRange.value = null
  load()
}

// ── 图表填充 ──────────────────────────────────────────────────────────────────
function fillDays(trend, days) {
  const result = []
  const labels = []
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date()
    d.setDate(d.getDate() - i)
    const day = d.toISOString().slice(0, 10)
    labels.push(day.slice(5))
    const found = trend.find(t => t.day === day)
    result.push(found ? found.cnt : 0)
  }
  return { labels, data: result }
}

function drawChart(canvas, type, labels, datasets, opts = {}) {
  if (!canvas || !Chart) return
  const existing = Chart.getChart(canvas)
  if (existing) existing.destroy()
  new Chart(canvas, {
    type,
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } },
      ...opts
    }
  })
}

function drawCharts() {
  const td   = data.value.trend_days ?? 30
  const at   = fillDays(data.value.alarm_trend || [], td)
  const lt   = fillDays(data.value.loc_trend   || [], td)
  const types= data.value.alarm_types || []
  const dev  = data.value.device || {}

  drawChart(alarmTrendCanvas.value, 'bar', at.labels, [{
    label: '报警次数', data: at.data,
    backgroundColor: 'rgba(245,108,108,0.7)', borderRadius: 3
  }], { scales: { y: { beginAtZero: true, ticks: { font: { size: 10 } } } } })

  drawChart(alarmTypeCanvas.value, 'doughnut', types.map(t => t.alarm_desc),
    [{ data: types.map(t => t.cnt),
       backgroundColor: ['#409EFF','#f56c6c','#e6a23c','#67c23a','#909399','#b39ddb'] }])

  drawChart(locTrendCanvas.value, 'line', lt.labels, [{
    label: '上报次数', data: lt.data,
    borderColor: '#409EFF', backgroundColor: 'rgba(64,158,255,0.15)',
    fill: true, tension: 0.4, pointRadius: 2
  }], { scales: { y: { beginAtZero: true, ticks: { font: { size: 10 } } } } })

  drawChart(lcCanvas.value, 'doughnut',
    ['未激活', '已激活', '已停用', '已报废'],
    [{ data: [dev.inactive ?? 0, dev.active ?? 0, dev.disabled ?? 0, 0],
       backgroundColor: ['#909399','#67c23a','#e6a23c','#f56c6c'] }])
}

// ── 加载数据 ─────────────────────────────────────────────────────────────────
async function load() {
  loading.value = true
  try {
    const params = {}
    if (dateRange.value?.[0]) {
      params.start = dateRange.value[0]
      params.end   = dateRange.value[1]
    } else {
      params.days = quickDays.value
    }
    const res = isAdmin() ? await reportApi.summary(params) : await portalApi.reportSummary(params)
    data.value = res.data || {}
    await nextTick()
    drawCharts()
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  if (window.Chart) {
    Chart = window.Chart
  } else {
    try {
      const m = await import('chart.js/auto')
      Chart = m.default
    } catch {
      chartLoadFailed.value = true
    }
  }
  load()
})
</script>
