package com.qri.tracker.config;

import com.baomidou.mybatisplus.core.handlers.MetaObjectHandler;
import org.apache.ibatis.reflection.MetaObject;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

/**
 * MyBatis-Plus 自动填充 createdAt / updatedAt
 */
@Component
public class MybatisPlusConfig implements MetaObjectHandler {

    @Override
    public void insertFill(MetaObject metaObject) {
        LocalDateTime now = LocalDateTime.now();
        this.strictInsertFill(metaObject, "createdAt",  LocalDateTime.class, now);
        this.strictInsertFill(metaObject, "updatedAt",  LocalDateTime.class, now);
        this.strictInsertFill(metaObject, "grantedAt",  LocalDateTime.class, now);  // SysOrgModuleAuth
    }

    @Override
    public void updateFill(MetaObject metaObject) {
        this.strictUpdateFill(metaObject, "updatedAt", LocalDateTime.class, LocalDateTime.now());
    }
}
