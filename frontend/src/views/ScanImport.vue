<template>
  <div style="padding:16px;">
    <el-card>
      <template #header>
        <b>扫码录入设备</b>
        <span style="color:#909399;font-size:12px;margin-left:12px;">
          扫码枪连续扫，新设备勾选后一键批量录入(归属当前账号)，老设备显示归属
        </span>
      </template>
      <el-input
        ref="scanInput"
        v-model="code"
        size="large"
        placeholder="点此处后用扫码枪扫码，或手动输入 IMEI/设备号 回车"
        clearable
        @keyup.enter="onScan"
        style="max-width:520px;"
      >
        <template #prepend>条码</template>
      </el-input>
      <div style="margin-top:10px;">
        <el-button type="primary" :disabled="!selectedNew.length" @click="doBatchAdd">
          批量录入选中的新设备 ({{ selectedNew.length }})
        </el-button>
        <el-button :disabled="!rows.length" @click="clearRows">清空列表</el-button>
        <span style="color:#909399;font-size:12px;margin-left:12px;">本次已扫 {{ rows.length }} 条</span>
      </div>
    </el-card>

    <el-card style="margin-top:16px;">
      <el-table :data="rows" size="small" border @selection-change="onSelect" ref="tableRef">
        <el-table-column type="selection" width="45" :selectable="row => row.status==='new'" />
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="code" label="条码/IMEI" min-width="160" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.status==='new'" type="success">新设备</el-tag>
            <el-tag v-else-if="row.status==='exist'" type="warning">已存在</el-tag>
            <el-tag v-else-if="row.status==='added'" type="primary">已录入</el-tag>
            <el-tag v-else-if="row.status==='skipped'" type="info">已跳过</el-tag>
            <el-tag v-else type="danger">失败</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="owner" label="归属" min-width="140" />
        <el-table-column label="设备名(选填)" width="150">
          <template #default="{ row }">
            <el-input v-if="row.status==='new'" v-model="row.name" size="small" placeholder="设备名" />
          </template>
        </el-table-column>
        <el-table-column prop="msg" label="说明" min-width="200" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { deviceApi } from '@/api'

const code = ref('')
const rows = ref([])
const selected = ref([])
const scanInput = ref(null)
const tableRef = ref(null)

const selectedNew = computed(() => selected.value.filter(r => r.status === 'new'))

function focusInput() {
  nextTick(() => { scanInput.value && scanInput.value.focus() })
}
onMounted(focusInput)

function onSelect(sel) { selected.value = sel }

function clearRows() {
  rows.value = []
  selected.value = []
  clearSelectionSafe()
  focusInput()
}

// 扫码 / 回车:查该条码是否已存在及归属
async function onScan() {
  const c = (code.value || '').trim()
  code.value = ''
  focusInput()
  if (!c) return
  if (rows.value.some(r => r.code === c)) {
    ElMessage.warning('该条码本次已扫过')
    return
  }
  try {
    const res = await deviceApi.scan({ code: c })
    const d = res.data || {}
    if (d.exists) {
      rows.value.unshift({ code: c, status: 'exist', owner: d.owner || '', name: d.name || '',
        msg: '设备已存在，归属：' + (d.owner || '未分配') })
    } else {
      rows.value.unshift({ code: c, status: 'new', owner: '', name: '', msg: '新设备，勾选后可批量录入' })
    }
  } catch (e) {
    rows.value.unshift({ code: c, status: 'fail', owner: '', msg: '查询失败：' + (e.message || '') })
  }
}

// 批量录入:调 /devices/import，用返回的 details 逐条精确回标，不再无条件全标成功
async function doBatchAdd() {
  const list = selectedNew.value.slice()
  if (!list.length) return
  // 后端主键 phone 优先取 deviceNo、无则取 imei；此处扫码值同时作为两者
  const payload = list.map(r => ({ deviceNo: r.code, imei: r.code, name: r.name || '' }))
  try {
    const res = await deviceApi.batchImport(payload)
    const d = res.data || {}
    const details = Array.isArray(d.details) ? d.details : []
    // 以 phone 为键建立回标索引；后端 phone == 扫码值(deviceNo/imei)
    const byPhone = new Map(details.map(x => [x.phone, x]))

    list.forEach(r => {
      const item = byPhone.get(r.code)
      if (!item) {
        // 理论上不应发生:返回明细里没有这条，保守标为失败待核对
        r.status = 'fail'
        r.msg = '未返回录入结果，请刷新核对'
        return
      }
      if (item.status === 'created') {
        r.status = 'added'
        r.msg = '已录入，归属当前账号'
      } else if (item.status === 'skipped') {
        r.status = 'skipped'
        r.msg = '已跳过：' + (item.reason || '设备已存在')
      } else {
        r.status = 'fail'
        r.msg = '录入失败：' + (item.reason || '未知错误')
      }
    })

    clearSelectionSafe()
    const created = d.created ?? 0
    const skipped = d.skipped ?? 0
    const failed = d.failed ?? 0
    if (failed > 0) {
      ElMessage.warning('批量录入完成：成功 ' + created + ' 条，跳过 ' + skipped + ' 条，失败 ' + failed + ' 条')
    } else if (skipped > 0) {
      ElMessage.warning('批量录入完成：成功 ' + created + ' 条，跳过 ' + skipped + ' 条')
    } else {
      ElMessage.success('批量录入完成：成功 ' + created + ' 条')
    }
  } catch (e) {
    ElMessage.error('批量录入失败：' + (e.response?.data?.msg || e.message || ''))
  }
  focusInput()
}

function clearSelectionSafe() {
  selected.value = []
  nextTick(() => { tableRef.value && tableRef.value.clearSelection && tableRef.value.clearSelection() })
}
</script>
