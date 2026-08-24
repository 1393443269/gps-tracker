<template>
  <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#001529 0%,#003a70 100%);">
    <el-card style="width:380px;border-radius:12px;box-shadow:0 8px 40px rgba(0,0,0,0.4);" body-style="padding:36px 40px;">

      <div style="text-align:center;margin-bottom:32px;">
        <div style="font-size:26px;margin-bottom:10px;">🛰</div>
        <div style="font-size:20px;font-weight:700;color:#303133;margin-bottom:4px;">设备管理平台</div>
        <div style="font-size:12px;color:#b0b8c4;">输入账号密码即可登录</div>
      </div>

      <el-form :model="form" @submit.prevent="doLogin">
        <el-form-item>
          <el-input v-model="form.username" placeholder="账号" size="large"
            :prefix-icon="User" @keyup.enter="doLogin" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" placeholder="密码" size="large"
            :prefix-icon="Lock" show-password @keyup.enter="doLogin" />
        </el-form-item>
        <el-button type="primary" size="large" style="width:100%;margin-top:4px;"
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
import { authApi, portalApi } from '@/api'

const router  = useRouter()
const loading = ref(false)
const form    = ref({ username: '', password: '' })

async function doLogin() {
  if (!form.value.username || !form.value.password) {
    ElMessage.warning('请填写账号和密码')
    return
  }
  loading.value = true
  try {
    // 先尝试管理员登录
    try {
      const res = await authApi.login({ username: form.value.username, password: form.value.password })
      if (res.code === 200) {
        localStorage.setItem('admin_token',    res.data.token)
        localStorage.setItem('admin_username', res.data.username)
        localStorage.setItem('user_role',      'admin')
        router.push('/dashboard')
        return
      }
    } catch {}

    // 管理员失败，再试客户账号
    try {
      const res = await portalApi.login({ login_name: form.value.username, password: form.value.password })
      if (res.code === 200) {
        // 切换到客户模式时，清除可能残留的管理员 token，防止 isAdmin() 返回错误值
        localStorage.removeItem('admin_token')
        localStorage.removeItem('admin_username')
        localStorage.setItem('customer_token', res.data.token)
        localStorage.setItem('customer_info',  JSON.stringify(res.data.customer))
        localStorage.setItem('user_role',      'customer')
        router.push('/dashboard')
        return
      }
    } catch {}

    ElMessage.error('账号或密码错误')
  } finally {
    loading.value = false
  }
}
</script>
