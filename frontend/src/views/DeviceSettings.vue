<template>
  <el-card>
    <!-- 搜索栏：账号 / 设备型号 / 设备IMEI（选择即查询，无需按钮） -->
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
        style="width:180px;" @change="loadData(1)">
        <el-option v-for="m in modelOptions" :key="m" :label="m" :value="m" />
      </el-select>

      <span style="font-size:14px;color:#606266;white-space:nowrap;margin-left:6px;">设备IMEI</span>
      <el-input v-model="queryImei" placeholder="请输入设备IMEI" clearable
        style="width:220px;" @change="loadData(1)" @clear="loadData(1)">
        <template #append>
          <el-button :icon="Search" @click="loadData(1)" />
        </template>
      </el-input>

      <el-dropdown @command="onBatchCommand" trigger="click" style="margin-left:6px;">
        <el-button type="warning">
          批量操作<el-icon class="el-icon--right"><ArrowDown /></el-icon>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="bind">批量绑定</el-dropdown-item>
            <el-dropdown-item command="unbind">批量解绑</el-dropdown-item>
            <el-dropdown-item command="transfer">转移设备</el-dropdown-item>
            <el-dropdown-item command="cmd">批量下发指令</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>

      <el-button :icon="Upload" @click="openImport">批量导入</el-button>
      <el-button :icon="Download" @click="exportAll" :loading="exporting">导出</el-button>

      <div style="flex:1;"></div>
      <el-select v-model="bindFilter" placeholder="绑定状态" clearable
        @change="loadData(1)" style="width:120px;">
        <el-option label="已绑定" value="bound" />
        <el-option label="未绑定" value="unbound" />
      </el-select>
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
      <el-table-column prop="name"           label="设备名称"   width="120" />
      <el-table-column label="设备号" width="160">
        <template #default="{ row }"><span>{{ row.terminal_id || row.phone }}</span></template>
      </el-table-column>
      <el-table-column label="IMEI" width="160">
        <template #default="{ row }">{{ row.imei || row.phone }}</template>
      </el-table-column>
      <el-table-column prop="account"        label="归属账号"   width="130">
        <template #default="{ row }">
          <el-tag v-if="row.account" size="small" type="success">{{ row.account }}</el-tag>
          <span v-else style="color:#ccc;font-size:12px;">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="terminal_model" label="设备型号"   width="110" />
      <el-table-column label="状态" width="90" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="row.customer_id ? 'success' : 'info'">
            {{ row.customer_id ? '已绑定' : '未绑定' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_location_time" label="最后通信" width="165">
        <template #default="{ row }">{{ row.last_location_time || '未通信' }}</template>
      </el-table-column>
      <el-table-column prop="activated_at" label="激活时间" width="165">
        <template #default="{ row }">{{ row.activated_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" fixed="right" width="120" align="center">
        <template #default="{ row }">
          <el-button v-if="row.customer_id"
            size="small" type="danger" plain
            :icon="Minus" @click="doUnbind(row)">解绑</el-button>
          <el-button v-else
            size="small" type="primary" plain
            :icon="Link" @click="openBind(row)">绑定</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination style="margin-top:16px;justify-content:flex-end;display:flex;"
      :current-page="page" :page-size="pageSize" :total="total"
      layout="total,prev,pager,next" @current-change="loadData" />

    <!-- 绑定 / 转移客户弹窗（单个 + 批量共用） -->
    <el-dialog v-model="bindVisible" :title="bindTitle" width="460px">
      <div style="margin-bottom:12px;font-size:13px;color:#606266;">
        <template v-if="bindMode === 'single'">
          设备 <b>{{ bindTarget?.phone }}</b> 绑定至：
        </template>
        <template v-else>
          将选中的 <b>{{ selected.length }}</b> 台设备{{ bindMode === 'transfer' ? '转移' : '绑定' }}至：
        </template>
      </div>
      <el-select v-model="bindCustomerId" placeholder="请选择客户账号" style="width:100%"
        filterable clearable>
        <el-option
          v-for="c in customerList"
          :key="c.id"
          :label="`${c.name}${c.login_name ? ' (' + c.login_name + ')' : ''}`"
          :value="c.id"
        />
      </el-select>
      <template #footer>
        <el-button @click="bindVisible = false">取消</el-button>
        <el-button type="primary" @click="doBindConfirm" :loading="bindSaving"
          :disabled="!bindCustomerId">确认</el-button>
      </template>
    </el-dialog>

    <!-- 批量下发指令弹窗 -->
    <el-dialog v-model="cmdVisible" title="批量下发指令" width="440px">
      <div style="margin-bottom:10px;font-size:13px;color:#606266;">
        向选中的 <b>{{ selected.length }}</b> 台设备下发文本指令（仅在线设备生效）：
      </div>
      <el-input v-model="cmdText" type="textarea" :rows="3" placeholder="输入指令内容" />
      <template #footer>
        <el-button @click="cmdVisible = false">取消</el-button>
        <el-button type="primary" @click="doBatchCommand" :loading="cmdSaving"
          :disabled="!cmdText.trim()">发送</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入弹窗 -->
    <el-dialog v-model="importVisible" title="批量导入设备" width="560px">
      <div style="font-size:13px;color:#606266;line-height:1.9;margin-bottom:12px;">
        <p style="margin:0 0 6px;">1. 下载模板,按列填写(<b>设备号与 IMEI 至少填一个</b>,两号相同的设备两列填一样即可,其余选填)。</p>
        <p style="margin:0 0 6px;">2. 支持 <b>.xlsx</b> 和 <b>.csv</b> 格式。已存在的设备号会自动跳过。</p>
        <el-button size="small" :icon="Download" @click="downloadTemplate">下载导入模板</el-button>
      </div>
      <el-upload drag :auto-upload="false" :show-file-list="false" accept=".xlsx,.csv"
        :on-change="onFilePicked">
        <el-icon class="el-icon--upload"><Upload /></el-icon>
        <div class="el-upload__text">把文件拖到这里,或<em>点击选择文件</em></div>
      </el-upload>
      <div v-if="importPreview.length" style="margin-top:12px;font-size:13px;">
        已解析 <b>{{ importPreview.length }}</b> 条记录,点「开始导入」提交。
      </div>
      <div v-if="importResult" style="margin-top:12px;">
        <el-alert :closable="false"
          :type="importResult.failed ? 'warning' : 'success'"
          :title="`导入完成:成功 ${importResult.created} 台,跳过 ${importResult.skipped} 台,失败 ${importResult.failed} 台`" />
        <el-table v-if="importResultRows.length" :data="importResultRows" size="small" border
          max-height="220" style="margin-top:10px;">
          <el-table-column prop="row" label="行" width="60" />
          <el-table-column prop="phone" label="设备号/IMEI" min-width="140" />
          <el-table-column prop="statusText" label="结果" width="80" />
          <el-table-column prop="reason" label="说明" min-width="120" />
        </el-table>
      </div>
      <template #footer>
        <el-button @click="importVisible = false">关闭</el-button>
        <el-button type="primary" :disabled="!importPreview.length" :loading="importing"
          @click="submitImport">开始导入</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Search, Minus, Link, ArrowDown, Download, Upload } from '@element-plus/icons-vue'
import * as XLSX from 'xlsx' 
import { deviceApi, customerApi, portalApi, isAdmin } from '@/api'
import { ElMessage, ElMessageBox } from 'element-plus'

const list        = ref([])
const loading     = ref(false)
const page        = ref(1)
const pageSize    = ref(20)
const total       = ref(0)
const bindFilter  = ref(null)
const exporting   = ref(false)

// ── 批量导入 ──────────────────────────────────────────────
const importVisible = ref(false)
const importing     = ref(false)
const importPreview = ref([])
const importResult  = ref(null)
const importResultRows = ref([])
const HEADER_MAP = {
  '设备号': 'deviceNo', 'deviceNo': 'deviceNo', 'terminalId': 'deviceNo',
  'IMEI': 'imei', 'imei': 'imei',
  'IMEI/设备号': 'deviceNo', 'phone': 'deviceNo',
  '名称': 'name', 'name': 'name',
  '位置/车牌': 'plateNo', '位置': 'plateNo', '车牌': 'plateNo', 'plateNo': 'plateNo',
  '型号': 'terminalModel', 'terminalModel': 'terminalModel',
  '备注': 'remark', 'remark': 'remark',
}
function openImport() {
  importPreview.value = []
  importResult.value  = null
  importResultRows.value = []
  importVisible.value = true
}
function downloadTemplate() {
  const ws = XLSX.utils.aoa_to_sheet([
    ['设备号', 'IMEI', '名称', '位置/车牌', '型号', '备注'],
    ['11526090071', '864924089464826', '示例设备A', '沪A12345', 'LT115', '设备号与IMEI至少填一个'],
    ['867940074800516', '867940074800516', '示例设备B', '', 'G618G', '两号相同也可'],
  ])
  ws['!cols'] = [{ wch: 18 }, { wch: 18 }, { wch: 14 }, { wch: 14 }, { wch: 12 }, { wch: 22 }]
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '设备导入')
  XLSX.writeFile(wb, '设备批量导入模板.xlsx')
}
function onFilePicked(file) {
  importResult.value = null
  importResultRows.value = []
  const reader = new FileReader()
  reader.onload = (e) => {
    try {
      const wb = XLSX.read(e.target.result, { type: 'array' })
      const ws = wb.Sheets[wb.SheetNames[0]]
      const raw = XLSX.utils.sheet_to_json(ws, { defval: '' })
      const rows = []
      for (const r of raw) {
        const obj = {}
        for (const k in r) {
          const field = HEADER_MAP[String(k).trim()]
          if (field) obj[field] = String(r[k]).trim()
        }
        if (obj.deviceNo || obj.imei) rows.push(obj)
      }
      if (!rows.length) {
        ElMessage.warning('未解析到有效数据,请确认表头含「设备号」或「IMEI」且有内容')
        importPreview.value = []
        return
      }
      importPreview.value = rows
      ElMessage.success(`已解析 ${rows.length} 条记录`)
    } catch (err) {
      ElMessage.error('文件解析失败,请确认格式为 .xlsx 或 .csv')
      importPreview.value = []
    }
  }
  reader.readAsArrayBuffer(file.raw)
}
async function submitImport() {
  if (!importPreview.value.length) return
  importing.value = true
  try {
    const res = isAdmin() ? await deviceApi.batchImport(importPreview.value) : await portalApi.batchImport(importPreview.value)
    importResult.value = res.data
    const STAT = { created: '成功', skipped: '跳过', failed: '失败' }
    importResultRows.value = (res.data.details || []).map(d => ({
      ...d, statusText: STAT[d.status] || d.status,
    }))
    importPreview.value = []
    loadData(1)
  } catch {} finally {
    importing.value = false
  }
}

