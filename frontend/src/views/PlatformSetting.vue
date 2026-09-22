<template>
  <el-card>
    <el-tabs v-model="activeTab">
      <!-- ══ 平台信息 ══ -->
      <el-tab-pane label="平台信息" name="info">
        <el-form :model="setting" label-width="90px" style="max-width:700px;" v-loading="loading">
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="大屏标题">
                <el-input v-model="setting.bigscreen_title" maxlength="15" show-word-limit />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="单位名称">
                <el-input v-model="setting.unit_name" placeholder="请填写" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="账号标题">
                <el-input v-model="setting.account_title" maxlength="15" show-word-limit />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="联系电话">
                <el-input v-model="setting.contact_phone" placeholder="请填写" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="电子邮箱">
                <el-input v-model="setting.email" placeholder="请填写" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="平台Logo">
                <el-upload
                  :action="UPLOAD_AVATAR_URL"
                  :headers="uploadHeaders()"
                  :show-file-list="false"
                  accept="image/*"
                  :before-upload="beforeLogoUpload"
                  :on-success="onLogoSuccess"
                  :on-error="onLogoError">
                  <img v-if="setting.logo_url" :src="logoSrc(setting.logo_url)"
                    style="height:64px;max-width:180px;object-fit:contain;border:1px solid #eee;border-radius:6px;" />
                  <div v-else class="logo-uploader-empty">
                    <el-icon><Plus /></el-icon>
                    <span style="font-size:12px;margin-top:4px;">上传Logo</span>
                  </div>
                </el-upload>
                <el-button v-if="setting.logo_url" link type="danger" size="small"
                  style="margin-top:6px;" @click="setting.logo_url = ''">移除</el-button>
                <div style="font-size:12px;color:#909399;margin-top:4px;line-height:1.5;">
                  建议横版图，尺寸 ≤ {{ LOGO_MAX_W }}×{{ LOGO_MAX_H }} 像素、≤ {{ LOGO_MAX_MB }}MB；<br>
                  推荐透明底 PNG，显示更佳
                </div>
              </el-form-item>
            </el-col>
            <el-col :span="24">
              <el-form-item label="单位地址">
                <el-input v-model="setting.address" type="textarea" :rows="2" maxlength="200" show-word-limit />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" @click="saveSetting" :loading="saving">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- ══ 功能配置 ══ -->
      <el-tab-pane v-if="isAdmin()" label="功能配置" name="feature">
        <div style="max-width:700px;" v-loading="loading">
          <div style="font-size:14px;font-weight:600;margin:6px 0 12px;">设备功能</div>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="批量下发指令" label-width="120px">
              <el-switch v-model="setting.enable_batch_cmd" />
              <span style="margin-left:12px;font-size:12px;color:#909399;">
                开启后可在【设备管理 → 设备设置】中执行批量下发操作
              </span>
            </el-descriptions-item>
          </el-descriptions>

          <div style="font-size:14px;font-weight:600;margin:18px 0 12px;">短信服务</div>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="服务状态" label-width="100px">
              <el-switch v-model="setting.sms_enabled" active-text="已开启" inactive-text="已关闭" inline-prompt />
            </el-descriptions-item>
            <el-descriptions-item label="充值条数" label-width="100px">
              <el-input-number v-model="setting.sms_total" :min="0" :controls="false" style="width:120px;" />
            </el-descriptions-item>
            <el-descriptions-item label="已用条数" label-width="100px">{{ setting.sms_used ?? 0 }}</el-descriptions-item>
            <el-descriptions-item label="剩余条数" label-width="100px">
              <span :style="{ color: remaining > 0 ? '#67c23a' : '#f56c6c' }">{{ remaining }}</span>
            </el-descriptions-item>
          </el-descriptions>

          <div style="margin-top:18px;">
            <el-button type="primary" @click="saveSetting" :loading="saving">保存</el-button>
          </div>
        </div>
      </el-tab-pane>

      <!-- ══ 收款设置 ══ -->
      <el-tab-pane v-if="isAdmin()" label="收款设置" name="payment">
        <el-form :model="setting" label-width="110px" style="max-width:640px;" v-loading="loading">
          <el-form-item label="启用收款展示">
            <el-switch v-model="setting.payment_enabled" active-text="开" inactive-text="关" inline-prompt />
            <span style="margin-left:12px;font-size:12px;color:#909399;">
              开启后，客户在充值页可看到下方收款信息
            </span>
          </el-form-item>
          <el-form-item label="收款二维码">
            <el-upload
              :action="UPLOAD_AVATAR_URL"
              :headers="uploadHeaders()"
              :show-file-list="false"
              accept="image/*"
              :before-upload="beforeQrUpload"
              :on-success="onQrSuccess"
              :on-error="onLogoError">
              <img v-if="setting.payment_qrcode_url" :src="logoSrc(setting.payment_qrcode_url)"
                style="width:160px;height:160px;object-fit:contain;border:1px solid #eee;border-radius:6px;" />
              <div v-else class="logo-uploader-empty" style="width:160px;height:160px;">
                <el-icon><Plus /></el-icon>
                <span style="font-size:12px;margin-top:4px;">上传收款码</span>
              </div>
            </el-upload>
            <el-button v-if="setting.payment_qrcode_url" link type="danger" size="small"
              style="margin-top:6px;" @click="setting.payment_qrcode_url = ''">移除</el-button>
            <div style="font-size:12px;color:#909399;margin-top:4px;">
              支付宝/微信收款码或对公收款码，建议 ≤ {{ QR_MAX_MB }}MB
            </div>
          </el-form-item>
          <el-form-item label="收款人/户名">
            <el-input v-model="setting.payment_payee" placeholder="如 XX科技有限公司" maxlength="50" />
          </el-form-item>
          <el-form-item label="收款账号">
            <el-input v-model="setting.payment_account" placeholder="银行卡号 / 支付宝账号" maxlength="50" />
          </el-form-item>
          <el-form-item label="开户行/渠道">
            <el-input v-model="setting.payment_bank" placeholder="如 工商银行XX支行（可选）" maxlength="50" />
          </el-form-item>
          <el-form-item label="收款说明">
            <el-input v-model="setting.payment_note" type="textarea" :rows="2"
              placeholder="如：转账后请备注设备IMEI号" maxlength="200" show-word-limit />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveSetting" :loading="saving">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- ══ 邮件通知 ══ -->
      <el-tab-pane v-if="isAdmin()" label="邮件通知" name="notify">
        <el-form label-width="140px" style="max-width:640px;">
          <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px;"
            title="SIM 卡到期、余额不足时自动发邮件提醒。优先发给设备所属客户的邮箱，查不到时发给下方管理员邮箱。发件邮箱需由技术人员在服务器配置授权码后生效。" />
          <el-form-item label="启用邮件通知">
            <el-switch v-model="setting.notify_email_enabled" active-text="开" inactive-text="关" inline-prompt />
          </el-form-item>
          <el-form-item label="管理员收件邮箱">
            <el-input v-model="setting.notify_admin_email" placeholder="兜底/汇总收件邮箱，如 admin@company.com" style="max-width:360px;" />
          </el-form-item>
          <el-form-item label="到期提前提醒">
            <el-input-number v-model="setting.notify_expire_days" :min="1" :max="90" /> <span style="margin-left:8px;color:#909399;">天（SIM 卡到期前多少天开始提醒）</span>
          </el-form-item>
          <el-form-item label="余额提醒阈值">
            <el-input-number v-model="setting.notify_balance_min" :min="0" :precision="2" :step="10" /> <span style="margin-left:8px;color:#909399;">元（余额低于此值提醒充值）</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveSetting" :loading="saving">保存</el-button>
          </el-form-item>
          <el-divider />
          <el-form-item label="配置状态">
            <el-tag v-if="notifyStatus.smtp_configured" type="success">发件邮箱已配置</el-tag>
            <el-tag v-else type="warning">发件邮箱未配置{{ notifyStatus.missing && notifyStatus.missing.length ? '（缺：' + notifyStatus.missing.join('、') + '）' : '' }}</el-tag>
          </el-form-item>
          <el-form-item label="测试发送">
            <el-input v-model="testEmail" placeholder="收测试邮件的邮箱" style="max-width:300px;margin-right:10px;" />
            <el-button @click="doTestMail" :loading="testing">发测试邮件</el-button>
          </el-form-item>
          <el-form-item label="立即扫描">
            <el-button @click="doScanNow" :loading="scanning">手动扫描并发送</el-button>
            <span style="margin-left:10px;color:#909399;">不等定时，立即扫一次到期/低余额</span>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- ══ 操作日志 ══ -->
      <el-tab-pane v-if="isAdmin()" label="操作日志" name="log">
        <el-table :data="logs" v-loading="logLoading" stripe border size="small">
          <el-table-column type="index" label="#" width="50" />
          <el-table-column prop="action"     label="操作类型" width="140" />
          <el-table-column prop="detail"     label="操作详情" min-width="240" />
          <el-table-column prop="ip"         label="IP" width="130" />
          <el-table-column prop="created_at" label="时间" min-width="165" />
        </el-table>
        <el-pagination style="margin-top:14px;justify-content:flex-end;display:flex;"
          :current-page="logPage" :page-size="logSize" :total="logTotal"
          layout="total,prev,pager,next" @current-change="loadLogs" />
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { platformApi, oplogApi, UPLOAD_AVATAR_URL, uploadHeaders, isAdmin } from '@/api'
import { ElMessage } from 'element-plus'

