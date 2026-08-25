<template>
  <div>
    <!-- 到期预警横幅 -->
    <el-alert v-if="expiring7 > 0" type="error" :closable="false" show-icon style="margin-bottom:12px;">
      <template #title>
        <span>
          <b>{{ expiring7 }}</b> 张 SIM 卡将在 <b>7 天内</b>到期，请尽快续费！
          <el-button link type="danger" size="small" style="margin-left:8px"
            @click="statusFilter=''; expiringFilter='7'; load()">查看</el-button>
        </span>
      </template>
    </el-alert>
    <el-alert v-else-if="expiring30 > 0" type="warning" :closable="false" show-icon style="margin-bottom:12px;">
      <template #title>
        <span>
          <b>{{ expiring30 }}</b> 张 SIM 卡将在 30 天内到期
          <el-button link type="warning" size="small" style="margin-left:8px"
            @click="statusFilter=''; expiringFilter='30'; load()">查看</el-button>
        </span>
      </template>
    </el-alert>

    <!-- 顶栏 -->
    <el-row :gutter="12" style="margin-bottom:14px;" align="middle">
      <el-col :span="7">
        <el-input v-model="keyword" placeholder="ICCID / IMSI / 运营商 / 设备IMEI" clearable @change="load">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </el-col>
      <el-col :span="4">
        <el-select v-model="statusFilter" placeholder="状态" clearable @change="load" style="width:100%">
          <el-option label="正常" value="正常" />
          <el-option label="欠费" value="欠费" />
          <el-option label="停用" value="停用" />
        </el-select>
      </el-col>
      <el-col :span="5">
        <el-select v-model="expiringFilter" placeholder="到期筛选" clearable @change="load" style="width:100%">
          <el-option label="7天内到期" value="7" />
          <el-option label="30天内到期" value="30" />
        </el-select>
      </el-col>
      <el-col :span="8" style="text-align:right">
        <el-button v-if="admin" type="primary" :icon="Plus" @click="openModal()">新增 SIM 卡</el-button>
      </el-col>
    </el-row>

    <!-- 表格 -->
    <el-table :data="list" border stripe v-loading="loading" size="small"
      :empty-text="admin ? '暂无 SIM 卡，点右上角「新增 SIM 卡」录入' : '当前账号下暂无 SIM 卡'">
      <el-table-column label="ICCID" min-width="195">
        <template #default="{ row }"><span v-html="highlight(row.iccid)" /></template>
      </el-table-column>
      <el-table-column label="绑定设备" min-width="150">
        <template #default="{ row }">
          <span v-if="row.device_phone" v-html="highlight(row.device_phone)" />
          <span v-else style="color:#ccc;font-size:12px;">未绑定</span>
        </template>
      </el-table-column>
      <el-table-column label="运营商" min-width="90">
        <template #default="{ row }"><span v-html="highlight(row.operator)" /></template>
      </el-table-column>
      <el-table-column prop="plan"      label="套餐"   width="110" />
      <el-table-column label="余额" width="90">
        <template #default="{ row }">
          <span :style="balanceStyle(row.balance)">¥{{ Number(row.balance).toFixed(2) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="到期日" width="130">
        <template #default="{ row }">
          <span v-if="row.expire_date">{{ row.expire_date }}</span>
          <span v-else style="color:#ccc">—</span>
        </template>
      </el-table-column>
      <el-table-column label="剩余天数" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.days_left !== null && row.days_left !== undefined"
            :type="daysTagType(row.days_left)" size="small">
            {{ row.days_left < 0 ? '已过期' : row.days_left + ' 天' }}
          </el-tag>
          <span v-else style="color:#ccc">—</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="75">
        <template #default="{ row }">
          <el-tag :type="row.status === '正常' ? 'success' : row.status === '欠费' ? 'danger' : 'info'" size="small">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:nowrap;">
            <el-button size="small" @click="openModal(row)">编辑</el-button>
            <el-button v-if="admin" size="small" @click="openBind(row)">绑定</el-button>
            <el-button size="small" type="primary" @click="openRecharge(row)">充值</el-button>
            <el-button v-if="admin" size="small" type="danger" @click="remove(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination v-model:current-page="page" v-model:page-size="pageSize"
      :total="total" layout="total,prev,pager,next" style="margin-top:14px;" @change="load" />

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="modalVisible" :title="form.id ? '编辑 SIM 卡' : '新增 SIM 卡'" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="ICCID" required>
          <el-input v-model="form.iccid" :disabled="!!form.id || !admin" placeholder="20位ICCID" />
        </el-form-item>
        <el-form-item v-if="admin" label="IMSI">
          <el-input v-model="form.imsi" placeholder="15位IMSI" />
        </el-form-item>
        <el-form-item label="运营商">
          <el-select v-model="form.operator" style="width:100%">
            <el-option label="中国移动" value="中国移动" />
            <el-option label="中国联通" value="中国联通" />
            <el-option label="中国电信" value="中国电信" />
          </el-select>
        </el-form-item>
        <el-form-item label="套餐">
          <el-input v-model="form.plan" placeholder="如 30GB/月" />
        </el-form-item>
        <el-form-item label="套餐到期日">
          <el-date-picker v-model="form.expire_date" type="date" value-format="YYYY-MM-DD"
            placeholder="选择到期日" style="width:100%" />
        </el-form-item>
        <el-form-item label="月租费(¥)" v-if="admin">
          <el-input-number v-model="form.monthly_fee" :precision="2" :step="5" :min="0" />
        </el-form-item>
        <el-form-item v-if="admin" label="余额(¥)">
          <el-input-number v-model="form.balance" :precision="2" :step="10" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status" style="width:100%">
            <el-option label="正常" value="正常" />
            <el-option label="欠费" value="欠费" />
            <el-option label="停用" value="停用" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="modalVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 绑定设备弹窗 -->
    <el-dialog v-model="bindVisible" title="绑定设备" width="440px">
      <div style="margin-bottom:12px;font-size:13px;color:#606266;">
        SIM 卡 <b>{{ currentSim?.iccid }}</b> 绑定至设备：
      </div>
      <el-select v-model="bindPhone" placeholder="请选择设备（清空则解绑）"
        filterable clearable style="width:100%" :loading="deviceLoading">
        <el-option
          v-for="d in deviceOptions"
          :key="d.phone"
          :label="`${d.name ? d.name + ' — ' : ''}${d.phone}${d.terminal_model ? ' (' + d.terminal_model + ')' : ''}`"
          :value="d.phone"
        />
      </el-select>
      <template #footer>
        <el-button @click="bindVisible = false">取消</el-button>
        <el-button type="primary" @click="doBind">确认</el-button>
      </template>
    </el-dialog>

    <!-- 充值弹窗 -->
    <el-dialog v-model="rechargeVisible" :title="`充值 — ${currentSim?.iccid}`" width="400px">
      <el-form label-width="90px">
        <el-form-item label="充值金额">
          <el-input-number v-model="rechargeAmount" :min="1" :precision="2" :step="50" />
        </el-form-item>
        <el-form-item label="支付方式">
          <el-select v-model="rechargeMethod" style="width:100%">
            <el-option label="支付宝" value="支付宝" />
            <el-option label="微信" value="微信" />
            <el-option label="银行转账" value="银行转账" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rechargeVisible = false">取消</el-button>
        <el-button type="primary" @click="doRecharge">确认充值</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Plus, Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { simApi, rechargeApi, portalApi, deviceApi, isAdmin } from '@/api'

const admin = isAdmin()

const list    = ref([])
const total   = ref(0)
const page    = ref(1)
const pageSize= ref(20)
const loading = ref(false)

const keyword       = ref('')
const statusFilter  = ref('')
const expiringFilter= ref('')

// 预警数量
const expiring7  = ref(0)
const expiring30 = ref(0)

// modal
const modalVisible = ref(false)
const form = ref({
  id: null, iccid: '', imsi: '', operator: '中国移动',
  plan: '', balance: 0, status: '正常', remark: '',
  expire_date: null, monthly_fee: 0
})

// bind
const bindVisible   = ref(false)
const bindPhone     = ref('')
const currentSim    = ref(null)
const deviceOptions = ref([])
const deviceLoading = ref(false)

// recharge
const rechargeVisible = ref(false)
const rechargeAmount  = ref(100)
const rechargeMethod  = ref('支付宝')

// ── 关键词高亮（先转义防 XSS，再包裹命中片段）────────────────────────────────
function _esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;')
}
function highlight(text) {
  const safe = _esc(text)
  const kw = keyword.value.trim()
  if (!kw) return safe
  // 转义正则特殊字符，避免用户输入含 . * 等导致匹配异常
  const escKw = kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return safe.replace(new RegExp(escKw, 'gi'),
    m => `<mark style="background:#ffe58f;color:inherit;padding:0 1px;border-radius:2px;">${m}</mark>`)
}

// ── 样式辅助 ─────────────────────────────────────────────────────────────────
function balanceStyle(bal) {
  const b = Number(bal)
  if (b < 0)  return { color: '#f56c6c', fontWeight: 600 }
  if (b === 0) return { color: '#e6a23c', fontWeight: 600 }
  return { color: '#67c23a', fontWeight: 600 }
}

function daysTagType(days) {
  if (days < 0)  return 'danger'
  if (days <= 7) return 'danger'
  if (days <= 30) return 'warning'
  return 'success'
}

// ── 数据加载 ─────────────────────────────────────────────────────────────────
async function loadExpiring() {
  if (!admin) return
  try {
    const res = await simApi.expiringCount()
    expiring7.value  = res.data?.expiring7  ?? 0
    expiring30.value = res.data?.expiring30 ?? 0
  } catch {}
}

async function load() {
  loading.value = true
  try {
    const params = {
      page: page.value, size: pageSize.value,
      keyword: keyword.value, status: statusFilter.value,
      expiring: expiringFilter.value
    }
    const res = admin
      ? await simApi.list(params)
      : await portalApi.sims.list(params)
    let records = res.data?.records || []
    // 有关键词时按匹配精度置顶：完全相等 > 开头匹配 > 包含
    const kw = keyword.value.trim().toLowerCase()
    if (kw) {
      const rank = (row) => {
        const fields = [row.iccid, row.imsi, row.operator, row.device_phone]
          .map(v => String(v ?? '').toLowerCase())
        if (fields.some(f => f === kw))            return 0   // 完全匹配
        if (fields.some(f => f.startsWith(kw)))    return 1   // 开头匹配
        return 2                                              // 包含匹配
      }
      records = records.map((r, i) => ({ r, i }))
        .sort((a, b) => rank(a.r) - rank(b.r) || a.i - b.i)   // 同级保持原顺序
        .map(x => x.r)
    }
    list.value  = records
    total.value = res.data?.total   || 0
  } finally {
    loading.value = false
  }
}

function openModal(row) {
  form.value = row
    ? { ...row, expire_date: row.expire_date || null, monthly_fee: row.monthly_fee || 0 }
    : { id: null, iccid: '', imsi: '', operator: '中国移动', plan: '', balance: 0,
        status: '正常', remark: '', expire_date: null, monthly_fee: 0 }
  modalVisible.value = true
}

async function save() {
  if (!form.value.iccid && admin) { ElMessage.error('ICCID 不能为空'); return }
  try {
    if (admin) {
      form.value.id ? await simApi.update(form.value.id, form.value) : await simApi.create(form.value)
    } else {
      await portalApi.sims.update(form.value.id, form.value)
    }
    ElMessage.success('保存成功')
    modalVisible.value = false
    load()
    loadExpiring()
  } catch {}
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除 SIM 卡 ${row.iccid}？`, '确认删除', { type: 'warning' })
  await simApi.remove(row.id)
  ElMessage.success('已删除')
  load()
  loadExpiring()
}

async function openBind(row) {
  currentSim.value = row
  bindPhone.value  = row.device_phone || ''
  bindVisible.value = true
  await loadDevices()
}

async function loadDevices() {
  if (deviceOptions.value.length) return
  deviceLoading.value = true
  try {
    const res = await deviceApi.list({ size: 500 })
    deviceOptions.value = res.data?.records || []
  } catch {} finally {
    deviceLoading.value = false
  }
}

async function doBind() {
  await simApi.bind(currentSim.value.id, bindPhone.value)
  ElMessage.success(bindPhone.value ? '绑定成功' : '已解绑')
  bindVisible.value = false
  load()
}

function openRecharge(row) {
  currentSim.value    = row
  rechargeAmount.value = 100
  rechargeMethod.value = '支付宝'
  rechargeVisible.value = true
}

async function doRecharge() {
  if (admin) {
    await rechargeApi.create({ sim_id: currentSim.value.id, amount: rechargeAmount.value, method: rechargeMethod.value })
  } else {
    await portalApi.sims.recharge(currentSim.value.id, { amount: rechargeAmount.value, method: rechargeMethod.value })
  }
  ElMessage.success(`充值成功 ¥${rechargeAmount.value}`)
  rechargeVisible.value = false
  load()
  loadExpiring()
}

onMounted(() => { load(); loadExpiring() })
</script>
