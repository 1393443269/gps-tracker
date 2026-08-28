<template>
  <div>
    <el-row :gutter="16" style="margin-bottom:16px;">
      <el-col :span="8">
        <el-input v-model="keyword" placeholder="搜索客户名称 / 联系人 / 电话" clearable @change="load" />
      </el-col>
      <el-col :span="4">
        <el-select v-model="statusFilter" placeholder="状态" clearable @change="load">
          <el-option label="活跃" value="活跃" />
          <el-option label="非活跃" value="非活跃" />
        </el-select>
      </el-col>
      <el-col :span="12" style="text-align:right;">
        <el-button type="primary" :icon="Plus" @click="openModal()">新增客户</el-button>
      </el-col>
    </el-row>

    <!-- 管理员：树形表格（无搜索条件时懒加载展开子级） -->
    <el-table v-if="admin"
      :key="isTreeMode ? 'tree' : 'flat'"
      :data="list"
      row-key="id"
      :lazy="isTreeMode"
      :load="isTreeMode ? loadChildren : undefined"
      :tree-props="isTreeMode ? { children: 'children', hasChildren: 'has_children' } : {}"
      border stripe v-loading="loading">
      <el-table-column prop="name"    label="客户名称" min-width="130" />
      <el-table-column prop="contact" label="联系人"   width="100" />
      <el-table-column prop="phone"   label="电话"     width="130" />
      <!-- 搜索模式：显示归属上级 -->
      <el-table-column v-if="!isTreeMode" label="归属上级" width="120">
        <template #default="{ row }">
          <span v-if="row.parent_name" style="color:#909399;font-size:12px;">{{ row.parent_name }}</span>
          <el-tag v-else size="small" type="info">顶级</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="登录账号" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.login_name" type="success" size="small">{{ row.login_name }}</el-tag>
          <span v-else style="color:#ccc;font-size:12px;">未设置</span>
        </template>
      </el-table-column>
      <el-table-column label="关联设备" width="90">
        <template #default="{ row }">
          <span :style="{ color: row.device_count > 0 ? '#409EFF' : '#ccc' }">
            {{ row.device_count || 0 }} 台
          </span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.status === '活跃' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="100" />
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:nowrap;">
            <el-button size="small" @click="openModal(row)">编辑</el-button>
            <el-button size="small" type="primary" plain @click="openDeviceAssign(row)">分配设备</el-button>
            <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- 客户端：普通列表（只显示自己的下级客户） -->
    <el-table v-else :data="list" border stripe v-loading="loading">
      <el-table-column prop="name"    label="客户名称" min-width="130" />
      <el-table-column prop="contact" label="联系人"   width="100" />
      <el-table-column prop="phone"   label="电话"     width="130" />
      <el-table-column label="登录账号" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.login_name" type="success" size="small">{{ row.login_name }}</el-tag>
          <span v-else style="color:#ccc;font-size:12px;">未设置</span>
        </template>
      </el-table-column>
      <el-table-column label="关联设备" width="90">
        <template #default="{ row }">
          <span :style="{ color: row.device_count > 0 ? '#409EFF' : '#ccc' }">
            {{ row.device_count || 0 }} 台
          </span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.status === '活跃' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="120" />
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:nowrap;">
            <el-button size="small" @click="openModal(row)">编辑</el-button>
            <el-button size="small" type="primary" plain @click="openDeviceAssign(row)">分配设备</el-button>
            <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- 管理员树形模式下只对顶级分页；搜索模式 / 客户端正常分页 -->
    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      layout="total,prev,pager,next"
      style="margin-top:14px;"
      @change="load"
    />

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="modalVisible" :title="form.id ? '编辑客户' : '新增客户'" width="540px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="客户名称" required>
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="联系人">
          <el-input v-model="form.contact" />
        </el-form-item>
        <el-form-item label="电话">
          <el-input v-model="form.phone" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="form.email" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status">
            <el-option label="活跃" value="活跃" />
            <el-option label="非活跃" value="非活跃" />
          </el-select>
        </el-form-item>
        <el-form-item label="注册日期">
          <el-date-picker v-model="form.reg_date" type="date" value-format="YYYY-MM-DD" style="width:100%;" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>

        <el-divider content-position="left">
          <span style="font-size:12px;color:#909399;">自助查询账号</span>
        </el-divider>

        <el-form-item label="登录账号">
          <el-input v-model="form.login_name" placeholder="客户登录用账号，留空则无法登录" />
        </el-form-item>
        <el-form-item label="登录密码">
          <el-input v-model="form.password" type="password" show-password
            :placeholder="form.id ? '不填则保持原密码不变' : '设置登录密码'" />
        </el-form-item>
        <el-form-item label="">
          <el-text type="info" size="small">
            客户通过 <b>/login</b> 页面选择「客户登录」，只能看到分配给他的设备
          </el-text>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="modalVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 分配设备弹窗 -->
    <el-dialog v-model="assignVisible"
      :title="assignTarget ? `分配设备 — ${assignTarget.name}` : '分配设备'"
      width="480px">
      <div style="margin-bottom:10px;font-size:13px;color:#606266;">
        勾选后该设备将归属此客户（客户自助门户可见）
      </div>
      <div v-if="!allDevices.length" style="text-align:center;color:#ccc;padding:20px 0;">暂无设备</div>
      <el-checkbox-group v-else v-model="assignedPhones">
        <div v-for="d in allDevices" :key="d.phone"
          style="padding:7px 0;border-bottom:1px solid #f5f5f5;display:flex;align-items:center;gap:8px;">
          <el-checkbox :value="String(d.phone)" style="margin:0;" />
          <div style="flex:1;min-width:0;">
            <div style="font-size:13px;font-weight:500;">{{ d.name || '未命名' }}</div>
            <div style="font-size:11px;color:#909399;">{{ d.phone }}</div>
          </div>
          <!-- 管理员模式：标出已分配给其他客户的设备 -->
          <el-tag v-if="admin && d.customer_id && Number(d.customer_id) !== Number(assignTarget?.id)"
            type="warning" size="small">已归属其他客户</el-tag>
          <!-- 客户模式：标出当前在哪个子账号里 -->
          <el-tag v-if="!admin && Number(d.customer_id) !== Number(assignTarget?.id) && d.holder_name"
            type="info" size="small">{{ d.holder_name }}</el-tag>
        </div>
      </el-checkbox-group>
      <div style="margin-top:10px;font-size:12px;color:#909399;">已选 {{ assignedPhones.length }} 台</div>
      <template #footer>
        <el-button @click="assignVisible = false">取消</el-button>
        <el-button type="primary" @click="saveAssign" :loading="assignSaving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { customerApi, deviceApi, portalApi, isAdmin } from '@/api'

