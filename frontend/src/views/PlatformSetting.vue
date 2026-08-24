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
                <el-input v-model="setting.logo_url" placeholder="Logo 图片 URL" />
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
import { platformApi, oplogApi } from '@/api'
import { ElMessage } from 'element-plus'

const activeTab = ref('info')
const loading   = ref(false)
const saving    = ref(false)

const setting = reactive({
  bigscreen_title: '资产管理平台', account_title: '资产管理平台',
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
      bigscreen_title: d.bigscreen_title || '资产管理平台',
      account_title:   d.account_title   || '资产管理平台',
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
