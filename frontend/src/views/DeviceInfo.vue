<template>
  <el-card>
    <!-- 顶部工具栏：账号 / 设备型号 / 设备IMEI / 角色 筛选 + 批量修改 + 导出 -->
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px;">
      <span style="font-size:14px;color:#606266;white-space:nowrap;">账号</span>
      <el-cascader
        v-model="queryCustomerPath"
        :options="customerTree"
        :props="cascaderProps"
        placeholder="请选择账号"
        clearable filterable
        style="width:220px;"
        @change="onCustomerChange" />

      <span style="font-size:14px;color:#606266;white-space:nowrap;margin-left:6px;">设备型号</span>
      <el-select v-model="queryModel" placeholder="请选择型号" filterable clearable
        style="width:160px;" @change="loadData(1)">
        <el-option v-for="m in modelOptions" :key="m" :label="m" :value="m" />
      </el-select>

      <span style="font-size:14px;color:#606266;white-space:nowrap;margin-left:6px;">设备IMEI</span>
      <el-input v-model="queryImei" placeholder="请输入设备IMEI" clearable style="width:200px;"
        @change="loadData(1)" @clear="loadData(1)">
        <template #append><el-button :icon="Search" @click="loadData(1)" /></template>
      </el-input>

      <span style="font-size:14px;color:#606266;white-space:nowrap;margin-left:6px;">角色</span>
      <el-select v-model="queryRoleId" placeholder="全部" clearable
        style="width:140px;" @change="loadData(1)">
        <el-option label="全部" value="" />
        <el-option label="未分配" value="none" />
        <el-option v-for="r in roleList" :key="r.id" :label="r.name" :value="r.id" />
      </el-select>

      <el-button v-if="isAdmin()" type="primary" @click="openBatchRole" style="margin-left:6px;">批量修改角色</el-button>
      <el-button type="success" :icon="Upload" @click="openImport">批量导入</el-button>
      <el-button v-if="isAdmin()" :icon="Download" @click="exportAll" :loading="exporting">导出</el-button>
    </div>

    <!-- 已选提示 -->
    <div v-if="selected.length"
      style="margin-bottom:12px;background:#f0f7ff;border:1px solid #d0e8ff;border-radius:6px;
             padding:8px 12px;font-size:13px;color:#409EFF;">
      已选 <b>{{ selected.length }}</b> 台设备
      <el-button size="small" text @click="clearSelection" style="margin-left:8px;">取消选择</el-button>
    </div>

    <el-table ref="tableRef" :data="list" v-loading="loading" stripe border size="small"
      @selection-change="onSelectionChange">
      <el-table-column type="selection" width="45" />
      <el-table-column type="index" label="#" width="50" />
      <el-table-column label="设备号" min-width="150">
        <template #default="{ row }"><span>{{ row.terminal_id || row.phone }}</span></template>
      </el-table-column>
      <el-table-column label="IMEI" min-width="150">
        <template #default="{ row }">{{ row.imei || row.phone }}</template>
      </el-table-column>
      <el-table-column prop="terminal_model" label="设备型号"   width="110" />
      <el-table-column label="设备围栏数" width="95" align="center">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ row.fence_count ?? 0 }}</el-tag>
        </template>
      </el-table-column>
      <!-- 角色名称：带颜色色块，点击可分配 -->
      <el-table-column label="角色名称" min-width="140">
        <template #default="{ row }">
          <el-button v-if="isAdmin()" link type="primary" style="padding:0;height:auto;" @click="openRole(row)">
            <div v-if="row.role_name" style="display:flex;align-items:center;gap:6px;">
              <span :style="roleIconStyle(row.role_color, row.icon_type)" />
              <span>{{ row.role_name }}</span>
            </div>
            <span v-else style="color:#909399;">未分配</span>
          </el-button>
          <div v-else>
            <div v-if="row.role_name" style="display:flex;align-items:center;gap:6px;">
              <span :style="roleIconStyle(row.role_color, row.icon_type)" />
              <span>{{ row.role_name }}</span>
            </div>
            <span v-else style="color:#909399;">未分配</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="头像" width="70" align="center">
        <template #default="{ row }">
          <el-avatar v-if="row.avatar" :size="36" :src="avatarSrc(row.avatar)" shape="square" />
          <span v-else style="color:#ccc;font-size:12px;">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="real_name"    label="姓名"       width="100" />
      <el-table-column prop="gender"       label="性别"       width="65" align="center">
        <template #default="{ row }">{{ row.gender || '—' }}</template>
      </el-table-column>
      <el-table-column prop="age"          label="年龄"       width="65" align="center">
        <template #default="{ row }">{{ row.age ?? '—' }}</template>
      </el-table-column>
      <el-table-column prop="contact_phone" label="联系方式"  min-width="130" />
      <el-table-column prop="address"      label="联系地址"   min-width="160" show-overflow-tooltip />
      <el-table-column label="备注" width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.customer_remark || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" fixed="right" width="70" align="center">
        <template #default="{ row }">
          <el-button size="small" :icon="EditIcon" circle title="编辑人员信息"
            @click="openEdit(row)" :disabled="!row.customer_id" />
        </template>
      </el-table-column>
    </el-table>

    <el-pagination style="margin-top:16px;justify-content:flex-end;display:flex;"
      :current-page="page" :page-size="pageSize" :total="total"
      layout="total,prev,pager,next" @current-change="loadData" />

    <!-- 分配角色弹窗（单台 + 批量共用） -->
    <el-dialog v-model="roleVisible" :title="roleBatch ? '批量修改角色' : '分配角色'" width="420px">
      <div style="margin-bottom:12px;font-size:13px;color:#606266;">
        <template v-if="roleBatch">为选中的 <b>{{ selected.length }}</b> 台设备设置角色：</template>
        <template v-else>为设备 <b>{{ roleTarget?.phone }}</b> 设置角色：</template>
      </div>
      <el-select v-model="roleChoice" placeholder="请选择角色" style="width:100%" clearable filterable>
        <el-option label="（清除角色）" :value="null" />
        <el-option v-for="r in roleList" :key="r.id" :value="r.id" :label="r.name">
          <span style="display:flex;align-items:center;gap:6px;">
            <span :style="roleIconStyle(r.color, r.icon_type)" />
            <span>{{ r.name }}</span>
          </span>
        </el-option>
      </el-select>
      <template #footer>
        <el-button @click="roleVisible = false">取消</el-button>
        <el-button type="primary" @click="submitRole" :loading="roleSaving">确定</el-button>
      </template>
    </el-dialog>

    <!-- 编辑人员信息弹窗 -->
    <el-dialog v-model="editVisible" title="编辑人员信息" width="480px">
      <el-form :model="editForm" label-width="80px" style="padding-right:20px;">
        <el-form-item label="头像">
          <el-upload
            :action="UPLOAD_AVATAR_URL"
            :headers="uploadHeaders()"
            :show-file-list="false"
            accept="image/*"
            :before-upload="beforeAvatarUpload"
            :on-success="onAvatarSuccess"
            :on-error="onAvatarError">
            <el-avatar v-if="editForm.avatar" :size="72" :src="avatarSrc(editForm.avatar)" shape="square" />
            <div v-else class="avatar-uploader-empty">
              <el-icon><Plus /></el-icon>
              <span style="font-size:12px;margin-top:4px;">上传头像</span>
            </div>
          </el-upload>
          <el-button v-if="editForm.avatar" link type="danger" size="small"
            style="margin-left:10px;" @click="editForm.avatar = ''">移除</el-button>
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="editForm.contact" placeholder="联系人真实姓名" />
        </el-form-item>
        <el-form-item label="性别">
          <el-select v-model="editForm.gender" placeholder="请选择" style="width:100%">
            <el-option label="男" value="男" />
            <el-option label="女" value="女" />
            <el-option label="未知" value="" />
          </el-select>
        </el-form-item>
        <el-form-item label="年龄">
          <el-input-number v-model="editForm.age" :min="1" :max="120" :controls="false"
            style="width:100%" placeholder="选填" />
        </el-form-item>
        <el-form-item label="联系方式">
          <el-input v-model="editForm.phone" placeholder="手机号" />
        </el-form-item>
        <el-form-item label="联系地址">
          <el-input v-model="editForm.address" type="textarea" :rows="2" placeholder="省市区镇详细地址" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="editForm.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="submitEdit" :loading="saving">保存</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入弹窗 -->
    <el-dialog v-model="importVisible" title="批量导入设备" width="720px" @closed="resetImport">
      <div style="margin-bottom:12px;">
        <el-button :icon="Download" @click="downloadTemplate">下载模版</el-button>
        <el-upload
          style="display:inline-block;margin-left:10px;"
          :show-file-list="false"
          :auto-upload="false"
          accept=".xlsx,.xls"
          :on-change="onImportFileChange">
          <el-button type="primary" :icon="Upload">选择 Excel 文件</el-button>
        </el-upload>
        <span v-if="importFileName" style="margin-left:10px;color:#606266;font-size:13px;">
          已选：{{ importFileName }}（解析 {{ importRows.length }} 行）
        </span>
      </div>

      <el-alert type="info" :closable="false" style="margin-bottom:12px;"
        title="模版说明：设备号与 IMEI 至少填一个；填了姓名/性别/年龄/联系方式/联系地址的行，会自动为该设备建立人员档案并绑定。已存在的设备号/IMEI 将跳过。"
        show-icon />

      <el-table v-if="importRows.length" :data="importRows.slice(0, 200)" height="320" border size="small" stripe>
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="deviceNo"     label="设备号"    min-width="130" show-overflow-tooltip />
        <el-table-column prop="imei"         label="IMEI"      min-width="130" show-overflow-tooltip />
        <el-table-column prop="terminalModel" label="设备型号" width="90" />
        <el-table-column prop="contact"      label="姓名"      width="80" />
        <el-table-column prop="gender"       label="性别"      width="55" />
        <el-table-column prop="age"          label="年龄"      width="55" />
        <el-table-column prop="contactPhone" label="联系方式"  min-width="115" />
        <el-table-column prop="address"      label="联系地址"  min-width="130" show-overflow-tooltip />
        <el-table-column prop="remark"       label="备注"      min-width="90" show-overflow-tooltip />
      </el-table>
      <el-empty v-else description="请选择 Excel 文件预览" :image-size="70" />
      <div v-if="importRows.length > 200" style="color:#909399;font-size:12px;margin-top:6px;">
        仅预览前 200 行，提交时按全部 {{ importRows.length }} 行导入。
      </div>

      <!-- 导入结果 -->
      <el-alert v-if="importResult" :type="importResult.failed ? 'warning' : 'success'"
        :closable="false" style="margin-top:12px;"
        :title="`导入完成：新增 ${importResult.created} 台，跳过 ${importResult.skipped} 台，失败 ${importResult.failed} 台`"
        show-icon />

      <template #footer>
        <el-button @click="importVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!importRows.length" :loading="importing"
          @click="submitImport">开始导入 {{ importRows.length ? '(' + importRows.length + ')' : '' }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Search, Edit as EditIcon, Download, Plus, Upload } from '@element-plus/icons-vue'
