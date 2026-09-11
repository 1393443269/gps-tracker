<template>
  <div>
    <!-- ApiKey 管理 -->
    <el-card style="margin-bottom:16px;">
      <div style="display:flex;align-items:center;margin-bottom:14px;">
        <span style="font-size:16px;font-weight:600;">开放 API 密钥</span>
        <span style="font-size:12px;color:#909399;margin-left:12px;">第三方系统凭 ApiKey + Secret 签名调用开放接口</span>
        <div style="flex:1;"></div>
        <el-button type="primary" :icon="Plus" @click="openCreateKey">生成 ApiKey</el-button>
      </div>
      <el-table :data="keys" v-loading="loadingKeys" border size="small" stripe>
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="app_name" label="应用名称" min-width="120" />
        <el-table-column prop="api_key" label="ApiKey" min-width="230">
          <template #default="{ row }">
            <span style="font-family:monospace;">{{ row.api_key }}</span>
            <el-button link type="primary" size="small" @click="copy(row.api_key)">复制</el-button>
          </template>
        </el-table-column>
        <el-table-column label="Secret" min-width="120">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="copy(row.api_secret)">复制 Secret</el-button>
          </template>
        </el-table-column>
        <el-table-column prop="last_used_at" label="最后使用" min-width="150">
          <template #default="{ row }">{{ row.last_used_at || '从未' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-switch :model-value="row.status === 1"
              @change="(v) => toggleKey(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click="removeKey(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 数据推送配置 -->
    <el-card style="margin-bottom:16px;">
      <div style="display:flex;align-items:center;margin-bottom:14px;">
        <span style="font-size:16px;font-weight:600;">数据推送回调</span>
        <span style="font-size:12px;color:#909399;margin-left:12px;">平台把报警事件实时 POST 到你的回调地址（带签名）</span>
        <div style="flex:1;"></div>
        <el-button type="primary" :icon="Plus" @click="openCreatePush">新增回调</el-button>
      </div>
      <el-table :data="pushes" v-loading="loadingPush" border size="small" stripe>
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="callback_url" label="回调地址" min-width="240" show-overflow-tooltip />
        <el-table-column prop="events" label="订阅事件" width="120" />
        <el-table-column label="最近成功" min-width="150">
          <template #default="{ row }">{{ row.last_ok_at || '—' }}</template>
        </el-table-column>
        <el-table-column label="最近错误" min-width="150">
          <template #default="{ row }">
            <span v-if="row.last_err" style="color:#f56c6c;font-size:12px;">{{ row.last_err }}</span>
            <span v-else style="color:#67c23a;">正常</span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80" align="center">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled === 1" @change="(v) => togglePush(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEditPush(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="removePush(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 接口说明 -->
    <el-card>
      <div style="font-size:16px;font-weight:600;margin-bottom:10px;">接口调用说明</div>
      <div style="font-size:13px;color:#606266;line-height:2;">
        <p style="margin:0;"><b>基地址</b>：<code>http(s)://本平台地址/openapi</code></p>
        <p style="margin:0;"><b>鉴权请求头</b>：<code>X-Api-Key</code>、<code>X-Timestamp</code>(unix秒)、<code>X-Sign</code></p>
        <p style="margin:0;"><b>签名算法</b>：<code>X-Sign = HMAC-SHA256(secret, api_key + "\n" + timestamp + "\n" + METHOD + "\n" + path)</code>，结果转 hex 小写。时间戳与服务器相差 5 分钟内有效。</p>
        <p style="margin:0;color:#e6a23c;"><b>注意</b>：签名中的 <code>path</code> 只取路径部分、<b>不含</b> <code>?</code> 后的查询参数（如 <code>/openapi/location/latest</code>，而非 <code>/openapi/location/latest?phone=xxx</code>）。</p>
        <p style="margin:8px 0 4px;"><b>可用接口</b>：</p>
        <el-table :data="apiDocs" size="small" border style="max-width:760px;">
          <el-table-column prop="method" label="方法" width="70" />
          <el-table-column prop="path" label="路径" min-width="220" />
          <el-table-column prop="desc" label="说明" min-width="200" />
        </el-table>
        <p style="margin:10px 0 0;"><b>推送格式</b>：平台 POST 到你的回调，body 为 <code>{"event":"alarm","data":{...},"timestamp":...}</code>，请求头带 <code>X-Push-Timestamp</code>、<code>X-Push-Sign = HMAC-SHA256(secret, timestamp + "\n" + body)</code>。你的接口需返回 2xx 表示接收成功，否则平台重试 3 次。</p>
      </div>
    </el-card>

    <!-- 生成 ApiKey 弹窗 -->
    <el-dialog v-model="createKeyVisible" title="生成 ApiKey" width="440px">
      <el-form label-width="90px">
        <el-form-item label="应用名称"><el-input v-model="keyForm.app_name" placeholder="如：某某调度系统" /></el-form-item>
        <el-form-item label="绑定客户">
          <el-select v-model="keyForm.customer_id" placeholder="不选=本组织全部设备" clearable filterable style="width:100%">
            <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="keyForm.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createKeyVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingKey" @click="submitKey">生成</el-button>
      </template>
    </el-dialog>

    <!-- 新生成的密钥展示 -->
    <el-dialog v-model="newKeyVisible" title="ApiKey 已生成" width="520px">
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom:12px;"
        title="Secret 仅在此完整展示，请立即复制保存。" />
      <div style="line-height:2.2;">
        <div>ApiKey：<code style="font-family:monospace;">{{ newKey.api_key }}</code>
          <el-button link type="primary" size="small" @click="copy(newKey.api_key)">复制</el-button></div>
        <div>Secret：<code style="font-family:monospace;">{{ newKey.api_secret }}</code>
          <el-button link type="primary" size="small" @click="copy(newKey.api_secret)">复制</el-button></div>
      </div>
      <template #footer><el-button type="primary" @click="newKeyVisible = false">我已保存</el-button></template>
    </el-dialog>

    <!-- 推送配置弹窗 -->
    <el-dialog v-model="pushVisible" :title="pushForm.id ? '编辑回调' : '新增回调'" width="480px">
      <el-form label-width="90px">
        <el-form-item label="回调地址"><el-input v-model="pushForm.callback_url" placeholder="https://your.server/callback" /></el-form-item>
        <el-form-item label="签名密钥"><el-input v-model="pushForm.secret" placeholder="用于校验推送来源，建议填写" /></el-form-item>
        <el-form-item label="订阅事件">
          <el-select v-model="pushForm.events" style="width:100%">
            <el-option label="报警(alarm)" value="alarm" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="pushForm.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pushVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingPush" @click="submitPush">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { openApiAdmin, customerApi } from '@/api'
import { ElMessage, ElMessageBox } from 'element-plus'

const keys = ref([])
const pushes = ref([])
const customers = ref([])
const loadingKeys = ref(false)
const loadingPush = ref(false)

const apiDocs = [
  { method: 'GET',  path: '/openapi/devices',          desc: '设备列表' },
  { method: 'GET',  path: '/openapi/location/latest',  desc: '单设备最新位置(phone=)' },
  { method: 'POST', path: '/openapi/location/batch',   desc: '批量最新位置({phones:[]})' },
  { method: 'GET',  path: '/openapi/location/history', desc: '历史轨迹(phone,start,end)' },
  { method: 'POST', path: '/openapi/command',          desc: '下发文本指令({phone,text})' },
  { method: 'GET',  path: '/openapi/ping',             desc: '连通性测试' },
]

async function loadKeys() {
  loadingKeys.value = true
  try { keys.value = (await openApiAdmin.listKeys()).data?.records || [] }
  finally { loadingKeys.value = false }
}
async function loadPush() {
  loadingPush.value = true
  try { pushes.value = (await openApiAdmin.listPush()).data?.records || [] }
  finally { loadingPush.value = false }
}
async function loadCustomers() {
  try { customers.value = (await customerApi.listAll()).data?.records || [] } catch {}
}

function copy(text) {
  navigator.clipboard?.writeText(text).then(
    () => ElMessage.success('已复制'),
    () => ElMessage.warning('复制失败，请手动选择')
  )
}

// ApiKey
const createKeyVisible = ref(false)
const savingKey = ref(false)
const keyForm = reactive({ app_name: '', customer_id: null, remark: '' })
const newKeyVisible = ref(false)
const newKey = reactive({ api_key: '', api_secret: '' })

function openCreateKey() {
  keyForm.app_name = ''; keyForm.customer_id = null; keyForm.remark = ''
  createKeyVisible.value = true
}
async function submitKey() {
  if (!keyForm.app_name.trim()) { ElMessage.warning('请填写应用名称'); return }
  savingKey.value = true
  try {
    const res = await openApiAdmin.createKey({ ...keyForm })
    newKey.api_key = res.data.api_key
    newKey.api_secret = res.data.api_secret
    createKeyVisible.value = false
    newKeyVisible.value = true
    loadKeys()
  } finally { savingKey.value = false }
}
async function toggleKey(row, v) {
  await openApiAdmin.updateKey(row.id, { app_name: row.app_name, status: v ? 1 : 0, remark: row.remark })
  ElMessage.success('已更新')
  loadKeys()
}
async function removeKey(row) {
  try {
    await ElMessageBox.confirm(`确认删除 ApiKey「${row.app_name}」？删除后使用它的第三方将立即失效。`, '删除确认', { type: 'warning' })
  } catch { return }
  await openApiAdmin.deleteKey(row.id)
  ElMessage.success('已删除')
  loadKeys()
}

// 推送
const pushVisible = ref(false)
const savingPush = ref(false)
const pushForm = reactive({ id: null, callback_url: '', secret: '', events: 'alarm', remark: '' })

function openCreatePush() {
  pushForm.id = null; pushForm.callback_url = ''; pushForm.secret = ''; pushForm.events = 'alarm'; pushForm.remark = ''
  pushVisible.value = true
}
function openEditPush(row) {
  pushForm.id = row.id; pushForm.callback_url = row.callback_url; pushForm.secret = row.secret || ''
  pushForm.events = row.events || 'alarm'; pushForm.remark = row.remark || ''
  pushVisible.value = true
}
async function submitPush() {
  if (!/^https?:\/\//.test(pushForm.callback_url)) { ElMessage.warning('回调地址需以 http(s) 开头'); return }
  savingPush.value = true
  try {
    if (pushForm.id) await openApiAdmin.updatePush(pushForm.id, { ...pushForm, enabled: true })
    else await openApiAdmin.createPush({ ...pushForm, enabled: true })
    ElMessage.success('已保存')
    pushVisible.value = false
    loadPush()
  } finally { savingPush.value = false }
}
async function togglePush(row, v) {
  await openApiAdmin.updatePush(row.id, {
    callback_url: row.callback_url, secret: row.secret, events: row.events,
    enabled: v ? true : false, remark: row.remark,
  })
  ElMessage.success('已更新')
  loadPush()
}
async function removePush(row) {
  try {
    await ElMessageBox.confirm('确认删除该回调配置？', '删除确认', { type: 'warning' })
  } catch { return }
  await openApiAdmin.deletePush(row.id)
  ElMessage.success('已删除')
  loadPush()
}

onMounted(() => { loadKeys(); loadPush(); loadCustomers() })
</script>
