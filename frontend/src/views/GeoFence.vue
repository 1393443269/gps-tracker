<template>
  <!-- 地图全屏容器，面板悬浮在地图上方 -->
  <div style="position:relative;height:calc(100vh - 140px);min-height:500px;border-radius:6px;overflow:hidden;">

    <!-- 地图全屏 -->
    <div id="fence-map" style="position:absolute;inset:0;width:100%;height:100%;" />
    <!-- 围栏 2D 画布叠加：绕过 MapLibre v6 raster style 下 GL fill 层不可见的问题 -->
    <canvas ref="fenceCanvas"
      style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:2;" />

    <!-- 选点模式遮罩提示：自身捕获点击，转换为地图坐标 -->
    <div v-if="pickingLocation" @click="onPickOverlayClick"
      style="position:absolute;inset:0;z-index:20;cursor:crosshair;background:rgba(0,0,0,.12);display:flex;align-items:center;justify-content:center;">
      <div @click.stop style="background:#fff;padding:12px 24px;border-radius:8px;font-size:14px;box-shadow:0 4px 16px rgba(0,0,0,.2);pointer-events:auto;">
        📍 请点击地图选择圆形围栏中心点
        <el-button size="small" style="margin-left:12px;" @click="pickingLocation=false;createVisible=true">取消</el-button>
      </div>
    </div>

    <!-- 顶部工具条（绘制提示） -->
    <div style="position:absolute;top:10px;left:10px;z-index:10;display:flex;gap:8px;align-items:center;background:rgba(255,255,255,.92);border-radius:6px;padding:6px 12px;box-shadow:0 2px 8px rgba(0,0,0,.15);">
      <span style="font-size:13px;font-weight:600;color:#303133;">电子围栏</span>
      <template v-if="activeTab === 'fence'">
        <el-divider direction="vertical" />
        <el-radio-group v-model="drawMode" size="small" @change="onDrawModeChange">
          <el-radio-button value="circle">⊙ 圆形</el-radio-button>
          <el-radio-button value="polygon">⬠ 多边形</el-radio-button>
          <el-radio-button value="administrative">🗺 行政区</el-radio-button>
        </el-radio-group>
        <el-tag v-if="drawMode==='polygon' && drawingPoly" type="warning" size="small">
          点击添加顶点，双击结束 ({{ polyPoints.length }}点)
        </el-tag>
      </template>
      <template v-if="activeTab === 'mark'">
        <el-divider direction="vertical" />
        <el-tag type="info" size="small">点击地图添加标注点</el-tag>
      </template>
      <template v-if="activeTab === 'risk'">
        <el-divider direction="vertical" />
        <el-tag type="danger" size="small">点击地图添加风险点</el-tag>
      </template>
    </div>

    <!-- 右侧悬浮面板 -->
    <div style="position:absolute;top:10px;right:10px;bottom:10px;z-index:10;width:300px;display:flex;flex-direction:column;">
      <el-card style="height:100%;box-shadow:0 2px 12px rgba(0,0,0,.2);" body-style="padding:0;height:calc(100% - 46px);overflow-y:auto;">
        <template #header>
          <el-tabs v-model="activeTab" @tab-change="onTabChange" style="margin:-4px -4px 0;">
            <el-tab-pane label="区域围栏" name="fence" />
            <el-tab-pane label="标注点" name="mark" />
            <el-tab-pane label="风险点" name="risk" />
          </el-tabs>
        </template>

        <!-- ── 区域围栏 tab ── -->
        <div v-if="activeTab==='fence'" style="padding:12px;">
          <!-- 搜索 + 筛选 -->
          <div style="display:flex;gap:6px;margin-bottom:8px;">
            <el-input v-model="fenceSearch" placeholder="搜索围栏名称" clearable size="small"
              @input="loadFences" style="flex:1;" />
            <el-select v-model="fenceTypeFilter" size="small" style="width:90px;" clearable
              placeholder="类型" @change="loadFences">
              <el-option label="圆形" value="circle" />
              <el-option label="多边形" value="polygon" />
              <el-option label="行政区" value="administrative" />
            </el-select>
          </div>
          <!-- 账号围栏查看(仅管理员):选账号后查看该账号及下级私建的围栏 -->
          <div v-if="isAdmin()" style="display:flex;gap:6px;margin-bottom:8px;">
            <el-select v-model="fenceAccountId" size="small" style="flex:1;" clearable filterable
              placeholder="按账号查看围栏(默认全局围栏)" @change="loadFences">
              <el-option v-for="c in accountOptions" :key="c.id" :label="c.name" :value="c.id" />
            </el-select>
          </div>

          <!-- 批量删除 / 全显切换 -->
          <div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap;">
            <el-button size="small" @click="toggleAllFenceVisible"
              :icon="fences.every(f=>visibleFenceIds.has(f.id)) ? Hide : View">
              {{ fences.every(f=>visibleFenceIds.has(f.id)) ? '全部隐藏' : '全部显示' }}
            </el-button>
            <template v-if="selectedFenceIds.length">
              <el-button type="danger" size="small" @click="batchDeleteFences">
                删除选中 ({{ selectedFenceIds.length }})
              </el-button>
              <el-button size="small" @click="selectedFenceIds=[]">取消</el-button>
            </template>
          </div>

          <!-- 围栏列表 -->
          <div v-if="!fences.length" style="color:#999;text-align:center;padding:30px 0;">暂无围栏</div>
          <div v-for="f in fences" :key="f.id" class="fence-item">
            <!-- 显示/隐藏眼睛按钮 -->
            <el-button
              size="small" text
              :style="{ padding:'0 2px', color: visibleFenceIds.has(f.id) ? f.color || '#409EFF' : '#ccc' }"
              @click.stop="toggleFenceVisible(f.id)"
              :title="visibleFenceIds.has(f.id) ? '点击隐藏' : '点击显示'"
            >
              <el-icon><component :is="visibleFenceIds.has(f.id) ? View : Hide" /></el-icon>
            </el-button>
            <el-checkbox
              :model-value="selectedFenceIds.includes(f.id)"
              @change="toggleSelect(f.id)"
              style="flex-shrink:0;"
            />
            <div class="fence-dot" :style="{ background: f.color }" />
            <div style="flex:1;min-width:0;cursor:pointer;" @click="flyToFence(f)">
              <div style="font-weight:500;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{{ f.name }}</div>
              <div style="font-size:11px;color:#909399;">
                {{ fenceTypeLabel(f.fence_type) }}
                <span v-if="f.fence_type==='circle'"> · 半径 {{ f.radius }}m</span>
                · {{ f.created_at?.slice(0,10) }}
                <span v-if="f.devices" style="color:#409EFF;"> · 已关联{{ f.devices.split(',').filter(Boolean).length }}台设备</span>
              </div>
            </div>
            <el-button size="small" @click.stop="openDeviceBinding(f)" style="padding:0 6px;">
              <el-icon><Setting /></el-icon>
            </el-button>
            <el-button size="small" type="danger" plain @click.stop="removeFence(f)">删除</el-button>
          </div>

          <!-- 新建按钮（行政区用弹窗，其余地图绘制） -->
          <el-button type="primary" style="width:100%;margin-top:10px;" @click="openCreate">
            + 新建围栏
          </el-button>
        </div>

        <!-- ── 标注点 tab ── -->
        <div v-if="activeTab==='mark'" style="padding:12px;">
          <el-input v-model="markSearch" placeholder="搜索标注点" clearable size="small"
            @input="loadMarks" style="margin-bottom:8px;" />
          <div v-if="!marks.length" style="color:#999;text-align:center;padding:30px 0;">暂无标注点</div>
          <div v-for="m in marks" :key="m.id" class="fence-item">
            <el-icon color="#409EFF"><Location /></el-icon>
            <div style="flex:1;min-width:0;">
              <div style="font-weight:500;font-size:13px;">{{ m.name }}</div>
              <div style="font-size:11px;color:#909399;">{{ m.remark || '无备注' }}</div>
            </div>
            <el-button size="small" type="danger" plain @click="removeMark(m.id)">删除</el-button>
          </div>
        </div>

        <!-- ── 风险点 tab ── -->
        <div v-if="activeTab==='risk'" style="padding:12px;">
          <div v-if="!risks.length" style="color:#999;text-align:center;padding:30px 0;">暂无风险点</div>
          <div v-for="r in risks" :key="r.id" class="fence-item">
            <el-icon :color="riskColor(r.level)">
              <Warning />
            </el-icon>
            <div style="flex:1;min-width:0;">
              <div style="font-weight:500;font-size:13px;">{{ r.name }}</div>
              <div style="font-size:11px;" :style="{ color: riskColor(r.level) }">
                {{ riskLevelLabel(r.level) }} · {{ r.remark || '无备注' }}
              </div>
            </div>
            <el-button size="small" type="danger" plain @click="removeRisk(r.id)">删除</el-button>
          </div>
        </div>
      </el-card>
    </div>

    <!-- ── 新建围栏弹窗 ── -->
    <el-dialog v-model="createVisible" :title="createDialogTitle" width="460px" @closed="resetCreate">
      <el-form :model="form" label-width="80px">
        <el-form-item label="围栏名称" required>
          <el-input v-model="form.name" placeholder="如: 仓库A" />
        </el-form-item>
        <el-form-item label="围栏类型">
          <el-radio-group v-model="form.fence_type" @change="onFormTypeChange">
            <el-radio value="circle">圆形</el-radio>
            <el-radio value="polygon">多边形（地图画）</el-radio>
            <el-radio value="administrative">行政区域</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- 圆形参数 -->
        <template v-if="form.fence_type==='circle'">
          <el-form-item label="选坐标">
            <el-button size="small" type="primary" plain @click="startPickLocation">
              📍 在地图上点选
            </el-button>
            <span style="margin-left:8px;font-size:12px;color:#909399;">
              当前: {{ form.lat.toFixed(5) }}, {{ form.lng.toFixed(5) }}
            </span>
          </el-form-item>
          <el-form-item label="中心纬度">
            <el-input-number v-model="form.lat" :precision="6" :step="0.001" style="width:100%;" />
          </el-form-item>
          <el-form-item label="中心经度">
            <el-input-number v-model="form.lng" :precision="6" :step="0.001" style="width:100%;" />
          </el-form-item>
          <el-form-item label="半径(米)">
            <el-input-number v-model="form.radius" :min="100" :max="200000" :step="500" style="width:100%;" />
          </el-form-item>
        </template>

        <!-- 多边形参数 -->
        <template v-if="form.fence_type==='polygon'">
          <el-form-item label="操作说明">
            <el-text type="warning" size="small">
              点击「开始绘制」后在地图上逐点单击，双击完成（至少3点）
            </el-text>
          </el-form-item>
        </template>

        <!-- 行政区参数：省 → 市 → 区三级 -->
        <template v-if="form.fence_type==='administrative'">
          <el-form-item label="省份" required>
            <el-select v-model="form.provinceCode" style="width:100%;" placeholder="选择省份"
              @change="onProvinceChange" clearable filterable>
              <el-option v-for="p in PROVINCES" :key="p.code" :label="p.name" :value="p.code" />
            </el-select>
          </el-form-item>
          <el-form-item label="城市/地区" v-if="form.provinceCode">
            <el-select v-model="form.adcode" style="width:100%;" placeholder="可选：选择具体城市"
              :loading="loadingCities" clearable @change="onAdcodeChange">
              <el-option :label="'整个省/直辖市'" :value="form.provinceCode" />
              <el-option v-for="c in cities" :key="c.code" :label="c.name" :value="c.code" />
            </el-select>
          </el-form-item>
          <el-form-item label="区县" v-if="districts.length">
            <el-select v-model="form.adcode" style="width:100%;" placeholder="可选：选择区县"
              clearable @change="onAdcodeChange">
              <el-option :label="'整个城市'" :value="form.cityCode" />
              <el-option v-for="d in districts" :key="d.code" :label="d.name" :value="d.code" />
            </el-select>
          </el-form-item>
        </template>

        <el-form-item label="颜色">
          <el-color-picker v-model="form.color"
            :predefine="['#409EFF','#67c23a','#e6a23c','#f56c6c','#909399','#9b59b6']" />
        </el-form-item>

        <el-form-item label="报警触发">
          <div style="display:flex;gap:16px;align-items:center;">
            <el-checkbox v-model="form.alarm_enter">进入围栏报警</el-checkbox>
            <el-checkbox v-model="form.alarm_exit">离开围栏报警</el-checkbox>
          </div>
          <div style="font-size:11px;color:#909399;margin-top:4px;">
            设备进入/离开围栏时自动产生报警记录
          </div>
        </el-form-item>

        <!-- ── 高级规则 ────────────────────────────────────────────── -->
        <el-divider content-position="left" style="margin:8px 0;">
          <span style="font-size:12px;color:#909399;">高级规则（选填）</span>
        </el-divider>

        <el-form-item label="停留超时">
          <el-input-number
            v-model="form.alarm_dwell"
            :min="0" :max="86400" :step="60"
            style="width:140px;"
            placeholder="0"
          />
          <span style="margin-left:8px;font-size:12px;color:#909399;">
            秒（0=不启用，如 600=10分钟）
          </span>
        </el-form-item>

        <el-form-item label="围栏限速">
          <el-input-number
            v-model="form.speed_limit"
            :min="0" :max="200" :step="10"
            style="width:120px;"
            placeholder="0"
          />
          <span style="margin-left:8px;font-size:12px;color:#909399;">
            km/h（0=不限速）
          </span>
        </el-form-item>

        <el-form-item label="生效时段">
          <div style="display:flex;align-items:center;gap:8px;">
            <el-time-select
              v-model="form.valid_start"
              start="00:00" end="23:30" step="00:30"
              placeholder="开始时间"
              style="width:120px;"
              clearable
            />
            <span style="color:#909399;">至</span>
            <el-time-select
              v-model="form.valid_end"
              start="00:30" end="24:00" step="00:30"
              placeholder="结束时间"
              style="width:120px;"
              clearable
            />
          </div>
          <div style="font-size:11px;color:#909399;margin-top:4px;">
            仅在指定时段内触发告警（留空=全天生效）
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible=false">取消</el-button>
        <el-button type="primary" @click="saveFence" :loading="saving">
          {{ form.fence_type==='polygon' ? '开始绘制' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ── 新建标注点弹窗 ── -->
    <el-dialog v-model="markVisible" title="新建标注点" width="400px">
      <el-form :model="markForm" label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="markForm.name" placeholder="如: 北门入口" />
        </el-form-item>
        <el-form-item label="坐标">
          <el-text size="small">纬度 {{ markForm.lat?.toFixed(6) }}，经度 {{ markForm.lng?.toFixed(6) }}</el-text>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="markForm.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="markVisible=false">取消</el-button>
        <el-button type="primary" @click="saveMark">创建</el-button>
      </template>
    </el-dialog>

    <!-- ── 新建风险点弹窗 ── -->
    <el-dialog v-model="riskVisible" title="新建风险点" width="400px">
      <el-form :model="riskForm" label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="riskForm.name" placeholder="如: 危险品存储区" />
        </el-form-item>
        <el-form-item label="坐标">
          <el-text size="small">纬度 {{ riskForm.lat?.toFixed(6) }}，经度 {{ riskForm.lng?.toFixed(6) }}</el-text>
        </el-form-item>
        <el-form-item label="危险等级">
          <el-radio-group v-model="riskForm.level">
            <el-radio value="low">低</el-radio>
            <el-radio value="medium">中</el-radio>
            <el-radio value="high">高</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="riskForm.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="riskVisible=false">取消</el-button>
        <el-button type="primary" @click="saveRisk">创建</el-button>
      </template>
    </el-dialog>

    <!-- ── 关联设备弹窗 ── -->
    <el-dialog v-model="deviceBindVisible"
      :title="bindingFence ? `关联设备 — ${bindingFence.name}` : '关联设备'"
      width="420px">
      <div v-if="!allDevices.length" style="text-align:center;color:#999;padding:20px 0;">
        暂无设备可选
      </div>
      <el-checkbox-group v-else v-model="selectedPhones">
        <div v-for="d in allDevices" :key="d.phone"
          style="padding:6px 0;border-bottom:1px solid #f5f5f5;display:flex;align-items:center;gap:8px;">
          <el-checkbox :value="d.phone" style="margin:0;" />
          <div style="flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.5;">
            <span style="font-size:13px;font-weight:500;">{{ d.name || '未命名' }}</span>
            <span style="font-size:11px;color:#909399;">{{ d.phone }}</span>
          </div>
        </div>
      </el-checkbox-group>
      <div style="margin-top:12px;font-size:12px;color:#909399;">
        已选 {{ selectedPhones.length }} 台设备
      </div>
      <template #footer>
        <el-button @click="deviceBindVisible=false">取消</el-button>
        <el-button type="primary" @click="saveDeviceBinding" :loading="bindingSaving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Location, Warning, Setting, View, Hide } from '@element-plus/icons-vue'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { fenceApi, markApi, riskApi, deviceApi, portalApi, customerApi, isAdmin } from '@/api'
import { TDT_MAP_STYLE, circleToPolygon } from '@/utils/mapStyle'

// ── 状态 ─────────────────────────────────────────────────────────────────────
const activeTab       = ref('fence')
const drawMode        = ref('circle')
const drawingPoly     = ref(false)
const polyPoints      = ref([])          // 当前正在画的多边形顶点 [lng,lat][]
const saving          = ref(false)
const pickingLocation = ref(false)       // 圆形围栏地图选点模式

// 围栏
const fences          = ref([])
const fenceSearch     = ref('')
const fenceTypeFilter = ref('')
const fenceAccountId  = ref(null)
const accountOptions  = ref([])
const selectedFenceIds= ref([])
const createVisible   = ref(false)
const form = ref({ name:'', fence_type:'circle', lat:39.9042, lng:116.4074, radius:2000, color:'#409EFF', adcode:'', provinceCode:'', cityCode:'', alarm_enter:true, alarm_exit:true, alarm_dwell:0, speed_limit:0, valid_start:'', valid_end:'' })

// 行政区级联
const cities          = ref([])     // 市列表
const districts       = ref([])     // 区县列表
const loadingCities   = ref(false)

// 标注点
const marks           = ref([])
const markSearch      = ref('')
const markVisible     = ref(false)
const markForm        = ref({ name:'', lat:null, lng:null, remark:'' })

// 风险点
const risks           = ref([])
const riskVisible     = ref(false)
const riskForm        = ref({ name:'', lat:null, lng:null, level:'medium', remark:'' })

// 关联设备
const deviceBindVisible = ref(false)
const bindingFence      = ref(null)
const allDevices        = ref([])
const selectedPhones    = ref([])
const bindingSaving     = ref(false)

// ── 常量 ─────────────────────────────────────────────────────────────────────
const PROVINCES = [
  { name:'北京市', code:'110000' }, { name:'天津市', code:'120000' },
  { name:'河北省', code:'130000' }, { name:'山西省', code:'140000' },
  { name:'内蒙古自治区', code:'150000' }, { name:'辽宁省', code:'210000' },
  { name:'吉林省', code:'220000' }, { name:'黑龙江省', code:'230000' },
  { name:'上海市', code:'310000' }, { name:'江苏省', code:'320000' },
  { name:'浙江省', code:'330000' }, { name:'安徽省', code:'340000' },
  { name:'福建省', code:'350000' }, { name:'江西省', code:'360000' },
  { name:'山东省', code:'370000' }, { name:'河南省', code:'410000' },
  { name:'湖北省', code:'420000' }, { name:'湖南省', code:'430000' },
  { name:'广东省', code:'440000' }, { name:'广西壮族自治区', code:'450000' },
  { name:'海南省', code:'460000' }, { name:'重庆市', code:'500000' },
  { name:'四川省', code:'510000' }, { name:'贵州省', code:'520000' },
  { name:'云南省', code:'530000' }, { name:'西藏自治区', code:'540000' },
  { name:'陕西省', code:'610000' }, { name:'甘肃省', code:'620000' },
  { name:'青海省', code:'630000' }, { name:'宁夏回族自治区', code:'640000' },
  { name:'新疆维吾尔自治区', code:'650000' },
]

const createDialogTitle = computed(() => ({
  circle: '新建圆形围栏', polygon: '新建多边形围栏', administrative: '新建行政区围栏'
}[form.value.fence_type] || '新建围栏'))

function fenceTypeLabel(t) {
  return { circle:'圆形', polygon:'多边形', administrative:'行政区' }[t] || t
}
function riskColor(l) {
  return { low:'#67c23a', medium:'#e6a23c', high:'#f56c6c' }[l] || '#909399'
}
function riskLevelLabel(l) {
  return { low:'低风险', medium:'中风险', high:'高风险' }[l] || l
}

// ── XSS 防护：HTML 转义 ───────────────────────────────────────────────────────
const _esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
// 颜色安全校验：只允许 #RGB 或 #RRGGBB 格式
const _safeColor = (c) => /^#[0-9a-fA-F]{3,6}$/.test(c) ? c : '#409EFF'

// ── 地图 ─────────────────────────────────────────────────────────────────────
let map = null
const fenceCanvas      = ref(null)   // 2D canvas 叠加层（绕过 MapLibre v6 GL fill 不渲染问题）
const visibleFenceIds  = ref(new Set())  // 当前在地图上显示的围栏 ID 集合
const fenceLayers      = new Set()   // 已加到地图的 layer id（保留用于点击 hit-test）
const fenceNameMarkers = []          // 围栏名称 HTML Marker，随围栏刷新一起清除
const deviceMarkers = []
const markMarkers   = {}           // id → Marker
const riskMarkers   = {}
let   polyMarkers   = []           // 多边形绘制临时标记
let   polyLineAdded = false
let   pendingPolyName       = ''
let   pendingPolyColor      = '#409EFF'
let   pendingPolyAlarmEnter = true
let   pendingPolyAlarmExit  = true
let   pendingPolyAlarmDwell = 0
let   pendingPolySpeedLimit = 0
let   pendingPolyValidStart = ''
let   pendingPolyValidEnd   = ''

// 围栏绘制到地图
function renderFences() {
  console.log('[renderFences] 开始 fences:', fences.value.length, 'styleLoaded:', map?.isStyleLoaded())
  if (!map || !map.isStyleLoaded()) { console.warn('[renderFences] 提前退出'); return }

  // ── 清理行政区预览图层（如果创建弹窗刚关闭但未清理干净）────────────────────
  try {
    if (map.getLayer('admin-preview-fill')) map.removeLayer('admin-preview-fill')
    if (map.getLayer('admin-preview-line')) map.removeLayer('admin-preview-line')
    if (map.getSource('admin-preview'))     map.removeSource('admin-preview')
  } catch {}

  // ── 清理旧图层：必须先删所有 layer，再删 source ────────────────────────────
  const prevLayerIds  = [...fenceLayers].filter(id => /-fill$|-glow$|-line$|-dash$/.test(id))
  const prevSourceIds = [...fenceLayers].filter(id => /^fence-\d+$/.test(id))
  prevLayerIds.forEach(id  => { try { if (map.getLayer(id))  map.removeLayer(id)  } catch {} })
  prevSourceIds.forEach(id => { try { if (map.getSource(id)) map.removeSource(id) } catch {} })
  fenceLayers.clear()

  // 清理旧名称 Marker
  fenceNameMarkers.forEach(m => m.remove())
  fenceNameMarkers.length = 0

  // ── 逐条围栏渲染 ──────────────────────────────────────────────────────────
  fences.value.forEach(f => {
    const sid = `fence-${f.id}`

    // 1. 计算 GeoJSON geometry 和质心
    let geometry  = null
    let centroidLng = null, centroidLat = null

    if (f.fence_type === 'circle' && f.lat && f.lng) {
      geometry     = circleToPolygon([f.lng, f.lat], f.radius)
      centroidLng  = f.lng
      centroidLat  = f.lat
    } else if ((f.fence_type === 'polygon' || f.fence_type === 'administrative') && f.coordinates) {
      try {
        const coords = typeof f.coordinates === 'string' ? JSON.parse(f.coordinates) : f.coordinates
        if (Array.isArray(coords) && coords.length >= 3) {
          geometry    = { type: 'Polygon', coordinates: [coords] }
          centroidLng = coords.reduce((s, c) => s + c[0], 0) / coords.length
          centroidLat = coords.reduce((s, c) => s + c[1], 0) / coords.length
        }
      } catch {}
    }
    if (!geometry) { console.warn('[renderFences] 跳过', sid, '无 geometry'); return }
    // 打印第一个坐标环的边界范围，便于确认坐标位置
    try {
      const ring = geometry.coordinates[0]
      const lngs = ring.map(c => c[0]), lats = ring.map(c => c[1])
      console.log(`[围栏] ${sid} 边界: lng[${Math.min(...lngs).toFixed(4)}, ${Math.max(...lngs).toFixed(4)}] lat[${Math.min(...lats).toFixed(4)}, ${Math.max(...lats).toFixed(4)}]`)
    } catch {}
    console.log('[renderFences] 渲染', sid, 'type:', f.fence_type, 'color:', f.color)

    // 2. 添加 GL 图层 —— 每层独立 try-catch，一层失败不影响其余层
    const color = f.color || '#FF4444'

    // Source：若已存在则更新数据（处理重复渲染场景），不存在则新建
    try {
      const existing = map.getSource(sid)
      if (existing) {
        existing.setData({ type: 'Feature', properties: { id: f.id }, geometry })
        console.log('[围栏] source 更新', sid)
      } else {
        map.addSource(sid, {
          type: 'geojson',
          data: { type: 'Feature', properties: { id: f.id }, geometry },
        })
        fenceLayers.add(sid)
        console.log('[围栏] source 新建', sid)
      }
    } catch (err) { console.warn(`[围栏] ${sid} source 失败:`, err) }

    // MapLibre v6 中动态 addLayer（不指定 before）会被 raster 层遮盖，
    // 因此把围栏层插入到 tdt-cva-layer（注记层）之前，确保在底图上可见。
    // 渲染顺序：tdt-vec → fence-fill/line/glow → tdt-cva（道路标注在围栏上方）
    const beforeLayer = map.getLayer('tdt-cva-layer') ? 'tdt-cva-layer' : undefined

    // 半透明填充（核心视觉层）
    try {
      const existingFill = map.getLayer(`${sid}-fill`)
      if (!existingFill) {
        map.addLayer({
          id: `${sid}-fill`, type: 'fill', source: sid,
          paint: { 'fill-color': '#FF2200', 'fill-opacity': 0.35 },
        }, beforeLayer)
        fenceLayers.add(`${sid}-fill`)
        console.log('[围栏] fill 图层已添加', sid, '位于', beforeLayer ?? '末尾')
        // 点击填充区弹出围栏信息
        map.on('click', `${sid}-fill`, e => {
          const devCount = f.devices ? f.devices.split(',').filter(Boolean).length : 0
          new maplibregl.Popup({ closeButton: true, maxWidth: '220px' })
            .setLngLat(e.lngLat)
            .setHTML([
              `<b style="font-size:14px;color:${_safeColor(color)};">${_esc(f.name)}</b>`,
              `<div style="margin-top:4px;font-size:12px;color:#555;">类型：${_esc(fenceTypeLabel(f.fence_type))}</div>`,
              f.fence_type === 'circle' ? `<div style="font-size:12px;color:#555;">半径：${_esc(String(f.radius))} m</div>` : '',
              devCount ? `<div style="font-size:12px;color:#409EFF;">关联设备：${_esc(String(devCount))} 台</div>` : '',
            ].join(''))
            .addTo(map)
        })
      }
    } catch (err) { console.warn(`[围栏] ${sid}-fill 失败:`, err) }

    // 实线主边框
    try {
      if (!map.getLayer(`${sid}-line`)) {
        map.addLayer({
          id: `${sid}-line`, type: 'line', source: sid,
          paint: { 'line-color': '#FF2200', 'line-width': 3 },
        }, beforeLayer)
        fenceLayers.add(`${sid}-line`)
        console.log('[围栏] line 图层已添加', sid)
      }
    } catch (err) { console.warn(`[围栏] ${sid}-line 失败:`, err) }

    // 外发光晕圈（可选，失败不影响主渲染）
    try {
      if (!map.getLayer(`${sid}-glow`)) {
        map.addLayer({
          id: `${sid}-glow`, type: 'line', source: sid,
          paint: { 'line-color': color, 'line-width': 10, 'line-opacity': 0.15, 'line-blur': 6 },
        }, beforeLayer)
        fenceLayers.add(`${sid}-glow`)
      }
    } catch (err) { /* 发光效果失败静默跳过 */ }

    // 围栏名称不在地图上显示（名称见左侧列表与点击弹窗）
  })

  // GL 层已加（用于 click hit-test），再用 2D canvas 绘制可见视觉效果
  drawFencesOnCanvas()
  try { map.triggerRepaint() } catch {}
  console.log('[renderFences] 完成, fenceLayers:', [...fenceLayers].join(','))
  console.log('[renderFences] 当前所有 GL 图层:', map.getStyle()?.layers?.map(l => l.id).join(','))
}

// 标注点到地图
function renderMarks() {
  if (!map) return
  // 清除旧 marker
  Object.values(markMarkers).forEach(m => m.remove())
  Object.keys(markMarkers).forEach(k => delete markMarkers[k])

  marks.value.forEach(m => {
    const el = document.createElement('div')
    el.style.cssText = 'width:12px;height:12px;border-radius:50%;background:#409eff;border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.3);cursor:pointer;'
    const marker = new maplibregl.Marker({ element: el })
      .setLngLat([m.lng, m.lat])
      .setPopup(new maplibregl.Popup({ closeButton:false })
        .setHTML(`<b>${_esc(m.name)}</b>${m.remark ? '<br>'+_esc(m.remark) : ''}`))
      .addTo(map)
    markMarkers[m.id] = marker
  })
}

// 风险点到地图
function renderRisks() {
  if (!map) return
  Object.values(riskMarkers).forEach(m => m.remove())
  Object.keys(riskMarkers).forEach(k => delete riskMarkers[k])

  risks.value.forEach(r => {
    const el = document.createElement('div')
    const c = riskColor(r.level)
    el.style.cssText = `width:14px;height:14px;border-radius:50%;background:${c};border:2px solid #fff;box-shadow:0 0 6px ${c};cursor:pointer;`
    const marker = new maplibregl.Marker({ element: el })
      .setLngLat([r.lng, r.lat])
      .setPopup(new maplibregl.Popup({ closeButton:false })
        .setHTML(`<b>${_esc(r.name)}</b><br>${_esc(riskLevelLabel(r.level))}${r.remark ? '<br>'+_esc(r.remark) : ''}`))
      .addTo(map)
    riskMarkers[r.id] = marker
  })
}

// 多边形绘制辅助线
function updatePolyLine() {
  if (!map) return
  const pts = polyPoints.value

  // GeoJSON：2个点以上画折线（含闭合预览），1个点时清空
  const geojson = pts.length >= 2
    ? { type: 'Feature', geometry: { type: 'LineString', coordinates: [...pts, pts[0]] } }
    : { type: 'FeatureCollection', features: [] }

  const src = map.getSource('poly-drawing-line')
  if (src) {
    // source 已存在，直接更新数据，不重建图层（避免重复创建报错）
    src.setData(geojson)
    return
  }

  if (pts.length < 2) return   // 不到两点且 source 还不存在，不建图层

  // 第一次：同时创建 source + layer
  try {
    map.addSource('poly-drawing-line', { type: 'geojson', data: geojson })
    map.addLayer({
      id: 'poly-drawing-line', type: 'line', source: 'poly-drawing-line',
      paint: { 'line-color': '#ff9900', 'line-width': 2.5 },
    })
    polyLineAdded = true
  } catch (err) {
    console.warn('[围栏绘制] 折线图层创建失败:', err)
  }
}

function clearPolyDrawing() {
  polyMarkers.forEach(m => m.remove())
  polyMarkers = []
  polyPoints.value = []
  if (map) {
    try { if (map.getLayer('poly-drawing-line')) map.removeLayer('poly-drawing-line') } catch {}
    try { if (map.getSource('poly-drawing-line')) map.removeSource('poly-drawing-line') } catch {}
  }
  polyLineAdded = false
  drawingPoly.value = false
}

// 完成多边形，保存并渲染
async function finishPolygon() {
  if (polyPoints.value.length < 3) {
    ElMessage.warning('多边形至少需要 3 个顶点')
    return
  }
  const coords = [...polyPoints.value, polyPoints.value[0]]  // 闭合
  const _polyPayload = { name: pendingPolyName, fence_type:'polygon', coordinates: coords, color: pendingPolyColor,
    alarm_enter: pendingPolyAlarmEnter, alarm_exit: pendingPolyAlarmExit,
    alarm_dwell: pendingPolyAlarmDwell, speed_limit: pendingPolySpeedLimit,
    valid_start: pendingPolyValidStart, valid_end: pendingPolyValidEnd }
  try {
    // 按身份分流：管理员走管理端接口，客户/子账号走门户接口(否则被 401 拦截、静默失败)
    if (isAdmin()) {
      await fenceApi.create(_polyPayload)
    } else {
      await portalApi.createFence(_polyPayload)
    }
    ElMessage.success('多边形围栏创建成功')
    clearPolyDrawing()
    await loadFences()    // 等围栏渲染完再 fit
    _fitAllFences()       // 立即跳视口到所有围栏（duration:0 无动画延迟）
  } catch (e) {
    ElMessage.error('多边形围栏创建失败：' + (e?.message || '请重试'))
  }
}

// ── 数据加载 ─────────────────────────────────────────────────────────────────
async function loadFences() {
  const res = isAdmin()
    ? await fenceApi.list({ name: fenceSearch.value, fence_type: fenceTypeFilter.value, customer_id: fenceAccountId.value || undefined })
    : await portalApi.fences({ name: fenceSearch.value, fence_type: fenceTypeFilter.value })
  const newFences = res.data || []
  // 只对本次新增的围栏默认设为可见，已有围栏的显隐状态保持不变
  const prevIds   = new Set(fences.value.map(f => f.id))
  fences.value    = newFences
  const vs        = new Set(visibleFenceIds.value)
  const hiddenIds = getStoredHiddenIds()
  // 新围栏默认显示，但若曾被手动隐藏（存在 localStorage）则保持隐藏
  newFences.forEach(f => { if (!prevIds.has(f.id) && !hiddenIds.has(f.id)) vs.add(f.id) })
  visibleFenceIds.value = vs
  if (map && map.isStyleLoaded()) {
    renderFences()
    // 每次加载完成后，若没有搜索过滤，就把视口对准所有围栏（duration:0 立即跳转）
    if (fences.value.length && !fenceSearch.value && !fenceTypeFilter.value) {
      _fitAllFences()
    }
  } else if (map) {
    map.once('idle', () => {
      renderFences()
      if (fences.value.length) _fitAllFences()
    })
  }
}

// ── 围栏可见状态持久化（localStorage）──────────────────────────────────────────
function getStoredHiddenIds() {
  try {
    const raw = localStorage.getItem('fence_hidden_ids')
    return raw ? new Set(JSON.parse(raw)) : new Set()
  } catch { return new Set() }
}
function saveHiddenIds(hiddenSet) {
  try {
    localStorage.setItem('fence_hidden_ids', JSON.stringify([...hiddenSet]))
  } catch {}
}

// ── 围栏 Canvas 叠加绘制（绕过 MapLibre v6 GL fill 层合成问题）──────────────────
function hexToRgba(hex, alpha) {
  // 支持 3 位 / 6 位 hex，默认回退红色
  const clean = (hex || '#FF4444').replace('#', '')
  const full = clean.length === 3
    ? clean.split('').map(c => c + c).join('')
    : clean
  const r = parseInt(full.slice(0, 2), 16) || 0
  const g = parseInt(full.slice(2, 4), 16) || 0
  const b = parseInt(full.slice(4, 6), 16) || 0
  return `rgba(${r},${g},${b},${alpha})`
}

function drawFencesOnCanvas() {
  const cvs = fenceCanvas.value
  if (!cvs || !map) return

  const container = map.getContainer()
  const w = container.offsetWidth
  const h = container.offsetHeight
  if (!w || !h) return

  const dpr = window.devicePixelRatio || 1
  // 只在尺寸变化时才重置 canvas 大小（避免每帧重分配内存）
  if (cvs.width !== w * dpr || cvs.height !== h * dpr) {
    cvs.width  = w * dpr
    cvs.height = h * dpr
  }

  const ctx = cvs.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  fences.value.forEach(f => {
    if (!visibleFenceIds.value.has(f.id)) return   // 被隐藏的围栏跳过

    // 1. 获取围栏顶点列表（[lng, lat] 格式）
    let ring = null
    if (f.fence_type === 'circle') {
      if (!f.lat || !f.lng) return
      const lat = f.lat, lng = f.lng, radius = f.radius || 2000
      ring = []
      for (let i = 0; i < 64; i++) {
        const angle = (i / 64) * 2 * Math.PI
        const dLat = (radius * Math.sin(angle) / 6371000) * (180 / Math.PI)
        const dLng = (radius * Math.cos(angle) / (6371000 * Math.cos(lat * Math.PI / 180))) * (180 / Math.PI)
        ring.push([lng + dLng, lat + dLat])
      }
    } else if (f.coordinates) {
      try { ring = typeof f.coordinates === 'string' ? JSON.parse(f.coordinates) : f.coordinates } catch { return }
    }
    if (!ring || ring.length < 3) return

    // 2. 经纬度 → 屏幕像素
    const pts = ring.map(([lng, lat]) => {
      const p = map.project([lng, lat])
      return [p.x, p.y]
    })

    const fc = f.color || '#FF4444'

    // 3. 绘制半透明填充
    ctx.beginPath()
    ctx.moveTo(pts[0][0], pts[0][1])
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1])
    ctx.closePath()
    ctx.fillStyle = hexToRgba(fc, 0.25)
    ctx.fill()

    // 4. 绘制实线边框
    ctx.beginPath()
    ctx.moveTo(pts[0][0], pts[0][1])
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1])
    ctx.closePath()
    ctx.strokeStyle = fc
    ctx.lineWidth   = 2.5
    ctx.stroke()

    // 5. 外发光效果
    ctx.beginPath()
    ctx.moveTo(pts[0][0], pts[0][1])
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1])
    ctx.closePath()
    ctx.strokeStyle = hexToRgba(fc, 0.2)
    ctx.lineWidth   = 8
    ctx.stroke()
  })

  ctx.setTransform(1, 0, 0, 1, 0, 0)
}