// 三种查询条件
const queryCustomerId   = ref(null)   // ① 账户（含子账户）
const queryCustomerPath = ref([])     // 级联选中路径
const queryModel        = ref(null)   // ② 设备型号
const queryImei         = ref('')     // ③ IMEI 号
const modelOptions      = ref([])     // 型号下拉选项
const customerTree      = ref([])      // 树形客户（级联用）

// 级联配置：可选任意层级、非叶子也可选
const cascaderProps = {
  value: 'id', label: 'label', children: 'children',
  checkStrictly: true, emitPath: true,
}
function onCustomerChange(path) {
  queryCustomerId.value = (path && path.length) ? path[path.length - 1] : null
  loadData(1)
}
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
    const node = map[c.id], pid = c.parent_id
    if (pid && map[pid]) map[pid].children.push(node)
    else roots.push(node)
  })
  const prune = (nodes) => nodes.forEach(n => {
    if (n.children.length) prune(n.children)
    else delete n.children
  })
  prune(roots)
  return roots
}

const tableRef  = ref(null)
const selected  = ref([])

const bindVisible    = ref(false)
const bindSaving     = ref(false)
const bindTarget     = ref(null)
const bindCustomerId = ref(null)
const bindMode       = ref('single')   // single | batch | transfer
const customerList   = ref([])

