# 架构演进方案：接入层解耦 与 app.py Blueprint 拆分

> **本文档是「方案」，不是「实现」。**
> 目标是给出可评审、可分阶段落地的改造路径，**不在本轮直接改动生产代码**。
> 原因：当前系统是一套正在运行、承载真机设备（808 / G618G / MQTT）的在线平台，
> 缺乏自动化回归测试，`sessions` 里存放的是**活的 TCP socket 句柄**、`_serial` / 围栏状态
> 全是进程内可变状态。在没有真机回归环境的前提下做大范围重构，指令下发静默失败、
> 轨迹丢帧这类问题**上线后才会暴露**，风险极高。因此先固化方案、分批验证，再动手。
>
> 全文基于对以下真实文件的通读：
> `server/gunicorn.conf.py`、`server/core/ingest.py`、`server/core/state.py`、
> `server/core/extensions.py`、`server/core/db.py`、`server/app.py`（4595 行 / 135 路由）、
> `server/core/security.py`。引用的函数名、路由、行为均来自实际代码。

---

# 文档一：接入层与 Web 层解耦（替代「Redis 外置 sessions」的错误方向）

## 0. 一句话结论

`sessions` 存的是**活的 TCP socket 对象**（`core/state.py:26` `sessions = {} # phone → socket`），
socket 是**进程内的文件描述符句柄，无法序列化进 Redis**。因此「把 sessions 外置到 Redis」
在物理上不成立。真正的解法不是搬 sessions，而是**把接入层拆成独立进程/服务（Ingest Service）**，
Web 层通过消息机制向「持有该设备 socket 的接入进程」转发下发指令。**放进 Redis 的应该是
「设备连接注册表」（phone → 哪个接入节点），而不是 socket 本身。**

## 1. 现状约束分析：为什么现在必须 `workers = 1`

### 1.1 硬约束来自两处进程内单例

`gunicorn.conf.py:14` 写死 `workers = 1`，注释已点明原因：
> `workers=1：808 TCP 服务是进程单例（端口只能绑定一次），必须单 worker`

拆开看，是两个独立的进程内约束叠加：

| 约束 | 代码位置 | 说明 |
|---|---|---|
| **TCP 端口单例** | `ingest.py:1206 start_tcp_server()` → `srv.bind(('0.0.0.0', 9090))` | 9090 端口在一台机器上只能被一个进程 `bind`。多 worker 时第二个 worker `bind` 会直接抛 `Address already in use`。虽然设了 `SO_REUSEADDR`（`ingest.py:1208`），但那只解决 TIME_WAIT 复用，不等于多进程负载均衡。 |
| **sessions 进程内** | `state.py:26` `sessions = {}` | socket 对象只在**接收该连接的那个进程**里有效。 |

### 1.2 `post_fork` 把所有 I/O 线程绑死在唯一 worker 上

`gunicorn.conf.py:27 post_fork()` 在 fork 出的**那一个** worker 里启动了：
`init_db` / `_setup_pg_partitions` / `start_partition_maintainer` / `start_batch_writer` /
`start_location_cleaner`，以及两个守护线程：
```
threading.Thread(target=start_tcp_server,      ... name='tcp-808').start()
threading.Thread(target=start_mqtt_subscriber, ... name='mqtt-sub').start()
```
这意味着 **TCP 接入、MQTT 接入、批量写、清理线程、Web 路由全部挤在同一个进程里**。
Web 层想水平扩展（多 worker / 多副本）就必然把这些单例逻辑一起复制，直接冲突。

### 1.3 如果贸然把 `workers` 改成 >1 会发生什么

假设直接把 `workers = 4`：

1. **指令下发静默失败**（最严重）。
   设备的 TCP 连接落在 worker-A，它的 socket 只存在于 worker-A 的 `sessions`。
   若一个下发请求（如 `POST /api/commands/text`，`app.py:1887`）被 nginx 负载到 worker-B，
   worker-B 的 `sessions.get(phone)` 返回 `None`（`app.py:1896-1899`），接口回
   `设备不在线: {phone}` 404 —— **设备明明在线，指令却发不出去**，且是概率性的（取决于请求落到哪个 worker），极难排查。
   全平台共 **6 处** `sessions.get(phone)` 下发点会同时中招：
   `app.py:1335`（batch_command）、`1896`（commands/text）、`1919`（commands/control）、
   `1952`（commands/track）、`2016`（commands/g618g）、`2066`（commands/zhiling）、
   以及客户门户 `3432`（customer/commands/text）。
2. **`_serial` 全局流水号冲突**。
   `state.py:28 _serial = [0]` + `next_serial()`（`state.py:60`）是**进程内**自增。
   多 worker 各有一份 `_serial`，同一台设备可能被不同进程用相同流水号下发，
   808 协议里流水号用于应答匹配，冲突会导致应答错配 / 设备行为异常。
