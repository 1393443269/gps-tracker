<template>
  <el-card>
    <div style="display:flex;gap:16px;">
      <!-- 左：账号列表 -->
      <div style="width:280px;border-right:1px solid #eee;padding-right:12px;">
        <div style="font-size:14px;font-weight:600;margin-bottom:10px;">管理账号</div>
        <el-table :data="accounts" size="small" highlight-current-row
          @current-change="onPickAccount" style="cursor:pointer;">
          <el-table-column prop="username" label="账号" min-width="120" />
          <el-table-column label="姓名" min-width="90">
            <template #default="{ row }">{{ row.realName || '—' }}</template>
          </el-table-column>
          <el-table-column label="类型" width="80" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.isSuper" type="danger" size="small">超管</el-tag>
              <el-tag v-else size="small" type="info">普通</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 右：权限勾选 -->
      <div style="flex:1;">
        <div v-if="!current" style="color:#909399;padding:40px;text-align:center;">
          请选择左侧一个账号，配置它可见的功能菜单
        </div>
        <div v-else>
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
            <span style="font-size:15px;font-weight:600;">{{ current.username }}</span>
            <span style="color:#909399;font-size:13px;">的功能权限</span>
            <el-tag v-if="current.isSuper" type="danger" size="small">超级管理员（拥有全部权限，无需配置）</el-tag>
          </div>

          <el-alert v-if="current.isSuper" type="info" :closable="false" show-icon
            title="超级管理员始终拥有全部功能权限，此处配置不生效。" style="margin-bottom:12px;" />

          <template v-else>
            <div style="margin-bottom:10px;">
              <el-button size="small" @click="checkAll(true)">全选</el-button>
              <el-button size="small" @click="checkAll(false)">全不选</el-button>
              <span style="color:#909399;font-size:12px;margin-left:8px;">
                勾选后该账号只能看到并操作选中的功能菜单；不勾选任何项＝无任何功能。
              </span>
            </div>
            <div v-for="grp in groupedMenus" :key="grp.group" style="margin-bottom:14px;">
              <div style="font-size:13px;font-weight:600;color:#606266;margin-bottom:6px;
                          border-left:3px solid #409EFF;padding-left:8px;">{{ grp.group }}</div>
              <el-checkbox-group v-model="checked">
                <el-checkbox v-for="m in grp.items" :key="m.key" :value="m.key"
                  style="width:150px;margin-right:0;">{{ m.name }}</el-checkbox>
              </el-checkbox-group>
            </div>
            <div style="margin-top:16px;">
              <el-button type="primary" :loading="saving" @click="save">保存权限</el-button>
            </div>
          </template>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { permApi } from '@/api'
import { ElMessage } from 'element-plus'

const accounts = ref([])
const menus    = ref([])
const current  = ref(null)
const checked  = ref([])
const saving   = ref(false)

const groupedMenus = computed(() => {
  const order = ['监控', '管理', '系统', '运营']
  const map = {}
  menus.value.forEach(m => { (map[m.group] = map[m.group] || []).push(m) })
  return order.filter(g => map[g]).map(g => ({ group: g, items: map[g] }))
})

async function loadAccounts() {
  try {
    const r = await permApi.accounts()
    accounts.value = r.data || []
  } catch (e) { ElMessage.error('加载账号失败') }
}
async function loadMenus() {
  try {
    const r = await permApi.menus()
    menus.value = r.data || []
  } catch (e) { ElMessage.error('加载菜单失败') }
}
async function onPickAccount(row) {
  if (!row) return
  current.value = row
  checked.value = []
  if (row.isSuper) return
  try {
    const r = await permApi.get(row.id)
    checked.value = r.data?.menu_keys || []
  } catch (e) { checked.value = [] }
}
function checkAll(all) {
  checked.value = all ? menus.value.map(m => m.key) : []
}
async function save() {
  if (!current.value) return
  saving.value = true
  try {
    await permApi.set(current.value.id, checked.value)
    ElMessage.success('权限已保存，该账号下次登录生效')
  } catch (e) {
    ElMessage.error(e.response?.data?.msg || '保存失败')
  } finally { saving.value = false }
}

onMounted(() => { loadAccounts(); loadMenus() })
</script>
