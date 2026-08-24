<template>
  <el-card>
    <!-- 搜索栏 -->
    <el-row :gutter="12" style="margin-bottom:14px;" align="middle">
      <el-col :span="8">
        <el-input v-model="keyword" placeholder="IMEI / 设备名称 / 角色名称 / 姓名" clearable
          @change="loadData(1)">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </el-col>
      <el-col :span="16" style="text-align:right;">
        <el-button type="primary" :icon="Search" @click="loadData(1)">搜索</el-button>
      </el-col>
    </el-row>

    <el-table :data="list" v-loading="loading" stripe border size="small">
      <el-table-column type="index" label="#" width="50" />
      <el-table-column prop="phone"          label="设备IMEI"   width="160" />
      <el-table-column prop="terminal_model" label="设备型号"   width="110" />
      <el-table-column label="设备围栏数" width="95" align="center">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ row.fence_count ?? 0 }}</el-tag>
        </template>
      </el-table-column>
      <!-- 角色名称：带颜色色块 -->
      <el-table-column label="角色名称" width="140">
        <template #default="{ row }">
          <div v-if="row.role_name" style="display:flex;align-items:center;gap:6px;">
            <span :style="{
              display:'inline-block', width:'12px', height:'12px',
              borderRadius: row.icon_type === '圆形' ? '50%' : '2px',
              background: row.role_color || '#409EFF', flexShrink:0
            }" />
            <span>{{ row.role_name }}</span>
          </div>
          <span v-else style="color:#ccc;font-size:12px;">未分配</span>
        </template>
      </el-table-column>
      <el-table-column prop="real_name"    label="姓名"       width="100" />
      <el-table-column prop="gender"       label="性别"       width="65" align="center">
        <template #default="{ row }">{{ row.gender || '—' }}</template>
      </el-table-column>
      <el-table-column prop="age"          label="年龄"       width="65" align="center">
        <template #default="{ row }">{{ row.age ?? '—' }}</template>
      </el-table-column>
      <el-table-column prop="contact_phone" label="联系方式"  width="130" />
      <el-table-column prop="address"      label="联系地址"   min-width="160" show-overflow-tooltip />
      <el-table-column label="备注" width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.customer_remark || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" fixed="right" width="70" align="center">
        <template #default="{ row }">
          <el-button
            size="small" :icon="EditIcon" circle title="编辑人员信息"
            @click="openEdit(row)"
            :disabled="!row.customer_id"
          />
        </template>
      </el-table-column>
    </el-table>

    <el-pagination style="margin-top:16px;justify-content:flex-end;display:flex;"
      :current-page="page" :page-size="pageSize" :total="total"
      layout="total,prev,pager,next" @current-change="loadData" />

    <!-- 编辑人员信息弹窗（只编辑 customer 里的个人信息，角色在「角色设置」里管理） -->
    <el-dialog v-model="editVisible" title="编辑人员信息" width="480px">
      <el-form :model="editForm" label-width="80px" style="padding-right:20px;">
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
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Search, Edit as EditIcon } from '@element-plus/icons-vue'
import { deviceApi, customerApi } from '@/api'
import { ElMessage } from 'element-plus'

const list      = ref([])
const loading   = ref(false)
const page      = ref(1)
const pageSize  = ref(20)
const total     = ref(0)
const keyword   = ref('')

const editVisible = ref(false)
const saving      = ref(false)
const editForm    = reactive({
  customerId: null,
  name: '', contact: '', gender: '', age: null, phone: '', address: '', remark: ''
})

async function loadData(p = page.value) {
  loading.value = true
  page.value = p
  try {
    const res = await deviceApi.withCustomer({
      page: p, size: pageSize.value,
      keyword: keyword.value || undefined,
    })
    list.value  = res.data?.records || []
    total.value = res.data?.total   || 0
  } finally {
    loading.value = false
  }
}

function openEdit(row) {
  if (!row.customer_id) return
  Object.assign(editForm, {
    customerId: row.customer_id,
    name:    row.role_name       || '',
    contact: row.real_name       || '',
    gender:  row.gender          || '',
    age:     row.age             || null,
    phone:   row.contact_phone   || '',
    address: row.address         || '',
    remark:  row.customer_remark || '',
  })
  editVisible.value = true
}

async function submitEdit() {
  saving.value = true
  try {
    await customerApi.update(editForm.customerId, {
      contact: editForm.contact,
      gender:  editForm.gender,
      age:     editForm.age,
      phone:   editForm.phone,
      address: editForm.address,
      remark:  editForm.remark,
    })
    ElMessage.success('保存成功')
    editVisible.value = false
    loadData()
  } finally {
    saving.value = false
  }
}

onMounted(() => loadData(1))
</script>
