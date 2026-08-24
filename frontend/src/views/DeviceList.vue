<template>
  <el-card>
    <!-- 搜索栏 -->
    <el-row :gutter="12" style="margin-bottom:14px;" align="middle">
      <el-col :span="7">
        <el-input v-model="keyword" placeholder="IMEI / 名称 / 位置" clearable @change="loadData(1)">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </el-col>
      <el-col :span="4">
        <el-select v-model="lcFilter" placeholder="生命周期" clearable @change="loadData(1)" style="width:100%">
          <el-option v-for="o in LC_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
      </el-col>
      <el-col :span="4">
        <el-select v-model="statusFilter" placeholder="在线状态" clearable @change="loadData(1)" style="width:100%">
          <el-option label="在线" :value="1" />
          <el-option label="报警" :value="2" />
          <el-option label="离线" :value="0" />
        </el-select>
      </el-col>
      <el-col :span="9" style="text-align:right;display:flex;gap:8px;justify-content:flex-end;">
        <el-button type="primary" :icon="Search" @click="loadData(1)">搜索</el-button>
        <el-button v-if="isAdmin()" type="success" :icon="Plus" @click="openCreate">新增设备</el-button>
      </el-col>
    </el-row>

    <!-- 批量操作栏 -->
    <div v-if="selectedDevices.length"
      style="margin-bottom:12px;display:flex;gap:8px;align-items:center;
             background:#f0f7ff;border:1px solid #d0e8ff;border-radius:6px;padding:8px 12px;">
      <span style="font-size:13px;color:#409EFF;font-weight:500;">
        已选 {{ selectedDevices.length }} 台设备
      </span>
      <el-button size="small" type="primary" @click="openBatchFence">批量分配围栏</el-button>
      <el-dropdown @command="batchSetLifecycle">
        <el-button size="small">批量变更状态 <el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="o in LC_OPTIONS" :key="o.value" :command="o.value">
              {{ o.label }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button size="small" @click="clearSelection">取消选择</el-button>
    </div>

    <el-table ref="tableRef" :data="list" v-loading="loading" stripe border size="small"
      @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="45" />
      <el-table-column prop="phone"         label="IMEI"     width="150" />
      <el-table-column prop="name"          label="名称"     width="120" />
      <el-table-column prop="plate_no"      label="位置"     width="120" />
      <el-table-column label="生命周期" width="95">
        <template #default="{ row }">
          <el-tag :type="lcTagType(row.lifecycle)" size="small">{{ lcLabel(row.lifecycle) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="在线状态" width="80">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最新坐标" width="200">
        <template #default="{ row }">
          <span v-if="row.last_lat">{{ Number(row.last_lat).toFixed(5) }}, {{ Number(row.last_lng).toFixed(5) }}</span>
          <span v-else style="color:#ccc">—</span>
        </template>
      </el-table-column>
      <el-table-column label="速度" width="80">
        <template #default="{ row }">
          {{ row.last_speed != null ? (row.last_speed / 10).toFixed(1) + ' km/h' : '—' }}
        </template>
      </el-table-column>
      <el-table-column prop="last_location_time" label="最新定位" width="165" />
      <el-table-column label="所属围栏" width="130">
        <template #default="{ row }">
          <span v-if="deviceFenceNames(row.phone)" style="font-size:12px;color:#409EFF">{{ deviceFenceNames(row.phone) }}</span>
          <span v-else style="color:#ccc;font-size:12px">未分配</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" fixed="right" width="160">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="warning" @click="sendText(row)">下发文本</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination style="margin-top:16px;justify-content:flex-end;display:flex;"
      :current-page="page" :page-size="pageSize" :total="total"
      layout="total,prev,pager,next" @current-change="loadData" />

    <!-- 编辑设备弹窗 -->
    <el-dialog v-model="editVisible" title="编辑设备" width="480px" @open="onEditOpen">
      <el-form :model="editForm" label-width="90px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="位置/车牌">
          <el-input v-model="editForm.plateNo" />
        </el-form-item>
        <el-form-item label="生命周期">
          <el-select v-model="editForm.lifecycle" style="width:100%">
            <el-option v-for="o in LC_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="editForm.remark" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="电子围栏">
          <el-select v-model="editForm.fenceIds" multiple collapse-tags collapse-tags-tooltip
            placeholder="选择关联围栏（可多选）" style="width:100%" :loading="loadingFences">
            <el-option v-for="f in allFences" :key="f.id" :label="f.name" :value="f.id">
              <span>{{ f.name }}</span>
              <span style="float:right;font-size:11px;color:#909399;margin-left:8px">{{ fenceTypeLabel(f.fence_type) }}</span>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="submitEdit" :loading="editSaving">保存</el-button>
      </template>
    </el-dialog>

    <!-- 文本下发弹窗 -->
    <el-dialog v-model="textVisible" title="下发文本消息" width="400px">
      <el-form :model="textForm" label-width="80px">
        <el-form-item label="设备"><el-input :model-value="textForm.phone" disabled /></el-form-item>
        <el-form-item label="消息内容">
          <el-input v-model="textForm.text" type="textarea" rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="textVisible = false">取消</el-button>
        <el-button type="primary" @click="submitText">发送</el-button>
      </template>
    </el-dialog>

    <!-- 新增设备弹窗 -->
    <el-dialog v-model="createVisible" title="新增设备" width="460px">
      <el-form :model="createForm" label-width="90px">
        <el-form-item label="IMEI/设备号" required>
          <el-input v-model="createForm.phone" placeholder="请输入15位IMEI或设备号" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="createForm.name" placeholder="选填" />
        </el-form-item>
        <el-form-item label="位置/车牌">
          <el-input v-model="createForm.plateNo" placeholder="选填" />
        </el-form-item>
        <el-form-item label="型号">
          <el-input v-model="createForm.terminalModel" placeholder="选填，如 EC800M" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate" :loading="createSaving">创建</el-button>
      </template>
    </el-dialog>

    <!-- 批量分配围栏弹窗 -->
    <el-dialog v-model="batchFenceVisible" title="批量分配围栏" width="460px" @open="onBatchFenceOpen">
      <div style="margin-bottom:10px;font-size:13px;color:#606266;">
        将 <b>{{ selectedDevices.length }}</b> 台设备同时加入选中围栏：
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px;">
        <el-tag v-for="d in selectedDevices" :key="d.phone" size="small" type="info">{{ d.name || d.phone }}</el-tag>
      </div>
      <el-divider style="margin:10px 0" />
      <el-checkbox-group v-model="batchSelectedFenceIds">
        <div v-for="f in allFences" :key="f.id"
          style="padding:6px 0;border-bottom:1px solid #f5f5f5;display:flex;align-items:center;gap:8px;">
          <el-checkbox :value="f.id" style="margin:0" />
          <span style="font-size:13px;font-weight:500;flex:1">{{ f.name }}</span>
          <el-tag size="small">{{ fenceTypeLabel(f.fence_type) }}</el-tag>
          <span style="font-size:11px;color:#909399">{{ fenceDeviceCount(f) }}台</span>
        </div>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="batchFenceVisible = false">取消</el-button>
        <el-button type="primary" @click="submitBatchFence" :loading="batchSaving"
          :disabled="!batchSelectedFenceIds.length">确认分配</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { Search, ArrowDown, Plus } from '@element-plus/icons-vue'
import { deviceApi, commandApi, fenceApi, portalApi, isAdmin } from '@/api'
import { ElMessage, ElMessageBox } from 'element-plus'

// ── 生命周期枚举 ──────────────────────────────────────────────────────────────
const LC_OPTIONS = [
  { value: 0, label: '未激活', tagType: 'info'    },
  { value: 1, label: '已激活', tagType: 'success' },
  { value: 2, label: '已停用', tagType: 'warning' },
  { value: 3, label: '已报废', tagType: 'danger'  },
]
const lcLabel   = (v) => LC_OPTIONS.find(o => o.value === v)?.label   ?? '未知'
const lcTagType = (v) => LC_OPTIONS.find(o => o.value === v)?.tagType ?? ''

// ── 状态辅助 ─────────────────────────────────────────────────────────────────
const statusLabel    = (s) => ({ 0:'离线', 1:'在线', 2:'报警' }[s] ?? '—')
const statusType     = (s) => ({ 0:'info',  1:'success', 2:'danger' }[s] ?? '')
const fenceTypeLabel = (t) => ({ circle:'圆形', polygon:'多边形', administrative:'行政区' }[t] || t)

// ── 列表状态 ─────────────────────────────────────────────────────────────────
const list       = ref([])
const loading    = ref(false)
const page       = ref(1)
const pageSize   = ref(20)
const total      = ref(0)
const keyword    = ref('')
const lcFilter   = ref(null)
const statusFilter = ref(null)

const allFences      = ref([])
const loadingFences  = ref(false)
const selectedDevices= ref([])
const tableRef       = ref(null)

// ── 编辑 ─────────────────────────────────────────────────────────────────────
const editVisible = ref(false)
const editSaving  = ref(false)
const editForm    = reactive({ id:null, phone:'', name:'', plateNo:'', lifecycle:1, remark:'', fenceIds:[] })
let   originalFenceIds = []

// ── 文本下发 ─────────────────────────────────────────────────────────────────
const textVisible = ref(false)
const textForm    = reactive({ phone:'', text:'' })

// ── 新增设备 ─────────────────────────────────────────────────────────────────
const createVisible = ref(false)
const createSaving  = ref(false)
const createForm    = reactive({ phone: '', name: '', plateNo: '', terminalModel: '', remark: '' })

function openCreate() {
  Object.assign(createForm, { phone: '', name: '', plateNo: '', terminalModel: '', remark: '' })
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.phone.trim()) return ElMessage.warning('请输入设备号')
  createSaving.value = true
  try {
    await deviceApi.create({ ...createForm })
    ElMessage.success('设备创建成功')
    createVisible.value = false
    loadData(1)
  } catch {} finally {
    createSaving.value = false
  }
}

// ── 批量围栏 ─────────────────────────────────────────────────────────────────
const batchFenceVisible     = ref(false)
const batchSaving           = ref(false)
const batchSelectedFenceIds = ref([])

// ── 数据加载 ─────────────────────────────────────────────────────────────────
async function loadData(p = page.value) {
  loading.value = true
  page.value = p
  try {
    const params = {
      page: p, size: pageSize.value,
      keyword:  keyword.value    || undefined,
      lifecycle: lcFilter.value  != null ? lcFilter.value : undefined,
      status:   statusFilter.value != null ? statusFilter.value : undefined,
    }
    const res = isAdmin()
      ? await deviceApi.list(params)
      : await portalApi.deviceList(params)
    list.value  = res.data?.records || []
    total.value = res.data?.total   || 0
  } finally {
    loading.value = false
  }
}

async function loadFences() {
  loadingFences.value = true
  try {
    const res = isAdmin() ? await fenceApi.list({}) : await portalApi.fences()
    allFences.value = res.data || []
  } finally {
    loadingFences.value = false
  }
}

function deviceFenceNames(phone) {
  const names = allFences.value
    .filter(f => f.devices && f.devices.split(',').filter(Boolean).includes(String(phone)))
    .map(f => f.name)
  if (!names.length) return ''
  return names.length <= 2 ? names.join('、') : `${names[0]} 等${names.length}个`
}

function fenceDeviceCount(f) {
  return f.devices ? f.devices.split(',').filter(Boolean).length : 0
}

// ── 批量勾选 ─────────────────────────────────────────────────────────────────
function handleSelectionChange(rows) { selectedDevices.value = rows }
function clearSelection() {
  tableRef.value?.clearSelection()
  selectedDevices.value = []
}

// ── 批量变更生命周期 ──────────────────────────────────────────────────────────
async function batchSetLifecycle(lc) {
  if (!selectedDevices.value.length) return
  const label = lcLabel(lc)
  try {
    await ElMessageBox.confirm(
      `将 ${selectedDevices.value.length} 台设备状态改为「${label}」？`,
      '批量变更', { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' }
    )
  } catch { return }
  const ids = selectedDevices.value.map(d => d.id)
  await deviceApi.batchLifecycle(ids, lc)
  ElMessage.success(`已将 ${ids.length} 台设备更新为「${label}」`)
  clearSelection()
  loadData()
}

// ── 编辑设备 ─────────────────────────────────────────────────────────────────
function openEdit(row) {
  Object.assign(editForm, {
    id: row.id, phone: row.phone,
    name: row.name || '', plateNo: row.plate_no || row.plateNo || '',
    lifecycle: row.lifecycle ?? 1, remark: row.remark || '',
    fenceIds: []
  })
  editVisible.value = true
}

async function onEditOpen() {
  await loadFences()
  const phone = String(editForm.phone)
  originalFenceIds = allFences.value
    .filter(f => f.devices && f.devices.split(',').filter(Boolean).includes(phone))
    .map(f => f.id)
  editForm.fenceIds = [...originalFenceIds]
}

async function submitEdit() {
  editSaving.value = true
  try {
    if (isAdmin()) {
      await deviceApi.update(editForm.id, {
        name: editForm.name, plateNo: editForm.plateNo,
        lifecycle: editForm.lifecycle, remark: editForm.remark
      })
    } else {
      await portalApi.updateDevice(editForm.phone, { name: editForm.name, plateNo: editForm.plateNo })
    }
    // 同步围栏
    const phone   = String(editForm.phone)
    const added   = editForm.fenceIds.filter(id => !originalFenceIds.includes(id))
    const removed = originalFenceIds.filter(id => !editForm.fenceIds.includes(id))
    const updFence = isAdmin() ? fenceApi.updateDevices : (id, ph) => portalApi.fenceDevices(id, ph)
    for (const fid of added) {
      const fence = allFences.value.find(f => f.id === fid)
      if (!fence) continue
      const phones = (fence.devices || '').split(',').filter(Boolean)
      if (!phones.includes(phone)) phones.push(phone)
      await updFence(fid, phones)
    }
    for (const fid of removed) {
      const fence = allFences.value.find(f => f.id === fid)
      if (!fence) continue
      await updFence(fid, (fence.devices || '').split(',').filter(Boolean).filter(p => p !== phone))
    }
    ElMessage.success('保存成功')
    editVisible.value = false
    loadData(); loadFences()
  } finally {
    editSaving.value = false
  }
}

// ── 批量分配围栏 ──────────────────────────────────────────────────────────────
function openBatchFence() {
  batchSelectedFenceIds.value = []
  batchFenceVisible.value = true
}
async function onBatchFenceOpen() { await loadFences() }

async function submitBatchFence() {
  batchSaving.value = true
  try {
    const phones   = selectedDevices.value.map(d => String(d.phone))
    const updFence = isAdmin() ? fenceApi.updateDevices : (id, ph) => portalApi.fenceDevices(id, ph)
    for (const fid of batchSelectedFenceIds.value) {
      const fence = allFences.value.find(f => f.id === fid)
      if (!fence) continue
      const merged = [...new Set([...(fence.devices || '').split(',').filter(Boolean), ...phones])]
      await updFence(fid, merged)
    }
    ElMessage.success(`已分配 ${phones.length} 台设备到 ${batchSelectedFenceIds.value.length} 个围栏`)
    batchFenceVisible.value = false
    clearSelection(); loadFences()
  } finally { batchSaving.value = false }
}

// ── 文本下发 ─────────────────────────────────────────────────────────────────
function sendText(row) {
  Object.assign(textForm, { phone: row.phone, text: '' })
  textVisible.value = true
}
async function submitText() {
  if (!textForm.text.trim()) return ElMessage.warning('请输入消息内容')
  isAdmin()
    ? await commandApi.sendText(textForm.phone, textForm.text)
    : await portalApi.sendCommand({ phone: textForm.phone, text: textForm.text })
  ElMessage.success('下发成功')
  textVisible.value = false
}

let refreshTimer = null
onMounted(() => {
  loadData(1)
  loadFences()
  refreshTimer = setInterval(() => loadData(page.value), 30000)
})
onUnmounted(() => { if (refreshTimer) clearInterval(refreshTimer) })
</script>
