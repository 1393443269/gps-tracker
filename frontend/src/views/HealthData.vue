<template>
  <el-card>
    <!-- 搜索栏 -->
    <el-row :gutter="12" style="margin-bottom:14px;" align="middle">
      <el-col :span="8">
        <el-input v-model="keyword" placeholder="归属账号 / 姓名 / 设备号" clearable @change="loadData(1)">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </el-col>
      <el-col :span="5">
        <el-date-picker v-model="day" type="date" value-format="YYYY-MM-DD"
          placeholder="按日期查询" clearable @change="loadData(1)" style="width:100%" />
      </el-col>
      <el-col :span="11" style="text-align:right;">
        <el-button type="primary" :icon="Search" @click="loadData(1)">查询</el-button>
        <el-button :icon="Download" @click="exportCsv">导出</el-button>
      </el-col>
    </el-row>

    <el-table :data="list" v-loading="loading" stripe border size="small">
      <el-table-column type="index" label="#" width="50" />
      <el-table-column prop="account"      label="归属账号"  width="140">
        <template #default="{ row }">{{ row.account || '—' }}</template>
      </el-table-column>
      <el-table-column prop="device_name"  label="姓名"      width="130">
        <template #default="{ row }">{{ row.device_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="设备号" width="160">
        <template #default="{ row }">{{ row.terminal_id || row.phone }}</template>
      </el-table-column>
      <el-table-column label="IMEI" width="160">
        <template #default="{ row }">{{ row.imei || row.phone }}</template>
      </el-table-column>
      <el-table-column label="体温℃" width="80" align="center">
        <template #default="{ row }">
          <span :style="tempStyle(row.temperature)">{{ fmt(row.temperature) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="腕温℃" width="80" align="center">
        <template #default="{ row }">{{ fmt(row.wrist_temp) }}</template>
      </el-table-column>
      <el-table-column label="心率" width="75" align="center">
        <template #default="{ row }">{{ fmt(row.heart_rate) }}</template>
      </el-table-column>
      <el-table-column label="血氧%" width="75" align="center">
        <template #default="{ row }">{{ fmt(row.blood_oxygen) }}</template>
      </el-table-column>
      <el-table-column label="收缩压" width="75" align="center">
        <template #default="{ row }">{{ fmt(row.systolic) }}</template>
      </el-table-column>
      <el-table-column label="舒张压" width="75" align="center">
        <template #default="{ row }">{{ fmt(row.diastolic) }}</template>
      </el-table-column>
      <el-table-column label="计步" width="85" align="center">
        <template #default="{ row }">{{ fmt(row.steps) }}</template>
      </el-table-column>
      <el-table-column prop="record_time" label="采集时间" min-width="160" />
    </el-table>

    <el-pagination style="margin-top:16px;justify-content:flex-end;display:flex;"
      :current-page="page" :page-size="pageSize" :total="total"
      layout="total,prev,pager,next" @current-change="loadData" />
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Search, Download } from '@element-plus/icons-vue'
import { healthApi } from '@/api'
import { ElMessage } from 'element-plus'

const list     = ref([])
const loading  = ref(false)
const page     = ref(1)
const pageSize = ref(20)
const total    = ref(0)
const keyword  = ref('')
const day      = ref('')

const fmt = (v) => (v === null || v === undefined || v === '') ? '—' : v

// 体温异常高亮（≥37.3℃ 标红）
function tempStyle(t) {
  if (t != null && Number(t) >= 37.3) return { color: '#f56c6c', fontWeight: 600 }
  return {}
}

async function loadData(p = page.value) {
  loading.value = true
  page.value = p
  try {
    const res = await healthApi.list({
      page: p, size: pageSize.value,
      keyword: keyword.value || undefined,
      day: day.value || undefined,
    })
    list.value  = res.data?.records || []
    total.value = res.data?.total   || 0
  } finally {
    loading.value = false
  }
}

function exportCsv() {
  if (!list.value.length) return ElMessage.warning('当前无数据可导出')
  const headers = ['归属账号', '姓名', '设备号', 'IMEI', '体温', '腕温', '心率', '血氧', '收缩压', '舒张压', '计步', '采集时间']
  const rows = list.value.map(r => [
    r.account || '', r.device_name || '', r.terminal_id || r.phone, r.imei || r.phone, r.temperature ?? '', r.wrist_temp ?? '',
    r.heart_rate ?? '', r.blood_oxygen ?? '', r.systolic ?? '', r.diastolic ?? '',
    r.steps ?? '', r.record_time || ''
  ])
  const csv = [headers, ...rows].map(row =>
    row.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')
  ).join('\n')
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `健康数据_${new Date().toISOString().slice(0, 10)}.csv`
  link.click()
  URL.revokeObjectURL(link.href)
}

onMounted(() => loadData(1))
</script>
