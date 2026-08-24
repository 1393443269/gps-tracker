<template>
  <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#1a2a4a 0%,#2c4a7a 100%);">
    <el-card style="width:360px;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,0.3);" body-style="padding:32px 36px;">
      <div style="text-align:center;margin-bottom:28px;">
        <div style="font-size:22px;font-weight:700;color:#303133;margin-bottom:6px;">设备管理平台</div>
        <div style="font-size:13px;color:#909399;">设备查询</div>
      </div>

      <el-form :model="form" @submit.prevent="doLogin">
        <el-form-item>
          <el-input v-model="form.login_name" placeholder="登录账号" size="large" :prefix-icon="User"
            @keyup.enter="doLogin" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" placeholder="密码" size="large"
            :prefix-icon="Lock" show-password @keyup.enter="doLogin" />
        </el-form-item>
        <el-button type="primary" size="large" style="width:100%;margin-top:8px;"
          :loading="loading" @click="doLogin">
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { portalApi } from '@/api'

const router  = useRouter()
const loading = ref(false)
const form    = ref({ login_name: '', password: '' })

async function doLogin() {
  if (!form.value.login_name || !form.value.password) {
    ElMessage.warning('请填写账号和密码')
    return
  }
  loading.value = true
  try {
    const res = await portalApi.login(form.value)
    if (res.code === 200) {
      localStorage.setItem('customer_token',    res.data.token)
      localStorage.setItem('customer_info',     JSON.stringify(res.data.customer))
      router.push('/customer-portal')
    }
  } catch {} finally { loading.value = false }
}
</script>