const cmdVisible = ref(false)
const cmdSaving  = ref(false)
const cmdText    = ref('')

const bindTitle = computed(() => {
  if (bindMode.value === 'transfer') return '转移设备'
  if (bindMode.value === 'batch')    return '批量绑定'
  return '绑定设备至客户'
})

async function loadData(p = page.value) {
  loading.value = true
  page.value = p
  try {
    const params = {
      page: p, size: pageSize.value,
      customer_id:    queryCustomerId.value ?? undefined,
      terminal_model: queryModel.value || undefined,
      imei:           queryImei.value.trim() || undefined,
    }
    const res = isAdmin() ? await deviceApi.withCustomer(params) : await portalApi.deviceList(params)
    let records = res.data?.records || []
    if (bindFilter.value === 'bound')   records = records.filter(r => r.customer_id)
    if (bindFilter.value === 'unbound') records = records.filter(r => !r.customer_id)
    list.value  = records
    total.value = res.data?.total || 0
  } finally {
    loading.value = false
  }
}

async function loadCustomers() {
  if (customerList.value.length) return
  try {
    const res = isAdmin() ? await customerApi.listAll() : await portalApi.subCustomers.list({ size: 500 })
    customerList.value = res.data?.records || []
    customerTree.value = buildCustomerTree(customerList.value)
  } catch {}
}

