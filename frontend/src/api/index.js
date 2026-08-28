import axios from 'axios'
import { ElMessage } from 'element-plus'

// 过滤后端原始错误信息，避免暴露内部实现
const _safeMsg = (msg) => {
  const dangerous = ['Error', 'Exception', 'SQL', 'sqlite', 'postgres', 'Traceback', '/app/', 'line ']
  if (!msg || dangerous.some(k => msg.includes(k))) return '操作失败，请稍后重试'
  return msg
}

const http = axios.create({
  baseURL: '/api',
  timeout: 10000
})

// 自动带上管理员 token；客户身份时补带客户 token（供放开给客户的只读/品牌接口鉴权）
http.interceptors.request.use(cfg => {
  const token = localStorage.getItem('admin_token')
  if (token) cfg.headers['X-Admin-Token'] = token
  const ctoken = localStorage.getItem('customer_token')
  if (ctoken) cfg.headers['X-Customer-Token'] = ctoken
  return cfg
})

http.interceptors.response.use(
  res => res.data,
  err => {
    const status   = err.response?.status
    const msg      = err.response?.data?.msg || '请求失败'
    const onLogin  = window.location.pathname.startsWith('/login')
    // 静默请求(如顶栏后台轮询):失败不弹错、不强制跳登录。
    // 用于避免页面加载瞬间 token 未就绪时,轮询抢先请求的 401 惊扰用户。
    if (err.config?.silent401 && status === 401) {
      return Promise.reject(err)
    }
    if (status === 401 && !onLogin) {
      const url = err.config?.url || ''
      if (localStorage.getItem('customer_token')) {
        // 客户 token 访问客户自身接口（/api/customer/...）返回 401 → token 已过期，清除并跳转
        if (url.includes('/customer/')) {
          localStorage.removeItem('customer_token')
          localStorage.removeItem('customer_info')
          localStorage.removeItem('user_role')
          window.location.href = '/login'
        }
        // 客户 token 访问管理员接口返回 401 → 属正常权限边界，静默放过
        return Promise.reject(err)
      }
      localStorage.removeItem('admin_token')
      localStorage.removeItem('user_role')
      window.location.href = '/login'
      return Promise.reject(err)
    }
    // 登录页上 401 静默（doLogin 自己处理），其余错误弹出提示
    if (!onLogin || status !== 401) ElMessage.error(_safeMsg(msg))
    return Promise.reject(err)
  }
)

export const authApi = {
  login:          (data) => http.post('/auth/login', data),
  changePassword: (data) => http.post('/auth/change_password', data),
}

// ── 文件上传（头像等） ─────────────────────────────────────────────────────────
// el-upload 用：上传地址 + 鉴权头
export const UPLOAD_AVATAR_URL = '/api/upload/avatar'
export function uploadHeaders() {
  const h = {}
  const token  = localStorage.getItem('admin_token')
  const ctoken = localStorage.getItem('customer_token')
  if (token)  h['X-Admin-Token']    = token
  if (ctoken) h['X-Customer-Token'] = ctoken
  return h
}

// ── 设备 ─────────────────────────────────────────────────────────────────────
export const deviceApi = {
  list:              (params)       => http.get('/devices', { params }),
  get:               (id)           => http.get(`/devices/${id}`),
  create:            (data)         => http.post('/devices', data),
  update:            (id, data)     => http.put(`/devices/${id}`, data),
  summary:           (cfg)          => http.get('/devices/summary', cfg),
  batchLifecycle:    (ids, lifecycle) => http.put('/devices/batch_lifecycle', { ids, lifecycle }),
  withCustomer:      (params)       => http.get('/devices/with_customer', { params }),
  bindCustomer:      (id, customerId) => http.post(`/devices/${id}/bind_customer`, { customer_id: customerId }),
  unbindCustomer:    (id)           => http.post(`/devices/${id}/unbind_customer`),
  batchBind:         (ids, customerId) => http.post('/devices/batch_bind', { ids, customer_id: customerId }),
  batchUnbind:       (ids)          => http.post('/devices/batch_unbind', { ids }),
  batchCommand:      (phones, text) => http.post('/devices/batch_command', { phones, text }),
  exportAll:         ()             => http.get('/devices/export'),
  setRole:           (id, roleId)   => http.put(`/devices/${id}/role`, { role_id: roleId }),
  batchRole:         (ids, roleId)  => http.post('/devices/batch_role', { ids, role_id: roleId }),
}