const activeTab = ref('info')
const loading   = ref(false)
const saving    = ref(false)

// Logo 相对路径 → 完整可访问地址
function logoSrc(url) {
  if (!url) return ''
  return /^https?:\/\//.test(url) ? url : (window.location.origin + url)
}
// ── Logo 上传回调 ──
// Logo 尺寸限制（显示区域很小，超大图纯属浪费带宽）
const LOGO_MAX_MB = 1        // 文件 ≤ 1MB
const LOGO_MAX_W  = 1000     // 宽 ≤ 1000px
const LOGO_MAX_H  = 400      // 高 ≤ 400px

function beforeLogoUpload(file) {
  if (!file.type.startsWith('image/')) {
    ElMessage.error('只能上传图片'); return false
  }
  if (file.size / 1024 / 1024 >= LOGO_MAX_MB) {
    ElMessage.error(`图片不能超过 ${LOGO_MAX_MB}MB`); return false
  }
  // 校验像素尺寸（异步读图）
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      if (img.width > LOGO_MAX_W || img.height > LOGO_MAX_H) {
        ElMessage.error(`图片尺寸不能超过 ${LOGO_MAX_W}×${LOGO_MAX_H} 像素（当前 ${img.width}×${img.height}）`)
        reject()
      } else {
        resolve()
      }
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      ElMessage.error('图片读取失败'); reject()
    }
    img.src = url
  })
}
function onLogoSuccess(res) {
  if (res?.code === 200 && res.data?.url) {
    setting.logo_url = res.data.url
    ElMessage.success('Logo 已上传，别忘了点保存')
  } else {
    ElMessage.error(res?.msg || '上传失败')
  }
}
function onLogoError() {
  ElMessage.error('上传失败，请重试')
}

