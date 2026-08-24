<template>
  <div>
    <el-row :gutter="16" style="margin-bottom:16px;">
      <el-col :span="6">
        <el-select v-model="simFilter" placeholder="筛选SIM卡" clearable filterable @change="load">
          <el-option v-for="s in simList" :key="s.id" :label="s.iccid" :value="s.id" />
        </el-select>
      </el-col>
      <el-col :span="12" />
      <el-col :span="6" style="text-align:right;">
        <el-button type="primary" :icon="Plus" @click="openModal()">新建充值记录</el-button>
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
      <el-table-column prop="iccid"      label="ICCID"     width="200" />
      <el-table-column label="充值金额">
        <template #default="{ row }">
          <span style="color:#67c23a;font-weight:600;">¥{{ Number(row.amount).toFixed(2) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="method"     label="支付方式"  width="100" />
      <el-table-column prop="plan"       label="套餐"      width="120" />
      <el-table-column prop="operator"   label="操作员"    width="100" />
      <el-table-column prop="remark"     label="备注" />
      <el-table-column prop="created_at" label="时间"      width="160" />
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
    <el-dialog v-model="modalVisible" title="新建充值记录" width="480px">
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
        <el-button type="primary" @click="save">确认充值</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { rechargeApi, simApi, portalApi, isAdmin } from '@/api'

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
    } else {
      await portalApi.recharges.create(form.value)
    }
    ElMessage.success('充值成功')
    modalVisible.value = false
    load()
    loadSims()
  } catch {}
}

onMounted(() => { loadSims(); load() })
</script>