import { deviceApi, portalApi, isAdmin, customerApi, roleApi, UPLOAD_AVATAR_URL, uploadHeaders } from '@/api'
import { ElMessage } from 'element-plus'
import * as XLSX from 'xlsx'

// 头像相对路径 → 完整可访问地址（后端返回 /uploads/xxx）
function avatarSrc(url) {
  if (!url) return ''
  return /^https?:\/\//.test(url) ? url : (window.location.origin + url)
}

// 角色图标色块样式（圆形/方形/星形/菱形）
function roleIconStyle(color, type, size = 12) {
  const base = {
    display: 'inline-block', width: `${size}px`, height: `${size}px`,
    background: color || '#409EFF', flexShrink: 0,
  }
  if (type === '圆形') return { ...base, borderRadius: '50%' }
  if (type === '菱形') return { ...base, transform: 'rotate(45deg)', borderRadius: '2px' }
  if (type === '星形') return {
    ...base,
    clipPath: 'polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%)',
  }
  return { ...base, borderRadius: '2px' }
}

const list      = ref([])
const loading   = ref(false)
const page      = ref(1)
const pageSize  = ref(20)
const total     = ref(0)
const exporting = ref(false)

// 查询条件
const queryCustomerId   = ref(null)   // 选中的账号 id（级联最末选中项）
const queryCustomerPath = ref([])     // 级联选中的路径
const queryModel        = ref(null)
const queryImei         = ref('')
const queryRoleId       = ref('')
const modelOptions      = ref([])
const customerList      = ref([])      // 平铺客户列表（编辑等复用）
const customerTree      = ref([])      // 树形客户（级联用）
const roleList          = ref([])