3. **围栏状态割裂**。
   `state.py:33-43` 的 `fence_device_inside` / `fence_device_pending` /
   `fence_device_enter_time` / `fence_device_dwell_alarmed` 都是进程内 dict。
   位置报文只会进入持有该 TCP 连接的那个进程，围栏防抖（`FENCE_DEBOUNCE_N=3`）、
   进出判定、滞留计时都依赖连续报文积累状态。多 worker 下这些状态天然不共享，
   但因为报文只走一个进程，实际不会「跨进程割裂」——真正的问题是 **TCP 端口只能被一个进程绑定，多余 worker 根本收不到设备连接**。
4. **批量写 / 清理线程重复启动**。
   `start_batch_writer` 等虽有 `_batch_writer_started` 幂等锁（`ingest.py:198-208`），
   但那是**进程内**幂等，多进程会各起一份线程、各自持 `_db_lock`（SQLite 下 `_db_lock` 也是进程内锁，跨进程失效），SQLite 并发写冲突风险陡增。

**小结**：`workers=1` 不是保守配置，而是当前架构的**正确性前提**。要放开多 worker，
必须先把「持有 socket 的接入层」与「无状态的 Web 层」在进程边界上切开。

## 2. 目标架构

把系统拆成两类进程，用共享存储 + 消息通道解耦：

```
                          ┌─────────────────────────────────────────┐
   808 / G618G 设备 ─TCP─▶│  Ingest Service（接入服务，可多节点）      │
   MQTT 设备 ───────MQTT─▶│  - 监听 9090 TCP / 订阅 MQTT              │
                          │  - 本地持有 sessions{phone: socket}      │
                          │  - 解析协议、写位置队列、围栏检测          │
                          │  - 向 Redis 注册: phone → 本节点ID        │
                          │  - 订阅「指令通道」，收到后用本地socket     │
                          │    真正 sendall                          │
                          └───────┬──────────────────┬──────────────┘
                                  │ 注册表           │ 位置/报警事件
                                  ▼                  ▼
                          ┌───────────────┐   ┌──────────────────┐
                          │  Redis         │   │  消息通道         │
                          │ - 连接注册表   │   │ pub/sub 或轻量MQ  │
                          │   phone→node   │   │ - 下行: 指令       │
                          │ - _serial(可选)│   │ - 上行: SocketIO   │
                          └───────┬───────┘   │   推送事件         │
                                  │           └────────┬─────────┘
                                  │ 查注册表定位设备      │ 消费推送事件
                                  ▼                    ▼
                          ┌─────────────────────────────────────────┐
                          │  Web Service（Flask，可多worker/多副本）  │
                          │  - REST API（135 路由）                   │
                          │  - 下发: 查注册表→投递指令到目标节点         │
                          │  - SocketIO 网关（向浏览器推送）           │
                          └─────────────────────────────────────────┘
                                  ▲ HTTP / WebSocket
                          浏览器 / 客户门户
```

**关键：Web 层不再直接碰 socket，也不再直接跑 TCP 监听。** 它只做三件事：
处理 REST、把下发指令投递给接入层、把接入层上报的事件推给浏览器。

## 3. 关键设计

### 3.1 设备连接注册表放 Redis（放的是「位置」，不是 socket）

- Key: `dev:conn:{phone}` → Value: `{"node": "ingest-1", "since": <ts>, "proto": "808"}`，
  设 TTL（如 90s），由接入节点心跳续期。
- **写入时机**：对齐现有 socket 登记点，每一处 `sessions[...] = sock/conn` 都追加一次 Redis 注册：
  - `ingest.py:802` `handle_register` → `sessions[canonical_phone] = sock`
  - `ingest.py:825` `handle_auth` 成功 → `sessions[canonical] = sock`
  - `ingest.py:972` `handle_g618g_frame` register → `sessions[imei] = conn`
  - `ingest.py:1146` 帧头通用登记 → `sessions[phone] = conn`
- **删除/失效时机**：对齐 `handle_client` 的 `finally`（`ingest.py:1178-1184`，identity-checked pop）
  与 `handle_auth` 鉴权失败清理（`ingest.py:831-833`）。
  接入节点崩溃时，靠 Redis TTL 自动过期兜底，避免注册表出现指向死节点的僵尸条目。
- socket 本身**永不进 Redis**，只留在产生它的接入节点进程内存里。

### 3.2 指令下发流程（新旧对比）

