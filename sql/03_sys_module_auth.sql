-- ============================================================
-- 功能模块注册 + 组织-模块授权表
-- 在 02_sys_org_user.sql 之后执行
-- ============================================================
USE asset_tracker;

-- ── 功能模块注册表 ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `sys_module` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT,
  `module_code` VARCHAR(64)  NOT NULL               COMMENT '模块唯一编码，如 WH_IN',
  `module_name` VARCHAR(128) NOT NULL               COMMENT '模块名称',
  `parent_code` VARCHAR(64)  DEFAULT NULL           COMMENT '父模块编码，一级模块为NULL',
  `sort_order`  INT          NOT NULL DEFAULT 0,
  `description` VARCHAR(255) DEFAULT NULL,
  `is_system`   TINYINT(1)   NOT NULL DEFAULT 0     COMMENT '1=超管直通，不参与授权配置',
  `deleted`     TINYINT(1)   NOT NULL DEFAULT 0,
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_module_code` (`module_code`),
  KEY `idx_parent_code` (`parent_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='功能模块注册表';

-- ── 组织-模块授权表 ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `sys_org_module_auth` (
  `id`              BIGINT      NOT NULL AUTO_INCREMENT,
  `org_id`          BIGINT      NOT NULL               COMMENT '被授权的组织ID',
  `module_code`     VARCHAR(64) NOT NULL               COMMENT '被授权的模块编码',
  `is_enabled`      TINYINT(1)  NOT NULL DEFAULT 1     COMMENT '是否开放',
  `granted_by_org`  BIGINT      NOT NULL DEFAULT 0     COMMENT '授权操作方组织ID（0=系统初始化）',
  `granted_by_user` BIGINT      NOT NULL DEFAULT 0     COMMENT '授权操作人ID',
  `granted_at`      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `remark`          VARCHAR(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_org_module` (`org_id`, `module_code`),
  KEY `idx_org_enabled` (`org_id`, `is_enabled`),
  KEY `idx_grantor_org` (`granted_by_org`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='组织-模块授权表';

-- ── 初始化模块清单 ────────────────────────────────────────────
INSERT IGNORE INTO `sys_module` (`module_code`, `module_name`, `parent_code`, `sort_order`) VALUES
-- 一级模块
('ASSET',   '资产管理', NULL, 10),
('FENCE',   '电子围栏', NULL, 20),
('ALERT',   '告警中心', NULL, 30),
('TRACK',   '轨迹回放', NULL, 40),
('MAP',     '地图大屏', NULL, 50),
('REPORT',  '报表中心', NULL, 60),
('SYS',     '系统管理', NULL, 70),
-- 报表二级
('REPORT_ASSET', '资产报表',  'REPORT', 1),
('REPORT_ALERT', '告警报表',  'REPORT', 2),
-- 系统管理二级
('SYS_USER',        '用户管理',     'SYS', 1),
('SYS_ORG',         '组织管理',     'SYS', 2),
('SYS_MODULE_AUTH', '模块授权配置', 'SYS', 3);

-- ── L1 总部全量授权（初始化，上线后管理员按需收权）────────────────
INSERT IGNORE INTO `sys_org_module_auth` (`org_id`, `module_code`, `is_enabled`, `granted_by_org`, `granted_by_user`)
SELECT 1, `module_code`, 1, 0, 0
FROM   `sys_module`
WHERE  `is_system` = 0;