// 级联配置：可选任意层级、非叶子也可选、单选
const cascaderProps = {
  value: 'id',
  label: 'label',
  children: 'children',
  checkStrictly: true,
  emitPath: true,
}

// 选中账号：取路径最末一级作为查询 id
function onCustomerChange(path) {
  queryCustomerId.value = (path && path.length) ? path[path.length - 1] : null
  loadData(1)
}

// 平铺客户列表 → 树形结构
function buildCustomerTree(flat) {
  const map = {}
  flat.forEach(c => {
    map[c.id] = {
      id: c.id,
      label: `${c.name}${c.login_name ? ' (' + c.login_name + ')' : ''}`,
      children: [],
    }
  })
  const roots = []
  flat.forEach(c => {
    const node = map[c.id]
    const pid = c.parent_id
    if (pid && map[pid]) map[pid].children.push(node)
    else roots.push(node)
  })
  // 去掉空 children，避免叶子节点出现空展开箭头
  const prune = (nodes) => nodes.forEach(n => {
    if (n.children.length) prune(n.children)
    else delete n.children
  })
  prune(roots)
  return roots
}

// 多选
const tableRef = ref(null)
const selected = ref([])
function onSelectionChange(rows) { selected.value = rows }
function clearSelection() {
  tableRef.value?.clearSelection()
  selected.value = []
}

