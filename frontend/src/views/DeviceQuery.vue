<template>
  <div style="display:flex;flex-direction:column;height:calc(100vh - 112px);background:#fff;border-radius:4px;overflow:hidden;">
    <!-- Tab 导航 -->
    <div style="display:flex;border-bottom:1px solid #e4e7ed;padding:0 16px;flex-shrink:0;">
      <div v-for="t in tabs" :key="t.key"
        class="dq-tab" :class="{ active: activeTab === t.key }"
        @click="switchTab(t.key)">
        {{ t.label }}
      </div>
    </div>

    <!-- ══ Tab 1：实时位置 ══ -->
    <div v-show="activeTab === 'realtime'" style="flex:1;display:flex;overflow:hidden;">
      <!-- 左侧设备列表 -->
      <div style="width:260px;border-right:1px solid #e4e7ed;display:flex;flex-direction:column;flex-shrink:0;">
        <div style="padding:10px 12px;border-bottom:1px solid #f0f0f0;display:flex;align-items:center;gap:8px;">
          <el-input v-model="devSearch" placeholder="搜索设备" size="small" clearable style="flex:1;" />
          <el-button size="small" text :icon="Refresh" @click="loadDevices" :loading="devLoading" />
        </div>
        <div style="flex:1;overflow-y:auto;">
          <div v-if="!filteredDevices.length && !devLoading"
            style="text-align:center;color:#ccc;padding:30px 0;font-size:13px;">暂无设备</div>
          <div v-for="d in filteredDevices" :key="d.phone"
            class="dq-device-item" :class="{ active: selected?.phone === d.phone }"
            @click="selectDevice(d)">
            <div style="display:flex;align-items:center;gap:8px;">
              <span :style="{
                width:'7px',height:'7px',borderRadius:'50%',flexShrink:0,
                background:d.status===1?'#67c23a':d.status===2?'#f56c6c':'#ccc'
              }" />
              <div style="flex:1;min-width:0;">
                <div style="font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                  {{ d.name || d.phone }}
                </div>
                <div style="font-size:11px;color:#909399;">{{ d.phone }}</div>
              </div>
              <el-tag size="small" :type="d.status===1?'success':d.status===2?'danger':'info'">
                {{ d.status===1?'在线':d.status===2?'报警':'离线' }}
              </el-tag>
            </div>
          </div>
        </div>
      </div>

      <!-- 地图 + 详情侧边栏 -->
      <div style="flex:1;display:flex;overflow:hidden;">
        <div style="flex:1;position:relative;">
          <div id="dq-map" style="position:absolute;inset:0;" />

        </div>
        <!-- 设备详情 -->
        <transition name="slide-detail">
          <div v-if="selected" style="width:260px;border-left:1px solid #e4e7ed;overflow-y:auto;flex-shrink:0;">
            <div style="padding:10px 12px;border-bottom:1px solid #f0f0f0;display:flex;align-items:center;justify-content:space-between;">
              <span style="font-size:13px;font-weight:600;">设备详情</span>
              <el-button text size="small" @click="selected=null">✕</el-button>
            </div>
            <div style="padding:12px;">
              <el-descriptions :column="1" size="small" border>
                <el-descriptions-item label="设备名称">{{ selected.name || '—' }}</el-descriptions-item>
                <el-descriptions-item label="设备号">{{ selected.terminal_id || selected.phone }}</el-descriptions-item>
                <el-descriptions-item label="IMEI">{{ selected.imei || selected.phone }}</el-descriptions-item>
                <el-descriptions-item label="状态">
                  <el-tag size="small" :type="selected.status===1?'success':selected.status===2?'danger':'info'">
                    {{ selected.status===1?'在线':selected.status===2?'报警':'离线' }}
                  </el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="最后上报">{{ selected.last_location_time || '—' }}</el-descriptions-item>
                <el-descriptions-item label="纬度">{{ selected.last_lat?.toFixed(6) || '—' }}</el-descriptions-item>
                <el-descriptions-item label="经度">{{ selected.last_lng?.toFixed(6) || '—' }}</el-descriptions-item>
                <el-descriptions-item label="速度">
                  {{ selected.last_speed != null ? (selected.last_speed/10).toFixed(1)+' km/h' : '—' }}
                </el-descriptions-item>
                <el-descriptions-item label="归属客户">{{ selected.customer_name || '—' }}</el-descriptions-item>
                <el-descriptions-item label="电量">
                  <span v-if="selected.last_battery != null" :style="{ color: selected.last_battery <= 20 ? '#f56c6c' : '#67c23a' }">
                    {{ selected.last_battery }}%
                  </span>
                  <span v-else>—</span>
                </el-descriptions-item>
              </el-descriptions>
              <div style="margin-top:14px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">
                <el-button type="primary" style="width:100%;margin:0;" @click="locateOnMap(selected)">地图定位</el-button>
                <el-button type="success" plain style="width:100%;margin:0;" @click="goTrack(selected.phone)">查看轨迹</el-button>
                <el-button type="warning" style="width:100%;margin:0;" @click="openZhiling(selected)">指令</el-button>
              </div>
            </div>
          </div>
        </transition>
      </div>
    </div>

    <!-- ══ Tab 2：轨迹回放 ══ -->
    <div v-show="activeTab === 'track'" style="flex:1;display:flex;overflow:hidden;">
      <div style="width:260px;border-right:1px solid #e4e7ed;padding:14px;display:flex;flex-direction:column;gap:10px;overflow-y:auto;flex-shrink:0;">
        <div style="font-size:13px;font-weight:600;color:#303133;">轨迹回放</div>
        <el-form label-width="56px" size="small">
          <el-form-item label="设备">
            <el-select v-model="trkPhone" placeholder="选择设备" style="width:100%;" filterable @change="resetTrack">
              <el-option v-for="d in devices" :key="d.phone" :label="d.name||d.phone" :value="d.phone" />
            </el-select>
          </el-form-item>
          <el-form-item label="开始">
            <el-date-picker v-model="trkStart" type="datetime" value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="开始时间" style="width:100%;" />
          </el-form-item>
          <el-form-item label="结束">
            <el-date-picker v-model="trkEnd" type="datetime" value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="结束时间" style="width:100%;" />
          </el-form-item>
          <el-form-item label="速度">
            <el-slider v-model="trkSpeed" :min="1" :max="10" :step="1" show-stops />
          </el-form-item>
        </el-form>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <el-button type="primary" size="small" @click="loadTrack" :loading="trkLoading">加载轨迹</el-button>
          <el-button size="small" @click="playTrack"  :disabled="!trkPoints.length||trkPlaying">播放</el-button>
          <el-button size="small" @click="pauseTrack" :disabled="!trkPlaying">暂停</el-button>
          <el-button size="small" @click="resetTrack">重置</el-button>
        </div>
        <el-descriptions :column="1" size="small" border v-if="trkPoints.length">
          <el-descriptions-item label="轨迹点">{{ trkPoints.length }}</el-descriptions-item>
          <el-descriptions-item label="进度">{{ trkIdx+1 }} / {{ trkPoints.length }}</el-descriptions-item>
          <el-descriptions-item label="速度" v-if="trkCurrent">{{ (trkCurrent.speed/10).toFixed(1) }} km/h</el-descriptions-item>
          <el-descriptions-item label="时间"  v-if="trkCurrent">{{ trkCurrent.gps_time }}</el-descriptions-item>
        </el-descriptions>
        <el-progress v-if="trkPoints.length" :percentage="trkProgress" :status="trkPlaying?'':'success'" />
      </div>
      <div style="flex:1;position:relative;overflow:hidden;">
        <div id="dq-track-map" style="position:absolute;inset:0;" />
      </div>
    </div>

    <!-- ══ Tab 3：报警记录 ══ -->
    <div v-show="activeTab === 'alarms'" style="flex:1;display:flex;flex-direction:column;overflow:hidden;padding:14px;gap:10px;">
      <!-- 筛选栏 -->
      <div style="display:flex;align-items:center;gap:10px;flex-shrink:0;">
        <el-input v-model="alarmPhone" placeholder="设备号筛选" size="small" clearable style="width:180px;" @clear="loadAlarms(true)" />
        <el-button size="small" type="primary" @click="loadAlarms(true)">查询</el-button>
        <el-button size="small" :icon="Refresh" @click="loadAlarms(true)" :loading="alarmLoading">刷新</el-button>
        <div style="flex:1;" />
        <span style="font-size:13px;color:#909399;">共 {{ alarmTotal }} 条</span>
      </div>
      <el-table :data="alarms" size="small" style="flex:1;" height="100%" stripe border>
        <el-table-column label="设备号" min-width="140">
          <template #default="{ row }">{{ row.terminal_id || row.phone }}</template>
        </el-table-column>
        <el-table-column label="报警类型" prop="alarm_type_name" min-width="130" />
        <el-table-column label="报警时间" prop="alarm_time"      min-width="160" />
        <el-table-column label="位置" min-width="180">
          <template #default="{ row }">
            <span v-if="row.lat&&row.lng">{{ row.lat?.toFixed(5) }}, {{ row.lng?.toFixed(5) }}</span>
            <span v-else style="color:#ccc;">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status===0?'danger':'info'">
              {{ row.status===0?'未处理':'已处理' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination background layout="prev,pager,next,total"
        :total="alarmTotal" :page-size="alarmPageSize"
        v-model:current-page="alarmPage" @current-change="loadAlarms(false)"
        style="justify-content:flex-end;flex-shrink:0;" />
    </div>

    <!-- ══ Tab 4：操作指令 ══ -->
    <div v-show="activeTab === 'cmd'" style="flex:1;display:flex;overflow:hidden;">
      <!-- 左：发送 -->
      <div style="width:300px;border-right:1px solid #e4e7ed;padding:16px;display:flex;flex-direction:column;gap:14px;flex-shrink:0;overflow-y:auto;">
        <div style="font-size:13px;font-weight:600;">发送指令</div>
        <el-form label-width="56px" size="small">
          <el-form-item label="设备">
            <el-select v-model="cmdPhone" placeholder="选择设备" style="width:100%;" filterable>
              <el-option v-for="d in devices" :key="d.phone" :label="d.name||d.phone" :value="d.phone">
                <span>{{ d.name||d.phone }}</span>
                <el-tag size="small" :type="d.status===1?'success':'info'" style="float:right;margin-top:6px;">
                  {{ d.status===1?'在线':'离线' }}
                </el-tag>
              </el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="指令">
            <el-input v-model="cmdText" placeholder="输入指令内容" type="textarea" :rows="3" />
          </el-form-item>
        </el-form>
        <div>
          <div style="font-size:12px;color:#909399;margin-bottom:6px;">快捷指令</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            <el-tag v-for="q in quickCmds" :key="q.label" style="cursor:pointer;" @click="cmdText=q.cmd">
              {{ q.label }}
            </el-tag>
          </div>
        </div>
        <el-button type="primary" @click="sendCmd" :loading="cmdSending"
          :disabled="!cmdPhone||!cmdText" style="width:100%;">发送</el-button>
        <el-alert v-if="cmdResult" :title="cmdResult.msg"
          :type="cmdResult.ok?'success':'error'" show-icon :closable="false" />
      </div>

      <!-- 右：历史 -->
      <div style="flex:1;display:flex;flex-direction:column;padding:14px;gap:10px;overflow:hidden;">
        <div style="display:flex;align-items:center;gap:10px;">
          <span style="font-size:14px;font-weight:600;">指令历史</span>
          <el-button size="small" :icon="Refresh" @click="loadCmdHistory(true)" :loading="cmdHisLoading">刷新</el-button>
          <div style="flex:1;" />
          <span style="font-size:13px;color:#909399;">共 {{ cmdHisTotal }} 条</span>
        </div>
        <el-table :data="cmdHistory" size="small" style="flex:1;" height="100%" stripe border>
          <el-table-column label="设备"   prop="phone"       min-width="140" />
          <el-table-column label="设备名称" prop="device_name" min-width="110" />
          <el-table-column label="指令"   prop="command"     min-width="160" />
          <el-table-column label="结果"   prop="result"      width="80" />
          <el-table-column label="时间"   prop="created_at"  min-width="160" />
        </el-table>
        <el-pagination background layout="prev,pager,next,total"
          :total="cmdHisTotal" :page-size="cmdHisPageSize"
          v-model:current-page="cmdHisPage" @current-change="loadCmdHistory(false)"
          style="justify-content:flex-end;flex-shrink:0;" />
      </div>
    </div>

    <!-- 天禧设备指令面板 -->
    <el-dialog v-model="zhilingVisible" title="设备参数" width="720px" top="6vh" destroy-on-close>
      <div style="margin-bottom:10px;color:#606266;font-size:13px;">
        目标设备：<b>{{ zlDev?.name || zlDev?.terminal_id || zlDev?.phone }}</b>
        （设备号 {{ zlDev?.phone }}）
        <el-tag v-if="zlDev?.status===1" size="small" type="success" style="margin-left:6px;">在线</el-tag>
        <el-tag v-else size="small" type="info" style="margin-left:6px;">离线</el-tag>
        <el-tag size="small" :type="isG618 ? 'warning' : 'primary'" style="margin-left:6px;">
          型号 {{ zlDev?.terminal_model || '未知' }}
        </el-tag>
      </div>
      <el-alert v-if="isG618" type="warning" :closable="false" show-icon style="margin-bottom:10px;"
        title="G618 为低功耗短连接设备，指令将排入队列，在设备下次上线时自动下发执行，可能有延迟。" />
      <el-alert type="info" :closable="false" show-icon style="margin-bottom:10px;"
        title="设备需在线才能接收指令；离线设备将返回“设备不在线”。" />
      <!-- 参数性质说明:上报频率已回填设备真实值;开关类为下发设置,不代表设备当前状态。 -->
      <el-alert type="warning" :closable="false" style="margin-bottom:10px;"
        title="说明：「上报频率」已回填设备当前真实值；其余开关(休眠/蓝牙/定位优先级等)为下发设置，代表你要设成的值，非设备当前状态(设备不上报这些参数的当前配置)。" />
      <!-- ===== 天禧设备指令（terminal_model 不含 G618 时显示） ===== -->
      <el-tabs v-model="zlTab" v-if="!isG618">
        <!-- 网络配置 -->
        <el-tab-pane label="网络配置" name="net">
          <div class="zl-card">
            <div class="zl-title">设置IP地址 <span class="zl-cmd">set_ip</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="IP"><el-input v-model="zf.set_ip.ip" placeholder="服务器IP/域名" style="width:180px" /></el-form-item>
              <el-form-item label="端口"><el-input v-model="zf.set_ip.port" placeholder="端口" style="width:100px" /></el-form-item>
              <el-form-item label="协议">
                <el-select v-model="zf.set_ip.proto" style="width:110px">
                  <el-option label="TCP(0)" :value="0" />
                  <el-option label="UDP(1)" :value="1" />
                </el-select>
              </el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='set_ip'" @click="sendZhiling('set_ip', zf.set_ip, ['ip','port','proto'])">下发</el-button></el-form-item>
            </el-form>
          </div>
          <div class="zl-card">
            <div class="zl-title">设置APN <span class="zl-cmd">set_apn</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="鉴权">
                <el-select v-model="zf.set_apn.apn_type" style="width:110px">
                  <el-option label="none(0)" :value="0" />
                  <el-option label="pap(1)" :value="1" />
                  <el-option label="chap(2)" :value="2" />
                </el-select>
              </el-form-item>
              <el-form-item label="APN名"><el-input v-model="zf.set_apn.name" placeholder="APN名称" style="width:160px" /></el-form-item>
              <el-form-item label="用户"><el-input v-model="zf.set_apn.user" placeholder="选填" style="width:130px" /></el-form-item>
              <el-form-item label="密码"><el-input v-model="zf.set_apn.pwd" placeholder="选填" style="width:130px" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='set_apn'" @click="sendZhiling('set_apn', zf.set_apn, ['apn_type','name'])">下发</el-button></el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <!-- 上报控制 -->
        <el-tab-pane label="上报控制" name="report">
          <div class="zl-card">
            <div class="zl-title">修改上传频率 <span class="zl-cmd">set_interval</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="运动(s)"><el-input v-model="zf.set_interval.move_sec" placeholder="≥3" style="width:90px" /></el-form-item>
              <el-form-item label="静止(s)"><el-input v-model="zf.set_interval.static_sec" placeholder="≥3" style="width:90px" /></el-form-item>
              <el-form-item label="心跳(s)"><el-input v-model="zf.set_interval.heartbeat_sec" placeholder="≥3" style="width:90px" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='set_interval'" @click="sendZhiling('set_interval', zf.set_interval, ['move_sec','static_sec','heartbeat_sec'])">下发</el-button></el-form-item>
            </el-form>
          </div>
          <div class="zl-card">
            <div class="zl-title">立即上传数据 <span class="zl-cmd">upload</span></div>
            <el-button type="primary" size="small" :loading="zlBusy==='upload'" @click="sendZhiling('upload', {}, [])">下发</el-button>
          </div>
        </el-tab-pane>

        <!-- 语音留言 -->
        <el-tab-pane label="语音留言" name="voice">
          <div class="zl-card">
            <div class="zl-title">设置音量 / 喇叭声音等级 <span class="zl-cmd">set_volume</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="音量(0-100)"><el-input v-model="zf.set_volume.level" placeholder="0-100" style="width:110px" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='set_volume'" @click="sendZhiling('set_volume', zf.set_volume, ['level'])">下发</el-button></el-form-item>
            </el-form>
          </div>
          <div class="zl-card">
            <div class="zl-title">发送留言 <span class="zl-cmd">send_message</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="留言"><el-input v-model="zf.send_message.message" placeholder="文本(不支持标点)" style="width:280px" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='send_message'" @click="sendZhiling('send_message', zf.send_message, ['message'])">下发</el-button></el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <!-- SOS设置 -->
        <el-tab-pane label="SOS设置" name="sos">
          <div class="zl-card">
            <div class="zl-title">设置亲情号码 <span class="zl-cmd">set_family</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="号码1"><el-input v-model="zf.set_family.num1" placeholder="可空" style="width:150px" /></el-form-item>
              <el-form-item label="号码2"><el-input v-model="zf.set_family.num2" placeholder="可空" style="width:150px" /></el-form-item>
              <el-form-item label="号码3"><el-input v-model="zf.set_family.num3" placeholder="可空" style="width:150px" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='set_family'" @click="sendZhiling('set_family', zf.set_family, [])">下发</el-button></el-form-item>
            </el-form>
          </div>
          <div class="zl-card">
            <div class="zl-title">设置SOS求救电话 <span class="zl-cmd">set_sos_numbers</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="号码"><el-input v-model="zf.set_sos_numbers.numbers" placeholder="逗号分隔,最多5个" style="width:280px" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='set_sos_numbers'" @click="sendSosNumbers()">下发</el-button></el-form-item>
            </el-form>
          </div>
          <div class="zl-card">
            <div class="zl-title">设置SOS求救短信内容 <span class="zl-cmd">set_sos_msg</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="短信"><el-input v-model="zf.set_sos_msg.msg" placeholder="不能有逗号" style="width:280px" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='set_sos_msg'" @click="sendZhiling('set_sos_msg', zf.set_sos_msg, ['msg'])">下发</el-button></el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <!-- 系统 -->
        <el-tab-pane label="系统" name="sys">
          <div class="zl-card">
            <div class="zl-title">远程复位 <span class="zl-cmd">reset</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="延迟(s)"><el-input v-model="zf.reset.delay_sec" placeholder="默认3" style="width:100px" /></el-form-item>
              <el-form-item><el-button type="danger" :loading="zlBusy==='reset'" @click="sendZhiling('reset', {delay_sec: (zf.reset.delay_sec===''||zf.reset.delay_sec==null) ? 3 : zf.reset.delay_sec}, [])">下发</el-button></el-form-item>
            </el-form>
          </div>
          <div class="zl-card">
            <div class="zl-title">OTA升级 <span class="zl-cmd">ota_http</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="URL"><el-input v-model="zf.ota_http.url" placeholder="固件URL" style="width:260px" /></el-form-item>
              <el-form-item label="大小"><el-input v-model="zf.ota_http.file_size" placeholder="字节" style="width:110px" /></el-form-item>
              <el-form-item label="MD5"><el-input v-model="zf.ota_http.md5" placeholder="MD5" style="width:260px" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='ota_http'" @click="sendZhiling('ota_http', zf.ota_http, ['url','file_size','md5'])">下发</el-button></el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <!-- 自定义指令 -->
        <el-tab-pane label="自定义" name="custom">
          <div class="zl-card">
            <div class="zl-title">手动输入指令文本 <span class="zl-cmd">text</span></div>
            <el-input v-model="zlCustomText" placeholder="输入指令内容" type="textarea" :rows="3" style="margin-bottom:8px;" />
            <div style="margin-bottom:10px;">
              <span style="color:#909399;font-size:12px;margin-right:6px;">快捷指令：</span>
              <el-tag v-for="q in quickCmds" :key="q.label" style="cursor:pointer;margin-right:6px;"
                @click="zlCustomText=q.cmd">{{ q.label }}</el-tag>
            </div>
            <el-button type="primary" size="small" :loading="zlBusy==='text'"
              :disabled="!zlCustomText" @click="sendCustomZhiling()">发送</el-button>
          </div>
        </el-tab-pane>
      </el-tabs>

      <!-- ===== G618 设备指令（terminal_model 含 G618 时显示） ===== -->
      <el-tabs v-model="zlTabG" v-if="isG618">
        <!-- 上报与连接 -->
        <el-tab-pane label="上报与连接" name="g_report">
          <div class="zl-card">
            <div class="zl-title">设置上报频率 <span class="zl-cmd">set_freq</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="间隔(分钟)"><el-input v-model="zfg.set_freq.interval" placeholder="默认10" style="width:110px" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='set_freq'" @click="sendG618('set_freq', { interval: zfg.set_freq.interval }, ['interval'])">下发</el-button></el-form-item>
            </el-form>
          </div>
          <div class="zl-card">
            <div class="zl-title">长短连接切换 <span class="zl-cmd">long_connection</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="长连接">
                <el-radio-group v-model="zfg.long_connection.on">
                  <el-radio-button :value="true">开启</el-radio-button>
                  <el-radio-button :value="false">关闭</el-radio-button>
                </el-radio-group>
              </el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='long_connection'" @click="sendG618('long_connection', { on: zfg.long_connection.on }, [])">下发</el-button></el-form-item>
            </el-form>
          </div>
          <div class="zl-card">
            <div class="zl-title">休眠开关 <span class="zl-cmd">sleep</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="休眠">
                <el-radio-group v-model="zfg.sleep.on">
                  <el-radio-button :value="true">开启</el-radio-button>
                  <el-radio-button :value="false">关闭</el-radio-button>
                </el-radio-group>
              </el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='sleep'" @click="sendG618('sleep', { on: zfg.sleep.on }, [])">下发</el-button></el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <!-- 开关设置 -->
        <el-tab-pane label="开关设置" name="g_switch">
          <div class="zl-card" v-for="sw in g618Switches" :key="sw.cmd">
            <div class="zl-title">{{ sw.label }} <span class="zl-cmd">{{ sw.cmd }}</span></div>
            <el-form :inline="true" size="small">
              <el-form-item label="状态">
                <el-radio-group v-model="zfg[sw.cmd].on">
                  <el-radio-button :value="true">开启</el-radio-button>
                  <el-radio-button :value="false">关闭</el-radio-button>
                </el-radio-group>
              </el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy===sw.cmd" @click="sendG618(sw.cmd, { on: zfg[sw.cmd].on }, [])">下发</el-button></el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <!-- 定位 -->
        <el-tab-pane label="定位" name="g_loc">
          <div class="zl-card">
            <div class="zl-title">定位优先级 <span class="zl-cmd">set_loc_priority</span></div>
            <div style="color:#909399;font-size:12px;margin-bottom:8px;">按优先级从高到低选择定位方式（1=GPS,2=WiFi,3=BLE蓝牙）。</div>
            <el-form :inline="true" size="small">
              <el-form-item label="第1优先">
                <el-select v-model="zfg.set_loc_priority.p1" style="width:110px"><el-option v-for="o in locOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
              </el-form-item>
              <el-form-item label="第2优先">
                <el-select v-model="zfg.set_loc_priority.p2" style="width:110px"><el-option v-for="o in locOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
              </el-form-item>
              <el-form-item label="第3优先">
                <el-select v-model="zfg.set_loc_priority.p3" style="width:110px"><el-option v-for="o in locOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
              </el-form-item>
              <el-form-item><el-button type="primary" :loading="zlBusy==='set_loc_priority'" @click="sendLocPriority()">下发</el-button></el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <!-- 系统【谨慎】 -->
        <el-tab-pane label="系统【谨慎】" name="g_sys">
          <div class="zl-card">
            <div class="zl-title">重启设备 <span class="zl-cmd">reboot</span></div>
            <el-button type="danger" size="small" :loading="zlBusy==='reboot'" @click="sendG618('reboot', {}, [])">下发</el-button>
          </div>
          <div class="zl-card">
            <div class="zl-title">关机 <span class="zl-cmd">shutdown</span></div>
            <el-button type="danger" size="small" :loading="zlBusy==='shutdown'" @click="sendG618('shutdown', {}, [])">下发</el-button>
          </div>
          <div class="zl-card">
            <div class="zl-title">修改服务器IP <span class="zl-cmd">set_server_ip</span></div>
            <div style="color:#f56c6c;font-weight:600;font-size:12px;margin-bottom:8px;">
              ⚠ 高危操作：改错会导致设备失联，无法再连回平台，请务必确认 IP/端口正确！
            </div>
            <el-form :inline="true" size="small">
              <el-form-item label="IP"><el-input v-model="zfg.set_server_ip.ip" placeholder="如 47.100.1.1" style="width:170px" /></el-form-item>
              <el-form-item label="端口"><el-input v-model="zfg.set_server_ip.port" placeholder="端口" style="width:100px" /></el-form-item>
              <el-form-item><el-button type="danger" :loading="zlBusy==='set_server_ip'" @click="sendG618('set_server_ip', { ip: zfg.set_server_ip.ip, port: zfg.set_server_ip.port }, ['ip','port'])">下发</el-button></el-form-item>
            </el-form>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { deviceApi, locationApi, alarmApi, commandApi, isAdmin, portalApi } from '@/api'
import { TDT_MAP_STYLE } from '@/utils/mapStyle'

// 角色自适应接口：管理员走管理端，客户走门户端
const api = {
  deviceList: (p)      => isAdmin() ? deviceApi.list(p)               : portalApi.deviceList(p),
  locHistory: (ph, p)  => isAdmin() ? locationApi.history(ph, p)      : portalApi.history(ph, p),
  alarmList:  (p)      => isAdmin() ? alarmApi.list(p)                : portalApi.alarms(p),
  sendCmd:    (ph, tx) => isAdmin() ? commandApi.sendText(ph, tx)     : portalApi.sendCommand({ phone: ph, text: tx }),
  addHistory: (d)      => isAdmin() ? commandApi.addHistory(d)        : Promise.resolve(), // 门户端后台自动记录
  cmdHistory: (p)      => isAdmin() ? commandApi.history(p)           : portalApi.cmdHistory(p),
}

const tabs = [
  { key: 'realtime', label: '实时位置' },
  { key: 'track',    label: '轨迹回放' },
  { key: 'alarms',   label: '报警记录' },
  { key: 'cmd',      label: '操作指令' },
]
const activeTab = ref('realtime')

async function switchTab(key) {
  activeTab.value = key
  await nextTick()
  if (key === 'realtime' && realtimeMap) realtimeMap.resize()
  if (key === 'track'    && trackMap)    trackMap.resize()
  if (key === 'alarms'   && !alarmsFetched.value)  loadAlarms(true)
  if (key === 'cmd'      && !cmdHisFetched.value)  loadCmdHistory(true)
}

// ══════════════════════════════════════════════════════════════════════════════
// Tab 1：实时位置
// ══════════════════════════════════════════════════════════════════════════════
const devices       = ref([])
const devLoading    = ref(false)
const devSearch     = ref('')
const selected      = ref(null)
const filteredDevices = computed(() => {
  if (!devSearch.value) return devices.value
  const q = devSearch.value.toLowerCase()
  return devices.value.filter(d =>
    (d.phone||'').includes(q) || (d.name||'').toLowerCase().includes(q)
  )
})

let realtimeMap = null
const markers   = {}
let pollTimer   = null

onMounted(async () => {
  await loadDevices()
  await nextTick()
  await new Promise(r => setTimeout(r, 200))

  realtimeMap = new maplibregl.Map({
    container: 'dq-map', style: TDT_MAP_STYLE,
    center: [104.19, 35.86], zoom: 5,
  })
  realtimeMap.addControl(new maplibregl.NavigationControl(), 'top-left')
  realtimeMap.on('load', () => {
    renderMarkers(devices.value)
    fitAll(devices.value)
    pollTimer = setInterval(async () => {
      await loadDevices()
    }, 30000)
  })

  trackMap = new maplibregl.Map({
    container: 'dq-track-map', style: TDT_MAP_STYLE,
    center: [104.19, 35.86], zoom: 5,
  })
  trackMap.addControl(new maplibregl.NavigationControl(), 'top-left')
})

onUnmounted(() => {
  clearInterval(pollTimer)
  clearTimeout(trkTimer)
  Object.values(markers).forEach(m => m.remove())
  if (realtimeMap) realtimeMap.remove()
  if (trackMap)    trackMap.remove()
})

async function loadDevices() {
  devLoading.value = true
  try {
    const res  = await api.deviceList({ size: 500 })
    const list = res.data?.records || []
    devices.value = list
    if (realtimeMap) renderMarkers(list)
  } catch {} finally { devLoading.value = false }
}

// 按设备状态/角色生成标记样式：报警红优先，否则用角色颜色+形状（圆/方/星/菱）
function markerCss(d) {
  const alarm = d.status === 2
  const color = alarm ? '#f56c6c' : (d.status === 0 ? '#ccc' : (d.role_color || '#67c23a'))
  const shape = d.role_icon || '圆形'
  let form = 'border-radius:50%;'
  if (shape === '方形')      form = 'border-radius:2px;'
  else if (shape === '菱形') form = 'clip-path:polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%);'
  else if (shape === '星形') form = 'clip-path:polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%);'
  return `width:12px;height:12px;background:${color};border:2px solid #fff;box-shadow:0 0 6px rgba(0,0,0,.4);cursor:pointer;${form}`
}
function renderMarkers(list) {
  const phones = new Set(list.map(d => d.phone))
  Object.keys(markers).forEach(ph => {
    if (!phones.has(ph)) { markers[ph].remove(); delete markers[ph] }
  })
  list.forEach(d => {
    if (!d.last_lat || !d.last_lng) return
    if (markers[d.phone]) {
      markers[d.phone].setLngLat([d.last_lng, d.last_lat])
      markers[d.phone].getElement().style.cssText = markerCss(d)
    } else {
      const el = document.createElement('div')
      el.style.cssText = markerCss(d)
      markers[d.phone] = new maplibregl.Marker({ element: el })
        .setLngLat([d.last_lng, d.last_lat]).addTo(realtimeMap)
      el.addEventListener('click', () => selectDevice(d))
    }
    if (selected.value?.phone === d.phone) selected.value = { ...d }
  })
}

function fitAll(list) {
  const pts = list.filter(d => d.last_lat && d.last_lng)
  if (!pts.length) return
  if (pts.length === 1) {
    realtimeMap.flyTo({ center: [pts[0].last_lng, pts[0].last_lat], zoom: 13 })
    return
  }
  const lngs = pts.map(d => d.last_lng), lats = pts.map(d => d.last_lat)
  realtimeMap.fitBounds(
    [[Math.min(...lngs), Math.min(...lats)],[Math.max(...lngs), Math.max(...lats)]],
    { padding: 60, maxZoom: 15 }
  )
}

function selectDevice(d) {
  selected.value = d
  locateOnMap(d)
}

function locateOnMap(d) {
  if (d.last_lat && d.last_lng && realtimeMap) {
    realtimeMap.flyTo({ center: [d.last_lng, d.last_lat], zoom: 14, duration: 600 })
  }
}

function goTrack(phone) { trkPhone.value = phone; switchTab('track') }
function goCmd(phone)   { cmdPhone.value  = phone; switchTab('cmd')   }

// ══════════════════════════════════════════════════════════════════════════════
// Tab 2：轨迹回放
// ══════════════════════════════════════════════════════════════════════════════
let trackMap     = null
let trkTimer     = null
let trkStartMark = null
let trkEndMark   = null
let trkCarMark   = null
let trkLineAdded = false

const trkPhone   = ref('')
const trkStart   = ref('')
const trkEnd     = ref('')
const trkSpeed   = ref(3)
const trkPoints  = ref([])
const trkIdx     = ref(-1)
const trkPlaying = ref(false)
const trkLoading = ref(false)

const trkCurrent  = computed(() => trkPoints.value[trkIdx.value] || null)
const trkProgress = computed(() => {
  if (!trkPoints.value.length) return 0
  return Math.round(((trkIdx.value+1)/trkPoints.value.length)*100)
})

function makeDot(color, size=14) {
  const el = document.createElement('div')
  el.style.cssText = `width:${size}px;height:${size}px;border-radius:50%;background:${color};border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.3);`
  return el
}

async function loadTrack() {
  if (!trkPhone.value) { ElMessage.warning('请先选择设备'); return }
  trkLoading.value = true; resetTrack()
  try {
    const params = { size: 1000 }
    if (trkStart.value && trkEnd.value) { params.start = trkStart.value; params.end = trkEnd.value }
    const res = await api.locHistory(trkPhone.value, params)
    const pts = (res.data?.records || []).filter(p => p.lat && p.lng)
    if (!pts.length) { ElMessage.warning('该时段无轨迹数据'); return }
    trkPoints.value = pts
    const coords = pts.map(p => [p.lng, p.lat])
    if (trackMap.getSource('trk')) {
      trackMap.getSource('trk').setData({ type:'Feature', geometry:{ type:'LineString', coordinates:coords } })
    } else {
      trackMap.addSource('trk', { type:'geojson', data:{ type:'Feature', geometry:{ type:'LineString', coordinates:coords } } })
      trackMap.addLayer({ id:'trk-line', type:'line', source:'trk', paint:{ 'line-color':'#409EFF','line-width':3,'line-opacity':0.85 } })
      trkLineAdded = true
    }
    trkStartMark = new maplibregl.Marker({ element:makeDot('#67c23a') }).setLngLat(coords[0]).setPopup(new maplibregl.Popup({ closeButton:false }).setHTML('起点')).addTo(trackMap)
    trkEndMark   = new maplibregl.Marker({ element:makeDot('#f56c6c') }).setLngLat(coords[coords.length-1]).setPopup(new maplibregl.Popup({ closeButton:false }).setHTML('终点')).addTo(trackMap)
    const lngs = coords.map(c=>c[0]), lats = coords.map(c=>c[1])
    trackMap.fitBounds([[Math.min(...lngs),Math.min(...lats)],[Math.max(...lngs),Math.max(...lats)]], { padding:50 })
    trkIdx.value = 0
    ElMessage.success(`加载完成，共 ${pts.length} 个轨迹点`)
  } catch { ElMessage.error('加载失败') }
  finally { trkLoading.value = false }
}

function playTrack() {
  if (!trkPoints.value.length) { ElMessage.warning('请先加载轨迹'); return }
  if (trkIdx.value >= trkPoints.value.length-1) trkIdx.value = 0
  trkPlaying.value = true
  const step = () => {
    if (trkIdx.value >= trkPoints.value.length-1) { trkPlaying.value=false; ElMessage.success('回放完成'); return }
    trkIdx.value++
    const pt = trkPoints.value[trkIdx.value], ll = [pt.lng, pt.lat]
    if (trkCarMark) trkCarMark.setLngLat(ll)
    else trkCarMark = new maplibregl.Marker({ element:makeDot('#e6a23c',18) }).setLngLat(ll).addTo(trackMap)
    trackMap.panTo(ll, { animate:true, duration:200 })
    trkTimer = setTimeout(step, Math.max(50, 500/trkSpeed.value))
  }
  trkTimer = setTimeout(step, 0)
}

function pauseTrack() { trkPlaying.value=false; clearTimeout(trkTimer) }

function resetTrack() {
  pauseTrack(); trkPoints.value=[]; trkIdx.value=-1
  trkStartMark?.remove(); trkStartMark=null
  trkEndMark?.remove();   trkEndMark=null
  trkCarMark?.remove();   trkCarMark=null
  if (trackMap && trkLineAdded) {
    try { trackMap.removeLayer('trk-line'); trackMap.removeSource('trk') } catch {}
    trkLineAdded = false
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Tab 3：报警记录
// ══════════════════════════════════════════════════════════════════════════════
const alarms        = ref([])
const alarmTotal    = ref(0)
const alarmPage     = ref(1)
const alarmPageSize = ref(20)
const alarmLoading  = ref(false)
const alarmsFetched = ref(false)
const alarmPhone    = ref('')

async function loadAlarms(reset=true) {
  if (reset) alarmPage.value = 1
  alarmLoading.value = true
  try {
    const params = { page: alarmPage.value, size: alarmPageSize.value }
    if (alarmPhone.value) params.phone = alarmPhone.value
    const res = await api.alarmList(params)
    alarms.value    = res.data?.records || []
    alarmTotal.value = res.data?.total  || 0
    alarmsFetched.value = true
  } catch {} finally { alarmLoading.value = false }
}

// ══════════════════════════════════════════════════════════════════════════════
// Tab 4：操作指令
// ══════════════════════════════════════════════════════════════════════════════
const cmdPhone   = ref('')
const cmdText    = ref('')
const cmdSending = ref(false)
const cmdResult  = ref(null)

const quickCmds = [
  { label:'查询位置', cmd:'WHERE'  },
  { label:'查询参数', cmd:'PARAMS' },
  { label:'设备重启', cmd:'RESET'  },
  { label:'消音',     cmd:'MUTE'   },
]

async function sendCmd() {
  if (!cmdPhone.value || !cmdText.value) return
  cmdSending.value = true; cmdResult.value = null
  try {
    const dev = devices.value.find(d => d.phone === cmdPhone.value)
    await api.sendCmd(cmdPhone.value, cmdText.value)
    // 写入历史（门户端后台自动记录，管理端需手动写入）
    await api.addHistory({
      phone: cmdPhone.value,
      device_name: dev?.name || cmdPhone.value,
      command: cmdText.value, result: '已发送'
    })
    cmdResult.value = { ok:true, msg:'指令已发送' }
    cmdText.value   = ''
    await loadCmdHistory(true)
  } catch (e) {
    cmdResult.value = { ok:false, msg: e.response?.data?.msg || '发送失败' }
  } finally { cmdSending.value = false }
}

const cmdHistory    = ref([])
const cmdHisTotal   = ref(0)
const cmdHisPage    = ref(1)
const cmdHisPageSize = ref(20)
const cmdHisLoading = ref(false)
const cmdHisFetched = ref(false)

async function loadCmdHistory(reset=true) {
  if (reset) cmdHisPage.value = 1
  cmdHisLoading.value = true
  try {
    const res = await api.cmdHistory({ page: cmdHisPage.value, size: cmdHisPageSize.value })
    cmdHistory.value  = res.data?.records || []
    cmdHisTotal.value = res.data?.total   || 0
    cmdHisFetched.value = true
  } catch {} finally { cmdHisLoading.value = false }
}

// ══════════════════════════════════════════════════════════════════════════════
// 天禧设备指令面板（管理端 /api/commands/zhiling）
// ══════════════════════════════════════════════════════════════════════════════
const zhilingVisible = ref(false)
const zlDev  = ref(null)
const zlTab  = ref('net')
const zlBusy = ref('')
const zlCustomText = ref('')
const zf = ref({
  set_ip:          { ip:'', port:'', proto:0 },
  set_apn:         { apn_type:0, name:'', user:'', pwd:'' },
  set_interval:    { move_sec:'', static_sec:'', heartbeat_sec:'' },
  set_volume:      { level:'' },
  send_message:    { message:'' },
  set_family:      { num1:'', num2:'', num3:'' },
  set_sos_numbers: { numbers:'' },
  set_sos_msg:     { msg:'' },
  reset:           { delay_sec:'' },
  ota_http:        { url:'', file_size:'', md5:'' },
})

function openZhiling(dev) {
  zlDev.value = dev
  zlTab.value = 'net'
  zlCustomText.value = ''
  // 回填设备真实上报频率(平台已知的真值),避免用户误把默认值当设备当前配置:
  //   G618 用 0xE9 上报的 expected_interval_sec;天禧用位置报文实测的 measured_interval_sec。
  //   仅频率有真值可回填,其余开关类参数设备不上报当前状态、平台拿不到,保持"下发设置"性质(见界面标注)。
  const realSec = dev.expected_interval_sec || dev.measured_interval_sec || null
  if (realSec && realSec > 0) {
    const mins = Math.max(1, Math.round(realSec / 60))
    // G618:频率单位为分钟
    zfg.value.set_freq.interval = String(mins)
    // 天禧:INTERVAL 是运动/静止/心跳三段秒数,把实测秒数回填到静止段(最能代表常态上报节奏),
    //   运动段留空由用户按需填,心跳段填实测值兜底。
    zf.value.set_interval.static_sec = String(realSec)
    zf.value.set_interval.heartbeat_sec = String(realSec)
  }
  zhilingVisible.value = true
}

// 自定义指令：向天禧设备下发手动输入的指令文本，复用 /commands/text 接口
async function sendCustomZhiling() {
  if (!zlDev.value?.phone) { ElMessage.warning('未选择设备'); return }
  if (!zlCustomText.value) { ElMessage.warning('请输入指令内容'); return }
  zlBusy.value = 'text'
  try {
    const text = zlCustomText.value
    await api.sendCmd(zlDev.value.phone, text)
    await api.addHistory({
      phone: zlDev.value.phone,
      device_name: zlDev.value.name || zlDev.value.terminal_id || zlDev.value.phone,
      command: text, result: '已发送'
    })
    ElMessage.success('指令已发送')
    zlCustomText.value = ''
  } catch (e) {
    ElMessage.error(e.response?.data?.msg || '发送失败')
  } finally {
    zlBusy.value = ''
  }
}

// 通用下发：cmd=命令名, form=参数对象, required=必填参数名数组
async function sendZhiling(cmd, form, required) {
  if (!zlDev.value?.phone) { ElMessage.warning('未选择设备'); return }
  for (const k of required) {
    if (form[k] === '' || form[k] === null || form[k] === undefined) {
      ElMessage.warning(`请填写参数：${k}`); return
    }
  }
  zlBusy.value = cmd
  try {
    await (isAdmin() ? commandApi.zhiling : portalApi.zhiling)({ phone: zlDev.value.phone, cmd, ...form })
    ElMessage.success('指令已下发')
  } catch (e) {
    ElMessage.error(e.response?.data?.msg || '指令下发失败')
  } finally {
    zlBusy.value = ''
  }
}

// SOS号码：逗号分隔转数组（最多5个），后端 numbers 支持数组
async function sendSosNumbers() {
  const raw = (zf.value.set_sos_numbers.numbers || '').trim()
  if (!raw) { ElMessage.warning('请填写SOS号码'); return }
  const arr = raw.split(/[,，]/).map(x => x.trim()).filter(Boolean)
  if (!arr.length) { ElMessage.warning('请填写SOS号码'); return }
  if (arr.length > 5) { ElMessage.warning('SOS号码最多5个'); return }
  zlBusy.value = 'set_sos_numbers'
  try {
    await (isAdmin() ? commandApi.zhiling : portalApi.zhiling)({ phone: zlDev.value.phone, cmd:'set_sos_numbers', numbers: arr })
    ElMessage.success('指令已下发')
  } catch (e) {
    ElMessage.error(e.response?.data?.msg || '指令下发失败')
  } finally {
    zlBusy.value = ''
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// G618 设备指令面板（管理端 /api/commands/g618g）—— 按 terminal_model 自动切换
// ══════════════════════════════════════════════════════════════════════════════
const isG618 = computed(() => (zlDev.value?.terminal_model || '').toUpperCase().includes('G618'))
const zlTabG = ref('g_report')

// G618 开关类指令（都是 on 布尔）
const g618Switches = [
  { cmd: 'ble_broadcast',   label: '蓝牙广播开关' },
  { cmd: 'fall_alarm',      label: '跌落报警开关' },
  { cmd: 'button_shutdown', label: '按键关机开关' },
  { cmd: 'sos_button',      label: 'SOS按键开关' },
  { cmd: 'charge_power',    label: '充电供电开关' },
]

// 定位方式代码：1=GPS, 2=WiFi, 3=BLE（严格对应后端 build_set_loc_priority）
const locOptions = [
  { value: 1, label: 'GPS' },
  { value: 2, label: 'WiFi' },
  { value: 3, label: 'BLE蓝牙' },
]

const zfg = ref({
  set_freq:         { interval: '10' },
  long_connection:  { on: true },
  sleep:            { on: false },
  ble_broadcast:    { on: true },
  fall_alarm:       { on: true },
  button_shutdown:  { on: true },
  sos_button:       { on: true },
  charge_power:     { on: true },
  set_loc_priority: { p1: 1, p2: 2, p3: 3 },
  set_server_ip:    { ip: '', port: '' },
})

// G618 通用下发：cmd=命令名, form=参数对象, required=必填参数名数组
async function sendG618(cmd, form, required) {
  if (!zlDev.value?.phone) { ElMessage.warning('未选择设备'); return }
  for (const k of required) {
    if (form[k] === '' || form[k] === null || form[k] === undefined) {
      ElMessage.warning(`请填写参数：${k}`); return
    }
  }
  zlBusy.value = cmd
  try {
    await (isAdmin() ? commandApi.g618 : portalApi.g618)({ phone: zlDev.value.phone, cmd, ...form })
    ElMessage.success('指令已下发（G618为短连接设备，将在设备下次上线时执行）')
  } catch (e) {
    ElMessage.error(e.response?.data?.msg || '指令下发失败')
  } finally {
    zlBusy.value = ''
  }
}

// 定位优先级：三个下拉转成 priorities 数组
async function sendLocPriority() {
  const f = zfg.value.set_loc_priority
  const priorities = [f.p1, f.p2, f.p3].map(Number).filter(x => x >= 1 && x <= 3)
  if (!priorities.length) { ElMessage.warning('请至少选择一种定位方式'); return }
  zlBusy.value = 'set_loc_priority'
  try {
    await commandApi.g618({ phone: zlDev.value.phone, cmd: 'set_loc_priority', priorities })
    ElMessage.success('指令已下发（G618为短连接设备，将在设备下次上线时执行）')
  } catch (e) {
    ElMessage.error(e.response?.data?.msg || '指令下发失败')
  } finally {
    zlBusy.value = ''
  }
}
</script>

<style scoped>
.dq-tab {
  padding: 10px 16px;
  font-size: 13px;
  color: #606266;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: color .2s, border-color .2s;
  user-select: none;
  white-space: nowrap;
}
.dq-tab:hover  { color: #409EFF; }
.dq-tab.active { color: #409EFF; border-bottom-color: #409EFF; font-weight: 600; }

.dq-device-item {
  padding: 8px 12px;
  cursor: pointer;
  border-bottom: 1px solid #f5f5f5;
  transition: background .15s;
}
.dq-device-item:hover  { background: #f5f7fa; }
.dq-device-item.active { background: #ecf5ff; }

.slide-detail-enter-active, .slide-detail-leave-active { transition: width .2s ease, opacity .2s; }
.slide-detail-enter-from, .slide-detail-leave-to      { width:0; opacity:0; overflow:hidden; }

.zl-card { border:1px solid #ebeef5; border-radius:6px; padding:10px 12px; margin-bottom:10px; background:#fafafa; }
.zl-title { font-weight:600; font-size:13px; color:#303133; margin-bottom:8px; }
.zl-cmd { font-weight:400; font-size:12px; color:#909399; margin-left:6px; font-family:monospace; }
</style>
