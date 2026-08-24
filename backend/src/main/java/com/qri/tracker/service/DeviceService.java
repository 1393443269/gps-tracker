package com.qri.tracker.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.qri.tracker.entity.Device;
import com.qri.tracker.protocol.body.LocationBody;

/**
 * 设备 Service
 */
public interface DeviceService extends IService<Device> {

    Device getByPhone(String phone);

    void setOnline(String phone);

    void setOffline(String phone);

    void updateLocation(String phone, LocationBody loc);
}