// 缩放到所有围栏的外接矩形
function _fitAllFences() {
  if (!map || !fences.value.length) return
  const allLngs = [], allLats = []
  fences.value.forEach(f => {
    if (f.fence_type === 'circle' && f.lat && f.lng) {
      const deg = (f.radius || 2000) / 111000
      allLngs.push(f.lng - deg, f.lng + deg)
      allLats.push(f.lat - deg, f.lat + deg)
    } else if (f.coordinates) {
      try {
        const coords = typeof f.coordinates === 'string' ? JSON.parse(f.coordinates) : f.coordinates
        coords.forEach(c => { allLngs.push(c[0]); allLats.push(c[1]) })
      } catch {}
    }
  })
  if (allLngs.length) {
    const sw = [Math.min(...allLngs), Math.min(...allLats)]
    const ne = [Math.max(...allLngs), Math.max(...allLats)]
    console.log('[_fitAllFences] 调整视口到围栏:', sw, ne)
    map.fitBounds([sw, ne], { padding: 80, maxZoom: 15, duration: 0 })
  }
}

async function loadMarks() {
  const res = isAdmin() ? await markApi.list({ name: markSearch.value }) : await portalApi.markPoints.list({ name: markSearch.value })
  marks.value = res.data || []
  renderMarks()
}

