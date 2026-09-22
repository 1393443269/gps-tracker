<template>
  <div>
    <el-row :gutter="16" style="margin-bottom:16px;">
      <el-col :span="6">
        <el-select v-model="simFilter" placeholder="筛选SIM卡" clearable filterable @change="load">
          <el-option v-for="s in simList" :key="s.id" :label="s.iccid" :value="s.id" />
        </el-select>
      </el-col>
      <el-col :span="12" style="text-align:right;">
        <!-- 管理员：待确认充值申请入口，带角标 -->
        <el-badge v-if="admin && pendingCount > 0" :value="pendingCount" class="pending-badge">
          <el-button type="warning" plain @click="openPending()">待确认申请</el-button>
        </el-badge>
      </el-col>
      <el-col :span="6" style="text-align:right;">
        <el-button type="primary" :icon="Plus" @click="openModal()">
          {{ admin ? '新建充值记录' : '申请充值' }}
        </el-button>
      </el-col>
    </el-row>

    <!-- 统计卡片 -->
    <el-row :gutter="16" style="margin-bottom:16px;">
      <el-col :span="6">
        <el-card shadow="never">
          <div style="font-size:12px;color:#909399;">总充值笔数</div>
          <div style="font-size:28px;font-weight:700;color:#409eff;margin-top:4px;">{{ total }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never">
          <div style="font-size:12px;color:#909399;">总充值金额</div>
          <div style="font-size:28px;font-weight:700;color:#67c23a;margin-top:4px;">¥{{ totalAmount.toFixed(2) }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-table :data="list" border stripe v-loading="loading">
      <el-table-column prop="iccid"      label="ICCID"     min-width="190" />
      <el-table-column label="充值金额">
        <template #default="{ row }">
          <span style="color:#67c23a;font-weight:600;">¥{{ Number(row.amount).toFixed(2) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="method"     label="支付方式"  width="100" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.status === '待确认'" type="warning" size="small">待确认</el-tag>
          <el-tag v-else-if="row.status === '已驳回'" type="danger" size="small">已驳回</el-tag>
          <el-tag v-else-if="row.status === '已冲正'" type="info" size="small">已冲正</el-tag>
          <el-tag v-else-if="row.status === '冲正流水'" type="info" size="small" effect="plain">冲正流水</el-tag>
          <el-tag v-else type="success" size="small">已确认</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="operator"   label="操作员"    width="100" />
      <el-table-column prop="remark"     label="备注" min-width="120" show-overflow-tooltip />
      <el-table-column prop="created_at" label="时间"      min-width="160" />
      <el-table-column v-if="admin" label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === '已确认'" type="danger" size="small" plain @click="doReverse(row)">冲正</el-button>
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

    <!-- 新建充值弹窗 -->
    <el-dialog v-model="modalVisible" :title="admin ? '新建充值记录' : '申请充值'" width="480px">
      <!-- 收款信息展示区（管理员在平台设置里配置并开启后显示） -->
      <div v-if="pay.payment_enabled" class="pay-box">
        <div class="pay-title">收款信息</div>
        <div class="pay-body">
          <el-image
            v-if="pay.payment_qrcode_url"
            :src="qrSrc(pay.payment_qrcode_url)"
            :preview-src-list="[qrSrc(pay.payment_qrcode_url)]"
            fit="contain"
            style="width:130px;height:130px;border:1px solid #eee;border-radius:6px;flex-shrink:0;" />
          <div class="pay-info">
            <div v-if="pay.payment_payee"><span class="pay-label">收款人：</span>{{ pay.payment_payee }}</div>
            <div v-if="pay.payment_account"><span class="pay-label">账号：</span>{{ pay.payment_account }}</div>
            <div v-if="pay.payment_bank"><span class="pay-label">开户行：</span>{{ pay.payment_bank }}</div>
            <div v-if="pay.payment_note" class="pay-note">{{ pay.payment_note }}</div>
          </div>
        </div>
        <div class="pay-tip">请扫码或转账付款后，填写下方金额并提交申请，工作人员核对到账后完成充值。</div>
      </div>

      <el-form :model="form" label-width="90px">
        <el-form-item label="SIM卡" required>
          <el-select v-model="form.sim_id" placeholder="选择SIM卡" filterable style="width:100%;">
            <el-option v-for="s in simList" :key="s.id" :label="`${s.iccid} (余额¥${s.balance})`" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="充值金额" required>
          <el-input-number v-model="form.amount" :min="1" :precision="2" :step="50" />
        </el-form-item>
        <el-form-item label="支付方式">
          <el-select v-model="form.method">
            <el-option label="支付宝" value="支付宝" />
            <el-option label="微信" value="微信" />
            <el-option label="银行转账" value="银行转账" />
          </el-select>
        </el-form-item>
        <el-form-item label="套餐">
          <el-input v-model="form.plan" placeholder="如 30GB/月" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="modalVisible = false">取消</el-button>
        <el-button type="primary" @click="save">{{ admin ? '确认充值' : '提交申请' }}</el-button>
      </template>
    </el-dialog>

    <!-- 管理员：待确认充值申请审核弹窗 -->
    <el-dialog v-model="pendingVisible" title="待确认充值申请" width="720px">
      <el-table :data="pendingList" border stripe v-loading="pendingLoading" max-height="420">
        <el-table-column prop="iccid" label="ICCID" min-width="180" />
        <el-table-column label="金额" width="110">
          <template #default="{ row }">
            <span style="color:#67c23a;font-weight:600;">¥{{ Number(row.amount).toFixed(2) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="operator" label="申请人" width="110" />
        <el-table-column prop="created_at" label="申请时间" min-width="150" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button type="success" size="small" @click="doConfirm(row)">确认到账</el-button>
            <el-button type="danger" size="small" plain @click="doReject(row)">驳回</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!pendingLoading && pendingList.length === 0" style="text-align:center;color:#909399;padding:20px;">
        暂无待确认的充值申请
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { rechargeApi, simApi, portalApi, platformApi, isAdmin } from '@/api'

const admin = isAdmin()

const list = ref([])
const simList = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const simFilter = ref(null)

const totalAmount = computed(() => list.value.reduce((s, r) => s + Number(r.amount), 0))

const modalVisible = ref(false)
const form = ref({ sim_id: null, amount: 100, method: '支付宝', plan: '', remark: '' })

// 管理员：待确认申请
const pendingVisible = ref(false)
const pendingList = ref([])
const pendingLoading = ref(false)
const pendingCount = ref(0)

// 收款信息（来自平台设置）
const pay = ref({
  payment_enabled: false, payment_qrcode_url: '', payment_payee: '',
  payment_account: '', payment_bank: '', payment_note: '',
})
function qrSrc(url) {
  if (!url) return ''
  return /^https?:\/\//.test(url) ? url : (window.location.origin + url)
}
async function loadPaySetting() {
  try {
    const res = await platformApi.get()
    const d = res.data || {}
    pay.value = {
      payment_enabled:    !!d.payment_enabled,
      payment_qrcode_url: d.payment_qrcode_url || '',
      payment_payee:      d.payment_payee      || '',
      payment_account:    d.payment_account    || '',
      payment_bank:       d.payment_bank       || '',
      payment_note:       d.payment_note       || '',
    }
  } catch {}
}

async function loadSims() {
  try {
    const res = admin
      ? await simApi.list({ size: 500 })
      : await portalApi.sims.list({ size: 500 })
    simList.value = res.data?.records || []
  } catch {}
}

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, size: pageSize.value }
    if (simFilter.value) params.sim_id = simFilter.value
    const res = admin
      ? await rechargeApi.list(params)
      : await portalApi.recharges.list(params)
    list.value = res.data?.records || []
    total.value = res.data?.total || 0
  } finally {
    loading.value = false
  }
}

function openModal() {
  form.value = { sim_id: null, amount: 100, method: '支付宝', plan: '', remark: '' }
  modalVisible.value = true
}

async function save() {
  if (!form.value.sim_id) { ElMessage.error('请选择SIM卡'); return }
  if (form.value.amount <= 0) { ElMessage.error('金额必须大于0'); return }
  try {
    if (admin) {
      await rechargeApi.create(form.value)
      ElMessage.success('充值成功')
    } else {
      await portalApi.recharges.create(form.value)
      ElMessage.success('充值申请已提交，请等待工作人员核对到账')
    }
    modalVisible.value = false
    load()
    loadSims()
    if (admin) loadPendingCount()
  } catch {}
}

// 管理员：加载待确认数量（角标）
async function loadPendingCount() {
  if (!admin) return
  try {
    const res = await rechargeApi.pending()
    pendingCount.value = res.data?.total || 0
  } catch {}
}

async function openPending() {
  pendingVisible.value = true
  pendingLoading.value = true
  try {
    const res = await rechargeApi.pending()
    pendingList.value = res.data?.records || []
    pendingCount.value = res.data?.total || 0
  } finally {
    pendingLoading.value = false
  }
}

async function doConfirm(row) {
  try {
    await ElMessageBox.confirm(
      `确认这笔 ¥${Number(row.amount).toFixed(2)} 的充值已真实到账？确认后余额将立即到账。`,
      '确认到账', { type: 'warning' }
    )
    await rechargeApi.confirm(row.id)
    ElMessage.success('已确认到账')
    openPending()
    load()
    loadSims()
  } catch {}
}

async function doReject(row) {
  try {
    await ElMessageBox.confirm('确定驳回这笔充值申请？余额不会变动。', '驳回申请', { type: 'warning' })
    await rechargeApi.reject(row.id)
    ElMessage.success('已驳回')
    openPending()
    load()
  } catch {}
}

// 冲正一笔已确认的充值:扣回余额+生成负向流水
async function doReverse(row) {
  try {
    const { value } = await ElMessageBox.prompt(
      `将冲正这笔 ¥${Number(row.amount).toFixed(2)} 的充值：从该卡余额扣回此金额，并生成一条冲正流水（原记录保留可追溯）。请填写冲正原因：`,
      '冲正确认',
      { inputPlaceholder: '如：金额填错/误确认', confirmButtonText: '确认冲正', cancelButtonText: '取消',
        type: 'warning', inputValidator: (v) => (v && v.trim()) ? true : '请填写冲正原因' }
    )
    await rechargeApi.reverse(row.id, { reason: value.trim() })
    ElMessage.success('已冲正')
    load()
    loadSims()
  } catch {}
}

onMounted(() => {
  loadSims(); load(); loadPaySetting()
  if (admin) loadPendingCount()
})
</script>

<style scoped>
.pay-box {
  border: 1px solid #ebeef5;
  background: #fafafa;
  border-radius: 6px;
  padding: 12px 14px;
  margin-bottom: 16px;
}
.pay-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 10px;
}
.pay-body {
  display: flex;
  gap: 14px;
  align-items: center;
}
.pay-info {
  font-size: 13px;
  color: #606266;
  line-height: 1.9;
}
.pay-label {
  color: #909399;
}
.pay-note {
  color: #e6a23c;
  margin-top: 4px;
}
.pay-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 10px;
  line-height: 1.5;
}
</style>
