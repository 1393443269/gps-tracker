<template>
  <el-card>
    <div style="margin-bottom:14px;">
      <el-button type="primary" :icon="Plus" @click="openCreate">添加报警规则</el-button>
    </div>

    <el-table :data="list" v-loading="loading" stripe border size="small">
      <el-table-column type="index" label="#" width="50" />
      <el-table-column prop="alarm_type_name" label="报警类型" min-width="130" />
      <el-table-column label="报警级别" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.level === '紧急级别' ? 'danger' : 'info'">
            {{ row.level }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="报警状态" width="90" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
            {{ row.enabled ? '启用' : '关闭' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="报警通知" width="150">
        <template #default="{ row }">
          <el-tag v-if="row.notify_page" size="small" style="margin-right:4px;">页面推送</el-tag>
          <el-tag v-if="row.notify_sms" size="small" type="warning">短信推送</el-tag>
          <span v-if="!row.notify_page && !row.notify_sms" style="color:#ccc;font-size:12px;">无</span>
        </template>
      </el-table-column>
      <el-table-column prop="ring_type" label="响铃类型" width="100" align="center" />
      <el-table-column prop="created_at" label="创建时间" width="165" />
      <el-table-column label="操作" fixed="right" width="120" align="center">
        <template #default="{ row }">
          <el-button size="small" :icon="EditIcon" circle @click="openEdit(row)" />
          <el-button size="small" :icon="Delete" circle type="danger" @click="doDelete(row)" />
        </template>
      </el-table-column>
    </el-table>

    <!-- 添加/编辑弹窗 -->
    <el-dialog v-model="formVisible" :title="isEdit ? '编辑报警规则' : '添加报警'" width="420px">
      <el-form :model="form" label-width="80px" style="padding-right:16px;">
        <el-form-item label="报警类型" required>
          <el-select v-model="form.alarm_type" placeholder="请选择" style="width:100%">
            <el-option v-for="t in types" :key="t.type" :label="t.name" :value="t.type" />
          </el-select>
        </el-form-item>
        <el-form-item label="报警级别">
          <el-select v-model="form.level" style="width:100%">
            <el-option label="普通级别" value="普通级别" />
            <el-option label="紧急级别" value="紧急级别" />
          </el-select>
        </el-form-item>
        <el-form-item label="报警状态">
          <el-switch v-model="form.enabled" />
          <span style="margin-left:10px;font-size:12px;color:#909399;">关闭后报警失效</span>
        </el-form-item>
        <el-form-item label="报警通知">
          <el-checkbox v-model="form.notify_page">页面推送</el-checkbox>
          <el-checkbox v-model="form.notify_sms">短信推送</el-checkbox>
        </el-form-item>
        <el-form-item label="响铃类型">
          <el-radio-group v-model="form.ring_type">
            <el-radio-button label="一直响" />
            <el-radio-button label="响几声" />
            <el-radio-button label="不响" />
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" @click="submitForm" :loading="saving">确定</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Plus, Edit as EditIcon, Delete } from '@element-plus/icons-vue'
import { alarmRuleApi } from '@/api'
import { ElMessage, ElMessageBox } from 'element-plus'

const list    = ref([])
const types   = ref([])
const loading = ref(false)

const formVisible = ref(false)
const saving      = ref(false)
const isEdit      = ref(false)
const form = reactive({
  id: null, alarm_type: null, level: '普通级别',
  enabled: true, notify_page: true, notify_sms: false, ring_type: '响几声'
})

async function loadTypes() {
  try {
    const res = await alarmRuleApi.types()
    types.value = res.data || []
  } catch {}
}

async function loadList() {
  loading.value = true
  try {
    const res = await alarmRuleApi.list()
    list.value = res.data?.records || []
  } finally {
    loading.value = false
  }
}

function openCreate() {
  isEdit.value = false
  Object.assign(form, {
    id: null, alarm_type: types.value[0]?.type ?? null, level: '普通级别',
    enabled: true, notify_page: true, notify_sms: false, ring_type: '响几声'
  })
  formVisible.value = true
}

function openEdit(row) {
  isEdit.value = true
  Object.assign(form, {
    id:          row.id,
    alarm_type:  row.alarm_type,
    level:       row.level,
    enabled:     !!row.enabled,
    notify_page: !!row.notify_page,
    notify_sms:  !!row.notify_sms,
    ring_type:   row.ring_type,
  })
  formVisible.value = true
}

async function submitForm() {
  if (form.alarm_type === null) return ElMessage.warning('请选择报警类型')
  saving.value = true
  try {
    const payload = {
      alarm_type: form.alarm_type, level: form.level, enabled: form.enabled,
      notify_page: form.notify_page, notify_sms: form.notify_sms, ring_type: form.ring_type,
    }
    if (isEdit.value) await alarmRuleApi.update(form.id, payload)
    else              await alarmRuleApi.create(payload)
    ElMessage.success('保存成功')
    formVisible.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function doDelete(row) {
  try {
    await ElMessageBox.confirm(`删除「${row.alarm_type_name}」的报警规则？`, '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  await alarmRuleApi.remove(row.id)
  ElMessage.success('已删除')
  loadList()
}

onMounted(() => { loadTypes(); loadList() })
</script>