async function loadRisks() {
  const res = isAdmin() ? await riskApi.list() : await portalApi.riskPoints.list()
  risks.value = res.data || []
  renderRisks()
}

// 定位到围栏
function flyToFence(f) {
  if (!map) return
  if (f.fence_type === 'circle' && f.lat && f.lng) {
    // 根据半径计算合适的 zoom
    const zoom = f.radius <= 500 ? 16 : f.radius <= 2000 ? 14 : f.radius <= 10000 ? 12 : f.radius <= 50000 ? 10 : 8
    map.flyTo({ center: [f.lng, f.lat], zoom, duration: 600 })
  } else if (f.coordinates) {
    try {
      const coords = typeof f.coordinates === 'string' ? JSON.parse(f.coordinates) : f.coordinates
      if (coords.length) {
        const lngs = coords.map(c => c[0]), lats = coords.map(c => c[1])
        map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: 40, duration: 600 })
      }
    } catch {}
  }
}

// ── 围栏 CRUD ─────────────────────────────────────────────────────────────────
function openCreate() {
  form.value = { name:'', fence_type: drawMode.value, lat:39.9042, lng:116.4074,
    radius:2000, color:'#409EFF', adcode:'', provinceCode:'', cityCode:'',
    alarm_enter: true, alarm_exit: true, alarm_dwell: 0, speed_limit: 0, valid_start: '', valid_end: '' }
  cities.value = []
  districts.value = []
  createVisible.value = true
}

