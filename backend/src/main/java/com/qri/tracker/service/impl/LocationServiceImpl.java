package com.qri.tracker.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.qri.tracker.entity.LocationRecord;
import com.qri.tracker.mapper.LocationRecordMapper;
import com.qri.tracker.service.LocationService;
import org.springframework.stereotype.Service;

@Service
public class LocationServiceImpl extends ServiceImpl<LocationRecordMapper, LocationRecord>
        implements LocationService {

    @Override
    public LocationRecord getLatest(String phone) {
        return baseMapper.selectLatest(phone);
    }
}
