<template>
  <el-card>
    <!-- 筛选 -->
    <el-form inline style="margin-bottom:16px;">
      <el-form-item label="状态">
        <el-select v-model="filterStatus" clearable placeholder="全部" style="width:120px;">
          <el-option label="未处理" :value="0" />
          <el-option label="已处理" :value="1" />
        </el-select>
      </el-form-item>
      <el-form-item label="设备号">
        <el-input v-model="filterPhone" placeholder="终端手机号" clearable style="width:160px;" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="loadData(1)">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </el-form-item>
    </el-form>

    <!-- 批量操作栏 -->
    <div v-if="selected.length"
      style="margin-bottom:12px;display:flex;gap:8px;align-items:center;
             background:#fef0f0;border:1px solid #fde2e2;border-radius:6px;padding:8px 12px;">
      <span style="font-size:13px;color:#f56c6c;font-weight:500;">
        已选 {{ selected.length }} 条报警
      </span>
      <el-button size="small" type="primary" @click="openBatchHandle">批量处理</el-button>
      <el-button size="small" @click="clearSelection">取消选择</el-button>
    </div>

    <el-table ref="tableRef" :data="list" v-loading="loading" stripe border
      @selection-change="onSelectionChange">
      <el-table-column type="selection" width="45" :selectable="row => row.status === 0" />
      <el-table-column prop="phone" label="设备号" width="170">
        <template #default="{ row }">
          <span style="white-space:nowrap;">{{ row.phone }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="alarm_desc" label="报警类型" width="160">
        <template #default="{ row }">
          <el-tag
            :type="row.alarm_type === 103 ? 'danger'
                 : (row.alarm_type >= 100 && row.alarm_type <= 102) ? 'warning'
                 : 'danger'"
            size="small"
          >{{ row.alarm_desc }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="位置" width="220">
        <template #default="{ row }">
          <span v-if="row.lat">{{ row.lat.toFixed(5) }}, {{ row.lng.toFixed(5) }}</span>
          <span v-else style="color:#ccc;">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="alarm_time" label="报警时间" width="170" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 0 ? 'danger' : 'success'" size="small">
            {{ row.status === 0 ? '未处理' : '已处理' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="handler"    label="处理人"   width="100" />
      <el-table-column prop="handle_time" label="处理时间" width="170" />
      <el-table-column label="操作" fixed="right" width="90">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 0"
            size="small"
            type="primary"
            @click="handleRow(row)"
          >处理</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      style="margin-top:16px; justify-content:flex-end; display:flex;"
      :current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      @current-change="loadData"
    />

    <!-- 处理对话框（单条 / 批量共用） -->
    <el-dialog v-model="handleVisible"
      :title="batchMode ? `批量处理 ${selected.length} 条报警` : '处理报警'" width="400px">
      <el-form :model="handleForm" label-width="80px">
        <el-form-item label="处理人">
          <el-input v-model="handleForm.handler" />
        </el-form-item>
        <el-form-item label="处理备注">
          <el-input v-model="handleForm.note" type="textarea" rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="handleVisible = false">取消</el-button>
        <el-button type="primary" @click="submitHandle">确认处理</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { alarmApi, portalApi, isAdmin } from '@/api'
import { ElMessage } from 'element-plus'

const list         = ref([])
const loading      = ref(false)
const page         = ref(1)
const pageSize     = ref(20)
const total        = ref(0)
const filterStatus = ref(undefined)
const filterPhone  = ref('')

const handleVisible = ref(false)
const handleForm    = reactive({ id: null, handler: '管理员', note: '' })

// 多选 + 批量处理
const tableRef  = ref(null)
const selected  = ref([])
const batchMode = ref(false)
function onSelectionChange(rows) { selected.value = rows }
function clearSelection() {
  tableRef.value?.clearSelection()
  selected.value = []
}
function openBatchHandle() {
  if (!selected.value.length) return
  batchMode.value = true
  Object.assign(handleForm, { id: null, note: '' })
  handleVisible.value = true
}

async function loadData(p = page.value) {
  loading.value = true
  page.value = p
  try {
    const params = { page: p, size: pageSize.value, status: filterStatus.value, phone: filterPhone.value || undefined }
    const res = isAdmin() ? await alarmApi.list(params) : await portalApi.alarms(params)
    list.value  = res.data?.records || []
    total.value = res.data?.total   || 0
  } finally {
    loading.value = false
  }
}

function reset() {
  filterStatus.value = undefined
  filterPhone.value  = ''
  loadData(1)
}

function handleRow(row) {
  batchMode.value = false
  Object.assign(handleForm, { id: row.id, note: '' })
  handleVisible.value = true
}

async function submitHandle() {
  const payload = { handler: handleForm.handler, note: handleForm.note }
  if (batchMode.value) {
    // 批量处理（后端接口仅管理端有；客户端逐条处理兜底）
    const ids = selected.value.map(a => a.id)
    if (isAdmin()) {
      await alarmApi.batchHandle(ids, payload)
    } else {
      for (const id of ids) await portalApi.handleAlarm(id, payload)
    }
    ElMessage.success(`已处理 ${ids.length} 条报警`)
    clearSelection()
  } else {
    if (isAdmin()) {
      await alarmApi.handle(handleForm.id, payload)
    } else {
      await portalApi.handleAlarm(handleForm.id, payload)
    }
    ElMessage.success('处理成功')
  }
  handleVisible.value = false
  loadData()
}

onMounted(() => loadData(1))
</script>