function resetCreate() {
  form.value = { name:'', fence_type:'circle', lat:39.9042, lng:116.4074,
    radius:2000, color:'#409EFF', adcode:'', provinceCode:'', cityCode:'',
    alarm_enter: true, alarm_exit: true, alarm_dwell: 0, speed_limit: 0, valid_start: '', valid_end: '' }
  cities.value = []
  districts.value = []
  // 清除行政区预览图层
  const src = 'admin-preview'
  if (map) {
    if (map.getLayer(`${src}-fill`)) map.removeLayer(`${src}-fill`)
    if (map.getLayer(`${src}-line`)) map.removeLayer(`${src}-line`)
    if (map.getSource(src)) map.removeSource(src)
  }
}

function onFormTypeChange() {
  cities.value = []
  districts.value = []
  form.value.adcode = ''
  form.value.provinceCode = ''
  form.value.cityCode = ''
}

// 圆形围栏：打开地图选点模式
function startPickLocation() {
  createVisible.value = false
  pickingLocation.value = true
}

// 遮罩层点击：把屏幕坐标转换为地图经纬度
function onPickOverlayClick(e) {
  if (!map) return
  const canvas = map.getCanvas()
  const rect = canvas.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  const lnglat = map.unproject([x, y])
  form.value.lat = parseFloat(lnglat.lat.toFixed(6))
  form.value.lng = parseFloat(lnglat.lng.toFixed(6))
  pickingLocation.value = false
  createVisible.value = true
}