// 角色分配弹窗
const roleVisible = ref(false)
const roleSaving  = ref(false)
const roleBatch   = ref(false)
const roleTarget  = ref(null)
const roleChoice  = ref(null)

// 编辑人员信息弹窗
const editVisible = ref(false)
const saving      = ref(false)
const editForm    = reactive({
  customerId: null, devicePhone: '', contact: '', gender: '', age: null, phone: '', address: '', remark: '', avatar: ''
})

// 批量导入弹窗
const importVisible  = ref(false)
const importing      = ref(false)
const importRows     = ref([])
const importFileName = ref('')
const importResult   = ref(null)

// 导入模版列：中文表头 → 内部字段名
const IMPORT_COLS = [
  { header: '设备号',   key: 'deviceNo' },
  { header: 'IMEI',     key: 'imei' },
  { header: '设备型号', key: 'terminalModel' },
  { header: '姓名',     key: 'contact' },
  { header: '性别',     key: 'gender' },
  { header: '年龄',     key: 'age' },
  { header: '联系方式', key: 'contactPhone' },
  { header: '联系地址', key: 'address' },
  { header: '备注',     key: 'remark' },
]

async function loadData(p = page.value) {
  loading.value = true
  page.value = p
  try {
    // 按身份分流：管理员走管理端(可按客户筛选)，客户走门户接口(只返回自己名下设备)
    const res = isAdmin()
      ? await deviceApi.withCustomer({
          page: p, size: pageSize.value,
          customer_id:    queryCustomerId.value ?? undefined,
          terminal_model: queryModel.value || undefined,
          imei:           queryImei.value.trim() || undefined,
          role_id:        queryRoleId.value || undefined,
        })
      : await portalApi.deviceList({
          page: p, size: pageSize.value,
          customer_id:    queryCustomerId.value ?? undefined,
          terminal_model: queryModel.value || undefined,
          imei:           queryImei.value.trim() || undefined,
        })
    list.value  = res.data?.records || []
    total.value = res.data?.total   || 0
  } finally {
    loading.value = false
  }
}

async function loadCustomers() {
  if (customerList.value.length) return
  try {
    const res = isAdmin() ? await customerApi.listAll() : await portalApi.subCustomers.list({ size: 500 })
    customerList.value = res.data?.records || res.data || []
    customerTree.value = buildCustomerTree(customerList.value)
  } catch {}
}

