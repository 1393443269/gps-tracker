const pptxgen = require("pptxgenjs");
const pres = new pptxgen();
pres.layout = 'LAYOUT_16x9';
pres.title = '电子围栏管理平台设计方案';

// ── 颜色常量 ─────────────────────────────────────────────────────────────────
const C = {
  darkBg:   '0F172A',
  navyBg:   '1E3A8A',
  lightBg:  'F1F5F9',
  white:    'FFFFFF',
  cyan:     '06B6D4',
  teal:     '0D9488',
  amber:    'F59E0B',
  red:      'DC2626',
  purple:   '7C3AED',
  textDark: '1E293B',
  textGray: '64748B',
  border:   'E2E8F0',
};
const mkShadow = () => ({ type:"outer", color:"000000", blur:8, offset:2, angle:45, opacity:0.10 });

// ── 通用：添加带色块的页眉 ───────────────────────────────────────────────────
function addHeader(s, title, sub) {
  s.addShape(pres.shapes.RECTANGLE, { x:0, y:0, w:10, h:1.05, fill:{color:C.navyBg}, line:{color:C.navyBg} });
  s.addText(title, { x:0.4, y:0.08, w:9, h:0.6, fontSize:26, bold:true, color:C.white, fontFace:'Calibri', margin:0 });
  if (sub) s.addText(sub, { x:0.4, y:0.72, w:8, h:0.28, fontSize:12, color:C.cyan, fontFace:'Calibri', margin:0 });
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 1 — 封面
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.darkBg };
  // 装饰圆
  s.addShape(pres.shapes.OVAL, { x:6.8, y:-0.8, w:4.5, h:4.5, fill:{color:'1E3A8A', transparency:70}, line:{color:'1E3A8A', transparency:70} });
  s.addShape(pres.shapes.OVAL, { x:-1.2, y:3.2, w:3.5, h:3.5, fill:{color:'1E3A8A', transparency:70}, line:{color:'1E3A8A', transparency:70} });
  // 标签
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:0.6, y:0.9, w:3.0, h:0.42, fill:{color:C.teal}, line:{color:C.teal}, rectRadius:0.06 });
  s.addText('资产管理平台 · 技术方案', { x:0.6, y:0.9, w:3.0, h:0.42, fontSize:13, bold:true, color:C.white, fontFace:'Calibri', align:'center', valign:'middle', margin:0 });
  // 主标题
  s.addText('电子围栏管理平台', { x:0.5, y:1.55, w:9, h:1.1, fontSize:44, bold:true, color:C.white, fontFace:'Calibri' });
  s.addText('完整设计方案', { x:0.5, y:2.6, w:9, h:0.9, fontSize:44, bold:true, color:C.cyan, fontFace:'Calibri' });
  // 技术栈
  s.addText('Web 管理平台  ·  天地图 + MapLibre-GL JS  ·  PostgreSQL + PostGIS  ·  Redis', {
    x:0.5, y:3.75, w:9, h:0.45, fontSize:14, color:'94A3B8', fontFace:'Calibri'
  });
  s.addShape(pres.shapes.LINE, { x:0.5, y:4.35, w:9, h:0, line:{color:C.cyan, width:1} });
  s.addText('GPS / 北斗定位设备上报点位 → 空间判断进出围栏 → 实时告警推送', {
    x:0.5, y:4.45, w:9, h:0.4, fontSize:13, color:'64748B', fontFace:'Calibri'
  });
  s.addNotes('封面页。平台定位：资产管理平台电子围栏模块完整设计方案。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 2 — 目录
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.darkBg };
  s.addText('目  录', { x:0.5, y:0.25, w:9, h:0.75, fontSize:30, bold:true, color:C.white, fontFace:'Calibri' });

  const items = [
    { n:'01', t:'围栏类型选型',   d:'圆形 · 多边形 · 路线缓冲 · 行政区',   c:C.teal   },
    { n:'02', t:'数据库设计',     d:'PostgreSQL+PostGIS + Redis 防抖',        c:C.navyBg },
    { n:'03', t:'前端实现',       d:'MapLibre-GL Geoman 绘制 · 围栏渲染',    c:C.purple },
    { n:'04', t:'后端处理流程',   d:'GPS上报 → 判断 → 事件 → 告警推送',     c:C.amber  },
    { n:'05', t:'部署与性能',     d:'两种部署模式对比 · 规模参考',            c:C.red    },
    { n:'06', t:'Web平台功能 & 坑点', d:'功能清单 · 四大常见坑',             c:'059669'  },
  ];

  items.forEach((item, i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.5 + col * 3.0, y = 1.2 + row * 1.9;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w:2.8, h:1.65, fill:{color:'1E293B'}, line:{color:'334155'}, rectRadius:0.1 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:x+0.15, y:y+0.15, w:0.58, h:0.38, fill:{color:item.c}, line:{color:item.c}, rectRadius:0.06 });
    s.addText(item.n, { x:x+0.15, y:y+0.15, w:0.58, h:0.38, fontSize:14, bold:true, color:C.white, fontFace:'Calibri', align:'center', valign:'middle', margin:0 });
    s.addText(item.t, { x:x+0.15, y:y+0.65, w:2.5, h:0.42, fontSize:15, bold:true, color:C.white, fontFace:'Calibri', margin:0 });
    s.addText(item.d, { x:x+0.15, y:y+1.1, w:2.5, h:0.45, fontSize:11, color:'94A3B8', fontFace:'Calibri', margin:0 });
  });
  s.addNotes('目录：6大章节覆盖电子围栏从设计到实现的全链路。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 3 — 围栏类型选型
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };
  addHeader(s, '围栏类型选型', '四种围栏类型，覆盖资产监控主要场景');

  const types = [
    { tag:'最常用', title:'圆形围栏',    c:C.teal,   desc:'中心点经纬度 + 半径（米）\n适合仓库、厂区、固定点位\n后端存为 Polygon（ST_Buffer）' },
    { tag:'灵活',   title:'多边形围栏',  c:C.navyBg, desc:'自由绘制不规则区域\n适合园区、工地、禁区\n直接存 GeoJSON Polygon' },
    { tag:'可选',   title:'路线缓冲围栏', c:C.purple, desc:'沿路线设置左右缓冲距离\n用于路线偏离告警\nST_Buffer 路线几何体生成' },
    { tag:'可选',   title:'行政区围栏',  c:C.amber,  desc:'调用天地图行政区边界\n省/市/县级，无需手动描点\n自动同步行政区划调整' },
  ];

  types.forEach((t, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.35 + col * 4.75, y = 1.15 + row * 2.15;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w:4.5, h:2.0, fill:{color:C.white}, line:{color:C.border}, rectRadius:0.12, shadow:mkShadow() });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:x+0.18, y:y+0.17, w:0.85, h:0.36, fill:{color:t.c}, line:{color:t.c}, rectRadius:0.07 });
    s.addText(t.tag, { x:x+0.18, y:y+0.17, w:0.85, h:0.36, fontSize:12, bold:true, color:C.white, fontFace:'Calibri', align:'center', valign:'middle', margin:0 });
    s.addText(t.title, { x:x+1.15, y:y+0.14, w:3.15, h:0.44, fontSize:17, bold:true, color:C.textDark, fontFace:'Calibri', margin:0 });
    s.addText(t.desc, { x:x+0.18, y:y+0.66, w:4.12, h:1.18, fontSize:12, color:C.textGray, fontFace:'Calibri', margin:0 });
  });
  s.addNotes('四种类型。圆形和多边形是主力，路线缓冲和行政区按需扩展。注意圆形需后端 ST_Buffer 转多边形存储。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 4 — 告警触发规则
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };
  addHeader(s, '告警触发规则', '5 种告警类型 + 防重复触发核心机制');

  // 左：5种告警
  const alarms = [
    { l:'进入围栏告警',   d:'设备从外部进入围栏范围触发' },
    { l:'离开围栏告警',   d:'设备从围栏内部离开范围触发' },
    { l:'围栏内停留超时', d:'在围栏内滞留超过设定时间（分钟）' },
    { l:'围栏内超速告警', d:'围栏区域内行驶速度超过限速值' },
    { l:'生效时间段配置', d:'仅在指定时段和星期内生效判断' },
  ];
  alarms.forEach((a, i) => {
    const y = 1.2 + i * 0.78;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:0.35, y, w:5.3, h:0.68, fill:{color:C.white}, line:{color:C.border}, rectRadius:0.08, shadow:mkShadow() });
    s.addShape(pres.shapes.OVAL, { x:0.5, y:y+0.23, w:0.22, h:0.22, fill:{color:C.teal}, line:{color:C.teal} });
    s.addText(a.l, { x:0.85, y:y+0.06, w:4.65, h:0.32, fontSize:14, bold:true, color:C.textDark, fontFace:'Calibri', margin:0 });
    s.addText(a.d, { x:0.85, y:y+0.38, w:4.65, h:0.25, fontSize:11, color:C.textGray, fontFace:'Calibri', margin:0 });
  });

  // 右：核心原则
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:5.95, y:1.15, w:3.7, h:2.5, fill:{color:C.navyBg}, line:{color:C.navyBg}, rectRadius:0.12 });
  s.addText('核心设计原则', { x:6.1, y:1.3, w:3.4, h:0.45, fontSize:16, bold:true, color:C.white, fontFace:'Calibri', margin:0 });
  s.addText('只在状态切换时触发告警', { x:6.1, y:1.82, w:3.4, h:0.45, fontSize:14, bold:true, color:C.cyan, fontFace:'Calibri', margin:0 });
  const principle = [
    { text:'外 → 内：', options:{bold:true, breakLine:false} },
    { text:'触发一次【进入告警】\n', options:{breakLine:true} },
    { text:'持续在内：', options:{bold:true, breakLine:false} },
    { text:'不重复告警\n', options:{breakLine:true} },
    { text:'内 → 外：', options:{bold:true, breakLine:false} },
    { text:'触发一次【离开告警】', options:{} },
  ];
  s.addText(principle, { x:6.1, y:2.35, w:3.4, h:1.15, fontSize:12, color:'CBD5E1', fontFace:'Calibri', margin:0 });

  // 右：抖动警告
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:5.95, y:3.8, w:3.7, h:1.45, fill:{color:'FFF7ED'}, line:{color:C.amber}, rectRadius:0.1 });
  s.addText('⚠ 边界抖动风险', { x:6.1, y:3.88, w:3.4, h:0.38, fontSize:13, bold:true, color:C.amber, fontFace:'Calibri', margin:0 });
  s.addText('GPS 误差导致边界处来回跳动\n每次都告警 → 大量垃圾数据\n→ 需防抖机制（见后续章节）', {
    x:6.1, y:4.28, w:3.4, h:0.88, fontSize:11, color:'92400E', fontFace:'Calibri', margin:0
  });
  s.addNotes('5种告警类型，核心原则是状态切换才触发，不是每条GPS都触发。边界抖动是最大风险。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 5 — 数据库设计
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };
  addHeader(s, '数据库架构设计', 'PostgreSQL + PostGIS（三张表）+ Redis 状态缓存');

  const tables = [
    {
      name:'geo_fence', label:'围栏主表', c:C.navyBg, x:0.25,
      fields:['id, fence_name, fence_type','geom geometry + GIST 空间索引','radius（圆形半径/米）','alarm_enter / alarm_leave','alarm_dwell_timeout, speed_limit','valid_start/end, valid_weekdays'],
    },
    {
      name:'geo_fence_device', label:'围栏设备绑定', c:C.teal, x:3.55,
      fields:['fence_id → geo_fence(id)','device_imei VARCHAR(64)','UNIQUE(fence_id, imei)','一围栏可绑多台设备','一设备可绑多个围栏'],
    },
    {
      name:'geo_fence_alarm_log', label:'告警事件表', c:C.purple, x:6.55,
      fields:['fence_id, device_imei','alarm_type (1进/2出/3滞/4速)','lng, lat, alarm_time','handle_status (0未/1已处理)','address, remark'],
    },
  ];

  tables.forEach(t => {
    const W = 3.1, y = 1.12;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:t.x, y, w:W, h:3.35, fill:{color:C.white}, line:{color:C.border}, rectRadius:0.1, shadow:mkShadow() });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:t.x, y, w:W, h:0.72, fill:{color:t.c}, line:{color:t.c}, rectRadius:0.1 });
    s.addShape(pres.shapes.RECTANGLE, { x:t.x, y:y+0.52, w:W, h:0.2, fill:{color:t.c}, line:{color:t.c} });
    s.addText(t.name, { x:t.x+0.1, y:y+0.06, w:W-0.2, h:0.36, fontSize:13, bold:true, color:C.white, fontFace:'Calibri', align:'center', margin:0 });
    s.addText(t.label, { x:t.x+0.1, y:y+0.42, w:W-0.2, h:0.26, fontSize:11, color:'CBD5E1', fontFace:'Calibri', align:'center', margin:0 });
    t.fields.forEach((f, fi) => {
      s.addText('· '+f, { x:t.x+0.15, y:y+0.88+fi*0.38, w:W-0.25, h:0.35, fontSize:11, color:C.textDark, fontFace:'Calibri', margin:0 });
    });
  });

  // Redis bar
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:0.25, y:4.6, w:9.5, h:0.85, fill:{color:C.red}, line:{color:C.red}, rectRadius:0.1 });
  s.addText('Redis  状态防抖缓存', { x:0.45, y:4.67, w:2.8, h:0.38, fontSize:15, bold:true, color:C.white, fontFace:'Calibri', margin:0 });
  s.addText('fence:state:{imei}:{fenceId} = inside / outside    |    fence:dwell:{imei}:{fenceId} = 进入时间戳', {
    x:3.4, y:4.67, w:6.2, h:0.38, fontSize:12, color:'FECACA', fontFace:'Calibri', margin:0
  });
  s.addText('状态只存 Redis，切换时才写告警日志，规则变更需清对应 key', {
    x:0.45, y:5.05, w:7, h:0.28, fontSize:11, color:'FCA5A5', fontFace:'Calibri', margin:0
  });
  s.addNotes('三表分工：主表存围栏规则，绑定表存设备关联，告警日志存事件。Redis存实时状态是防抖核心。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 6 — 前端实现
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };
  addHeader(s, '前端实现方案', 'MapLibre-GL JS + @geoman-io/maplibre-gl-geoman');

  const steps = [
    { n:'1', t:'绘制围栏',    d:'startDraw("circle")\n或 startDraw("polygon")\n用户在地图点击绘制', c:C.navyBg },
    { n:'2', t:'获取GeoJSON', d:'getAllGeojson()\n圆形→Point+radius\n多边形→Polygon',              c:C.teal   },
    { n:'3', t:'后端入库',    d:'圆形 ST_Buffer 转 Polygon\n多边形直接存 geom\n统一 EPSG:4326',     c:C.purple },
    { n:'4', t:'图层渲染',    d:'addSource + addLayer(fill)\n禁区红 / 作业绿\n点击围栏弹窗规则',    c:C.amber  },
  ];

  steps.forEach((step, i) => {
    const x = 0.3 + i * 2.37;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y:1.18, w:2.18, h:2.85, fill:{color:C.white}, line:{color:C.border}, rectRadius:0.12, shadow:mkShadow() });
    s.addShape(pres.shapes.OVAL, { x:x+0.79, y:1.28, w:0.6, h:0.6, fill:{color:step.c}, line:{color:step.c} });
    s.addText(step.n, { x:x+0.79, y:1.28, w:0.6, h:0.6, fontSize:18, bold:true, color:C.white, fontFace:'Calibri', align:'center', valign:'middle', margin:0 });
    s.addText(step.t, { x:x+0.1, y:2.0, w:1.98, h:0.45, fontSize:15, bold:true, color:C.textDark, fontFace:'Calibri', align:'center', margin:0 });
    s.addText(step.d, { x:x+0.1, y:2.5, w:1.98, h:1.3, fontSize:11, color:C.textGray, fontFace:'Calibri', align:'center', margin:0 });
    if (i < 3) {
      s.addShape(pres.shapes.LINE, { x:x+2.18, y:2.58, w:0.19, h:0, line:{color:C.teal, width:2} });
    }
  });

  // 重要提示
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:0.3, y:4.2, w:9.4, h:1.08, fill:{color:'FFF7ED'}, line:{color:C.amber}, rectRadius:0.1 });
  s.addText('⚠ 重要：geoman circle 输出是 Point，不是 Polygon，前端不可直接渲染为围栏区域', {
    x:0.5, y:4.28, w:9.0, h:0.38, fontSize:13, bold:true, color:C.amber, fontFace:'Calibri', margin:0
  });
  s.addText('正确做法：前端把 Point + radius 传给后端，后端执行 ST_Buffer(point::geography, radius)::geometry 生成多边形后入库；前端渲染时读后端返回的 Polygon。', {
    x:0.5, y:4.65, w:9.0, h:0.52, fontSize:12, color:'92400E', fontFace:'Calibri', margin:0
  });
  s.addNotes('前端4步：绘制→获取GeoJSON→后端入库→渲染图层。关键：圆形输出Point，需后端转Polygon。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 7 — 后端处理流程
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };
  addHeader(s, '后端处理流程', '设备 GPS 上报 → 过滤 → 空间判断 → 状态比对 → 告警');

  // 左列：步骤 1-4
  const left = [
    { n:'1', t:'接收 GPS 上报',   d:'IMEI、lng、lat、speed、hdop、timestamp',   c:C.navyBg },
    { n:'2', t:'过滤无效点位',    d:'hdop > 5 跳过；重复点位跳过',               c:'6B7280'  },
    { n:'3', t:'查询绑定围栏',    d:'JOIN geo_fence_device WHERE imei=? AND enable=1', c:C.teal  },
    { n:'4', t:'PostGIS 空间判断', d:'ST_Contains(geom, ST_MakePoint(lng,lat))',  c:C.purple  },
  ];
  left.forEach((step, i) => {
    const y = 1.15 + i * 1.06;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:0.3, y, w:4.55, h:0.95, fill:{color:C.white}, line:{color:C.border}, rectRadius:0.1, shadow:mkShadow() });
    s.addShape(pres.shapes.OVAL, { x:0.45, y:y+0.22, w:0.5, h:0.5, fill:{color:step.c}, line:{color:step.c} });
    s.addText(step.n, { x:0.45, y:y+0.22, w:0.5, h:0.5, fontSize:15, bold:true, color:C.white, fontFace:'Calibri', align:'center', valign:'middle', margin:0 });
    s.addText(step.t, { x:1.05, y:y+0.06, w:3.65, h:0.36, fontSize:14, bold:true, color:C.textDark, fontFace:'Calibri', margin:0 });
    s.addText(step.d, { x:1.05, y:y+0.44, w:3.65, h:0.44, fontSize:11, color:C.textGray, fontFace:'Calibri', margin:0 });
  });

  // 右列：步骤 5-8
  const right = [
    { n:'5', t:'对比 Redis 状态',  d:'GET fence:state:{imei}:{fid} → inside/outside', c:C.amber  },
    { n:'6', t:'生成告警事件',     d:'外→内：进入事件 | 内→外：离开事件 | 滞留：超时事件', c:C.red   },
    { n:'7', t:'写入告警日志',     d:'INSERT geo_fence_alarm_log',                     c:'059669'  },
    { n:'8', t:'更新 Redis + 推送', d:'SET fence:state:new | WebSocket 推送前端弹窗',   c:C.navyBg },
  ];
  right.forEach((step, i) => {
    const y = 1.15 + i * 1.06;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:5.15, y, w:4.55, h:0.95, fill:{color:C.white}, line:{color:C.border}, rectRadius:0.1, shadow:mkShadow() });
    s.addShape(pres.shapes.OVAL, { x:5.3, y:y+0.22, w:0.5, h:0.5, fill:{color:step.c}, line:{color:step.c} });
    s.addText(step.n, { x:5.3, y:y+0.22, w:0.5, h:0.5, fontSize:15, bold:true, color:C.white, fontFace:'Calibri', align:'center', valign:'middle', margin:0 });
    s.addText(step.t, { x:5.9, y:y+0.06, w:3.65, h:0.36, fontSize:14, bold:true, color:C.textDark, fontFace:'Calibri', margin:0 });
    s.addText(step.d, { x:5.9, y:y+0.44, w:3.65, h:0.44, fontSize:11, color:C.textGray, fontFace:'Calibri', margin:0 });
  });

  // 中间分隔箭头
  s.addShape(pres.shapes.LINE, { x:4.85, y:2.65, w:0.3, h:0, line:{color:C.teal, width:2} });
  s.addShape(pres.shapes.LINE, { x:4.85, y:3.71, w:0.3, h:0, line:{color:C.teal, width:2} });
  s.addShape(pres.shapes.LINE, { x:4.85, y:4.77, w:0.3, h:0, line:{color:C.teal, width:2} });
  s.addNotes('8步处理流程。左侧：输入过滤与空间判断。右侧：状态比对→生成事件→写DB→推送。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 8 — 边界抖动解决方案
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };
  addHeader(s, '边界抖动问题与解决方案', 'GPS 误差导致边界处反复进出 → 必须防抖');

  // 问题描述
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:0.35, y:1.12, w:9.3, h:0.78, fill:{color:'FEF2F2'}, line:{color:C.red}, rectRadius:0.1 });
  s.addText('问题：GPS 定位误差 5-30 米，设备在围栏边界时连续上报点位来回跳动（内→外→内），产生大量垃圾告警，严重影响使用体验。', {
    x:0.55, y:1.2, w:9.0, h:0.62, fontSize:13, color:'7F1D1D', fontFace:'Calibri', margin:0
  });

  // 三种方案
  const sols = [
    {
      num:'方案 01', title:'状态连续确认（推荐）',
      desc:'连续 2-3 次上报状态一致，才更新 Redis 正式状态并触发告警；单次抖动忽略，不修改状态。',
      code:'Redis key：fence:cnt:{imei}:{fid}  记录连续计数',
      c:C.teal,
    },
    {
      num:'方案 02', title:'围栏缓冲区扩展',
      desc:'围栏向外扩展 20-50 米缓冲带；设备在缓冲区内不触发进出事件，超出缓冲带才切换状态。',
      code:'ST_Buffer(geom, 30) 生成外扩判断边界',
      c:C.navyBg,
    },
    {
      num:'方案 03', title:'过滤精度差点位',
      desc:'当 hdop > 5（水平精度因子过大）时，直接跳过该点位的围栏判断，不更新任何状态。',
      code:'NMEA 定位报文中 hdop 字段标识精度',
      c:C.purple,
    },
  ];

  sols.forEach((sol, i) => {
    const x = 0.35 + i * 3.15;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y:2.05, w:2.98, h:3.3, fill:{color:C.white}, line:{color:C.border}, rectRadius:0.12, shadow:mkShadow() });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:x+0.12, y:2.18, w:1.25, h:0.38, fill:{color:sol.c}, line:{color:sol.c}, rectRadius:0.07 });
    s.addText(sol.num, { x:x+0.12, y:2.18, w:1.25, h:0.38, fontSize:12, bold:true, color:C.white, fontFace:'Calibri', align:'center', valign:'middle', margin:0 });
    s.addText(sol.title, { x:x+0.12, y:2.65, w:2.74, h:0.52, fontSize:14, bold:true, color:C.textDark, fontFace:'Calibri', margin:0 });
    s.addText(sol.desc, { x:x+0.12, y:3.22, w:2.74, h:1.18, fontSize:12, color:C.textGray, fontFace:'Calibri', margin:0 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:x+0.1, y:4.52, w:2.78, h:0.68, fill:{color:'F1F5F9'}, line:{color:C.border}, rectRadius:0.07 });
    s.addText(sol.code, { x:x+0.15, y:4.56, w:2.68, h:0.58, fontSize:10, color:C.textGray, fontFace:'Calibri', margin:0 });
  });
  s.addNotes('三种防抖方案。实际推荐方案1（连续确认）+ 方案3（过滤精度差点位）组合使用。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 9 — 部署模式对比
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };
  addHeader(s, '部署模式对比', '平台服务端判断（推荐）vs 终端本地判断');

  const rows = [
    [
      { text:'对比维度',                    options:{bold:true, color:C.white, fill:{color:C.navyBg}} },
      { text:'模式 A：平台服务端判断（推荐）', options:{bold:true, color:C.white, fill:{color:C.teal}} },
      { text:'模式 B：终端本地判断',          options:{bold:true, color:C.white, fill:{color:C.purple}} },
    ],
    ['围栏逻辑位置',      '云端服务器计算',                 '设备本地固件计算'],
    ['是否需改设备固件',  '不需要，设备只管上报 GPS',       '需要，支持围栏 AT 指令下发'],
    ['断网是否能告警',    '不能（依赖上报）',               '可以（本地计算）'],
    ['规则修改生效',      '立刻生效，无需更新设备',         '需重新下发规则到设备'],
    ['适用场景',          '绝大多数资产管理平台',           '矿山、边境等高安全场景'],
    ['硬件要求',          '通用 GPS 设备均可',              '设备须支持围栏下发功能'],
    ['实施难度',          '低（后端开发即可）',             '高（固件+平台双端联调）'],
  ];

  s.addTable(rows, {
    x:0.3, y:1.18, w:9.4,
    colW:[2.2, 3.6, 3.6],
    rowH:0.43,
    border:{pt:1, color:C.border},
    fontFace:'Calibri', fontSize:12,
    align:'left', valign:'middle',
    color:C.textDark,
    fill:{color:C.white},
  });
  s.addNotes('两种部署模式。推荐模式A：服务端判断，无需改设备固件，立刻生效。模式B适合断网也需告警的高安全场景。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 10 — 性能规模参考
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };
  addHeader(s, '性能规模参考', '从百台到万台的技术选型建议');

  // 小规模
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:0.3, y:1.2, w:4.55, h:4.1, fill:{color:C.white}, line:{color:C.border}, rectRadius:0.14, shadow:mkShadow() });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:0.45, y:1.35, w:2.3, h:0.42, fill:{color:C.teal}, line:{color:C.teal}, rectRadius:0.08 });
  s.addText('几百 ～ 几千台设备', { x:0.45, y:1.35, w:2.3, h:0.42, fontSize:13, bold:true, color:C.white, fontFace:'Calibri', align:'center', valign:'middle', margin:0 });
  s.addText('单节点 PostgreSQL + PostGIS', { x:0.45, y:1.9, w:4.2, h:0.5, fontSize:18, bold:true, color:C.textDark, fontFace:'Calibri', margin:0 });
  ['PostGIS + GIST 空间索引完全够用','同步 HTTP 接口即可，无需消息队列','单节点可处理上万 QPS 定位上报','Redis 单实例维护状态，实施简单','运维成本低，快速落地'].forEach((p,i) => {
    s.addText('✓  '+p, { x:0.45, y:2.55+i*0.5, w:4.2, h:0.42, fontSize:13, color:i<3?C.textDark:C.textGray, fontFace:'Calibri', margin:0 });
  });

  // 大规模
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:5.15, y:1.2, w:4.55, h:4.1, fill:{color:C.navyBg}, line:{color:C.navyBg}, rectRadius:0.14 });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:5.3, y:1.35, w:1.8, h:0.42, fill:{color:C.amber}, line:{color:C.amber}, rectRadius:0.08 });
  s.addText('上万台设备', { x:5.3, y:1.35, w:1.8, h:0.42, fontSize:13, bold:true, color:C.white, fontFace:'Calibri', align:'center', valign:'middle', margin:0 });
  s.addText('分层过滤 + 消息队列架构', { x:5.3, y:1.9, w:4.2, h:0.5, fontSize:18, bold:true, color:C.white, fontFace:'Calibri', margin:0 });
  ['消息队列（Kafka/MQ）削峰缓冲','Redis GEO 粗过滤候选围栏','PostGIS 对候选围栏精确判断','只查设备绑定围栏，不全库扫描','Redis Cluster 横向扩展状态存储'].forEach((p,i) => {
    s.addText('▸  '+p, { x:5.3, y:2.55+i*0.5, w:4.2, h:0.42, fontSize:13, color:i<3?C.white:'94A3B8', fontFace:'Calibri', margin:0 });
  });
  s.addNotes('两个规模场景。小规模单节点PostGIS够用；大规模需消息队列+Redis GEO两级过滤。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 11 — Web 平台功能 & 常见坑点
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };
  addHeader(s, 'Web 平台功能 & 常见坑点', '五大功能模块 + 四个必须规避的设计陷阱');

  // 功能清单（左）
  s.addText('Web 平台功能', { x:0.3, y:1.18, w:4.5, h:0.4, fontSize:16, bold:true, color:C.navyBg, fontFace:'Calibri', margin:0 });
  const features = [
    { t:'围栏管理',   d:'新增/编辑/删除围栏，绑定设备，启用禁用' },
    { t:'地图可视化', d:'围栏图层渲染，点击弹窗，颜色区分类型'   },
    { t:'告警中心',   d:'实时弹窗，分页列表，处理标记，导出记录'  },
    { t:'资产联动',   d:'选中设备↔高亮关联围栏，批量绑定操作'    },
    { t:'轨迹回放',   d:'历史轨迹叠加围栏，查看历史越界时间点'    },
  ];
  features.forEach((f, i) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:0.3, y:1.68+i*0.74, w:4.5, h:0.65, fill:{color:C.white}, line:{color:C.border}, rectRadius:0.08, shadow:mkShadow() });
    s.addShape(pres.shapes.OVAL, { x:0.45, y:1.84+i*0.74, w:0.22, h:0.22, fill:{color:C.teal}, line:{color:C.teal} });
    s.addText(f.t, { x:0.8, y:1.72+i*0.74, w:1.3, h:0.3, fontSize:13, bold:true, color:C.textDark, fontFace:'Calibri', margin:0 });
    s.addText(f.d, { x:0.8, y:2.01+i*0.74, w:3.85, h:0.28, fontSize:11, color:C.textGray, fontFace:'Calibri', margin:0 });
  });

  // 坑点（右）
  s.addText('常见坑点', { x:5.2, y:1.18, w:4.5, h:0.4, fontSize:16, bold:true, color:C.red, fontFace:'Calibri', margin:0 });
  const pitfalls = [
    { t:'坑1 坐标系不统一',   d:'混用 GCJ-02 火星坐标导致围栏偏移数十米 → 全链路统一 EPSG:4326',        c:C.red    },
    { t:'坑2 手写射线法',      d:'自写判断代码有边界 bug、无空间索引 → 优先使用 PostGIS ST_Contains',      c:C.amber  },
    { t:'坑3 规则变更不清缓存', d:'修改/删除围栏后 Redis 旧状态残留 → 编辑时同步清理 fence:state:* key', c:C.purple },
    { t:'坑4 遍历全库围栏',    d:'每次上报扫全表，万台时崩溃 → 只查该设备绑定的围栏集合',                 c:C.navyBg },
  ];
  pitfalls.forEach((p, i) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:5.2, y:1.68+i*0.92, w:4.5, h:0.82, fill:{color:C.white}, line:{color:C.border}, rectRadius:0.08, shadow:mkShadow() });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:5.35, y:1.77+i*0.92, w:0.28, h:0.28, fill:{color:p.c}, line:{color:p.c}, rectRadius:0.05 });
    s.addText(p.t, { x:5.75, y:1.75+i*0.92, w:3.8, h:0.32, fontSize:13, bold:true, color:C.textDark, fontFace:'Calibri', margin:0 });
    s.addText(p.d, { x:5.35, y:2.07+i*0.92, w:4.25, h:0.36, fontSize:11, color:C.textGray, fontFace:'Calibri', margin:0 });
  });
  s.addNotes('Web平台5功能+4坑点合并一页。坐标系问题最隐蔽，全库扫描是最常见性能陷阱。');
}

