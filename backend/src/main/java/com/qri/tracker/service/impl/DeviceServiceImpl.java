package com.qri.tracker.service.impl;

import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.qri.tracker.entity.Device;
import com.qri.tracker.mapper.DeviceMapper;
import com.qri.tracker.protocol.body.LocationBody;
import com.qri.tracker.service.DeviceService;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

@Service
public class DeviceServiceImpl extends ServiceImpl<DeviceMapper, Device> implements DeviceService {

    @Override
    public Device getByPhone(String phone) {
        return baseMapper.selectByPhone(phone);
    }

    @Override
    public void setOnline(String phone) {
        update(new LambdaUpdateWrapper<Device>()
                .eq(Device::getPhone, phone)
                .set(Device::getStatus, 1)
                .set(Device::getOnlineTime, LocalDateTime.now()));
    }

    @Override
    public void setOffline(String phone) {
        update(new LambdaUpdateWrapper<Device>()
                .eq(Device::getPhone, phone)
                .set(Device::getStatus, 0)
                .set(Device::getOfflineTime, LocalDateTime.now()));
    }

    @Override
    public void updateLocation(String phone, LocationBody loc) {
        byte newStatus = loc.hasAlarm() ? (byte) 2 : (byte) 1;
        update(new LambdaUpdateWrapper<Device>()
                .eq(Device::getPhone, phone)
                .set(Device::getLastLat, loc.getLat())
                .set(Device::getLastLng, loc.getLng())
                .set(Device::getLastSpeed, loc.getSpeed())
                .set(Device::getLastLocationTime, loc.getGpsTime())
                .set(Device::getStatus, newStatus));
    }
}
