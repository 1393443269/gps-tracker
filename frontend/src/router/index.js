import { createRouter, createWebHistory } from 'vue-router'
import Layout from '@/components/Layout.vue'

// 统一鉴权：管理员 token 或客户 token 都可以进入后台 Layout
const authGuard = (to, from, next) => {
  const hasAdmin    = !!localStorage.getItem('admin_token')
  const hasCustomer = !!localStorage.getItem('customer_token')
  if (!hasAdmin && !hasCustomer) { next('/login'); return }
  // adminOnly 路由只允许管理员
  if (to.meta.adminOnly && !hasAdmin) { next('/dashboard'); return }
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
      { path: 'dashboard', component: () => import('@/views/Dashboard.vue'),   meta: { title: '系统概览'  } },
      { path: 'devices',        component: () => import('@/views/DeviceList.vue'),     meta: { title: '设备管理'  } },
      { path: 'device-info',    component: () => import('@/views/DeviceInfo.vue'),     meta: { title: '设备信息', adminOnly: true } },
      { path: 'device-settings',component: () => import('@/views/DeviceSettings.vue'), meta: { title: '设备设置', adminOnly: true } },
      { path: 'role-settings',  component: () => import('@/views/RoleSettings.vue'),   meta: { title: '角色设置', adminOnly: true } },
      { path: 'map',       component: () => import('@/views/RealtimeMap.vue'), meta: { title: '实时地图'  } },
      { path: 'alarms',        component: () => import('@/views/AlarmList.vue'),    meta: { title: '报警管理'  } },
      { path: 'alarm-setting', component: () => import('@/views/AlarmSetting.vue'), meta: { title: '报警设置', adminOnly: true } },
      { path: 'attendance',    component: () => import('@/views/Attendance.vue'),   meta: { title: '考勤统计', adminOnly: true } },
      { path: 'health',        component: () => import('@/views/HealthData.vue'),   meta: { title: '健康数据', adminOnly: true } },
      { path: 'fence',     component: () => import('@/views/GeoFence.vue'),    meta: { title: '电子围栏'  } },
      { path: 'query',     component: () => import('@/views/DeviceQuery.vue'), meta: { title: '设备查询'  } },
      { path: 'track',     component: () => import('@/views/TrackReplay.vue'), meta: { title: '轨迹回放'  } },
      { path: 'customers', component: () => import('@/views/Customer.vue'),    meta: { title: '客户管理'  } },
      { path: 'reports',   component: () => import('@/views/Reports.vue'),     meta: { title: '报表统计'  } },
      { path: 'bigscreen',    component: () => import('@/views/BigScreen.vue'),    meta: { title: '大屏展示',  adminOnly: true } },
      { path: 'org',          component: () => import('@/views/OrgManage.vue'),    meta: { title: '组织管理',  adminOnly: true } },
      { path: 'module-auth',  component: () => import('@/views/ModuleAuth.vue'),   meta: { title: '模块授权',  adminOnly: true } },
      { path: 'platform-setting', component: () => import('@/views/PlatformSetting.vue'), meta: { title: '平台设置', adminOnly: true } },
      // 仅管理员可见
      { path: 'sims',      component: () => import('@/views/SimCard.vue'),     meta: { title: 'SIM卡管理', adminOnly: true } },
      { path: 'recharges', component: () => import('@/views/Recharge.vue'),    meta: { title: '充值管理',  adminOnly: true } },
    ]
  },
]

export default createRouter({
  history: createWebHistory(),
  routes
})
