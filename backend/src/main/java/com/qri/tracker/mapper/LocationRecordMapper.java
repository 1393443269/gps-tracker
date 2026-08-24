package com.qri.tracker.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.qri.tracker.entity.LocationRecord;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

/**
 * 位置记录 Mapper
 */
@Mapper
public interface LocationRecordMapper extends BaseMapper<LocationRecord> {

    @Select("SELECT * FROM location_record WHERE phone = #{phone} " +
            "AND gps_time BETWEEN #{start} AND #{end} " +
            "ORDER BY gps_time ASC")
    IPage<LocationRecord> selectByPhoneAndTimeRange(
            Page<LocationRecord> page,
            @Param("phone") String phone,
            @Param("start") String start,
            @Param("end") String end);

    @Select("SELECT * FROM location_record WHERE phone = #{phone} ORDER BY gps_time DESC LIMIT 1")
    LocationRecord selectLatest(String phone);
}