// 省份变更：获取下级城市列表
async function onProvinceChange(code) {
  form.value.adcode = code   // 默认选整个省
  form.value.cityCode = ''
  cities.value = []
  districts.value = []
  if (!code) return
  // 预览省份边界
  await previewAdminBoundary(code)
  // 拉取子区域（城市）
  loadingCities.value = true
  try {
    const r = await fetch(`/mapdata/areas_v3/bound/${code}_full.json`)
    const geo = await r.json()
    const features = geo.features || []
    cities.value = features
      .filter(f => f.properties?.adcode && f.properties.adcode !== code)
      .map(f => ({ name: f.properties.name, code: String(f.properties.adcode) }))
  } catch {} finally { loadingCities.value = false }
}

// 城市/adcode 变更：预览边界，并尝试拉取下级区县
async function onAdcodeChange(adcode) {
  if (!adcode) return
  await previewAdminBoundary(adcode)
  // 判断是否是城市级（6位且不是省代码）
  const isCity = adcode.length === 6 && !adcode.endsWith('0000')
  if (isCity) {
    form.value.cityCode = adcode
    districts.value = []
    try {
      const r = await fetch(`/mapdata/areas_v3/bound/${adcode}_full.json`)
      const geo = await r.json()
      const features = geo.features || []
      const subs = features.filter(f => f.properties?.adcode && String(f.properties.adcode) !== adcode)
      if (subs.length) {
        districts.value = subs.map(f => ({ name: f.properties.name, code: String(f.properties.adcode) }))
      }
    } catch {}
  }
}

