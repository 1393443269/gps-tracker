<template>
  <div class="org-manage">
    <el-row :gutter="16" style="height:100%">

      <!-- 左：组织树 -->
      <el-col :span="7">
        <el-card shadow="never" style="height:100%">
          <template #header>
            <div style="display:flex;align-items:center;justify-content:space-between">
              <span>组织架构</span>
              <el-button size="small" type="primary" @click="openCreateDialog(null)">+ 新建</el-button>
            </div>
          </template>

          <el-tree
            ref="treeRef"
            :data="treeData"
            :props="{ label: 'orgName', children: 'children' }"
            node-key="id"
            highlight-current
            :expand-on-click-node="false"
            default-expand-all
            @node-click="handleNodeClick"
          >
            <template #default="{ node, data }">
              <div class="tree-node">
                <el-tag size="small" :type="levelTagType(data.orgLevel)" style="margin-right:6px">
                  L{{ data.orgLevel }}
                </el-tag>
                <span>{{ data.orgName }}</span>
                <div class="node-actions">
                  <el-icon v-if="data.orgLevel < 5" style="cursor:pointer;color:#409eff"
                    title="新建下级" @click.stop="openCreateDialog(data)"><Plus /></el-icon>
                  <el-icon style="cursor:pointer;color:#e6a23c"
                    title="编辑" @click.stop="openEditDialog(data)"><Edit /></el-icon>
                  <el-icon v-if="data.id !== 1" style="cursor:pointer;color:#f56c6c"
                    title="删除" @click.stop="confirmDeleteOrg(data)"><Delete /></el-icon>
                </div>
              </div>
            </template>
          </el-tree>
        </el-card>
      </el-col>

      <!-- 右：用户列表 -->
      <el-col :span="17">
        <el-card shadow="never" v-if="selected" style="height:100%">
          <template #header>
            <div style="display:flex;align-items:center;gap:10px">
              <el-tag :type="levelTagType(selected.orgLevel)">L{{ selected.orgLevel }}</el-tag>
              <span style="font-weight:600">{{ selected.orgName }}</span>
              <el-tag size="small" type="info">{{ selected.orgCode }}</el-tag>
              <el-tag size="small" :type="selected.isActive ? 'success' : 'danger'">
                {{ selected.isActive ? '启用' : '停用' }}
              </el-tag>
              <div style="margin-left:auto">
                <el-button size="small" type="primary" @click="openUserDialog()">+ 新建用户</el-button>
              </div>
            </div>
          </template>

          <el-table :data="users" v-loading="userLoading" border size="small">
            <el-table-column prop="username"  label="用户名"   width="120" />
            <el-table-column prop="realName"  label="姓名"     width="100" />
            <el-table-column prop="phone"     label="手机号"   width="130" />
            <el-table-column label="类型" width="110">
              <template #default="{ row }">
                <el-tag :type="row.userType === 9 ? 'danger' : 'primary'" size="small">
                  {{ row.userType === 9 ? '超级管理员' : '普通管理员' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.isActive ? 'success' : 'info'" size="small">
                  {{ row.isActive ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="lastLogin" label="最后登录" min-width="140" />
            <el-table-column label="操作" width="200" fixed="right">
              <template #default="{ row }">
                <el-button size="small" @click="openEditUser(row)">编辑</el-button>
                <el-button size="small" type="warning" @click="openResetPwd(row)">重置密码</el-button>
                <el-button size="small" type="danger"
                  :disabled="row.userType === 9 && row.username === 'admin'"
                  @click="confirmDeleteUser(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-empty v-else description="请在左侧选择组织" />
      </el-col>
    </el-row>

    <!-- 新建/编辑组织弹窗 -->
    <el-dialog v-model="orgDialog.visible"
      :title="orgDialog.isEdit ? '编辑组织' : `新建下级组织（上级：${orgDialog.parentName}）`"
      width="460px">
      <el-form :model="orgDialog.form" label-width="90px">
        <el-form-item label="组织名称" required>
          <el-input v-model="orgDialog.form.orgName" />
        </el-form-item>
        <el-form-item label="组织编码" required>
          <el-input v-model="orgDialog.form.orgCode" :disabled="orgDialog.isEdit"
            placeholder="唯一标识，创建后不可改" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="orgDialog.form.sortOrder" :min="0" controls-position="right" />
        </el-form-item>
        <el-form-item label="状态" v-if="orgDialog.isEdit">
          <el-switch v-model="orgDialog.form.isActive" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="orgDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="orgDialog.saving" @click="saveOrg">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新建用户弹窗 -->
    <el-dialog v-model="userDialog.visible" title="新建用户" width="440px">
      <el-form :model="userDialog.form" label-width="90px">
        <el-form-item label="用户名" required>
          <el-input v-model="userDialog.form.username" />
        </el-form-item>
        <el-form-item label="初始密码" required>
          <el-input v-model="userDialog.form.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="userDialog.form.realName" />
        </el-form-item>
        <el-form-item label="手机号">
          <el-input v-model="userDialog.form.phone" />
        </el-form-item>
        <el-form-item label="用户类型">
          <el-radio-group v-model="userDialog.form.userType">
            <el-radio :value="1">普通管理员</el-radio>
            <el-radio :value="9">超级管理员</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="userDialog.saving" @click="saveUser">创建</el-button>
      </template>
    </el-dialog>

    <!-- 编辑用户弹窗 -->
    <el-dialog v-model="editUserDialog.visible" title="编辑用户" width="440px">
      <el-form :model="editUserDialog.form" label-width="90px">
        <el-form-item label="用户名">
          <el-input :value="editUserDialog.form.username" disabled />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="editUserDialog.form.realName" />
        </el-form-item>
        <el-form-item label="手机号">
          <el-input v-model="editUserDialog.form.phone" />
        </el-form-item>
        <el-form-item label="账号状态">
          <el-switch v-model="editUserDialog.form.isActive"
            active-text="启用" inactive-text="停用"
            :disabled="editUserDialog.form.userType === 9" />
          <span v-if="editUserDialog.form.userType === 9"
            style="font-size:12px;color:#909399;margin-left:8px">超管不可停用</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editUserDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="editUserDialog.saving" @click="saveEditUser">保存</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码弹窗 -->
    <el-dialog v-model="resetPwdDialog.visible"
      :title="`重置密码 — ${resetPwdDialog.username}`" width="400px">
      <el-form label-width="90px">
        <el-form-item label="新密码" required>
          <el-input v-model="resetPwdDialog.newPassword" type="password" show-password
            placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="确认密码" required>
          <el-input v-model="resetPwdDialog.confirm" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetPwdDialog.visible = false">取消</el-button>
        <el-button type="warning" :loading="resetPwdDialog.saving" @click="doResetPwd">确认重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Edit, Delete } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { orgApi, userApi } from '@/api'

// ── 组织树 ──────────────────────────────────────────────────────────────────
const treeRef  = ref()
const treeData = ref([])
const selected = ref(null)
const users       = ref([])
const userLoading = ref(false)

async function loadTree() {
  const res = await orgApi.tree()
  treeData.value = res.data ?? []
}

async function handleNodeClick(data) {
  selected.value = data
  userLoading.value = true
  try {
    const res = await userApi.listByOrg(data.id)
    users.value = res.data ?? []
  } finally { userLoading.value = false }
}

function levelTagType(level) {
  return ['', 'danger', 'warning', '', 'info', ''][level] ?? ''
}

// ── 组织弹窗 ────────────────────────────────────────────────────────────────
const orgDialog = reactive({
  visible: false, isEdit: false, saving: false, parentName: '',
  form: { id: null, orgName: '', orgCode: '', sortOrder: 0, parentId: null, isActive: true }
})

function openCreateDialog(parent) {
  orgDialog.isEdit     = false
  orgDialog.parentName = parent ? parent.orgName : '根节点'
  orgDialog.form = { orgName: '', orgCode: '', sortOrder: 0,
    parentId: parent ? parent.id : null, isActive: true }
  orgDialog.visible = true
}

function openEditDialog(data) {
  orgDialog.isEdit = true
  orgDialog.form   = { ...data }
  orgDialog.visible = true
}

async function saveOrg() {
  if (!orgDialog.form.orgName || !orgDialog.form.orgCode) {
    ElMessage.warning('组织名称和编码为必填项'); return
  }
  orgDialog.saving = true
  try {
    if (orgDialog.isEdit) {
      await orgApi.update(orgDialog.form.id, orgDialog.form)
    } else {
      await orgApi.create(orgDialog.form)
    }
    ElMessage.success('保存成功')
    orgDialog.visible = false
    loadTree()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || '保存失败')
  } finally { orgDialog.saving = false }
}

async function confirmDeleteOrg(data) {
  // 先试普通删除；若后端返回「有子组织/用户」再询问是否级联
  try {
    await ElMessageBox.confirm(
      `确认删除组织「${data.orgName}」？`,
      '删除组织',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger' }
    )
  } catch { return }

  try {
    await orgApi.remove(data.id)
    ElMessage.success('组织已删除')
    if (selected.value?.id === data.id) selected.value = null
    loadTree()
  } catch (e) {
    const msg = e?.response?.data?.msg || ''
    // 后端告知有子组织或用户，提示级联删除
    if (msg.includes('子组织') || msg.includes('用户')) {
      try {
        await ElMessageBox.confirm(
          `${msg}\n\n是否连同所有子组织和用户一起删除？此操作不可恢复。`,
          '级联删除确认',
          { type: 'error', confirmButtonText: '级联删除', cancelButtonText: '取消',
            confirmButtonClass: 'el-button--danger' }
        )
      } catch { return }
      try {
        const res = await orgApi.removeCascade(data.id)
        const d = res.data ?? {}
        ElMessage.success(`已删除 ${d.deletedOrgs ?? 1} 个组织、${d.deletedUsers ?? 0} 名用户`)
        if (selected.value?.id === data.id) selected.value = null
        loadTree()
      } catch (e2) {
        ElMessage.error(e2?.response?.data?.msg || '级联删除失败')
      }
    } else {
      ElMessage.error(msg || '删除失败')
    }
  }
}

async function confirmDeleteUser(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除用户「${row.username}」？`,
      '删除用户',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger' }
    )
  } catch { return }
  try {
    await userApi.remove(row.id)
    ElMessage.success(`用户「${row.username}」已删除`)
    handleNodeClick(selected.value)
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || '删除失败')
  }
}

// ── 新建用户弹窗 ─────────────────────────────────────────────────────────────
const userDialog = reactive({
  visible: false, saving: false,
  form: { username: '', password: '', realName: '', phone: '', userType: 1 }
})

function openUserDialog() {
  userDialog.form = { username: '', password: '', realName: '', phone: '', userType: 1 }
  userDialog.visible = true
}

async function saveUser() {
  if (!userDialog.form.username || !userDialog.form.password) {
    ElMessage.warning('用户名和密码为必填项'); return
  }
  userDialog.saving = true
  try {
    await userApi.create({
      ...userDialog.form,
      orgId:    selected.value.id,
      orgLevel: selected.value.orgLevel
    })
    ElMessage.success('用户创建成功')
    userDialog.visible = false
    handleNodeClick(selected.value)
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || '创建失败')
  } finally { userDialog.saving = false }
}

// ── 编辑用户弹窗 ─────────────────────────────────────────────────────────────
const editUserDialog = reactive({
  visible: false, saving: false,
  form: { id: null, username: '', realName: '', phone: '', isActive: true, userType: 1 }
})

function openEditUser(row) {
  editUserDialog.form = {
    id: row.id, username: row.username, realName: row.realName ?? '',
    phone: row.phone ?? '', isActive: row.isActive, userType: row.userType
  }
  editUserDialog.visible = true
}

async function saveEditUser() {
  editUserDialog.saving = true
  try {
    await userApi.update(editUserDialog.form.id, {
      realName: editUserDialog.form.realName,
      phone:    editUserDialog.form.phone,
      isActive: editUserDialog.form.isActive
    })
    ElMessage.success('用户信息已更新')
    editUserDialog.visible = false
    handleNodeClick(selected.value)
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || '更新失败')
  } finally { editUserDialog.saving = false }
}

// ── 重置密码弹窗 ─────────────────────────────────────────────────────────────
const resetPwdDialog = reactive({
  visible: false, saving: false,
  userId: null, username: '', newPassword: '', confirm: ''
})

function openResetPwd(row) {
  resetPwdDialog.userId      = row.id
  resetPwdDialog.username    = row.username
  resetPwdDialog.newPassword = ''
  resetPwdDialog.confirm     = ''
  resetPwdDialog.visible     = true
}

async function doResetPwd() {
  if (!resetPwdDialog.newPassword || resetPwdDialog.newPassword.length < 6) {
    ElMessage.warning('密码不能少于 6 位'); return
  }
  if (resetPwdDialog.newPassword !== resetPwdDialog.confirm) {
    ElMessage.warning('两次输入的密码不一致'); return
  }
  resetPwdDialog.saving = true
  try {
    await userApi.resetPwd(resetPwdDialog.userId, { newPassword: resetPwdDialog.newPassword })
    ElMessage.success('密码已重置')
    resetPwdDialog.visible = false
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || '重置失败')
  } finally { resetPwdDialog.saving = false }
}

onMounted(loadTree)
</script>

<style scoped>
.org-manage { height: calc(100vh - 100px); }
.tree-node {
  display: flex;
  align-items: center;
  width: 100%;
}
.node-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  opacity: 0.3;
  transition: opacity 0.15s;
}
.el-tree-node__content:hover .node-actions {
  opacity: 1;
}
</style>