async function loadRoles() {
  try {
    const res = isAdmin() ? await roleApi.list() : await portalApi.roles.list()
    roleList.value = res.data?.records || res.data || []
  } catch {}
}

async function loadModelOptions() {
  try {
    const res = isAdmin()
      ? await deviceApi.exportAll()
      : await portalApi.deviceList({ page: 1, size: 1000 })
    const recs = res.data?.records || res.data || []
    const models = recs.map(r => r.terminal_model).filter(Boolean)
    modelOptions.value = [...new Set(models)].sort()
  } catch {}
}

// ── 分配角色（单台） ──
function openRole(row) {
  roleBatch.value  = false
  roleTarget.value = row
  roleChoice.value = row.role_id || null
  roleVisible.value = true
}

// ── 批量修改角色 ──
function openBatchRole() {
  if (!selected.value.length) { ElMessage.warning('请先勾选设备'); return }
  roleBatch.value  = true
  roleChoice.value = null
  roleVisible.value = true
}

async function submitRole() {
  roleSaving.value = true
  try {
    if (roleBatch.value) {
      const ids = selected.value.map(d => d.id)
      await deviceApi.batchRole(ids, roleChoice.value)
    } else {
      await deviceApi.setRole(roleTarget.value.id, roleChoice.value)
    }
    ElMessage.success('角色已更新')
    roleVisible.value = false
    clearSelection()
    loadData()
  } finally {
    roleSaving.value = false
  }
}

// ── 编辑人员信息 ──
function openEdit(row) {
  if (!row.customer_id) return
  Object.assign(editForm, {
    customerId: row.customer_id,
    devicePhone: row.phone       || '',
    contact: row.real_name       || '',
    gender:  row.gender          || '',
    age:     row.age             || null,
    phone:   row.contact_phone   || '',
    address: row.address         || '',
    remark:  row.customer_remark || '',
    avatar:  row.avatar          || '',
  })
  editVisible.value = true
}

async function submitEdit() {
  saving.value = true
  try {
    const payload = {
      contact: editForm.contact, gender: editForm.gender, age: editForm.age,
      phone: editForm.phone, address: editForm.address, remark: editForm.remark,
      avatar: editForm.avatar,
    }
    // 管理员按客户id改；客户按设备phone走门户专属接口(只能改自己名下设备的绑定人)
    if (isAdmin()) {
      await customerApi.update(editForm.customerId, payload)
    } else {
      await portalApi.updateDeviceHolder(editForm.devicePhone, payload)
    }
    ElMessage.success('保存成功')
    editVisible.value = false
    loadData()
  } finally {
    saving.value = false
  }
}

// ── 头像上传回调 ──
function beforeAvatarUpload(file) {
  const isImg = file.type.startsWith('image/')
  const okSize = file.size / 1024 / 1024 < 2
  if (!isImg) { ElMessage.error('只能上传图片'); return false }
  if (!okSize) { ElMessage.error('图片不能超过 2MB'); return false }
  return true
}
function onAvatarSuccess(res) {
  if (res?.code === 200 && res.data?.url) {
    editForm.avatar = res.data.url
    ElMessage.success('头像已上传')
  } else {
    ElMessage.error(res?.msg || '上传失败')
  }
}
function onAvatarError() {
  ElMessage.error('上传失败，请重试')
}

// ── 导出 ──
async function exportAll() {
  exporting.value = true
  try {
    const res = await deviceApi.exportAll()
    const rows = res.data?.records || []
    if (!rows.length) { ElMessage.warning('暂无设备可导出'); return }
    const statusMap = { 0: '离线', 1: '在线', 2: '报警' }
    const headers = ['设备IMEI', '设备名称', '设备型号', '在线状态', '归属账号', '姓名', '联系方式', '角色']
    const data = rows.map(r => [
      r.phone, r.name || '', r.terminal_model || '', statusMap[r.status] ?? '',
      r.account || '', r.real_name || '', r.contact_phone || '', r.role_name || ''
    ])
    // 防 CSV 公式注入：以 = + - @ Tab CR 开头的值前置单引号，避免 Excel 当公式执行
    const csvCell = (c) => {
      let s = String(c)
      if (/^[=+\-@\t\r]/.test(s)) s = "'" + s
      return `"${s.replace(/"/g, '""')}"`
    }
    const csv = [headers, ...data].map(row =>
      row.map(csvCell).join(',')).join('\n')
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `设备信息_${new Date().toISOString().slice(0, 10)}.csv`
    link.click()
    URL.revokeObjectURL(link.href)
    ElMessage.success(`已导出 ${rows.length} 台设备`)
  } finally {
    exporting.value = false
  }
}

