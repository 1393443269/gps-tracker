<template>
  <div>
    <!-- 统计卡片 -->
    <el-row :gutter="16" style="margin-bottom:20px;">
      <el-col :span="6" v-for="card in cards" :key="card.label">
        <el-card shadow="hover" style="text-align:center; padding:10px 0;">
          <div style="font-size:32px; font-weight:700;" :style="{ color: card.color }">{{ card.value }}</div>
          <div style="color:#909399; margin-top:6px;">{{ card.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 最近报警 -->
    <el-card>
      <template #header>
        <span>最近报警（未处理）</span>
        <el-button style="float:right;" type="primary" link @click="$router.push('/alarms')">查看全部</el-button>
      </template>
      <el-table :data="recentAlarms" size="small" stripe>
        <el-table-column prop="phone"     label="设备号"   width="140" />
        <el-table-column prop="alarmDesc" label="报警类型" width="140" />
        <el-table-column label="位置" width="220">
          <template #default="{ row }">
            {{ row.lat?.toFixed(6) }}, {{ row.lng?.toFixed(6) }}
          </template>
        </el-table-column>
        <el-table-column prop="alarmTime" label="报警时间" />
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="handleAlarm(row)">处理</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { deviceApi, alarmApi, portalApi, isAdmin } from '@/api'
import { ElMessage } from 'element-plus'

const summary     = reactive({ total: 0, online: 0, offline: 0, alarm: 0 })
const recentAlarms = ref([])

const cards = [
  { label: '设备总数',   value: () => summary.total,   color: '#409eff' },
  { label: '在线设备',   value: () => summary.online,  color: '#67c23a' },
  { label: '离线设备',   value: () => summary.offline, color: '#909399' },
  { label: '报警中',     value: () => summary.alarm,   color: '#f56c6c' },
].map(c => ({ ...c, get value() { return c.value() } }))

async function loadData() {
  const [res, res2] = await Promise.all([
    isAdmin() ? deviceApi.summary() : portalApi.summary(),
    isAdmin() ? alarmApi.list({ status: 0, size: 10 }) : portalApi.alarms({ status: 0, size: 10 }),
  ])
  Object.assign(summary, res.data || {})
  recentAlarms.value = res2.data?.records || []
}

async function handleAlarm(row) {
  if (isAdmin()) {
    await alarmApi.handle(row.id, { handler: '管理员', note: '已确认' })
  } else {
    await portalApi.handleAlarm(row.id, { handler: '客户', note: '已确认' })
  }
  ElMessage.success('处理成功')
  loadData()
}

onMounted(loadData)
</script>
