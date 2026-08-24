<template>
  <el-card>
    <!-- 搜索栏 -->
    <el-row :gutter="12" style="margin-bottom:14px;" align="middle">
      <el-col :span="7">
        <el-input v-model="keyword" placeholder="IMEI / 设备名称 / 归属账号" clearable
          @change="loadData(1)">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </el-col>
      <el-col :span="5">
        <el-select v-model="bindFilter" placeholder="绑定状态" clearable @change="loadData(1)" style="width:100%">
          <el-option label="已绑定" value="bound" />
          <el-option label="未绑定" value="unbound" />
        </el-select>
      </el-col>
      <el-col :span="12" style="text-align:right;">
        <el-button type="primary" :icon="Search" @click="loadData(1)">搜索</el-button>
      </el-col>
    </el-row>

    <el-table :data="list" v-loading="loading" stripe border size="small">
      <el-table-column type="index" label="#" width="50" />
      <el-table-column prop="name"           label="姓名"       width="120" />
      <el-table-column prop="phone"          label="设备IMEI"   width="160" />
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
          <!-- 已绑定：显示解绑按钮 -->
          <el-button v-if="row.customer_id"
            size="small" type="danger" plain
            :icon="Minus" @click="doUnbind(row)">解绑</el-button>
          <!-- 未绑定：显示绑定按钮 -->
          <el-button v-else
            size="small" type="primary" plain
            :icon="Link" @click="openBind(row)">绑定</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination style="margin-top:16px;justify-content:flex-end;display:flex;"
      :current-page="page" :page-size="pageSize" :total="total"
      layout="total,prev,pager,next" @current-change="loadData" />

    <!-- 绑定客户弹窗 -->
    <el-dialog v-model="bindVisible" title="绑定设备至客户" width="440px">
      <div style="margin-bottom:12px;font-size:13px;color:#606266;">
        设备 <b>{{ bindTarget?.phone }}</b> 绑定至：
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
          :disabled="!bindCustomerId">确认绑定</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Search, Minus, Link } from '@element-plus/icons-vue'
import { deviceApi, customerApi } from '@/api'
import { ElMessage, ElMessageBox } from 'element-plus'

const list        = ref([])
const loading     = ref(false)
const page        = ref(1)
const pageSize    = ref(20)
const total       = ref(0)
const keyword     = ref('')
const bindFilter  = ref(null)

const bindVisible    = ref(false)
const bindSaving     = ref(false)
const bindTarget     = ref(null)
const bindCustomerId = ref(null)
const customerList   = ref([])

async function loadData(p = page.value) {
  loading.value = true
  page.value = p
  try {
    const params = {
      page: p, size: pageSize.value,
      keyword: keyword.value || undefined,
    }
    const res = await deviceApi.withCustomer(params)
    let records = res.data?.records || []
    // 前端过滤绑定状态
    if (bindFilter.value === 'bound')   records = records.filter(r => r.customer_id)
    if (bindFilter.value === 'unbound') records = records.filter(r => !r.customer_id)
    list.value  = records
    total.value = res.data?.total || 0
  } finally {
    loading.value = false
  }
}

async function loadCustomers() {
  try {
    const res = await customerApi.listAll()
    customerList.value = res.data?.records || []
  } catch {}
}

async function doUnbind(row) {
  try {
    await ElMessageBox.confirm(
      `确认解除设备 ${row.phone} 与账号 ${row.account || ''} 的绑定？`,
      '解绑确认', { type: 'warning', confirmButtonText: '解绑', cancelButtonText: '取消' }
    )
  } catch { return }
  await deviceApi.unbindCustomer(row.id)
  ElMessage.success('解绑成功')
  loadData()
}

async function openBind(row) {
  bindTarget.value     = row
  bindCustomerId.value = null
  bindVisible.value    = true
  await loadCustomers()
}

async function doBindConfirm() {
  if (!bindCustomerId.value) return
  bindSaving.value = true
  try {
    await deviceApi.bindCustomer(bindTarget.value.id, bindCustomerId.value)
    ElMessage.success('绑定成功')
    bindVisible.value = false
    loadData()
  } finally {
    bindSaving.value = false
  }
}

onMounted(() => loadData(1))
</script>