// ── 批量导入 ──
function openImport() {
  importResult.value = null
  importRows.value = []
  importFileName.value = ''
  importVisible.value = true
}

function resetImport() {
  importRows.value = []
  importFileName.value = ''
  importResult.value = null
}

// 下载 Excel 模版（带表头 + 一行示例）
function downloadTemplate() {
  const headers = IMPORT_COLS.map(c => c.header)
  const sample = ['866000000000001', '860000000000001', 'G618G', '张三', '男', 30, '13800138000', '广东省深圳市南山区', '示例行，可删除']
  const ws = XLSX.utils.aoa_to_sheet([headers, sample])
  ws['!cols'] = headers.map(() => ({ wch: 16 }))
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '设备导入')
  XLSX.writeFile(wb, `设备导入模版_${new Date().toISOString().slice(0, 10)}.xlsx`)
}

// 选择文件后解析为 importRows
function onImportFileChange(file) {
  const raw = file.raw || file
  importResult.value = null
  const reader = new FileReader()
  reader.onload = (e) => {
    try {
      const wb = XLSX.read(e.target.result, { type: 'array' })
      const ws = wb.Sheets[wb.SheetNames[0]]
      const aoa = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '' })
      if (!aoa.length) { ElMessage.warning('文件内容为空'); return }
      // 第一行为表头，建立「中文表头 → 列下标」映射
      const headerRow = aoa[0].map(h => String(h).trim())
      const idxOf = (h) => headerRow.indexOf(h)
      const rows = []
      for (let i = 1; i < aoa.length; i++) {
        const r = aoa[i]
        if (!r || r.every(c => String(c).trim() === '')) continue   // 跳过空行
        const obj = {}
        IMPORT_COLS.forEach(col => {
          const ci = idxOf(col.header)
          obj[col.key] = ci >= 0 ? String(r[ci] ?? '').trim() : ''
        })
        rows.push(obj)
      }
      if (!rows.length) { ElMessage.warning('没有可导入的数据行'); return }
      importRows.value = rows
      importFileName.value = file.name || '已选文件'
      ElMessage.success(`解析成功，共 ${rows.length} 行`)
    } catch (err) {
      ElMessage.error('文件解析失败，请确认是标准 Excel 文件')
    }
  }
  reader.readAsArrayBuffer(raw)
}

// 提交导入
async function submitImport() {
  if (!importRows.value.length) { ElMessage.warning('请先选择并解析文件'); return }
  importing.value = true
  try {
    const res = isAdmin() ? await deviceApi.batchImport(importRows.value)
                          : await portalApi.batchImport(importRows.value)
    importResult.value = res.data || res
    const rst = importResult.value
    if (rst.failed) {
      ElMessage.warning(`导入完成，有 ${rst.failed} 台失败`)
    } else {
      ElMessage.success(`导入完成，新增 ${rst.created} 台`)
    }
    loadData(1)
    loadModelOptions()
  } catch (e) {
    ElMessage.error('导入失败，请重试')
  } finally {
    importing.value = false
  }
}

onMounted(() => {
  loadData(1)
  loadCustomers()
  loadRoles()
  loadModelOptions()
})
</script>

<style scoped>
.avatar-uploader-empty {
  width: 72px;
  height: 72px;
  border: 1px dashed #d9d9d9;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #8c939d;
  cursor: pointer;
  transition: border-color .2s;
}
.avatar-uploader-empty:hover {
  border-color: #409eff;
  color: #409eff;
}
</style>