// ── 位置 ─────────────────────────────────────────────────────────────────────
export const locationApi = {
  latest:  (phone)         => http.get(`/locations/${phone}/latest`),
  history: (phone, params) => http.get(`/locations/${phone}/history`, { params }),
}

// ── 报警 ─────────────────────────────────────────────────────────────────────
export const alarmApi = {
  list:        (params, cfg) => http.get('/alarms', { params, ...cfg }),
  handle:      (id, data)   => http.put(`/alarms/${id}/handle`, data),
  batchHandle: (ids, data)  => http.post('/alarms/batch_handle', { ids, ...data }),
}

// ── 报警规则 ──────────────────────────────────────────────────────────────────
export const alarmRuleApi = {
  types:  ()          => http.get('/alarm-types'),
  list:   ()          => http.get('/alarm-rules'),
  create: (data)      => http.post('/alarm-rules', data),
  update: (id, data)  => http.put(`/alarm-rules/${id}`, data),
  remove: (id)        => http.delete(`/alarm-rules/${id}`),
}

// ── 考勤统计 ──────────────────────────────────────────────────────────────────
export const attendanceApi = {
  list:   ()          => isAdmin() ? http.get('/attendance')                 : portalHttp.get('/attendance'),
  detail: (params)    => isAdmin() ? http.get('/attendance/detail',{params}) : portalHttp.get('/attendance/detail',{params}),
}

// ── 健康数据 ──────────────────────────────────────────────────────────────────
export const healthApi = {
  list:   (params)    => isAdmin() ? http.get('/health',{params})           : portalHttp.get('/health',{params}),
}

// ── 平台设置 ──────────────────────────────────────────────────────────────────
export const platformApi = {
  get:    ()          => http.get('/platform-setting'),
  update: (data)      => http.put('/platform-setting', data),
}

// ── 指令下发 ─────────────────────────────────────────────────────────────────
export const commandApi = {
  sendText: (phone, text)   => http.post('/commands/text',    { phone, text }),
  control:  (phone, cmd)    => http.post('/commands/control', { phone, cmd }),
  track:    (phone, interval, duration) =>
                               http.post('/commands/track',   { phone, interval, duration }),
  history:  (params)        => http.get('/command-history', { params }),
  addHistory: (data)        => http.post('/command-history', data),
}

// ── SIM 卡 ────────────────────────────────────────────────────────────────────
export const simApi = {
  list:          (params)    => http.get('/sims', { params }),
  expiringCount: ()          => http.get('/sims/expiring_count'),
  create:        (data)      => http.post('/sims', data),
  update:        (id, data)  => http.put(`/sims/${id}`, data),
  remove:        (id)        => http.delete(`/sims/${id}`),
  bind:          (id, phone) => http.post(`/sims/${id}/bind`, { phone }),
}

// ── 充值 ─────────────────────────────────────────────────────────────────────
export const rechargeApi = {
  list:   (params) => http.get('/recharges', { params }),
  create: (data)   => http.post('/recharges', data),
}

// ── 客户 ─────────────────────────────────────────────────────────────────────
export const customerApi = {
  list:           (params)        => http.get('/customers', { params }),
  create:         (data)          => http.post('/customers', data),
  update:         (id, data)      => http.put(`/customers/${id}`, data),
  remove:         (id)            => http.delete(`/customers/${id}`),
  setPassword:    (id, data)      => http.put(`/customers/${id}/password`, data),
  getDevices:     (id)            => http.get(`/customers/${id}/devices`),
  assignDevices:  (id, phones)    => http.put(`/customers/${id}/devices`, { phones }),
  // 设备信息页使用：获取所有客户列表（用于绑定选择）
  listAll:        ()              => http.get('/customers', { params: { size: 500 } }),
}