**现状（进程内直发）**，以 `send_text`（`app.py:1887`）为例：
```
Web收到 POST /api/commands/text
  → _device_in_scope(phone) 鉴权
  → with sessions_lock: conn = sessions.get(phone)   # 本进程内查 socket
  → conn.sendall(p.encode_message(0x8300, phone, next_serial(), body))
```

**目标（跨进程转发）**：
```
Web收到 POST /api/commands/text
  → _device_in_scope(phone) 鉴权（不变）
  → node = redis.get("dev:conn:{phone}")             # 查注册表定位设备在哪个接入节点
  → 若 node 为空 → 返回「设备不在线」（等价现在的 404）
  → 把「下发任务」发布到该节点的指令通道:
       redis.publish("cmd:ingest-1", {
         "phone": phone, "msg_id": 0x8300, "proto": "zhiling",
         "payload": <指令内容>, "req_id": <uuid>
       })
  → 接入节点订阅 cmd:{self_node}，收到后:
       with sessions_lock: conn = sessions.get(phone) # 本地 socket 一定在
       serial = next_serial()                          # 流水号归属接入节点（见 3.4）
       conn.sendall(encode_message(msg_id, phone, serial, payload))
       redis.publish("cmd:ack:{req_id}", {"ok": true}) # 回执，供 Web 决定同步返回
```

**要点**：
- **指令编码放在哪？** 建议**指令报文的字节编码（`p.encode_message` / `zl.build_*` /
  `g618.build_*`）下沉到接入节点执行**，Web 层只传「语义指令」（如 `{"cmd":"text","text":"..."}`）。
  理由：流水号 `next_serial()` 必须由持有连接的接入节点分配（见 3.4），编码时才用得到，
  放 Web 层会引入跨进程流水号协调难题。
- **同步返回语义保持不变**：现有接口是同步返回「已发送 / 失败」。用 `req_id` + 短超时（如 3s）
  等接入节点回执，超时则返回「下发超时」。避免改动前端契约。

### 3.3 位置数据流与 SocketIO 推送在新架构下怎么走

- **位置落库**：保持接入节点内 `enqueue_location`（`ingest.py:63`）→ `_batch_writer_loop`
  （`ingest.py:189`）→ DB 的路径不变。**每个接入节点各自跑一份批量写线程**，直接写 PG
  （PG 连接池 `db.py:62 _get_pg_pool` 支持多进程；SQLite 单写锁则不适合多接入节点，见 4/风险）。
- **SocketIO 推送（上行）**：现在接入线程直接 `socketio.emit`（`ingest.py:_sio_emit` 549、
  `_emit_alarm` 742）。新架构下浏览器的 WebSocket 连在 **Web Service** 上，接入节点没有这些
  浏览器连接。两种落地方式：
  - **方式 A（推荐，改动最小）**：用 **Flask-SocketIO 的 message_queue（Redis）** 特性。
    `SocketIO(app, message_queue="redis://...")` 后，任意进程调用 `socketio.emit(...)` 都会经
    Redis 广播到持有浏览器连接的 Web 进程。接入节点只需把 `extensions.py` 的 `socketio` 也接上
    同一个 message_queue，`_sio_emit` / `_emit_alarm` 代码几乎不用改。
  - **方式 B**：接入节点把推送事件 `publish` 到上行通道，Web 层订阅后再 `socketio.emit`。
    更显式但要多写一层桥接。
- 结论：**优先用 Flask-SocketIO 的 Redis message_queue**，它正是为「多进程共享 SocketIO 广播」设计的。

### 3.4 进程内状态如何处理

| 状态 | 现状位置 | 新归属 | 理由 |
|---|---|---|---|
| `sessions{phone:socket}` | `state.py:26` | **接入节点本地内存**（不共享） | socket 不可序列化，必须留在持有它的进程 |
| `_serial` 流水号 | `state.py:28`,`next_serial()` 60 | **归属接入节点**（每节点一份，随连接下发） | 流水号只需在「同一条 TCP 连接的上下行」内单调唯一；一台设备只连一个接入节点，故节点内自增即可满足 808 应答匹配，**无需全局共享**。若未来做接入节点故障转移，可退化为 Redis `INCR` 分配。 |
| 围栏状态 4 dict | `state.py:33-43` | **接入节点本地内存** | 依赖同一设备连续位置报文积累（防抖/滞留计时）；设备固定连一个节点，节点内维护即可。设备迁移节点（重连到别的节点）时状态从零重建，等价于现在的设备重连，可接受。 |
| `_devid_cache` / `_alarm_last_ts` | `state.py:19-22` | **接入节点本地内存** | 纯性能/去重缓存，进程内即可，无需共享 |
| `_dev_latest` / `_loc_queue` | `state.py:16-18` | **接入节点本地内存** | 批量写缓冲，天然属于产生数据的节点 |