// ══════════════════════════════════════════════════════════════════════════════
// Slide 12 — 总结
// ══════════════════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.darkBg };
  s.addShape(pres.shapes.OVAL, { x:6.5, y:2.2, w:5.5, h:5.5, fill:{color:'1E3A8A', transparency:72}, line:{color:'1E3A8A', transparency:72} });

  s.addText('总结与后续规划', { x:0.5, y:0.3, w:8, h:0.75, fontSize:28, bold:true, color:C.white, fontFace:'Calibri' });

  const summaryItems = [
    '围栏类型：圆形 + 多边形为主，路线缓冲 / 行政区可选扩展',
    '数据库：PostGIS + GIST 空间索引 + Redis 状态防抖，三层职责清晰',
    '告警机制：状态机切换触发，三方案组合防边界抖动，WebSocket 实时推送',
    '部署：平台服务端判断（推荐），通用设备即可接入，规则修改立刻生效',
    '性能：百台单节点足够；万台以上加消息队列 + Redis GEO 两级过滤',
  ];
  summaryItems.forEach((item, i) => {
    s.addShape(pres.shapes.OVAL, { x:0.5, y:1.32+i*0.65, w:0.28, h:0.28, fill:{color:C.cyan}, line:{color:C.cyan} });
    s.addText(item, { x:0.92, y:1.28+i*0.65, w:7.8, h:0.48, fontSize:14, color:'CBD5E1', fontFace:'Calibri', margin:0 });
  });

  s.addShape(pres.shapes.LINE, { x:0.5, y:4.72, w:9, h:0, line:{color:C.teal, width:1} });
  s.addText('下一步', { x:0.5, y:4.82, w:1.5, h:0.42, fontSize:14, bold:true, color:C.cyan, fontFace:'Calibri', margin:0 });
  s.addText('① 前端 Geoman 组件接入  ② PostGIS 建库建索引  ③ 后端围栏判断逻辑  ④ WebSocket 告警推送', {
    x:2.1, y:4.82, w:7.5, h:0.42, fontSize:12, color:'94A3B8', fontFace:'Calibri', margin:0
  });
  s.addNotes('总结5条核心设计决策，4个后续开发步骤。');
}

// ── 写出文件 ─────────────────────────────────────────────────────────────────
const OUT = "C:/Users/admin/Agent工作区/dome/电子围栏管理平台设计方案.pptx";
pres.writeFile({ fileName: OUT })
  .then(() => console.log("OK:" + OUT))
  .catch(err => { console.error(err); process.exit(1); });
