<template>
  <div class="module-auth-page">
    <el-row :gutter="16" style="height:100%">

      <!-- 左：下级组织列表 -->
      <el-col :span="7">
        <el-card shadow="never" style="height:100%">
          <template #header>
            <div style="font-weight:600">选择下级组织</div>
            <div style="font-size:12px;color:#909399;margin-top:4px">
              选中组织后，在右侧配置其可用功能模块
            </div>
          </template>

          <el-input
            v-model="orgSearch"
            placeholder="搜索组织名称"
            :prefix-icon="Search"
            clearable
            size="small"
            style="margin-bottom:10px"
          />

          <div class="org-list" v-loading="orgLoading">
            <div
              v-for="org in filteredOrgs"
              :key="org.id"
              class="org-item"
              :class="{ active: selectedOrg?.id === org.id }"
              @click="selectOrg(org)"
            >
              <el-tag size="small" :type="levelTagType(org.orgLevel)" style="margin-right:8px">
                L{{ org.orgLevel }}
              </el-tag>
              <span class="org-name">{{ org.orgName }}</span>
              <el-tag v-if="!org.isActive" type="danger" size="small" style="margin-left:auto">停用</el-tag>
            </div>
            <el-empty v-if="filteredOrgs.length === 0 && !orgLoading"
              description="暂无下级组织" :image-size="60" />
          </div>
        </el-card>
      </el-col>

      <!-- 右：模块权限配置 -->
      <el-col :span="17">
        <el-card shadow="never" v-if="selectedOrg" style="height:100%">
          <template #header>
            <div style="display:flex;align-items:center;justify-content:space-between">
              <div>
                <span style="font-weight:600">{{ selectedOrg.orgName }}</span>
                <span style="color:#909399;font-size:13px;margin-left:8px">的功能模块权限配置</span>
              </div>
              <div style="display:flex;gap:8px">
                <el-button size="small" @click="selectAll(true)">全选</el-button>
                <el-button size="small" @click="selectAll(false)">全不选</el-button>
                <el-button type="primary" size="small" :loading="saving" @click="saveAuth">
                  保存配置
                </el-button>
              </div>
            </div>
          </template>

          <div class="hint-bar">
            <el-icon color="#909399"><InfoFilled /></el-icon>
            <span>灰色模块表示本级组织未被授权，无法向下开放；橙色表示有子模块需要先开启父模块</span>
          </div>

          <div class="module-tree" v-loading="moduleLoading">
            <div v-for="group in moduleTree" :key="group.moduleCode" class="module-group">
              <!-- 一级模块（父节点） -->
              <div class="module-row parent-row" :class="{ disabled: !group.parentEnabled }">
                <el-checkbox
                  v-model="group.enabled"
                  :disabled="!group.parentEnabled"
                  :indeterminate="group.indeterminate"
                  @change="(v) => handleParentChange(group, v)"
                >
                  <span class="module-label">
                    <el-icon style="margin-right:4px"><component :is="groupIcon(group.moduleCode)" /></el-icon>
                    {{ group.moduleName }}
                  </span>
                </el-checkbox>
                <el-tag v-if="!group.parentEnabled" type="info" size="small">上级未授权</el-tag>
              </div>

              <!-- 二级子模块 -->
              <div
                v-for="child in group.children"
                :key="child.moduleCode"
                class="module-row child-row"
                :class="{ disabled: !child.parentEnabled || !group.enabled }"
              >
                <el-checkbox
                  v-model="child.enabled"
                  :disabled="!child.parentEnabled || !group.enabled"
                  @change="() => updateIndeterminate(group)"
                >
                  <span class="module-label">{{ child.moduleName }}</span>
                </el-checkbox>
                <el-tag v-if="!child.parentEnabled" type="info" size="small">上级未授权</el-tag>
              </div>
            </div>

            <el-empty v-if="moduleTree.length === 0 && !moduleLoading"
              description="暂无可配置的模块" />
          </div>
        </el-card>

        <div v-else class="empty-hint">
          <el-empty description="请在左侧选择一个下级组织，然后配置其可用功能模块" />
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { InfoFilled, Goods, Box, MapLocation, Bell, TrendCharts, Setting, Search } from '@element-plus/icons-vue'
import { orgApi, moduleApi } from '@/api'

// ── 左侧：组织列表 ───────────────────────────────────────────────────────────
const orgLoading  = ref(false)
const childOrgs   = ref([])
const orgSearch   = ref('')
const selectedOrg = ref(null)

const filteredOrgs = computed(() => {
  const q = orgSearch.value.trim()
  if (!q) return childOrgs.value
  return childOrgs.value.filter(o => o.orgName?.includes(q))
})

function levelTagType(level) {
  return ['', 'danger', 'warning', '', 'info', ''][level] ?? ''
}

/** 将嵌套树扁平化为数组（保留缩进层级信息） */
function flattenTree(nodes, result = []) {
  for (const node of nodes) {
    result.push(node)
    if (node.children?.length) flattenTree(node.children, result)
  }
  return result
}

async function loadChildOrgs() {
  orgLoading.value = true
  try {
    const res = await orgApi.tree()          // 获取完整组织树，扁平化后显示
    childOrgs.value = flattenTree(res.data ?? [])
  } finally {
    orgLoading.value = false
  }
}

async function selectOrg(org) {
  selectedOrg.value = org
  await loadModules(org.id)
}

