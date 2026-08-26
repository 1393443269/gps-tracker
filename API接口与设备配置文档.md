# GPS/资产管理平台 — API 接口与设备配置文档

> 服务器地址：`http://121.40.115.63`（生产）
> 后端共 117 个 REST 接口，分「管理端」和「客户门户端」两套鉴权。
> 本文档按功能模块整理，含请求方法、路径、用途、鉴权要求。

---

## 一、鉴权说明

| 端类型 | 请求头 | 获取方式 |
|--------|--------|---------|
| 管理端 | `X-Admin-Token: <token>` | `POST /api/auth/login` |
| 客户门户端 | `X-Customer-Token: <token>` | `POST /api/customer/login` |

- 所有 `/api/*` 接口（除 `/api/auth/*`、`/api/customer/*`）都要求管理端 token。
- `/api/customer/*` 走客户端 token，只能访问该客户及其下级客户的数据。
- Token 有效期 30 天。

**登录接口**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 管理员登录，返回 token。body: `{username, password}` |
| POST | `/api/auth/change_password` | 管理员改密。body: `{oldPassword, newPassword}` |
| POST | `/api/customer/login` | 客户登录，返回 token |
| GET | `/api/customer/me` | 当前客户信息 |

---

## 二、管理端接口（按模块）

### 1. 设备管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/devices` | 设备列表（分页/搜索/生命周期/状态筛选），带角色颜色形状 |
| GET | `/api/devices/summary` | 设备统计（总数/在线/离线/报警）|
| POST | `/api/devices` | 新增设备 |
| GET | `/api/devices/<id>` | 单个设备详情 |
| PUT | `/api/devices/<id>` | 编辑设备 |
| GET | `/api/devices/with_customer` | 设备信息列表（JOIN 客户+角色，支持 customer_id/型号/IMEI/角色 筛选）|
| GET | `/api/devices/export` | 导出全部设备 |
| PUT | `/api/devices/batch_lifecycle` | 批量改生命周期。body: `{ids, lifecycle}` |
| POST | `/api/devices/<id>/bind_customer` | 绑定客户。body: `{customer_id}` |
| POST | `/api/devices/<id>/unbind_customer` | 解绑客户 |
| POST | `/api/devices/batch_bind` | 批量绑定/转移。body: `{ids, customer_id}` |
| POST | `/api/devices/batch_unbind` | 批量解绑。body: `{ids}` |
| PUT | `/api/devices/<id>/role` | 设置设备角色。body: `{role_id}`（null 清除）|
| POST | `/api/devices/batch_role` | 批量设角色。body: `{ids, role_id}` |
| POST | `/api/devices/batch_command` | 批量下发指令。body: `{phones, text}` |

### 2. 角色（设备分组）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/roles` | 角色列表（带设备数）|
| POST | `/api/roles` | 新增角色。body: `{name, color, icon_type, description}` |
| PUT | `/api/roles/<id>` | 编辑角色 |
| DELETE | `/api/roles/<id>` | 删除角色 |
| PUT | `/api/roles/<id>/assign` | 批量分配设备到角色。body: `{phones}` |

> 角色图标 icon_type 取值：`圆形` / `方形` / `星形` / `菱形`；color 为颜色值。

### 3. 位置 / 轨迹

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/locations/<phone>/latest` | 设备最新位置 |
| GET | `/api/locations/<phone>/history` | 轨迹历史（支持 start/end 时间段）|

### 4. 报警管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/alarms` | 报警列表（状态/设备号筛选）|
| PUT | `/api/alarms/<id>/handle` | 处理单条报警。body: `{handler, note}` |
| POST | `/api/alarms/batch_handle` | 批量处理。body: `{ids, handler, note}` |
| GET | `/api/alarm-types` | 可配置的报警类型清单 |
| GET | `/api/alarm-rules` | 报警规则列表（按组织隔离）|
| POST | `/api/alarm-rules` | 新增规则。body: `{alarm_type, level, enabled, notify_page, notify_sms, ring_type}` |
| PUT | `/api/alarm-rules/<id>` | 编辑规则 |
| DELETE | `/api/alarm-rules/<id>` | 删除规则 |

### 5. 考勤 / 健康数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/attendance` | 考勤统计（按围栏聚合进出）|
| GET | `/api/attendance/detail` | 某围栏考勤明细（fence_id/day 筛选）|
| GET | `/api/health` | 健康数据查询（体温/心率/血氧/血压/计步）|
| POST | `/api/health` | 上报健康数据（设备/网关调用）|

### 6. 指令下发

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/commands/text` | 下发文本消息。body: `{phone, text}` |
| POST | `/api/commands/control` | 终端控制。body: `{phone, cmd}` |
| POST | `/api/commands/track` | 位置跟踪设置。body: `{phone, interval, duration}` |
| GET | `/api/command-history` | 指令历史 |
| POST | `/api/command-history` | 写入指令历史 |

### 7. 电子围栏 / 标注点 / 风险点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/fences` | 围栏列表 |
| POST | `/api/fences` | 新建围栏（圆形/多边形/行政区）|
| DELETE | `/api/fences/<id>` | 删除围栏 |
| POST | `/api/fences/batch_delete` | 批量删除。body: `{ids}` |
| GET | `/api/mark_points` / POST / DELETE `/<id>` | 标注点增删查 |
| GET | `/api/risk_points` / POST / DELETE `/<id>` | 风险点增删查 |

