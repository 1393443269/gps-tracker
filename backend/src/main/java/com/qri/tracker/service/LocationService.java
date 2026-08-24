package com.qri.tracker.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.qri.tracker.entity.LocationRecord;

/**
 * 位置记录 Service
 */
public interface LocationService extends IService<LocationRecord> {

    LocationRecord getLatest(String phone);
}