**核心洞察**：除了「连接注册表」必须共享（Web 要靠它定位设备），
**其余进程内状态都可以「归属接入节点」而无需真正外置**——因为一台设备只与一个接入节点建立 TCP 连接，
所有依赖该连接连续报文的状态天然聚集在那个节点。这也是为什么「外置 sessions 到 Redis」是错误方向：
它试图共享本不需要共享、且物理上无法共享的东西。

## 4. 分阶段迁移步骤（每阶段可独立上线验证）

> 原则：**每一步都能单独部署、单独回滚、单独验证**，绝不一次性大爆炸。

### 阶段 0（准备，0 风险）
- 引入 Redis（若尚无）。仅作基础设施接入，不改任何业务逻辑。
- 补一份**下发链路冒烟脚本**：对若干真机设备逐个调用 6 个下发接口，人工确认设备有反应
  （为后续每阶段回归打基础）。

### 阶段 1：抽出独立的 Ingest 进程，但仍单实例
- 把 `start_tcp_server` / `start_mqtt_subscriber` / `start_batch_writer` /
  `start_location_cleaner` / 分区维护，从 `gunicorn.conf.py:post_fork` 移到**独立进程**
  （新建 `ingest_main.py`，`if __name__=='__main__'` 里启动这些线程）。
- Web 的 gunicorn 仍 `workers=1`（因为此时下发仍走进程内 socket）。
  ⚠️ 此阶段下发还不能跨进程，所以**接入进程和 Web 进程必须仍是同一进程**，
  否则下发立刻失败。**因此阶段 1 的真实目标是「代码解耦 + 部署脚本就绪」，
  运行时仍合进程**，为阶段 2 铺路。可先只做「把启动逻辑集中到可独立调用的入口函数」，
  不真正分进程。
- **验证**：功能全回归，下发冒烟脚本全绿；确认 `post_fork` 逻辑抽走后现有单进程行为完全一致。

### 阶段 2：引入注册表 + 指令转发通道（仍单接入节点、单 Web）
- 在 3.1 列出的 4 个 socket 登记点追加 Redis 注册；在断连清理点删除注册。
- 把 6 处下发点（`sessions.get`）改造成「查注册表 → publish 到指令通道 → 接入节点 sendall」。
  接入节点新增指令通道订阅循环。
- **此时可以真正分进程**：Ingest 一个进程、Web 一个进程，下发靠 Redis 通道打通。
- SocketIO 接 Redis message_queue（3.3 方式 A）。
- **验证（最关键）**：真机 + 压测。逐个下发接口在「Web 与 Ingest 分进程」下验证指令到达设备；
  用回执 `req_id` 统计**下发成功率必须 100%**；位置上报、围栏进出、报警推送到浏览器全链路确认。

### 阶段 3：Web 层放开多 worker / 多副本
- 前提：DB 后端必须是 **PostgreSQL**（多进程各自持连接池，`db.py` 已支持）。
  ⚠️ **SQLite 不支持多写进程**（`_db_lock` 是进程内锁，跨进程失效），阶段 3 前必须完成 PG 迁移。
- Web 的 `gunicorn workers` 提到 N（如 4）。因为 Web 已无 socket / 无 `_serial` / 无围栏状态，
  纯无状态（登录限流 `_login_fail_map` 是进程内的，多 worker 下限流变宽松——可接受，或后续挪 Redis）。
- **验证**：多 worker 下反复下发（请求会随机落到不同 Web worker），成功率仍须 100%；
  并发登录限流行为符合预期。

### 阶段 4：接入层多节点
- 部署多个 Ingest 节点（不同机器 / 不同 9090 端口 + 前置 TCP 负载均衡，如 LVS/nginx stream）。
- 每个节点用唯一 `node_id` 注册；下发通道按 `cmd:{node_id}` 精确投递。
- 设备重连可能落到不同节点，注册表随之更新——天然支持。
- **验证**：拔掉一个接入节点，设备重连到另一节点后下发仍可达；注册表 TTL 正确回收死节点条目。

## 5. 风险、回滚点、验证方法

| 阶段 | 主要风险 | 回滚点 | 验证方法 |
|---|---|---|---|
| 1 | 启动逻辑抽错，后台线程漏起（如批量写没起→轨迹不落库） | 保留旧 `post_fork` 版本，改配置即回滚 | 启动日志确认 5 个后台线程/服务全起；下发冒烟全绿 |
| 2 | **下发丢失**（注册表未及时更新、通道消息丢）、回执超时误判 | 保留「进程内直发」代码路径，用开关切换 | **真机逐接口下发成功率 100%**；压测下发 QPS；kill 接入节点看注册表 TTL 回收 |
| 3 | SQLite 未迁 PG 导致多进程写冲突；`_serial` 若误留 Web 层会冲突 | Web `workers` 调回 1 | 多 worker 反复下发成功率 100%；DB 无锁冲突日志 |
| 4 | TCP 负载均衡把同设备打散、注册表指向死节点 | 缩回单接入节点 | 节点故障演练；设备重连后下发可达 |

