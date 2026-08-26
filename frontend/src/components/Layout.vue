<template>
  <el-container style="height: 100vh;">
    <!-- 侧边导航 -->
    <el-aside width="210px" style="background: #ffffff; display:flex; flex-direction:column; border-right:1px solid #e6e8eb;">
      <div class="logo">
        <span v-if="platformLogo" class="logo-badge">
          <img :src="platformLogo" />
        </span>
        <span class="logo-title">{{ platformTitle }}</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        background-color="#ffffff"
        text-color="#4a5568"
        active-text-color="#409eff"
        style="flex:1; overflow-y:auto; border-right:none;"
        router
      >
        <div class="menu-group-label">监控</div>
        <el-menu-item index="/bigscreen">
          <el-icon><Monitor /></el-icon><span>大屏展示</span>
        </el-menu-item>
        <el-menu-item index="/dashboard">
          <el-icon><DataBoard /></el-icon><span>系统概览</span>
        </el-menu-item>
        <el-menu-item index="/map">
          <el-icon><MapLocation /></el-icon><span>实时地图</span>
        </el-menu-item>
        <el-menu-item index="/track">
          <el-icon><Aim /></el-icon><span>轨迹回放</span>
        </el-menu-item>
        <el-menu-item index="/fence">
          <el-icon><Position /></el-icon><span>电子围栏</span>
        </el-menu-item>
        <el-menu-item v-if="isAdmin" index="/health">
          <el-icon><FirstAidKit /></el-icon><span>健康数据</span>
        </el-menu-item>
        <el-menu-item index="/query">
          <el-icon><Search /></el-icon><span>设备查询</span>
        </el-menu-item>

        <div class="menu-group-label">管理</div>
        <el-menu-item index="/device-info">
          <el-icon><Cellphone /></el-icon>
          <span>设备信息</span>
        </el-menu-item>
        <el-menu-item index="/device-settings">
          <el-icon><Setting /></el-icon>
          <span>设备设置</span>
        </el-menu-item>
        <el-menu-item index="/role-settings">
          <el-icon><UserFilled /></el-icon>
          <span>角色设置</span>
        </el-menu-item>
        <el-menu-item index="/sims">
          <el-icon><Coin /></el-icon><span>SIM卡管理</span>
        </el-menu-item>
        <el-menu-item index="/customers">
          <el-icon><User /></el-icon>
          <span>客户管理</span>
        </el-menu-item>

        <div class="menu-group-label">系统</div>
        <el-menu-item v-if="isAdmin" index="/org">
          <el-icon><OfficeBuilding /></el-icon>
          <span>组织管理</span>
        </el-menu-item>
        <el-menu-item v-if="isAdmin" index="/module-auth">
          <el-icon><Operation /></el-icon>
          <span>模块授权</span>
        </el-menu-item>
        <el-menu-item index="/platform-setting">
          <el-icon><Tools /></el-icon>
          <span>平台设置</span>
        </el-menu-item>

        <div class="menu-group-label">运营</div>
        <el-menu-item index="/alarms">
          <el-icon><Bell /></el-icon>
          <span>报警管理</span>
          <el-badge v-if="alarmCount > 0" :value="alarmCount" class="alarm-badge" />
        </el-menu-item>
        <el-menu-item v-if="isAdmin" index="/alarm-setting">
          <el-icon><SetUp /></el-icon><span>报警设置</span>
        </el-menu-item>
        <el-menu-item v-if="isAdmin" index="/attendance">
          <el-icon><Calendar /></el-icon><span>考勤统计</span>
        </el-menu-item>
        <el-menu-item index="/recharges">
          <el-icon><WalletFilled /></el-icon><span>充值管理</span>
        </el-menu-item>
        <el-menu-item index="/reports">
          <el-icon><TrendCharts /></el-icon><span>报表统计</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- 主内容区 -->
    <el-container>
      <el-header style="background:#fff; border-bottom:1px solid #eee; display:flex; align-items:center; justify-content:space-between; padding:0 20px;">
        <span style="font-size:16px; font-weight:500;">{{ currentTitle }}</span>
        <div style="display:flex; align-items:center; gap:12px;">
          <el-tag type="success">在线: {{ onlineCount }}</el-tag>
          <el-tag type="danger" v-if="alarmCount > 0">未处理报警: {{ alarmCount }}</el-tag>
          <el-divider direction="vertical" />
          <el-tag v-if="!isAdmin" type="warning" size="small">客户</el-tag>
          <el-dropdown @command="handleCmd" trigger="click">
            <span style="font-size:13px;color:#606266;cursor:pointer;display:flex;align-items:center;gap:4px">
              {{ displayName }}<el-icon style="font-size:12px"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="changePwd">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided style="color:#f56c6c">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main style="background:#f5f7fa; overflow:auto;">
        <router-view />
      </el-main>
    </el-container>
  </el-container>

  <!-- 修改密码弹窗 -->
  <el-dialog v-model="pwdDialog.visible" title="修改密码" width="400px" :close-on-click-modal="false">
    <el-form :model="pwdDialog" label-width="90px">
      <el-form-item label="原密码" required>
        <el-input v-model="pwdDialog.oldPassword" type="password" show-password placeholder="请输入当前密码" />
      </el-form-item>
      <el-form-item label="新密码" required>
        <el-input v-model="pwdDialog.newPassword" type="password" show-password placeholder="至少 6 位" />
      </el-form-item>
      <el-form-item label="确认密码" required>
        <el-input v-model="pwdDialog.confirm" type="password" show-password />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="pwdDialog.visible = false">取消</el-button>
      <el-button type="primary" :loading="pwdDialog.saving" @click="doChangePwd">确认修改</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { deviceApi, alarmApi, portalApi, authApi, platformApi, isAdmin as checkAdmin } from '@/api'