// 从后端导出接口提取全部型号（去重），作为型号下拉选项
async function loadModelOptions() {
  try {
    const res = isAdmin() ? await deviceApi.exportAll() : await portalApi.deviceList({ size: 500 })
    const models = (res.data?.records || [])
      .map(r => r.terminal_model)
      .filter(Boolean)
    modelOptions.value = [...new Set(models)].sort()
  } catch {}
}

// ── 多选 ──
function onSelectionChange(rows) { selected.value = rows }
function clearSelection() {
  tableRef.value?.clearSelection()
  selected.value = []
}

// ── 单个绑定 / 解绑 ──
async function doUnbind(row) {
  try {
    await ElMessageBox.confirm(
      `确认解除设备 ${row.phone} 与账号 ${row.account || ''} 的绑定？`,
      '解绑确认', { type: 'warning', confirmButtonText: '解绑', cancelButtonText: '取消' }
    )
  } catch { return }
  if (isAdmin()) {
    await deviceApi.unbindCustomer(row.id)
  } else {
    // 客户端:把设备从所属子账号收回自己名下 —— 取该子账号现有设备,剔除本台后全量重提交
    const sid = row.customer_id
    const cur = await portalApi.subCustomers.getDevices(sid)
    const rows = cur.data?.records || cur.data || []
    const keep = rows.filter(x => x.customer_id === sid && x.phone !== row.phone).map(x => x.phone)
    await portalApi.subCustomers.assignDevices(sid, keep)
  }
  ElMessage.success('解绑成功')
  loadData()
}

async function openBind(row) {
  bindMode.value       = 'single'
  bindTarget.value     = row
  bindCustomerId.value = null
  bindVisible.value    = true
  await loadCustomers()
}

async function doBindConfirm() {
  if (!bindCustomerId.value) return
  bindSaving.value = true
  try {
    if (isAdmin()) {
      if (bindMode.value === 'single') {
        await deviceApi.bindCustomer(bindTarget.value.id, bindCustomerId.value)
      } else {
        const ids = selected.value.map(d => d.id)
        await deviceApi.batchBind(ids, bindCustomerId.value)
      }
    } else {
      // 客户端:分配给下级子账号。把选中设备的 phone 追加到该子账号(全量语义由后端处理)
      const phones = (bindMode.value === 'single' ? [bindTarget.value] : selected.value).map(x => x.phone)
      // 先取该子账号当前已分配设备,合并后提交(避免覆盖)
      const cur = await portalApi.subCustomers.getDevices(bindCustomerId.value)
      const curPhones = (cur.data || cur.data?.records || []).filter(x => x.customer_id === bindCustomerId.value).map(x => x.phone)
      const merged = [...new Set([...curPhones, ...phones])]
      await portalApi.subCustomers.assignDevices(bindCustomerId.value, merged)
    }
    ElMessage.success('操作成功')
    bindVisible.value = false
    clearSelection()
    loadData()
  } finally {
    bindSaving.value = false
  }
}

