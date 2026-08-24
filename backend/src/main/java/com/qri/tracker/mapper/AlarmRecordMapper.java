package com.qri.tracker.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.qri.tracker.entity.AlarmRecord;
import org.apache.ibatis.annotations.Mapper;

/**
 * 报警记录 Mapper
 */
@Mapper
public interface AlarmRecordMapper extends BaseMapper<AlarmRecord> {
}
