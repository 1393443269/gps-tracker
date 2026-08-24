CREATE DATABASE IF NOT EXISTS asset_tracker DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;
USE asset_tracker;

-- 设备表
CREATE TABLE IF NOT EXISTS `device` (
  `id`                  BIGINT       NOT NULL AUTO_INCREMENT,
  `phone`               VARCHAR(20)  NOT NULL COMMENT '终端手机号（唯一标识）',
  `name`                VARCHAR(100) DEFAULT NULL COMMENT '设备名称',
  `plate_no`            VARCHAR(20)  DEFAULT NULL COMMENT '车牌号',
  `plate_color`         TINYINT      DEFAULT 0   COMMENT '车牌颜色(0=无,1=蓝,2=黄,3=黑,4=白)',
  `manufacturer`        VARCHAR(50)  DEFAULT NULL COMMENT '制造商',
  `terminal_model`      VARCHAR(50)  DEFAULT NULL COMMENT '终端型号',
  `terminal_id`         VARCHAR(50)  DEFAULT NULL COMMENT '终端 ID',
  `auth_code`           VARCHAR(100) DEFAULT NULL COMMENT '鉴权码',
  `status`              TINYINT      DEFAULT 0   COMMENT '状态(0=离线,1=在线,2=报警)',
  `online_time`         DATETIME     DEFAULT NULL COMMENT '最后上线时间',
  `offline_time`        DATETIME     DEFAULT NULL COMMENT '最后离线时间',
  `last_lat`            DOUBLE       DEFAULT NULL COMMENT '最新纬度',
  `last_lng`            DOUBLE       DEFAULT NULL COMMENT '最新经度',
  `last_speed`          INT          DEFAULT NULL COMMENT '最新速度(单位: 0.1 km/h)',
  `last_location_time`  DATETIME     DEFAULT NULL COMMENT '最新定位时间',
  `created_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_phone` (`phone`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '设备信息';

-- 位置记录表
CREATE TABLE IF NOT EXISTS `location_record` (
  `id`          BIGINT   NOT NULL AUTO_INCREMENT,
  `device_id`   BIGINT   NOT NULL,
  `phone`       VARCHAR(20) NOT NULL,
  `lat`         DOUBLE   NOT NULL COMMENT '纬度(WGS-84)',
  `lng`         DOUBLE   NOT NULL COMMENT '经度(WGS-84)',
  `altitude`    INT      DEFAULT NULL COMMENT '海拔(米)',
  `speed`       INT      DEFAULT NULL COMMENT '速度(0.1 km/h)',
  `direction`   INT      DEFAULT NULL COMMENT '方向(0-359度,正北为0)',
  `alarm_flag`  BIGINT   DEFAULT 0 COMMENT '报警标志位',
  `status_flag` BIGINT   DEFAULT 0 COMMENT '状态标志位',
  `mileage`     BIGINT   DEFAULT NULL COMMENT '里程(0.1 km)',
  `gps_time`    DATETIME NOT NULL COMMENT 'GPS 定位时间',
  `created_at`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_phone_gps_time` (`phone`, `gps_time`),
  KEY `idx_device_id` (`device_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '位置记录';

-- 报警记录表
CREATE TABLE IF NOT EXISTS `alarm_record` (
  `id`           BIGINT       NOT NULL AUTO_INCREMENT,
  `device_id`    BIGINT       NOT NULL,
  `phone`        VARCHAR(20)  NOT NULL,
  `alarm_type`   INT          NOT NULL COMMENT '报警类型(位号,0=SOS,1=超速,8=主电断...)',
  `alarm_desc`   VARCHAR(200) DEFAULT NULL COMMENT '报警描述',
  `lat`          DOUBLE       DEFAULT NULL,
  `lng`          DOUBLE       DEFAULT NULL,
  `speed`        INT          DEFAULT NULL,
  `alarm_time`   DATETIME     NOT NULL,
  `status`       TINYINT      DEFAULT 0 COMMENT '处理状态(0=未处理,1=已处理)',
  `handler`      VARCHAR(50)  DEFAULT NULL,
  `handle_time`  DATETIME     DEFAULT NULL,
  `handle_note`  VARCHAR(500) DEFAULT NULL,
  `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_phone_alarm_time` (`phone`, `alarm_time`),
  KEY `idx_status` (`status`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '报警记录';
