import { createRouter, createWebHistory } from 'vue-router'
import Layout from '@/components/Layout.vue'

// 统一鉴权：管理员 token 或客户 token 都可以进入后台 Layout
const authGuard = (to, from, next) => {
  const hasAdmin    = !!localStorage.getItem('admin_token')
  const hasCustomer = !!localStorage.getItem('customer_token')
  if (!hasAdmin && !hasCustomer) { next('/login'); return }

  // roles 数组控制访问权限
  // roles: ['admin']    → 仅管理员
  // roles: ['customer'] → 仅客户
  // 不设 roles          → 两者均可访问
  // 账号菜单权限:非超管账号,访问未授权菜单页时重定向到概览
  try {
    const isSuper = localStorage.getItem('is_super') === '1'
    const mk = JSON.parse(localStorage.getItem('menu_keys') || 'null')
    const pageKey = (to.path || '').replace(/^\//, '')
    if (hasAdmin && !isSuper && Array.isArray(mk) && pageKey && pageKey !== 'dashboard') {
      if (!mk.includes(pageKey)) { next('/dashboard'); return }
    }
  } catch (e) {}
  const roles = to.meta.roles
  if (roles) {
    if (roles.includes('admin') && !hasAdmin)    { next('/dashboard'); return }
    if (roles.includes('customer') && !hasCustomer) { next('/dashboard'); return }
  }

  next()
}

const routes = [
  // 统一登录页（管理员 + 客户）
  { path: '/login', component: () => import('@/views/Login.vue'), meta: { title: '登录' } },

  // 老客户端 URL 兼容重定向
  { path: '/customer-login',  redirect: '/login' },
  { path: '/customer-portal', redirect: '/dashboard' },

  {
    path: '/',
    component: Layout,
    redirect: '/dashboard',
    beforeEnter: authGuard,
    children: [
      { path: 'dashboard',       component: () => import('@/views/Dashboard.vue'),       meta: { title: '系统概览' } },
      { path: 'devices',         component: () => import('@/views/DeviceList.vue'),      meta: { title: '设备管理' } },
      { path: 'device-info',     component: () => import('@/views/DeviceInfo.vue'),      meta: { title: '设备信息' } },
      { path: 'device-settings', component: () => import('@/views/DeviceSettings.vue'), meta: { title: '设备设置' } },
      { path: 'role-settings',   component: () => import('@/views/RoleSettings.vue'),   meta: { title: '角色设置' } },
      { path: 'map',             component: () => import('@/views/RealtimeMap.vue'),     meta: { title: '实时地图' } },
      { path: 'alarms',          component: () => import('@/views/AlarmList.vue'),       meta: { title: '报警管理' } },
      { path: 'alarm-setting',   component: () => import('@/views/AlarmSetting.vue'),    meta: { title: '报警设置',  roles: ['admin'] } },
      { path: 'attendance',      component: () => import('@/views/Attendance.vue'),      meta: { title: '考勤统计' } },
      { path: 'health',          component: () => import('@/views/HealthData.vue'),      meta: { title: '健康数据' } },
      { path: 'fence',           component: () => import('@/views/GeoFence.vue'),        meta: { title: '电子围栏' } },
      { path: 'query',           component: () => import('@/views/DeviceQuery.vue'),     meta: { title: '设备查询' } },
      { path: 'track',           component: () => import('@/views/TrackReplay.vue'),     meta: { title: '轨迹回放' } },
      { path: 'customers',       component: () => import('@/views/Customer.vue'),        meta: { title: '客户管理' } },
      { path: 'reports',         component: () => import('@/views/Reports.vue'),         meta: { title: '报表统计' } },
      { path: 'bigscreen',       component: () => import('@/views/BigScreen.vue'),       meta: { title: '大屏展示' } },
      { path: 'org',             component: () => import('@/views/OrgManage.vue'),       meta: { title: '组织管理',  roles: ['admin'] } },
      { path: 'module-auth',     component: () => import('@/views/ModuleAuth.vue'),      meta: { title: '模块授权',  roles: ['admin'] } },
      { path: 'platform-setting',component: () => import('@/views/PlatformSetting.vue'),meta: { title: '平台设置' } },
      { path: 'account-permission',component: () => import('@/views/AccountPermission.vue'),meta: { title: '账号权限', roles: ['admin'] } },
      { path: 'sims',            component: () => import('@/views/SimCard.vue'),         meta: { title: 'SIM卡管理' } },
      { path: 'recharges',       component: () => import('@/views/Recharge.vue'),        meta: { title: '充值管理' } },
    ]
  },
]

export default createRouter({
  history: createWebHistory(),
  routes
})