// ── 批量操作分发 ──
async function onBatchCommand(cmd) {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选设备')
    return
  }
  if (cmd === 'bind' || cmd === 'transfer') {
    bindMode.value       = cmd === 'transfer' ? 'transfer' : 'batch'
    bindCustomerId.value = null
    bindVisible.value    = true
    await loadCustomers()
  } else if (cmd === 'unbind') {
    doBatchUnbind()
  } else if (cmd === 'cmd') {
    cmdText.value    = ''
    cmdVisible.value = true
  }
}

async function doBatchUnbind() {
  try {
    await ElMessageBox.confirm(
      `确认批量解绑选中的 ${selected.value.length} 台设备？`,
      '批量解绑', { type: 'warning', confirmButtonText: '解绑', cancelButtonText: '取消' }
    )
  } catch { return }
  if (isAdmin()) {
    const ids = selected.value.map(d => d.id)
    await deviceApi.batchUnbind(ids)
    ElMessage.success(`已解绑 ${ids.length} 台设备`)
  } else {
    // 客户端:按所属子账号分组,逐个子账号剔除选中设备后重提交
    const bySid = {}
    selected.value.forEach(x => { if (x.customer_id) { (bySid[x.customer_id] ||= new Set()).add(x.phone) } })
    for (const sid of Object.keys(bySid)) {
      const cur = await portalApi.subCustomers.getDevices(Number(sid))
      const rows = cur.data?.records || cur.data || []
      const keep = rows.filter(x => x.customer_id === Number(sid) && !bySid[sid].has(x.phone)).map(x => x.phone)
      await portalApi.subCustomers.assignDevices(Number(sid), keep)
    }
    ElMessage.success(`已解绑 ${selected.value.length} 台设备`)
  }
  clearSelection()
  loadData()
}

async function doBatchCommand() {
  if (!cmdText.value.trim()) return
  cmdSaving.value = true
  try {
    const phones = selected.value.map(d => d.phone)
    const res = isAdmin() ? await deviceApi.batchCommand(phones, cmdText.value.trim()) : await portalApi.batchCommand(phones, cmdText.value.trim())
    const { sent = 0, offline = 0 } = res.data || {}
    ElMessage.success(`下发完成：成功 ${sent} 台，离线跳过 ${offline} 台`)
    cmdVisible.value = false
    clearSelection()
  } finally {
    cmdSaving.value = false
  }
}

// ── 导出全部设备 ──
async function exportAll() {
  exporting.value = true
  try {
    const res = isAdmin() ? await deviceApi.exportAll() : await portalApi.deviceList({ size: 1000 })
    const rows = res.data?.records || []
    if (!rows.length) { ElMessage.warning('暂无设备可导出'); return }
    const statusMap = { 0: '离线', 1: '在线', 2: '报警' }
    const lcMap = { 0: '未激活', 1: '已激活', 2: '已停用', 3: '已报废' }
    const headers = ['设备IMEI', '设备名称', '设备型号', '在线状态', '生命周期',
      '归属账号', '姓名', '联系方式', '角色', '最后通信', '激活时间']
    const data = rows.map(r => [
      r.phone, r.name || '', r.terminal_model || '',
      statusMap[r.status] ?? '', lcMap[r.lifecycle] ?? '',
      r.account || '', r.real_name || '', r.contact_phone || '',
      r.role_name || '', r.last_location_time || '', r.activated_at || ''
    ])
    // 防 CSV 公式注入：以 = + - @ Tab CR 开头的值前置单引号，避免 Excel 当公式执行
    const csvCell = (c) => {
      let s = String(c)
      if (/^[=+\-@\t\r]/.test(s)) s = "'" + s
      return `"${s.replace(/"/g, '""')}"`
    }
    const csv = [headers, ...data].map(row =>
      row.map(csvCell).join(',')
    ).join('\n')
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `设备清单_${new Date().toISOString().slice(0, 10)}.csv`
    link.click()
    URL.revokeObjectURL(link.href)
    ElMessage.success(`已导出 ${rows.length} 台设备`)
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  loadData(1)
  loadCustomers()      // 账户查询下拉
  loadModelOptions()   // 型号查询下拉
})
</script>