// 拉取并预览行政区边界
async function previewAdminBoundary(adcode) {
  if (!adcode || !map) return
  try {
    const r = await fetch(`/mapdata/areas_v3/bound/${adcode}.json`)
    const geo = await r.json()
    const src = 'admin-preview'
    if (map.getLayer(`${src}-fill`)) map.removeLayer(`${src}-fill`)
    if (map.getLayer(`${src}-line`)) map.removeLayer(`${src}-line`)
    if (map.getSource(src)) map.removeSource(src)
    map.addSource(src, { type:'geojson', data: geo })
    map.addLayer({ id:`${src}-fill`, type:'fill', source:src, paint:{ 'fill-color':form.value.color,'fill-opacity':0.2 } })
    map.addLayer({ id:`${src}-line`, type:'line', source:src, paint:{ 'line-color':form.value.color,'line-width':2 } })
    // 提取坐标（支持 MultiPolygon）
    const feat = geo.features?.[0] || geo
    const geom = feat.geometry || feat
    let coords = []
    if (geom.type === 'Polygon') coords = geom.coordinates[0] || []
    else if (geom.type === 'MultiPolygon') coords = geom.coordinates.flat(1)[0] || []
    else if (feat.features) {
      const g0 = feat.features[0]?.geometry
      coords = g0?.type === 'MultiPolygon' ? g0.coordinates.flat(1)[0] || [] : g0?.coordinates?.[0] || []
    }
    form.value._adminCoords = coords
    if (coords.length) {
      const lngs = coords.map(c => c[0]), lats = coords.map(c => c[1])
      map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding:30 })
    }
  } catch(e) { console.error('行政区边界加载失败', e) }
}