// ── 收款二维码 上传回调 ──
const QR_MAX_MB = 2          // 收款码 ≤ 2MB
function beforeQrUpload(file) {
  if (!file.type.startsWith('image/')) {
    ElMessage.error('只能上传图片'); return false
  }
  if (file.size / 1024 / 1024 >= QR_MAX_MB) {
    ElMessage.error(`图片不能超过 ${QR_MAX_MB}MB`); return false
  }
  return true
}
function onQrSuccess(res) {
  if (res?.code === 200 && res.data?.url) {
    setting.payment_qrcode_url = res.data.url
    ElMessage.success('收款码已上传，别忘了点保存')
  } else {
    ElMessage.error(res?.msg || '上传失败')
  }
}

const setting = reactive({
  bigscreen_title: '应急物资管理系统', account_title: '应急物资管理系统',
  unit_name: '', contact_phone: '', email: '', address: '', logo_url: '',
  enable_batch_cmd: true, sms_enabled: false, sms_total: 0, sms_used: 0,
  payment_enabled: false, payment_qrcode_url: '', payment_payee: '',
  payment_account: '', payment_bank: '', payment_note: '',
  notify_email_enabled: false, notify_admin_email: '',
  notify_expire_days: 7, notify_balance_min: 10,
})

const remaining = computed(() => Math.max(0, (setting.sms_total || 0) - (setting.sms_used || 0)))