// ── 角色判断 ──────────────────────────────────────────────────────────────────
export function isAdmin() { return localStorage.getItem('user_role') === 'admin' }

// ── 客户门户（客户自助，使用独立 X-Customer-Token） ──────────────────────────
const portalHttp = axios.create({ baseURL: '/api/customer', timeout: 10000 })
portalHttp.interceptors.request.use(cfg => {
  const token = localStorage.getItem('customer_token')
  if (token) cfg.headers['X-Customer-Token'] = token
  return cfg
})
portalHttp.interceptors.response.use(res => res.data, err => {
  const status  = err.response?.status
  const onLogin = window.location.pathname.startsWith('/login')
  if (status === 401 && !onLogin) {
    localStorage.removeItem('customer_token')
    localStorage.removeItem('customer_info')
    localStorage.removeItem('user_role')
    window.location.href = '/login'
    return Promise.reject(err)
  }
  if (!onLogin || status !== 401) ElMessage.error(_safeMsg(err.response?.data?.msg || '请求失败'))
  return Promise.reject(err)
})
export const portalApi = {
  login:          (data)          => portalHttp.post('/login', data),
  me:             ()              => portalHttp.get('/me'),
  // 设备
  deviceList:     (params)        => portalHttp.get('/device_list', { params }),
  devices:        ()              => portalHttp.get('/devices'),
  updateDevice:   (phone, data)   => portalHttp.put(`/devices/${phone}/update`, data),
  summary:        ()              => portalHttp.get('/summary'),
  // 位置
  latest:         (phone)         => portalHttp.get(`/locations/${phone}/latest`),
  history:        (phone, params) => portalHttp.get(`/locations/${phone}/history`, { params }),
  // 报警
  alarms:         (params)        => portalHttp.get('/alarms', { params }),
  handleAlarm:    (id, data)      => portalHttp.put(`/alarms/${id}/handle`, data),
  // 指令
  sendCommand:    (data)          => portalHttp.post('/commands/text', data),
  cmdHistory:     (params)        => portalHttp.get('/commands/history', { params }),
  // 电子围栏
  fences:         (params)        => portalHttp.get('/fences', { params }),
  createFence:    (data)          => portalHttp.post('/fences', data),
  updateFence:    (id, data)      => portalHttp.put(`/fences/${id}`, data),
  removeFence:    (id)            => portalHttp.delete(`/fences/${id}`),
  fenceDevices:   (id, phones)    => portalHttp.put(`/fences/${id}/devices`, { phones }),
  // 下级客户
  subCustomers: {
    list:          (params)        => portalHttp.get('/sub_customers', { params }),
    create:        (data)          => portalHttp.post('/sub_customers', data),
    update:        (id, data)      => portalHttp.put(`/sub_customers/${id}`, data),
    remove:        (id)            => portalHttp.delete(`/sub_customers/${id}`),
    getDevices:    (id)            => portalHttp.get(`/sub_customers/${id}/devices`),
    assignDevices: (id, phones)    => portalHttp.put(`/sub_customers/${id}/devices`, { phones }),
  },
  // 全量设备池（自己+子账号，供分配界面）
  poolDevices: () => portalHttp.get('/pool_devices'),
  // SIM 卡（客户只能查/改/充值，不能新增/绑定/删除）
  sims: {
    list:     (params)   => portalHttp.get('/sims', { params }),
    update:   (id, data) => portalHttp.put(`/sims/${id}`, data),
    recharge: (id, data) => portalHttp.post(`/sims/${id}/recharge`, data),
  },
  // 充值记录
  recharges: {
    list:   (params) => portalHttp.get('/recharges', { params }),
    create: (data)   => portalHttp.post('/recharges', data),
  },
}