import {
  DataBoard, MapLocation, Aim, Position, Search,
  Cellphone, Coin, User, Setting, UserFilled,
  Bell, WalletFilled, TrendCharts, Monitor,
  OfficeBuilding, Operation, ArrowDown,
  Tools, SetUp, Calendar, FirstAidKit
} from '@element-plus/icons-vue'

const route       = useRoute()
const router      = useRouter()
const onlineCount = ref(0)
const alarmCount  = ref(0)

// 白标：平台标题 / Logo（从平台设置读取）
const platformTitle = ref('🛰 应急物资管理系统')
const platformLogo  = ref('')

async function loadPlatform() {
  try {
    const res = await platformApi.get()
    const d = res.data || {}
    if (d.account_title) platformTitle.value = d.account_title
    if (d.logo_url) {
      platformLogo.value = /^https?:\/\//.test(d.logo_url)
        ? d.logo_url : (window.location.origin + d.logo_url)
    }
  } catch {}
}

const isAdmin = computed(() => checkAdmin())

const displayName = computed(() => {
  if (isAdmin.value) return localStorage.getItem('admin_username') || '管理员'
  try { return JSON.parse(localStorage.getItem('customer_info') || '{}').name || '客户' } catch { return '客户' }
})

function logout() {
  localStorage.removeItem('admin_token')
  localStorage.removeItem('admin_username')
  localStorage.removeItem('customer_token')
  localStorage.removeItem('customer_info')
  localStorage.removeItem('user_role')
  router.push('/login')
}

// ── 下拉菜单指令分发 ────────────────────────────────────────────────────────
function handleCmd(cmd) {
  if (cmd === 'logout')    logout()
  if (cmd === 'changePwd') pwdDialog.visible = true
}

// ── 修改密码弹窗 ─────────────────────────────────────────────────────────────
const pwdDialog = reactive({
  visible: false, saving: false,
  oldPassword: '', newPassword: '', confirm: ''
})

async function doChangePwd() {
  if (!pwdDialog.oldPassword || !pwdDialog.newPassword) {
    ElMessage.warning('请填写原密码和新密码'); return
  }
  if (pwdDialog.newPassword.length < 6) {
    ElMessage.warning('新密码不能少于 6 位'); return
  }
  if (pwdDialog.newPassword !== pwdDialog.confirm) {
    ElMessage.warning('两次输入的新密码不一致'); return
  }
  pwdDialog.saving = true
  try {
    await authApi.changePassword({
      oldPassword: pwdDialog.oldPassword,
      newPassword: pwdDialog.newPassword
    })
    ElMessage.success('密码修改成功，请重新登录')
    pwdDialog.visible = false
    // 修改密码后注销，让用户重新登录
    setTimeout(logout, 1200)
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || '修改失败，请检查原密码是否正确')
  } finally { pwdDialog.saving = false }
}

const activeMenu   = computed(() => route.path)
const currentTitle = computed(() => route.meta.title || '')

let timer

async function refreshStats() {
  try {
    const res = isAdmin.value ? await deviceApi.summary() : await portalApi.summary()
    onlineCount.value = (res.data?.online ?? 0) + (res.data?.alarm ?? 0)
  } catch {}
  try {
    const res = isAdmin.value
      ? await alarmApi.list({ status: 0, size: 1 })
      : await portalApi.alarms({ status: 0, size: 1 })
    alarmCount.value = res.data?.total ?? 0
  } catch {}
}

onMounted(() => {
  loadPlatform()
  refreshStats()
  timer = setInterval(refreshStats, 30000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #1d2f45;
  font-size: 15px;
  font-weight: 600;
  border-bottom: 1px solid #e6e8eb;
  flex-shrink: 0;
  padding: 0 10px;
}
/* 浅色侧边栏下 Logo 白底天然融入，无需卡片 */
.logo-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 34px;
  flex-shrink: 0;
}
.logo-badge img {
  height: 30px;
  max-width: 100px;
  object-fit: contain;
  display: block;
}
.logo-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #1d2f45;
}
.menu-group-label {
  padding: 12px 20px 4px;
  font-size: 11px;
  color: #909399;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.alarm-badge {
  margin-left: 8px;
}

/* 浅色侧边栏：菜单项悬停/选中态 */
:deep(.el-menu-item:hover) {
  background-color: #f0f2f5 !important;
}
:deep(.el-menu-item.is-active) {
  background-color: #ecf5ff !important;
  font-weight: 600;
}
/* 选中项左侧蓝色高亮条 */
:deep(.el-menu-item.is-active)::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 3px;
  background: #409eff;
}
</style>