const admin = isAdmin()

// 根据角色选择 API：管理员用 customerApi，客户用 portalApi.subCustomers
const subApi = {
  list:          (p)       => isAdmin() ? customerApi.list(p)                : portalApi.subCustomers.list(p),
  create:        (d)       => isAdmin() ? customerApi.create(d)              : portalApi.subCustomers.create(d),
  update:        (id, d)   => isAdmin() ? customerApi.update(id, d)          : portalApi.subCustomers.update(id, d),
  remove:        (id)      => isAdmin() ? customerApi.remove(id)             : portalApi.subCustomers.remove(id),
  getDevices:    (id)      => isAdmin() ? customerApi.getDevices(id)         : portalApi.subCustomers.getDevices(id),
  assignDevices: (id, ph)  => isAdmin() ? customerApi.assignDevices(id, ph) : portalApi.subCustomers.assignDevices(id, ph),
}

const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const keyword = ref('')
const statusFilter = ref('')

// 管理员模式：无过滤条件时用树形懒加载，有关键词/状态过滤时退化为平铺搜索
const isTreeMode = computed(() => admin && !keyword.value && !statusFilter.value)

const modalVisible = ref(false)
const defaultForm = () => ({
  id: null, name: '', contact: '', phone: '', email: '',
  status: '活跃', reg_date: '', remark: '', login_name: '', password: ''
})
const form = ref(defaultForm())

// 分配设备
const assignVisible  = ref(false)
const assignTarget   = ref(null)
const allDevices     = ref([])
const assignedPhones = ref([])
const assignSaving   = ref(false)

// 给行附加设备数量（管理员模式）
async function fillDeviceCount(rows) {
  if (!admin) return
  await Promise.all(rows.map(async r => {
    try {
      const dr = await subApi.getDevices(r.id)
      r.device_count = (dr.data || []).length
    } catch { r.device_count = 0 }
  }))
}

async function load() {
  loading.value = true
  try {
    const params = {
      page: page.value, size: pageSize.value,
      keyword: keyword.value, status: statusFilter.value,
    }
    // 树形模式只加载顶级客户，搜索模式加载全量
    if (isTreeMode.value) params.parent_id = 'null'

    const res = await subApi.list(params)
    const rows = res.data?.records || []
    await fillDeviceCount(rows)
    list.value  = rows
    total.value = res.data?.total || 0
  } finally {
    loading.value = false
  }
}

// el-table 树形懒加载：展开一级时拉取其子级
async function loadChildren(row, _treeNode, resolve) {
  try {
    const res = await customerApi.list({ parent_id: row.id, size: 100 })
    const rows = res.data?.records || []
    await fillDeviceCount(rows)
    resolve(rows)
  } catch {
    resolve([])
  }
}

function openModal(row) {
  form.value = row ? { ...row, password: '' } : defaultForm()
  modalVisible.value = true
}

async function save() {
  if (!form.value.name) { ElMessage.error('客户名称不能为空'); return }
  try {
    const payload = { ...form.value }
    if (!payload.password) delete payload.password  // 编辑时不发空密码，避免覆盖原密码
    if (payload.id) {
      await subApi.update(payload.id, payload)
    } else {
      await subApi.create(payload)
    }
    ElMessage.success('保存成功')
    modalVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || '保存失败')
  }
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除客户 "${row.name}" 吗？`, '确认删除', { type: 'warning' })
  await subApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

// ── 分配设备 ──────────────────────────────────────────────────────────────────
async function openDeviceAssign(row) {
  assignTarget.value   = row
  assignedPhones.value = []
  assignVisible.value  = true
  try {
    if (isAdmin()) {
      const [devRes, assignedRes] = await Promise.all([
        deviceApi.list({ size: 500 }),
        subApi.getDevices(row.id),
      ])
      allDevices.value     = devRes.data?.records || []
      assignedPhones.value = (assignedRes.data || []).map(d => String(d.phone))
    } else {
      // 客户：用全量设备池接口（自己直属 + 所有子账号的设备都可调配）
      const poolRes = await portalApi.poolDevices()
      const pool = poolRes.data || []
      allDevices.value     = pool
      assignedPhones.value = pool
        .filter(d => Number(d.customer_id) === Number(row.id))
        .map(d => String(d.phone))
    }
  } catch (e) {
    allDevices.value = []
    ElMessage.error('加载设备列表失败，请重试')
    console.error('openDeviceAssign error:', e)
  }
}

async function saveAssign() {
  assignSaving.value = true
  try {
    await subApi.assignDevices(assignTarget.value.id, assignedPhones.value)
    ElMessage.success('分配成功')
    assignVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || '分配失败，请重试')
  } finally { assignSaving.value = false }
}

onMounted(load)
</script>
