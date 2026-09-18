<template>
  <div>
    <el-card shadow="never" style="margin-bottom:16px;">
      <div style="font-size:14px;color:#606266;">
        为各客户设置<b>返点比例</b>和<b>分销开关</b>。某客户发生充值时,其各级上级中开了分销的按比例获得返点;
        充值方本人和顶级总代(收款方)不参与分润。同一条分销链上各级比例之和不得超过 100%。
      </div>
    </el-card>

    <el-table :data="list" border stripe v-loading="loading" row-key="customer_id">
      <el-table-column label="客户名称" min-width="240">
        <template #default="{ row }">
          <span :style="{ paddingLeft: (row._depth * 20) + 'px' }">
            <el-tag v-if="!row.parent_id" size="small" type="warning" style="margin-right:6px;">总代</el-tag>
            {{ row.name }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="分销开关" width="120">
        <template #default="{ row }">
          <el-switch v-model="row.is_distributor" :active-value="1" :inactive-value="0"
            :disabled="!row.parent_id" />
        </template>
      </el-table-column>
      <el-table-column label="返点比例(%)" width="180">
        <template #default="{ row }">
          <el-input-number v-model="row.rate" :min="0" :max="100" :precision="2" :step="1"
            size="small" controls-position="right"
            :disabled="!row.parent_id || !row.is_distributor" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button type="primary" size="small" :disabled="!row.parent_id"
            :loading="row._saving" @click="save(row)">保存</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { profitApi } from '@/api'

const list = ref([])
const loading = ref(false)

function toTreeOrder(items) {
  const byParent = {}
  items.forEach(it => {
    const p = it.parent_id || 0
    if (!byParent[p]) byParent[p] = []
    byParent[p].push(it)
  })
  const out = []
  function walk(pid, depth) {
    (byParent[pid] || []).forEach(node => {
      node._depth = depth
      out.push(node)
      walk(node.customer_id, depth + 1)
    })
  }
  walk(0, 0)
  items.forEach(it => { if (!out.includes(it)) { it._depth = 0; out.push(it) } })
  return out
}

async function load() {
  loading.value = true
  try {
    const res = await profitApi.configList()
    const items = (res.data.items || []).map(r => ({
      ...r,
      rate: Number(r.rate) || 0,
      is_distributor: Number(r.is_distributor) || 0,
      _saving: false,
    }))
    list.value = toTreeOrder(items)
  } catch (e) {
    ElMessage.error('加载失败:' + (e.response?.data?.msg || e.message || ''))
  }
  loading.value = false
}

async function save(row) {
  row._saving = true
  try {
    const res = await profitApi.saveConfig({
      customer_id: row.customer_id,
      rate: row.rate,
      is_distributor: row.is_distributor,
      is_active: 1,
    })
    // 只回填本行为后端确认后的值,不整表重载(避免正在编辑的输入框跳变)
    const d = res.data || {}
    row.rate = Number(d.rate)
    row.is_distributor = Number(d.is_distributor)
    ElMessage.success('已保存:' + row.name)
  } catch (e) {
    ElMessage.error(e.response?.data?.msg || e.message || '保存失败')
  }
  row._saving = false
}

onMounted(load)
</script>
