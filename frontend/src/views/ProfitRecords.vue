<template>
  <div>
    <el-card shadow="never" style="margin-bottom:16px;">
      <el-row :gutter="16" align="middle">
        <el-col :span="6">
          <el-date-picker v-model="period" type="month" placeholder="选择结算月份"
            value-format="YYYY-MM" style="width:100%;" />
        </el-col>
        <el-col :span="10">
          <el-button type="primary" :disabled="!period" :loading="settling" @click="doSettle">结算该月分润</el-button>
          <el-button :disabled="!period" @click="load">查询</el-button>
          <el-button type="success" plain :disabled="!records.length" @click="exportCsv">导出CSV</el-button>
        </el-col>
        <el-col :span="8" style="text-align:right;">
          <span style="color:#909399;font-size:13px;">合计分润:</span>
          <span style="color:#67c23a;font-size:22px;font-weight:700;margin-left:6px;">¥{{ sumProfit.toFixed(2) }}</span>
        </el-col>
      </el-row>
    </el-card>

    <el-card v-if="summary.length" shadow="never" style="margin-bottom:16px;">
      <div style="font-size:13px;color:#909399;margin-bottom:8px;">按受益方汇总(当前页)</div>
      <el-space wrap>
        <el-tag v-for="s in summary" :key="s.name" type="info" size="large">
          {{ s.name }}:¥{{ s.total.toFixed(2) }}({{ s.count }}笔)
        </el-tag>
      </el-space>
    </el-card>

    <el-table :data="records" border stripe v-loading="loading">
      <el-table-column prop="period" label="结算月份" width="110" />
      <el-table-column prop="beneficiary_name" label="受益方(分销)" min-width="180" />
      <el-table-column prop="source_name" label="来源(总代)" min-width="180" />
      <el-table-column label="充值金额" width="110">
        <template #default="{ row }">¥{{ Number(row.amount).toFixed(2) }}</template>
      </el-table-column>
      <el-table-column prop="rate" label="比例" width="90">
        <template #default="{ row }">{{ Number(row.rate) }}%</template>
      </el-table-column>
      <el-table-column label="分润金额" width="120">
        <template #default="{ row }">
          <span style="color:#67c23a;font-weight:600;">¥{{ Number(row.profit).toFixed(2) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === '已付' ? 'success' : 'warning'" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="生成时间" min-width="160" />
      <el-table-column label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status !== '已付'" type="success" size="small" @click="markPaid(row)">标记已付</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      layout="total,prev,pager,next"
      style="margin-top:14px;"
      @change="load"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { profitApi } from '@/api'

const period = ref('')
const records = ref([])
const total = ref(0)
const sumProfit = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const settling = ref(false)

const summary = computed(() => {
  const m = {}
  records.value.forEach(r => {
    const k = r.beneficiary_name || '未知'
    if (!m[k]) m[k] = { name: k, total: 0, count: 0 }
    m[k].total += Number(r.profit) || 0
    m[k].count += 1
  })
  return Object.values(m)
})

async function load() {
  loading.value = true
  try {
    const res = await profitApi.records({ period: period.value || undefined, page: page.value, page_size: pageSize.value })
    records.value = res.data.records || []
    total.value = res.data.total || 0
    sumProfit.value = Number(res.data.sum_profit) || 0
  } catch (e) {
    ElMessage.error('加载失败:' + (e.response?.data?.msg || e.message || ''))
  }
  loading.value = false
}

async function doSettle() {
  try {
    await ElMessageBox.confirm(`确认结算 ${period.value} 的分润?已生成的记录不会重复计算。`, '结算确认', { type: 'warning' })
  } catch { return }
  settling.value = true
  try {
    const res = await profitApi.settle(period.value)
    const d = res.data || {}
    ElMessage.success(`结算完成:新增 ${d.created} 条,跳过 ${d.skipped} 条,未关联客户 ${d.unlinked} 笔`)
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.msg || e.message || '结算失败')
  }
  settling.value = false
}

async function markPaid(row) {
  try {
    await profitApi.markPaid(row.id)
    ElMessage.success('已标记已付')
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.msg || e.message || '操作失败')
  }
}

function exportCsv() {
  const head = ['结算月份', '受益方', '来源总代', '充值金额', '比例%', '分润金额', '状态', '生成时间']
  const rows = records.value.map(r => [
    r.period, r.beneficiary_name, r.source_name,
    Number(r.amount).toFixed(2), Number(r.rate), Number(r.profit).toFixed(2),
    r.status, r.created_at,
  ])
  const csv = [head, ...rows].map(line =>
    line.map(c => `"${String(c == null ? '' : c).replace(/"/g, '""')}"`).join(',')
  ).join('\n')
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `分润流水_${period.value || '全部'}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(load)
</script>