**下发不丢的验证是重中之重**：每阶段都要用真机（至少覆盖 808、G618G、天禧 zhiling 三种协议路径）
逐个跑通 `commands/text`、`commands/control`、`commands/track`、`commands/g618g`、
`commands/zhiling`、`devices/batch_command`、`customer/commands/text` 这 7 条下发接口。

## 6. 工作量粗估（人天）

| 阶段 | 内容 | 估时 |
|---|---|---|
| 0 | Redis 接入 + 下发冒烟脚本 | 1.5 |
| 1 | 启动逻辑抽独立入口 + 部署脚本 | 2 |
| 2 | 注册表 + 6处下发改造 + 指令通道 + SocketIO message_queue + 真机压测 | 6~8 |
| 3 | PG 迁移收尾 + Web 多 worker + 限流评估 | 3~4（PG 若已就绪则减半） |
| 4 | 接入多节点 + TCP LB + 故障演练 | 4~5 |
| — | 联调 / 回归 / 文档 | 3 |
| **合计** | | **约 20~24 人天** |

---

# 文档二：app.py 拆 Blueprint

> **本文档同样是「方案」，不是「实现」。** `app.py` 4595 行、135 路由、无任何 Blueprint，
> 大量路由与全局状态、辅助函数、`@app.before_request`、`init_db`、SPA 兜底交织。
> 无自动化测试的前提下机械拆分极易引入「路由丢失 / 路径变化 / 循环 import」。
> 先固化拆分策略与回归口径，再分批小步执行。

## 1. 现状

- **单文件规模**：`server/app.py` 4595 行，135 个 `@app.route/get/post/put/delete`，**零 Blueprint**。
- **与全局状态交织**：
  - 会话/下发：`sessions` / `sessions_lock` / `next_serial`（从 `core.state` import，`app.py:691`），
    6 处下发点直接 `conn.sendall`。
  - DB 抽象：`db_exec/db_query/db_query_one/db_scalar/get_db/_db_lock/DB_BACKEND`（`app.py:140-143`）。
  - 队列/缓存：`_loc_queue/_dev_latest/_devid_cache/_alarm_last_ts`（`app.py:155-159`）。
  - 鉴权/scope：`_current_admin/_org_scope_ids/_org_where/_get_portal_customer/_verify_admin_token`
    等（从 `core.security` import，`app.py:124-130`）。
  - SocketIO 单例：`app` / `socketio`（从 `core.extensions` import，`app.py:104`）。
- **文件内自有辅助函数**（拆分时必须一并安置）：
  `ok`（792）、`fail`（797）、`add_op_log`（2156）、`_page_params`（764）、
  `_num_or_none`（777）、`_device_in_scope`（1872）、`_customer_and_descendants`（1046）、
  `_get_all_descendant_cids` / `_get_subtree_phones`（客户门户用）、`_sync_fence_devices`（2579）、
  `_admin_fence_or_none`（2685）、`_get_platform_setting`（1774）、`_resolve_branding`（1793）、
  `_get_device_org`（2985）、`_invalidate_device_org_cache`（2997）、`send_zhiling_cmd`（2042）等。
- **拦截器**：`@app.before_request _require_admin_for_api`（`app.py:3108`），
  按路径前缀放行 `/api/ping`、`/api/auth/*`、`/api/customer/*`、`/api/platform-setting` 等。
- **init_db**：`app.py:168`，一大段 `executescript` 建表；`__main__`（4575）和
  `gunicorn post_fork` 都调用它。
- **SocketIO 事件**：`on_connect`（3007）、`on_disconnect`（3046）用 `@socketio.on`。
- **SPA 兜底 + 错误处理**：`_serve_spa`（4525）、`@app.errorhandler`（4545/4558/4566）。

## 2. 目标分组（按资源域）

按 `/api/` 前缀统计（实测）得到分组建议。**注意 `/api/customer/*` 独占 33 个路由**，
是最大的一块，且它与管理端共享很多逻辑但走独立 token，应单独成蓝图。