async function saveFence() {
  if (!form.value.name.trim()) { ElMessage.error('请填写围栏名称'); return }

  if (form.value.fence_type === 'polygon') {
    // 关闭弹窗，进入地图绘制模式
    pendingPolyName       = form.value.name
    pendingPolyColor      = form.value.color
    pendingPolyAlarmEnter = form.value.alarm_enter
    pendingPolyAlarmExit  = form.value.alarm_exit
    pendingPolyAlarmDwell = form.value.alarm_dwell
    pendingPolySpeedLimit = form.value.speed_limit
    pendingPolyValidStart = form.value.valid_start
    pendingPolyValidEnd   = form.value.valid_end
    createVisible.value = false
    drawMode.value = 'polygon'
    drawingPoly.value = true
    ElMessage.info('请在地图上点击添加顶点，双击完成绘制')
    return
  }

  saving.value = true
  try {
    const payload = { ...form.value }
    if (payload.fence_type === 'administrative') {
      payload.coordinates = payload._adminCoords || []
      delete payload._adminCoords
    }
    if (isAdmin()) {
      await fenceApi.create(payload)
    } else {
      await portalApi.createFence(payload)
    }
    ElMessage.success('围栏创建成功')
    createVisible.value = false
    // 清除行政区预览
    if (map) {
      const src = 'admin-preview'
      if (map.getLayer(`${src}-fill`)) map.removeLayer(`${src}-fill`)
      if (map.getLayer(`${src}-line`)) map.removeLayer(`${src}-line`)
      if (map.getSource(src)) map.removeSource(src)
    }
    // 先加载并渲染围栏，再立即跳到目标位置（duration:0 确保用户马上看到围栏）
    await loadFences()
    if (map) {
      _fitAllFences()   // 把视口直接 fit 到所有围栏，无延迟
    }
  } catch {} finally { saving.value = false }
}

async function removeFence(f) {
  await ElMessageBox.confirm(`确定删除围栏 "${f.name}" 吗？`, '确认删除', { type:'warning' })
  if (isAdmin()) {
    await fenceApi.remove(f.id)
  } else {
    await portalApi.removeFence(f.id)
  }
  ElMessage.success('已删除')
  loadFences()
}

async function batchDeleteFences() {
  await ElMessageBox.confirm(`确定删除选中的 ${selectedFenceIds.value.length} 条围栏吗？`, '批量删除', { type:'warning' })
  if (isAdmin()) {
    await fenceApi.batchDelete(selectedFenceIds.value)
  } else {
    // 客户逐条删除
    for (const id of selectedFenceIds.value) await portalApi.removeFence(id)
  }
  ElMessage.success('批量删除成功')
  selectedFenceIds.value = []
  loadFences()
}

// 围栏地图显示/隐藏切换
function toggleFenceVisible(id) {
  const s      = new Set(visibleFenceIds.value)
  const hidden = getStoredHiddenIds()
  if (s.has(id)) { s.delete(id); hidden.add(id) }
  else           { s.add(id);    hidden.delete(id) }
  visibleFenceIds.value = s
  saveHiddenIds(hidden)
  drawFencesOnCanvas()
}

function toggleAllFenceVisible() {
  const allVisible = fences.value.every(f => visibleFenceIds.value.has(f.id))
  const s = new Set(allVisible ? [] : fences.value.map(f => f.id))
  visibleFenceIds.value = s
  // 全部隐藏时把当前所有 id 写入 localStorage；全部显示时清空隐藏列表
  saveHiddenIds(allVisible ? new Set(fences.value.map(f => f.id)) : new Set())
  drawFencesOnCanvas()
}

