package com.qri.tracker.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.qri.tracker.entity.Device;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

/**
 * 设备 Mapper
 */
@Mapper
public interface DeviceMapper extends BaseMapper<Device> {

    @Select("SELECT * FROM device WHERE phone = #{phone} LIMIT 1")
    Device selectByPhone(String phone);
}