async function loadSetting() {
  loading.value = true
  try {
    const res = await platformApi.get()
    const d = res.data || {}
    Object.assign(setting, {
      bigscreen_title: d.bigscreen_title || '应急物资管理系统',
      account_title:   d.account_title   || '应急物资管理系统',
      unit_name:       d.unit_name       || '',
      contact_phone:   d.contact_phone   || '',
      email:           d.email           || '',
      address:         d.address         || '',
      logo_url:        d.logo_url         || '',
      enable_batch_cmd: !!d.enable_batch_cmd,
      sms_enabled:     !!d.sms_enabled,
      sms_total:       d.sms_total       || 0,
      sms_used:        d.sms_used        || 0,
      payment_enabled:    !!d.payment_enabled,
      payment_qrcode_url: d.payment_qrcode_url || '',
      payment_payee:      d.payment_payee      || '',
      payment_account:    d.payment_account    || '',
      payment_bank:       d.payment_bank       || '',
      payment_note:       d.payment_note       || '',
      notify_email_enabled: !!d.notify_email_enabled,
      notify_admin_email:   d.notify_admin_email || '',
      notify_expire_days:   d.notify_expire_days ?? 7,
      notify_balance_min:   d.notify_balance_min ?? 10,
    })
  } finally {
    loading.value = false
  }
}

async function saveSetting() {
  saving.value = true
  try {
    await platformApi.update({ ...setting })
    ElMessage.success('保存成功')
    if (activeTab.value === 'notify') loadNotifyStatus()
  } finally {
    saving.value = false
  }
}

// ── 邮件通知 ──
const notifyStatus = reactive({ smtp_configured: false, missing: [] })
const testEmail = ref('')
const testing = ref(false)
const scanning = ref(false)

async function loadNotifyStatus() {
  try {
    const res = await platformApi.notifyStatus()
    const d = res.data || {}
    notifyStatus.smtp_configured = !!d.smtp_configured
    notifyStatus.missing = d.missing || []
  } catch {}
}

async function doTestMail() {
  if (!testEmail.value.trim()) { ElMessage.warning('请输入收测试邮件的邮箱'); return }
  testing.value = true
  try {
    const res = await platformApi.notifyTest({ to_email: testEmail.value.trim() })
    if (res.data?.ok) ElMessage.success('测试邮件已发送，请查收')
    else ElMessage.error('发送失败：' + (res.data?.msg || '请检查发件邮箱配置'))
  } catch (e) {
    ElMessage.error('发送失败：' + (e.message || '请检查发件邮箱配置'))
  } finally {
    testing.value = false
  }
}

async function doScanNow() {
  scanning.value = true
  try {
    const res = await platformApi.notifyScanNow()
    const d = res.data || {}
    ElMessage.success(`扫描完成：检查 ${d.scanned ?? 0} 项，发送 ${d.sent ?? 0} 封，跳过 ${d.skipped ?? 0} 项`)
  } catch (e) {
    ElMessage.error('扫描失败：' + (e.message || '请稍后重试'))
  } finally {
    scanning.value = false
  }
}

// ── 操作日志 ──
const logs       = ref([])
const logLoading = ref(false)
const logPage    = ref(1)
const logSize    = ref(20)
const logTotal   = ref(0)

async function loadLogs(p = logPage.value) {
  logPage.value = p
  logLoading.value = true
  try {
    const res = await oplogApi.list({ page: p, size: logSize.value })
    logs.value    = res.data?.records || []
    logTotal.value = res.data?.total  || 0
  } finally {
    logLoading.value = false
  }
}

// 首次切到日志 Tab 时加载
watch(activeTab, (v) => {
  if (v === 'log' && !logs.value.length) loadLogs(1)
  if (v === 'notify') loadNotifyStatus()
})

onMounted(() => loadSetting())
</script>

<style scoped>
.logo-uploader-empty {
  width: 120px;
  height: 64px;
  border: 1px dashed #d9d9d9;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #8c939d;
  cursor: pointer;
  transition: border-color .2s;
}
.logo-uploader-empty:hover {
  border-color: #409eff;
  color: #409eff;
}
</style>
