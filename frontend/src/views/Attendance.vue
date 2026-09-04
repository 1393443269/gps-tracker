<template>
  <el-card>
    <div style="font-size:14px;font-weight:600;margin-bottom:14px;">考勤统计（基于电子围栏进出）</div>

    <el-table :data="list" v-loading="loading" stripe border size="small">
      <el-table-column type="index" label="#" width="50" />
      <el-table-column prop="fence_name" label="围栏名称" width="200" show-overflow-tooltip />
      <el-table-column prop="device_count" label="设备数" width="90" align="center">
        <template #default="{ row }">
          <el-tag size="small" type="primary">{{ row.device_count ?? 0 }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="enter_count" label="进入次数" width="100" align="center" />
      <el-table-column prop="exit_count"  label="离开次数" width="100" align="center" />
      <el-table-column prop="last_time"   label="最近事件" width="180" />
      <el-table-column />   <!-- 占位空列,吸收右侧余量,避免固定宽列被拉伸 -->
      <el-table-column label="操作" fixed="right" width="110" align="center">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="openDetail(row)">查看明细</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && !list.length" description="暂无考勤数据（设备进出围栏后自动生成）" />

    <!-- 明细弹窗 -->
    <el-dialog v-model="detailVisible" :title="`${detailFence?.fence_name || ''} — 考勤明细`" width="640px">
      <div style="margin-bottom:10px;display:flex;gap:10px;align-items:center;">
        <el-date-picker v-model="detailDay" type="date" value-format="YYYY-MM-DD"
          placeholder="按日期筛选" clearable @change="loadDetail(1)" size="small" />
        <span style="font-size:12px;color:#909399;">共 {{ detailTotal }} 条</span>
      </div>
      <el-table :data="detailList" size="small" border stripe height="360" v-loading="detailLoading">
        <el-table-column prop="device_name" label="名称" width="130">
          <template #default="{ row }">{{ row.device_name || '—' }}</template>
        </el-table-column>
        <el-table-column label="设备号" width="160">
          <template #default="{ row }">{{ row.terminal_id || row.phone }}</template>
        </el-table-column>
        <el-table-column label="IMEI" width="160">
          <template #default="{ row }">{{ row.imei || row.phone }}</template>
        </el-table-column>
        <el-table-column label="动作" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.action === 'enter' ? 'success' : 'info'">
              {{ row.action === 'enter' ? '进入' : '离开' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="event_time" label="时间" min-width="160" />
      </el-table>
      <el-pagination style="margin-top:12px;justify-content:flex-end;display:flex;"
        :current-page="detailPage" :page-size="detailSize" :total="detailTotal"
        layout="total,prev,pager,next" @current-change="loadDetail" />
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { attendanceApi } from '@/api'

const list    = ref([])
const loading = ref(false)

const detailVisible = ref(false)
const detailFence   = ref(null)
const detailList    = ref([])
const detailLoading = ref(false)
const detailDay     = ref('')
const detailPage    = ref(1)
const detailSize    = ref(50)
const detailTotal   = ref(0)

async function loadList() {
  loading.value = true
  try {
    const res = await attendanceApi.list()
    list.value = res.data?.records || []
  } finally {
    loading.value = false
  }
}

function openDetail(row) {
  detailFence.value = row
  detailDay.value   = ''
  detailVisible.value = true
  loadDetail(1)
}

async function loadDetail(p = detailPage.value) {
  if (!detailFence.value) return
  detailPage.value = p
  detailLoading.value = true
  try {
    const res = await attendanceApi.detail({
      fence_id: detailFence.value.fence_id,
      day: detailDay.value || undefined,
      page: p, size: detailSize.value,
    })
    detailList.value  = res.data?.records || []
    detailTotal.value = res.data?.total   || 0
  } finally {
    detailLoading.value = false
  }
}

onMounted(() => loadList())
</script>