// ── 右侧：模块权限树 ─────────────────────────────────────────────────────────
const moduleLoading = ref(false)
const moduleTree    = ref([])  // [{moduleCode, moduleName, enabled, parentEnabled, indeterminate, children:[...]}]
const saving        = ref(false)

// 图标映射
const ICON_MAP = {
  ASSET: Goods, WH: Box, FENCE: MapLocation, ALERT: Bell,
  TRACK: MapLocation, MAP: MapLocation, REPORT: TrendCharts, SYS: Setting,
}
function groupIcon(code) {
  return ICON_MAP[code] ?? Setting
}

async function loadModules(orgId) {
  moduleLoading.value = true
  try {
    // 并行拉取：模块树结构 + 该组织的已授权编码
    const [treeRes, authRes] = await Promise.all([
      moduleApi.tree(),
      moduleApi.getOrgAuth(orgId),
    ])
    const enabledSet = new Set(authRes.data?.enabledCodes ?? [])
    const rawTree    = treeRes.data ?? []

    moduleTree.value = rawTree.map(parent => {
      const pEnabled  = true                     // 简化：顶层模块始终可配置
      const pChecked  = enabledSet.has(parent.moduleCode)
      const children  = (parent.children ?? []).map(c => ({
        moduleCode:    c.moduleCode,
        moduleName:    c.moduleName,
        enabled:       enabledSet.has(c.moduleCode),
        parentEnabled: pEnabled,
      }))
      const enabledCount = children.filter(c => c.enabled).length
      return {
        moduleCode:    parent.moduleCode,
        moduleName:    parent.moduleName,
        enabled:       pChecked,
        parentEnabled: pEnabled,
        indeterminate: !pChecked && enabledCount > 0,
        children,
      }
    })
  } finally {
    moduleLoading.value = false
  }
}

function handleParentChange(group, checked) {
  if (!checked) {
    // 取消父模块 → 同步取消所有子模块
    group.children.forEach(c => { c.enabled = false })
  }
  group.indeterminate = false
}

function updateIndeterminate(group) {
  const enabledCount = group.children.filter(c => c.enabled).length
  group.indeterminate = enabledCount > 0 && enabledCount < group.children.length
  // 子模块全取消 → 自动取消父模块
  if (enabledCount === 0) group.enabled = false
  // 子模块有选中 → 自动勾选父模块
  if (enabledCount > 0) group.enabled = true
}

function selectAll(val) {
  moduleTree.value.forEach(group => {
    if (!group.parentEnabled) return   // 上级没授权的不能强开
    group.enabled = val
    group.indeterminate = false
    group.children.forEach(c => {
      if (!c.parentEnabled) return
      c.enabled = val
    })
  })
}

async function saveAuth() {
  if (!selectedOrg.value) return

  // 收集已勾选的 moduleCode 列表
  const enabledCodes = []
  moduleTree.value.forEach(group => {
    if (group.enabled) enabledCodes.push(group.moduleCode)
    group.children.forEach(c => {
      if (c.enabled) enabledCodes.push(c.moduleCode)
    })
  })

  // 如果有被撤销的模块（相比之前），提示级联影响
  const hadEnabled = moduleTree.value.flatMap(g => {
    const all = g.children.map(c => c.moduleCode)
    all.push(g.moduleCode)
    return all
  })
  const willRevoke = hadEnabled.filter(code => {
    const group = moduleTree.value.find(g => g.moduleCode === code)
    if (group) return !group.enabled
    for (const g of moduleTree.value) {
      const child = g.children.find(c => c.moduleCode === code)
      if (child) return !child.enabled
    }
    return false
  })

  try {
    saving.value = true
    await moduleApi.saveOrgAuth(selectedOrg.value.id, { enabledCodes })
    ElMessage.success('模块权限配置已保存')
  } catch (e) {
    const err = e?.response?.data
    if (err?.code === 'MODULE_OVER_GRANT') {
      ElMessage.error(`超授权：${err.msg}`)
    } else {
      ElMessage.error(err?.msg || '保存失败')
    }
  } finally {
    saving.value = false
  }
}

onMounted(loadChildOrgs)
</script>

<style scoped>
.module-auth-page {
  height: calc(100vh - 100px);
}

/* 左侧组织列表 */
.org-list {
  max-height: calc(100vh - 220px);
  overflow-y: auto;
}
.org-item {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 0.15s;
}
.org-item:hover { background: #f0f7ff; }
.org-item.active { background: #e6f0ff; border-left: 3px solid #409eff; }
.org-name {
  font-size: 14px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 右侧模块树 */
.hint-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  background: #f5f7fa;
  border-radius: 6px;
  padding: 8px 14px;
  margin-bottom: 16px;
  font-size: 12px;
  color: #606266;
}

.module-tree {
  max-height: calc(100vh - 240px);
  overflow-y: auto;
  padding-right: 4px;
}

.module-group {
  margin-bottom: 4px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  overflow: hidden;
}

.module-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  transition: background 0.15s;
}
.module-row:hover { background: #fafafa; }

.parent-row {
  background: #f5f7fa;
  font-weight: 500;
  border-bottom: 1px solid #ebeef5;
}
.parent-row:last-child { border-bottom: none; }

.child-row {
  padding-left: 36px;
  border-top: 1px solid #f0f0f0;
}
.child-row:first-of-type { border-top: none; }

.module-row.disabled {
  opacity: 0.5;
}
.module-label {
  display: flex;
  align-items: center;
  font-size: 14px;
}

.empty-hint {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