### 8. SIM 卡 / 充值

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sims` | SIM 卡列表（ICCID/IMSI/运营商搜索）|
| GET | `/api/sims/expiring_count` | 即将到期数量 |
| POST | `/api/sims` | 新增 SIM 卡 |
| PUT | `/api/sims/<id>` | 编辑 |
| DELETE | `/api/sims/<id>` | 删除 |
| POST | `/api/sims/<id>/bind` | 绑定设备。body: `{phone}` |
| GET | `/api/recharges` | 充值记录 |
| POST | `/api/recharges` | 新增充值。body: `{sim_id, amount, method}` |

### 9. 客户管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/customers` | 客户列表（树形，含子客户）|
| POST | `/api/customers` | 新增客户 |
| PUT | `/api/customers/<id>` | 编辑客户（含头像 avatar、性别/年龄/地址）|
| DELETE | `/api/customers/<id>` | 删除客户（回收其设备）|
| PUT | `/api/customers/<id>/password` | 设置客户登录密码 |
| GET | `/api/customers/<id>/devices` | 客户名下设备 |
| PUT | `/api/customers/<id>/devices` | 分配设备给客户。body: `{phones}` |

### 10. 文件上传

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/upload/avatar` | 上传图片（头像/Logo），返回 URL。限图片、≤1MB、≤1000×400 |
| GET | `/uploads/<filename>` | 访问已上传文件 |

### 11. 组织 / 系统用户 / 模块授权

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/org/tree` | 组织树 |
| GET | `/api/org/children` / `/api/org/<id>/children` | 子组织 |
| POST/PUT/DELETE | `/api/org` `/api/org/<id>` | 组织增改删 |
| GET | `/api/sys/users` | 系统用户列表 |
| POST/PUT/DELETE | `/api/sys/users` `/<id>` | 用户增改删 |
| PUT | `/api/sys/users/<id>/password` | 重置用户密码 |
| GET | `/api/modules/tree` | 模块树 |
| GET/POST | `/api/modules/org/<org_id>/auth` | 组织模块授权查询/保存 |

### 12. 平台设置 / 报表 / 日志

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/platform-setting` | 平台设置（标题/Logo/短信）|
| PUT | `/api/platform-setting` | 保存平台设置 |
| GET | `/api/report/summary` | 报表统计 |
| GET | `/api/oplogs` | 操作日志（按组织隔离）|

---

## 三、客户门户端接口（`/api/customer/*`）

客户端接口与管理端功能对应，但只能访问自己及下级客户的数据。主要有：

- 设备：`GET /api/customer/devices`、`/device_list`、`/summary`、`/pool_devices`
- 位置：`/locations/<phone>/latest`、`/history`
- 报警：`GET /api/customer/alarms`、`PUT .../alarms/<id>/handle`
- 指令：`POST /api/customer/commands/text`、`GET .../commands/history`
- 围栏：`GET/POST/PUT/DELETE /api/customer/fences`、`.../fences/<id>/devices`
- 下级客户：`/api/customer/sub_customers` 全套增删改查 + 分配设备
- SIM/充值：`/api/customer/sims`、`.../recharge`、`/recharges`
- 设备编辑：`PUT /api/customer/devices/<phone>/update`

---

## 四、设备配置（接入平台）

### 4.1 JT/T 808 协议接入（TCP 直连，主用）

设备通过 **TCP 长连接**上报，流程：注册 → 鉴权 → 循环上报位置/心跳。

| 配置项 | 值 |
|--------|-----|
| 服务器地址 | `121.40.115.63` |
| 端口 | `9090` |
| 协议 | JT/T 808-2013 二进制帧 |

**支持的上行报文**：

| 消息 ID | 说明 |
|---------|------|
| `0x0100` | 终端注册（返回鉴权码）|
| `0x0102` | 终端鉴权 |
| `0x0002` | 心跳 |
| `0x0200` | 位置信息汇报（经纬度/速度/方向/报警标志）|

**支持的报警类型**（0x0200 报警标志位）：SOS 紧急报警、超速、疲劳驾驶、主电源断开、碰撞、侧翻；围栏类：进入/离开/停留超时/围栏内超速。

**设备端配置**：平台里「设备管理→新增设备」录入设备的 IMEI（15位），设备脚本里把 `SERVER_HOST` 设为 `121.40.115.63`、`SERVER_PORT` 设为 `9090`、`PHONE` 设为该 IMEI，即可接入。参考 `device/device_jt808.py`（QuecPython EC600x/EC800x）。

### 4.2 MQTT 接入（可选）

| 配置项 | 值 |
|--------|-----|
| Broker 地址 | `121.40.115.63` |
| 端口 | `1883` |
| 订阅主题 | `gps/#` |
| 上报格式 | JSON（含 phone、lat、lng、speed、alarm 等）|

### 4.3 健康数据上报（穿戴设备）

体温/心率/血氧/血压/计步等，通过 `POST /api/health` 上报（body 含 phone 及各生理字段）。808 定位器本身不传这些，需专门的穿戴设备或网关。

---

## 五、生产环境备注

- **管理员默认账号**：`admin` / `admin123`（**上线务必改密**）
- **设备接入地址**：`121.40.115.63:9090`（808）、`:1883`（MQTT）
- **数据持久化**：SQLite 存于 Docker volume `gps-tracker_tracker_data`
- **自动备份**：每天 3:00，保留 14 天，位于服务器 `/opt/backups/`
- **代码更新**：`cd /opt/gps-tracker && git pull && docker compose up -d --build`
