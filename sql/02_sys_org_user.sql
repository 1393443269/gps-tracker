-- ============================================================
-- 五级组织体系 + 用户表
-- 在 asset_tracker 库执行
-- MySQL 8.0+
-- ============================================================
USE asset_tracker;

-- ── 五级组织表 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `sys_org` (
  `id`         BIGINT       NOT NULL AUTO_INCREMENT,
  `org_name`   VARCHAR(128) NOT NULL               COMMENT '组织名称',
  `parent_id`  BIGINT       DEFAULT NULL           COMMENT '父组织ID，根节点为NULL',
  `org_level`  TINYINT      NOT NULL               COMMENT '层级：1=总部 2=分公司 3=厂区/项目部 4=班组/部门 5=资产/设备/库位',
  `org_code`   VARCHAR(64)  NOT NULL               COMMENT '业务编码，如 HQ / SC-01',
  `org_path`   VARCHAR(512) DEFAULT NULL           COMMENT '物化路径，如 /1/3/12/，加速子树查询',
  `sort_order` INT          NOT NULL DEFAULT 0     COMMENT '同级排序',
  `is_active`  TINYINT(1)   NOT NULL DEFAULT 1     COMMENT '是否启用',
  `deleted`    TINYINT(1)   NOT NULL DEFAULT 0     COMMENT '逻辑删除',
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_org_code` (`org_code`),
  KEY `idx_parent_id` (`parent_id`),
  KEY `idx_org_path`  (`org_path`(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='五级组织架构表';

-- ── 用户表 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS `sys_user` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT,
  `username`    VARCHAR(64)  NOT NULL               COMMENT '登录用户名（唯一）',
  `password`    VARCHAR(255) NOT NULL               COMMENT 'BCrypt 哈希密码',
  `real_name`   VARCHAR(64)  DEFAULT NULL           COMMENT '真实姓名',
  `org_id`      BIGINT       NOT NULL               COMMENT '所属组织ID',
  `org_level`   TINYINT      NOT NULL               COMMENT '继承自所属组织层级',
  `user_type`   TINYINT      NOT NULL DEFAULT 1     COMMENT '1=普通管理员 9=超级管理员(L1总部，跳过模块校验)',
  `phone`       VARCHAR(20)  DEFAULT NULL,
  `email`       VARCHAR(128) DEFAULT NULL,
  `is_active`   TINYINT(1)   NOT NULL DEFAULT 1     COMMENT '是否启用',
  `last_login`  DATETIME     DEFAULT NULL           COMMENT '最后登录时间',
  `deleted`     TINYINT(1)   NOT NULL DEFAULT 0     COMMENT '逻辑删除',
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`),
  KEY `idx_org_id` (`org_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统用户表';

-- ── 初始化数据 ────────────────────────────────────────────────
-- L1 总部（根节点）
INSERT IGNORE INTO `sys_org` (`id`, `org_name`, `parent_id`, `org_level`, `org_code`, `org_path`, `sort_order`)
VALUES (1, '总部', NULL, 1, 'HQ', '/1/', 0);

-- 超级管理员（密码：Admin@123，BCrypt 哈希）
-- 如需重置密码，用 Hutool BCrypt.hashpw("新密码", BCrypt.gensalt()) 重新生成
INSERT IGNORE INTO `sys_user` (`username`, `password`, `real_name`, `org_id`, `org_level`, `user_type`)
VALUES (
  'admin',
  '$2a$10$7EqJtq98hPqEX7fNZaFWoOa9/UoEaeSjPaepClDPMnFf6DjmDlwgS',  -- Admin@123
  '超级管理员',
  1,
  1,
  9
);