| Blueprint | url_prefix | 覆盖路由（前缀） | 约数量 | 备注 |
|---|---|---|---|---|
| `devices_bp` | 无（保持 `/api`） | `/api/devices*`、`/api/locations*` | 16 + 2 | 与 `_device_in_scope`、批量操作耦合 |
| `command_bp` | 无 | `/api/commands*`、`/api/command-history*` | 5 + 2 | **与 sessions/下发深度耦合，最后拆** |
| `customer_bp` | 无 | `/api/customer/*` | 33 | 最大块；独立客户 token |
| `fence_bp` | 无 | `/api/fences*`、`/api/mark_points*`、`/api/risk_points*` | 6 + 3 + 3 | 含 `_sync_fence_devices` |
| `sim_bp` | 无 | `/api/sims*`、`/api/recharges*` | 6 + 2 | |
| `role_bp` | 无 | `/api/roles*` | 5 | 低耦合，优先拆 |
| `alarm_bp` | 无 | `/api/alarms*`、`/api/alarm-rules*`、`/api/alarm-types` | 3 + 4 + 1 | |
| `org_bp` | 无 | `/api/org*` | 6 | |
| `sys_bp` | 无 | `/api/sys/users*`、`/api/modules*` | 5 + 3 | 用户/权限管理 |
| `customers_bp` | 无 | `/api/customers*`（管理端管客户，区别于门户 `/api/customer/`） | 7 | 注意与 `customer_bp` 路径易混 |
| `auth_bp` | 无 | `/api/auth/*` | 3 | 含登录限流 |
| `dashboard_bp` | 无 | `/api/report/summary`、`/api/devices/summary`、`/api/oplogs`、`/api/health*`、`/api/attendance*`、`/api/beacons*` | ~12 | 报表/健康/考勤/信标等只读为主 |
| `misc_bp` | 无 | `/api/ping`、`/api/platform-setting*`、`/api/upload/avatar`、`/uploads/*`、`/api/_routes`、`/api/fences/check`、SPA、错误处理 | ~8 | 兜底与杂项 |

> **关键约束：所有 Blueprint 的 `url_prefix` 一律不设（或设为空）**，让每个路由继续在装饰器里写完整
> `/api/...` 路径。**绝不能用 `url_prefix='/api/devices'` 再把装饰器改成 `@bp.get('')`**——那会改变
> 现有 URL、破坏前端契约（见风险 4）。

## 3. 拆分策略

### 3.1 共享依赖的安置

- **`ok` / `fail`**：这是全项目最高频的两个响应包装器。**下沉到 `core/response.py`**（新建），
  所有 Blueprint `from core.response import ok, fail`。它俩零依赖，最安全。
- **`add_op_log`**（2156）、`_page_params`、`_num_or_none`：下沉到 `core/helpers.py`（新建），
  纯工具函数，只依赖 `db_exec`/`request`。
- **鉴权/scope**：已在 `core/security.py`，Blueprint 直接 import，无需再动。
- **`_device_in_scope` / `_get_device_org` / `_customer_and_descendants` /
  `_get_all_descendant_cids` / `_get_subtree_phones`**：这些是「设备/客户可见域」逻辑，
  被 devices/command/customer 多个蓝图共用。**下沉到 `core/scope_helpers.py`**（新建），
  依赖 `db_query`/`security`，不依赖 `app`，可安全共享。
- **`app` / `socketio`**：已在 `core/extensions.py`。**Blueprint 从这里 import `socketio`**
  用于 emit；**Blueprint 本身不 import `app`**（只 import `Blueprint`），最后在一个集中处
  （`app.py` 或新 `bootstrap.py`）`app.register_blueprint(...)`。这是**打破循环 import 的关键**。

### 3.2 循环 import 的规避原则

现有工程已经用「中立底层模块」思路打破过环（`extensions.py` 注释明确说明）。延续这套：
```
Blueprint 模块  ──import──▶  core.response / core.helpers / core.scope_helpers
                              / core.security / core.db / core.state / core.extensions.socketio
                              （全是无 app 依赖的底层）
app.py (或 bootstrap.py) ──import 各 blueprint 并 register_blueprint──▶ 完成装配
```
**规则**：Blueprint 只能依赖「不依赖 app 的底层模块」，
`app` 对 Blueprint 是单向依赖（app 注册 blueprint，blueprint 不反向 import app）。

### 3.3 before_request 怎么处理

`_require_admin_for_api`（3108）目前是 `@app.before_request`（**应用级**，拦截所有请求）。
两个选择：
- **方案 A（推荐，行为最稳）**：**保持它为应用级 `before_request`，留在 `app.py`/`bootstrap.py`**，
  不拆进任何 Blueprint。因为它靠 `request.path` 前缀判断放行，与蓝图无关，逻辑天然是全局的。
  拆进蓝图反而要复制多份、易漏。
- **方案 B**：拆成蓝图级 `@bp.before_request`。**不推荐**——放行白名单是跨蓝图的
  （如 `/api/customer/*` 整段放行），蓝图级会割裂这段逻辑。