function toggleSelect(id) {
  const idx = selectedFenceIds.value.indexOf(id)
  if (idx === -1) selectedFenceIds.value.push(id)
  else selectedFenceIds.value.splice(idx, 1)
}

// ── 关联设备 ──────────────────────────────────────────────────────────────────
async function openDeviceBinding(f) {
  bindingFence.value = f
  // 初始化已勾选的手机号
  selectedPhones.value = f.devices ? f.devices.split(',').filter(Boolean) : []
  // 拉取所有设备
  try {
    const res = isAdmin() ? await deviceApi.list({ size: 500 }) : await portalApi.deviceList({ size: 500 })
    allDevices.value = res.data?.records || []
  } catch {
    allDevices.value = []
  }
  deviceBindVisible.value = true
}

async function saveDeviceBinding() {
  if (!bindingFence.value) return
  bindingSaving.value = true
  try {
    if (isAdmin()) {
      await fenceApi.updateDevices(bindingFence.value.id, selectedPhones.value)
    } else {
      await portalApi.fenceDevices(bindingFence.value.id, selectedPhones.value)
    }
    ElMessage.success('关联设备已保存')
    deviceBindVisible.value = false
    // 本地更新 devices 字段，无需重新拉接口
    const f = fences.value.find(x => x.id === bindingFence.value.id)
    if (f) f.devices = selectedPhones.value.join(',')
  } catch {} finally { bindingSaving.value = false }
}

// ── 标注点 CRUD ───────────────────────────────────────────────────────────────
async function saveMark() {
  if (!markForm.value.name.trim()) { ElMessage.error('请填写名称'); return }
  isAdmin() ? await markApi.create(markForm.value) : await portalApi.markPoints.create(markForm.value)
  ElMessage.success('标注点创建成功')
  markVisible.value = false
  loadMarks()
}

async function removeMark(id) {
  isAdmin() ? await markApi.remove(id) : await portalApi.markPoints.remove(id)
  ElMessage.success('已删除')
  loadMarks()
}

// ── 风险点 CRUD ───────────────────────────────────────────────────────────────
async function saveRisk() {
  if (!riskForm.value.name.trim()) { ElMessage.error('请填写名称'); return }
  isAdmin() ? await riskApi.create(riskForm.value) : await portalApi.riskPoints.create(riskForm.value)
  ElMessage.success('风险点创建成功')
  riskVisible.value = false
  loadRisks()
}

async function removeRisk(id) {
  isAdmin() ? await riskApi.remove(id) : await portalApi.riskPoints.remove(id)
  ElMessage.success('已删除')
  loadRisks()
}

// ── tab 切换 ──────────────────────────────────────────────────────────────────
function onTabChange() {
  clearPolyDrawing()
}

// ── 绘制模式切换 ──────────────────────────────────────────────────────────────
function onDrawModeChange() {
  clearPolyDrawing()
}

// ── 生命周期 ──────────────────────────────────────────────────────────────────
onMounted(async () => {
  // 管理员:加载账号列表供"按账号查看围栏"下拉使用
  if (isAdmin()) {
    try {
      const r = await customerApi.listAll()
      accountOptions.value = r.data?.records || r.data || []
    } catch (e) { accountOptions.value = [] }
  }
  await new Promise(r => setTimeout(r, 300))

  map = new maplibregl.Map({
    container: 'fence-map',
    style: TDT_MAP_STYLE,
    center: [104.19, 35.86],
    zoom: 5,
  })
  map.addControl(new maplibregl.NavigationControl(), 'bottom-left')  // 左下角,避免被左上角围栏面板遮挡缩放按钮

  map.on('load', async () => {
    // 地图每次重绘时同步更新围栏 canvas（跟随移动/缩放）
    map.on('render', drawFencesOnCanvas)

    // 单击地图
    map.on('click', e => {
      const { lng, lat } = e.lngLat

      // 优先处理：圆形围栏选点模式
      if (pickingLocation.value) {
        form.value.lat = parseFloat(lat.toFixed(6))
        form.value.lng = parseFloat(lng.toFixed(6))
        pickingLocation.value = false
        createVisible.value = true
        return
      }

      if (activeTab.value === 'fence' && drawMode.value === 'polygon' && drawingPoly.value) {
        // 多边形：添加顶点
        polyPoints.value.push([lng, lat])
        const el = document.createElement('div')
        el.style.cssText = 'width:8px;height:8px;border-radius:50%;background:#ff9900;border:1.5px solid #fff;'
        const m = new maplibregl.Marker({ element: el }).setLngLat([lng, lat]).addTo(map)
        polyMarkers.push(m)
        updatePolyLine()
        return
      }

      if (activeTab.value === 'mark') {
        markForm.value.lat = parseFloat(lat.toFixed(6))
        markForm.value.lng = parseFloat(lng.toFixed(6))
        markVisible.value = true
        return
      }

      if (activeTab.value === 'risk') {
        riskForm.value.lat = parseFloat(lat.toFixed(6))
        riskForm.value.lng = parseFloat(lng.toFixed(6))
        riskVisible.value = true
      }
    })

    // 双击结束多边形绘制
    map.on('dblclick', e => {
      if (activeTab.value === 'fence' && drawMode.value === 'polygon' && drawingPoly.value) {
        e.preventDefault()
        finishPolygon()
      }
    })

    // 加载设备位置
    try {
      const { deviceApi: _da, portalApi: _pa, isAdmin: _ia } = await import('@/api')
      const res = _ia() ? await _da.list({ size: 500 }) : await _pa.deviceList({ size: 500 })
      for (const d of (res.data?.records || [])) {
        if (d.last_lat && d.last_lng) {
          const el = document.createElement('div')
          el.style.cssText = 'width:10px;height:10px;border-radius:50%;background:#409eff;border:1.5px solid #fff;box-shadow:0 0 3px rgba(0,0,0,.3);'
          const m = new maplibregl.Marker({ element: el })
            .setLngLat([d.last_lng, d.last_lat])
            .setPopup(new maplibregl.Popup({ closeButton:false }).setHTML(`<b>${_esc(d.phone)}</b>${d.name ? '<br>'+_esc(d.name) : ''}`))
            .addTo(map)
          deviceMarkers.push(m)
        }
      }
    } catch {}

    loadFences()
    loadMarks()
    loadRisks()
  })
})

// fences.value 变化时兜底触发重渲（处理 map.isStyleLoaded 时序边界情况）
watch(fences, () => {
  if (!map) return
  if (map.isStyleLoaded()) {
    renderFences()
  } else {
    map.once('style.load', renderFences)
  }
})

onUnmounted(() => {
  deviceMarkers.forEach(m => m.remove())
  Object.values(markMarkers).forEach(m => m.remove())
  Object.values(riskMarkers).forEach(m => m.remove())
  if (map) {
    map.off('render', drawFencesOnCanvas)
    map.remove()
  }
})
</script>

<style scoped>
.fence-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
}
.fence-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
}
</style>
