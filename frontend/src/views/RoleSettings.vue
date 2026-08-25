<template>
  <el-card>
    <!-- 顶部操作栏 -->
    <div style="margin-bottom:14px;display:flex;gap:10px;">
      <el-button type="primary" :icon="Plus" @click="openCreate">添加角色</el-button>
      <el-button :icon="Grid" @click="openAssign">分配角色</el-button>
    </div>

    <el-table :data="list" v-loading="loading" stripe border size="small">
      <el-table-column type="index" label="#" width="50" />
      <el-table-column prop="name" label="角色名称" min-width="140" />
      <el-table-column label="角色颜色" width="90" align="center">
        <template #default="{ row }">
          <span :style="{
            display:'inline-block', width:'24px', height:'24px',
            background: row.color, borderRadius:'4px',
            border:'1px solid #e4e7ed', verticalAlign:'middle'
          }" />
        </template>
      </el-table-column>
      <el-table-column label="角色图标" width="100" align="center">
        <template #default="{ row }">
          <span style="display:inline-flex;align-items:center;gap:6px;justify-content:center;">
            <span :style="iconStyle(row.color, row.icon_type, 14)" />
            <span style="font-size:12px;">{{ row.icon_type }}</span>
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="device_count" label="设备数" width="80" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="row.device_count > 0 ? 'primary' : 'info'">
            {{ row.device_count ?? 0 }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="角色描述" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">{{ row.description || '—' }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="165" />
      <el-table-column label="操作" fixed="right" width="100" align="center">
        <template #default="{ row }">
          <el-button size="small" :icon="EditIcon" circle @click="openEdit(row)" />
          <el-button size="small" :icon="Delete" circle type="danger" @click="doDelete(row)" />
        </template>
      </el-table-column>
    </el-table>

    <!-- 新增 / 编辑角色弹窗 -->
    <el-dialog v-model="formVisible" :title="isEdit ? '编辑角色' : '添加角色'" width="440px">
      <el-form :model="form" label-width="80px" style="padding-right:16px;">
        <el-form-item label="角色名称" required>
          <el-input v-model="form.name" placeholder="如：xx一组" />
        </el-form-item>
        <el-form-item label="角色颜色">
          <div style="display:flex;align-items:center;gap:10px;">
            <el-color-picker v-model="form.color" show-alpha />
            <span style="font-size:12px;color:#909399;">{{ form.color }}</span>
          </div>
        </el-form-item>
        <el-form-item label="角色图标">
          <el-select v-model="form.icon_type" style="width:100%">
            <el-option v-for="opt in ICON_TYPES" :key="opt" :label="opt" :value="opt">
              <span style="display:flex;align-items:center;gap:8px;">
                <span :style="iconStyle(form.color || '#409EFF', opt)" />
                {{ opt }}
              </span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="角色描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="选填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" @click="submitForm" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- 分配角色弹窗 -->
    <el-dialog v-model="assignVisible" title="分配角色" width="560px" @open="onAssignOpen">
      <el-row :gutter="12" style="margin-bottom:12px;">
        <el-col :span="12">
          <el-select v-model="assignRoleId" placeholder="选择角色" style="width:100%"
            @change="syncAssignedDevices">
            <el-option v-for="r in list" :key="r.id"
              :label="r.name" :value="r.id">
              <span style="display:flex;align-items:center;gap:8px;">
                <span :style="iconStyle(r.color, r.icon_type, 10)" />
                {{ r.name }}
              </span>
            </el-option>
          </el-select>
        </el-col>
        <el-col :span="12">
          <el-input v-model="assignSearch" placeholder="搜索设备 IMEI/名称" clearable />
        </el-col>
      </el-row>
      <div style="font-size:12px;color:#909399;margin-bottom:6px;">
        已选 <b>{{ assignSelected.length }}</b> 台设备分配到该角色（取消勾选则从此角色移除）
      </div>
      <div style="max-height:340px;overflow-y:auto;border:1px solid #e4e7ed;border-radius:4px;">
        <el-table :data="filteredAssignDevices" size="small" height="340"
          @selection-change="handleAssignSelect" ref="assignTableRef">
          <el-table-column type="selection" width="45" />
          <el-table-column prop="phone" label="IMEI" min-width="150" />
          <el-table-column prop="name"  label="名称" width="120">
            <template #default="{ row }">{{ row.name || '—' }}</template>
          </el-table-column>
          <el-table-column label="当前角色" width="110">
            <template #default="{ row }">
              <el-tag v-if="row.role_name" size="small">{{ row.role_name }}</el-tag>
              <span v-else style="color:#ccc;font-size:11px;">未分配</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="assignVisible = false">取消</el-button>
        <el-button type="primary" @click="submitAssign" :loading="assignSaving"
          :disabled="!assignRoleId">确认分配</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { Plus, Grid, Edit as EditIcon, Delete } from '@element-plus/icons-vue'
import { roleApi, deviceApi } from '@/api'
import { ElMessage, ElMessageBox } from 'element-plus'

// ── 角色图标形状 ──────────────────────────────────────────────────────────────
const ICON_TYPES = ['圆形', '方形', '星形', '菱形']

// 根据颜色和形状生成小色块样式（供下拉/列表/分配处统一复用）
function iconStyle(color, type, size = 12) {
  const base = {
    display: 'inline-block',
    width: `${size}px`,
    height: `${size}px`,
    background: color || '#409EFF',
    flexShrink: 0,
  }
  if (type === '圆形') return { ...base, borderRadius: '50%' }
  if (type === '菱形') return { ...base, transform: 'rotate(45deg)', borderRadius: '2px' }
  if (type === '星形') {
    return {
      ...base,
      clipPath: 'polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%)',
    }
  }
  // 方形（默认）
  return { ...base, borderRadius: '2px' }
}

// ── 列表 ─────────────────────────────────────────────────────────────────────
const list    = ref([])
const loading = ref(false)

async function loadList() {
  loading.value = true
  try {
    const res = await roleApi.list()
    list.value = res.data?.records || []
  } finally {
    loading.value = false
  }
}

// ── 新增/编辑 ─────────────────────────────────────────────────────────────────
const formVisible = ref(false)
const saving      = ref(false)
const isEdit      = ref(false)
const form        = reactive({
  id: null, name: '', color: '#409EFF', icon_type: '圆形', description: ''
})

function openCreate() {
  isEdit.value = false
  Object.assign(form, { id: null, name: '', color: '#409EFF', icon_type: '圆形', description: '' })
  formVisible.value = true
}

function openEdit(row) {
  isEdit.value = true
  Object.assign(form, {
    id:          row.id,
    name:        row.name,
    color:       row.color       || '#409EFF',
    icon_type:   row.icon_type   || '圆形',
    description: row.description || '',
  })
  formVisible.value = true
}

async function submitForm() {
  if (!form.name.trim()) return ElMessage.warning('角色名称不能为空')
  saving.value = true
  try {
    const payload = { name: form.name, color: form.color, icon_type: form.icon_type, description: form.description }
    if (isEdit.value) {
      await roleApi.update(form.id, payload)
    } else {
      await roleApi.create(payload)
    }
    ElMessage.success(isEdit.value ? '保存成功' : '角色创建成功')
    formVisible.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function doDelete(row) {
  try {
    await ElMessageBox.confirm(
      `删除角色「${row.name}」后，该角色下所有设备将变为未分配。确认？`,
      '删除确认', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch { return }
  await roleApi.remove(row.id)
  ElMessage.success('已删除')
  loadList()
}

// ── 分配角色 ──────────────────────────────────────────────────────────────────
const assignVisible  = ref(false)
const assignSaving   = ref(false)
const assignRoleId   = ref(null)
const assignSearch   = ref('')
const assignSelected = ref([])
const assignDevices  = ref([])   // 全量设备列表（含当前 role 信息）
const assignTableRef = ref(null)

const filteredAssignDevices = computed(() => {
  if (!assignSearch.value) return assignDevices.value
  const q = assignSearch.value.toLowerCase()
  return assignDevices.value.filter(d =>
    (d.phone || '').includes(q) || (d.name || '').toLowerCase().includes(q)
  )
})

async function openAssign() {
  assignRoleId.value   = null
  assignSelected.value = []
  assignSearch.value   = ''
  assignVisible.value  = true
}

async function onAssignOpen() {
  try {
    const res = await deviceApi.withCustomer({ size: 500 })
    assignDevices.value = res.data?.records || []
  } catch {}
}

async function syncAssignedDevices() {
  // 勾选当前已在此角色下的设备
  await nextTick()
  if (!assignTableRef.value || !assignRoleId.value) return
  assignTableRef.value.clearSelection()
  const rid = assignRoleId.value
  filteredAssignDevices.value.forEach(row => {
    if (row.role_id === rid) {
      assignTableRef.value.toggleRowSelection(row, true)
    }
  })
}

function handleAssignSelect(rows) {
  assignSelected.value = rows
}

async function submitAssign() {
  if (!assignRoleId.value) return
  assignSaving.value = true
  try {
    const phones = assignSelected.value.map(r => r.phone)
    await roleApi.assignDevices(assignRoleId.value, phones)
    ElMessage.success(`已将 ${phones.length} 台设备分配到该角色`)
    assignVisible.value = false
    loadList()
  } finally {
    assignSaving.value = false
  }
}

onMounted(() => loadList())
</script>