### 3.4 init_db 怎么放

`init_db`（168）与 `_setup_pg_partitions` 等被 `__main__` 和 `gunicorn post_fork` 调用。
**下沉到 `core/schema.py`（新建）**，`app.py`/`gunicorn.conf.py` 从那里 import。
建表 SQL 与路由无关，早该独立。此步可**先于蓝图拆分单独做**（低风险独立项）。

### 3.5 SPA 兜底路由与错误处理

- `_serve_spa`（4525，含 `/` 与 `/<path:path>`）**必须最后注册**——它是 catch-all，
  若在 API 蓝图之前注册会吞掉所有请求。放在 `misc_bp` 且**保证 `register_blueprint` 顺序里
  misc_bp / SPA 路由最后注册**。
- `@app.errorhandler`（4545/4558/4566）是**应用级**，保留在 `app.py`/`bootstrap.py`，不进蓝图。

## 4. 迁移风险清单（自动化/手工拆分最易踩的坑）

1. **漏迁全局变量 / 辅助函数**。
   路由体内用到的 `ok/fail/add_op_log/_device_in_scope/next_serial/sessions/...` 若没在新模块里
   import 到位，运行时才 `NameError`。**必须逐路由静态扫描其自由变量**，确认每个都在新模块可解析。
2. **装饰器路由路径变化**。
   从 `@app.get('/api/devices')` 改成 `@bp.get('/api/devices')` 时，**若给 Blueprint 设了
   `url_prefix`，路径会翻倍或改变**（如 prefix `/api` + `/api/devices` = `/api/api/devices`）。
   **本方案强制 `url_prefix=None` 并保留完整路径**，规避此坑。
3. **循环 import**。
   最常见于「Blueprint import app / app import Blueprint」双向。用 3.2 的单向依赖原则规避。
   另一处隐患：辅助函数下沉后，若 `core/helpers.py` 又 import 了某个 Blueprint，会成环。
4. **蓝图注册顺序**。
   - SPA catch-all（`/<path:path>`）必须**最后**注册，否则吞掉 API 路由。
   - `/api/customers`（管理端，`customers_bp`）与 `/api/customer/`（门户，`customer_bp`）
     前缀相近，注册两者都需保证各自完整路径不被对方 catch-all 式规则影响（本项目它们都是精确路径，风险低，但需在回归里专门对比）。
5. **`url_prefix` 改变现有 API 路径（最高危）**。
   **硬性要求：拆分前后 `/api/...` 全部 URL 一字不差。** 前端与真机客户端都硬编码这些路径，
   任何变化都是线上事故。验证靠 §6 的 `url_map` 全量对比。
6. **`@socketio.on` 事件处理器**（`on_connect`/`on_disconnect`，3007/3046）。
   SocketIO 事件不是 Flask 路由，**不能放进 Blueprint 的路由体系**。保留在 `app.py` 或
   独立 `socket_events.py`，用 `@socketio.on` 注册（`socketio` 从 extensions import）。
7. **`before_request` / `errorhandler` 归属**。见 3.3 / 3.5，保持应用级，别拆进蓝图。
8. **`request` 上下文依赖**。辅助函数如 `_org_scope_ids(request)`、`add_op_log`（内部读
   `request.remote_addr`）依赖请求上下文，下沉后仍在请求内调用，无问题；但要确保没有在模块
   加载期（import 时）就调用它们。

## 5. 分批拆分顺序（先低风险独立，后核心耦合）

> 每批**独立提交、独立回归、独立可回滚**。

- **批 0（基础设施，0 路由变动）**：抽 `core/response.py`(ok/fail)、`core/helpers.py`、
  `core/scope_helpers.py`、`core/schema.py`(init_db)。此批不建任何 Blueprint，只是把辅助函数搬家
  并让 `app.py` 从新位置 import。**先验证纯搬家不改行为**。
- **批 1（低风险独立域）**：`role_bp`（5，几乎不碰 sessions/scope）、`alarm_bp`（8）、
  `sim_bp`（8）、`org_bp`（6）。这些是相对独立的 CRUD。
- **批 2（中等）**：`fence_bp`（12，含 `_sync_fence_devices`）、`sys_bp`（8）、
  `customers_bp`（7，管理端管客户）、`dashboard_bp`（报表/健康/考勤/信标）、`auth_bp`（3）。
- **批 3（大而独立）**：`customer_bp`（33）。量大但边界清晰（全 `/api/customer/` 前缀、独立 token），
  单独一批集中处理。