// ── 统一自适应 API（自动选管理员或客户端点；当前未被页面引用，保留备用） ──────────
export const unifiedApi = {
  deviceList:   (p)    => isAdmin() ? deviceApi.list(p)          : portalApi.deviceList(p),
  summary:      ()     => isAdmin() ? deviceApi.summary()         : portalApi.summary(),
  latest:       (ph)   => isAdmin() ? locationApi.latest(ph)      : portalApi.latest(ph),
  history:      (ph,p) => isAdmin() ? locationApi.history(ph,p)   : portalApi.history(ph,p),
  alarmList:    (p)    => isAdmin() ? alarmApi.list(p)            : portalApi.alarms(p),
  handleAlarm:  (id,d) => isAdmin() ? alarmApi.handle(id,d)       : portalApi.handleAlarm(id,d),
  fenceList:    (p)    => isAdmin() ? fenceApi.list(p)            : portalApi.fences(p),
  createFence:  (d)    => isAdmin() ? fenceApi.create(d)          : portalApi.createFence(d),
  removeFence:  (id)   => isAdmin() ? fenceApi.remove(id)         : portalApi.removeFence(id),
  fenceDevices: (id,ph)=> isAdmin() ? fenceApi.updateDevices(id,ph): portalApi.fenceDevices(id,ph),
}

// ── 电子围栏 ──────────────────────────────────────────────────────────────────
export const fenceApi = {
  list:          (params)     => http.get('/fences', { params }),
  create:        (data)       => http.post('/fences', data),
  remove:        (id)         => http.delete(`/fences/${id}`),
  batchDelete:   (ids)        => http.post('/fences/batch_delete', { ids }),
  updateDevices: (id, phones) => http.put(`/fences/${id}/devices`, { phones }),
}

// ── 标注点 ────────────────────────────────────────────────────────────────────
export const markApi = {
  list:   (params) => http.get('/mark_points', { params }),
  create: (data)   => http.post('/mark_points', data),
  remove: (id)     => http.delete(`/mark_points/${id}`),
}

// ── 共享风险点 ────────────────────────────────────────────────────────────────
export const riskApi = {
  list:   ()     => http.get('/risk_points'),
  create: (data) => http.post('/risk_points', data),
  remove: (id)   => http.delete(`/risk_points/${id}`),
}

// ── 报表 ─────────────────────────────────────────────────────────────────────
export const reportApi = {
  summary: (params) => http.get('/report/summary', { params }),
}

// ── 操作日志 ──────────────────────────────────────────────────────────────────
export const oplogApi = {
  list: (params) => http.get('/oplogs', { params }),
}

// ── 设备角色（分组） ───────────────────────────────────────────────────────────
export const roleApi = {
  list:          ()              => http.get('/roles'),
  create:        (data)          => http.post('/roles', data),
  update:        (id, data)      => http.put(`/roles/${id}`, data),
  remove:        (id)            => http.delete(`/roles/${id}`),
  assignDevices: (id, phones)    => http.put(`/roles/${id}/assign`, { phones }),
}

// ── 组织管理 ──────────────────────────────────────────────────────────────────
export const orgApi = {
  tree:          ()         => http.get('/org/tree'),
  children:      ()         => http.get('/org/children'),
  childrenOf:    (id)       => http.get(`/org/${id}/children`),
  create:        (data)     => http.post('/org', data),
  update:        (id, data) => http.put(`/org/${id}`, data),
  remove:        (id)       => http.delete(`/org/${id}`),
  removeCascade: (id)       => http.delete(`/org/${id}?cascade=1`),
}

// ── 系统用户管理 ──────────────────────────────────────────────────────────────
export const userApi = {
  listByOrg: (orgId)        => http.get('/sys/users', { params: { orgId } }),
  create:    (data)         => http.post('/sys/users', data),
  update:    (id, data)     => http.put(`/sys/users/${id}`, data),
  resetPwd:  (id, data)     => http.put(`/sys/users/${id}/password`, data),
  remove:    (id)           => http.delete(`/sys/users/${id}`),
}

// ── 模块授权 ──────────────────────────────────────────────────────────────────
export const moduleApi = {
  tree:        ()           => http.get('/modules/tree'),                  // 全量模块树
  getOrgAuth:  (orgId)      => http.get(`/modules/org/${orgId}/auth`),    // 某组织已授权情况
  saveOrgAuth: (orgId, data)=> http.post(`/modules/org/${orgId}/auth`, data), // 保存授权
}
