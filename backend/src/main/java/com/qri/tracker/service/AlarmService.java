package com.qri.tracker.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.qri.tracker.entity.AlarmRecord;

/**
 * 报警 Service
 */
public interface AlarmService extends IService<AlarmRecord> {

    boolean handle(Long id, String handler, String note);
}