- **批 4（核心耦合，最后拆）**：`devices_bp`（含 locations）+ `command_bp`。
  这两块与 `sessions` / `next_serial` / 6 处下发点深度耦合，且 `devices/batch_command`（1318）
  也走下发。**放最后**，此时其余已稳定，出问题范围可控。
- **批 5（收尾）**：`misc_bp` + SPA + errorhandler + `@socketio.on` 事件归位，`bootstrap.py` 统一
  `register_blueprint`（确定注册顺序，SPA 最后）。

## 6. 每批回归验证方法

### 6.1 路由清单全量对比（硬门槛）

拆分前后，`app.url_map` **必须完全一致**。项目已内置 `/api/_routes`（`debug_routes`，3096）
和 `app.py:3095` 附近路由可直接利用。做法：
1. 拆分**前**导出基线：`GET /api/_routes` 或脚本遍历 `app.url_map.iter_rules()`，
   把 `(rule, methods)` 排序后存 `routes_before.txt`。
2. 每批拆分**后**再导出 `routes_after.txt`。
3. `diff routes_before.txt routes_after.txt` **必须为空**。任何新增/缺失/方法变化/路径变化都要在合并前解决。

### 6.2 冒烟测试关键接口

每批至少覆盖：
- `GET /api/ping`（放行路径，验证 before_request 未被破坏）。
- 该批每个资源域的一个读接口 + 一个写接口（带真实 X-Admin-Token）。
- **批 4 专项**：7 条下发接口 + `devices/batch_command` 真机验证（同文档一 §5）。
- **customer 批**：用客户 token 验证 `/api/customer/*` 全段仍被 before_request 正确放行且门户 token 校验生效。
- SPA：`GET /` 与 `GET /some/vue/route` 返回 index.html；`GET /api/不存在` 返回 JSON 404（验证 errorhandler）。

## 7. 工作量粗估（人天）

| 批 | 内容 | 估时 |
|---|---|---|
| 0 | 辅助函数/init_db 下沉 + 回归 | 2 |
| 1 | role/alarm/sim/org 蓝图 | 2 |
| 2 | fence/sys/customers/dashboard/auth 蓝图 | 3 |
| 3 | customer_bp（33 路由） | 2.5 |
| 4 | devices + command（核心耦合）+ 真机下发回归 | 3 |
| 5 | misc/SPA/socket 事件/注册装配收尾 | 1.5 |
| — | 每批 url_map 对比 + 冒烟 + 联调 | 2 |
| **合计** | | **约 16 人天** |

---

## 附：从代码里确认的关键事实（供评审复核）

1. `sessions = {} # phone → socket`（`state.py:26`），存的是**活 socket 对象**，无法进 Redis。
2. `workers = 1` 硬约束来自 **TCP 9090 端口单例**（`ingest.py:1209 bind`）+ **sessions 进程内**，
   `gunicorn.conf.py:14` 注释已明说。
3. `post_fork`（`gunicorn.conf.py:27`）在唯一 worker 里起 TCP/MQTT/批量写/清理/分区维护全部后台线程。
4. 下发点共 **6 处** `sessions.get(phone)`：`app.py` 行 1335 / 1896 / 1919 / 1952 / 2016 / 2066，
   另有客户门户第 7 处 3432。
5. `_serial`（`state.py:28`）+ `next_serial()`（`state.py:60`）是进程内自增流水号。
6. 围栏状态 4 个 dict（`state.py:33-43`）+ 防抖 `FENCE_DEBOUNCE_N=3`，依赖同设备连续报文，天然属接入节点。
7. DB 双后端（`core/db.py`）：PG 有连接池（`_get_pg_pool`，`db.py:62`）支持多进程；
   SQLite 用**进程内** `_db_lock`（`db.py:230`），不支持多写进程 → 多 worker 前必须迁 PG。
8. SocketIO 单例在 `core/extensions.py:42`；多进程共享 emit 可用 Flask-SocketIO 的 Redis message_queue。
9. `app.py` = **4595 行 / 135 路由 / 0 Blueprint**；`/api/customer/*` 独占 **33** 路由（最大块），
   `/api/devices*` 16、`/api/customers*` 7、`/api/sims*` 6、`/api/org*` 6、`/api/fences*` 6、
   `/api/commands*` 5、`/api/roles*` 5、`/api/sys*` 5。
10. `@app.before_request _require_admin_for_api`（`app.py:3108`）按 `request.path` 前缀放行，天然全局。
11. `init_db`（`app.py:168`）被 `__main__`（4575）与 `post_fork` 双调用。
12. SPA catch-all `_serve_spa`（`app.py:4525`，`/<path:path>`）必须最后注册，否则吞掉 API。
13. 已有 `/api/_routes`（`debug_routes`，`app.py:3096`）可直接用于 url_map 基线对比。
