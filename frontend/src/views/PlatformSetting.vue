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
      <el-tab-pane label="功能配置" name="feature">
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

      <!-- ══ 操作日志 ══ -->
      <el-tab-pane label="操作日志" name="log">
        <el-table :data="logs" v-loading="logLoading" stripe border size="small">
          <el-table-column type="index" label="#" width="50" />
          <el-table-column prop="action"     label="操作类型" width="140" />
          <el-table-column prop="detail"     label="操作详情" min-width="240" />
          <el-table-column prop="ip"         label="IP" width="130" />
          <el-table-column prop="created_at" label="时间" width="165" />
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
import { platformApi, oplogApi, UPLOAD_AVATAR_URL, uploadHeaders } from '@/api'
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

const setting = reactive({
  bigscreen_title: '应急物资管理系统', account_title: '应急物资管理系统',
  unit_name: '', contact_phone: '', email: '', address: '', logo_url: '',
  enable_batch_cmd: true, sms_enabled: false, sms_total: 0, sms_used: 0,
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
  } finally {
    saving.value = false
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
