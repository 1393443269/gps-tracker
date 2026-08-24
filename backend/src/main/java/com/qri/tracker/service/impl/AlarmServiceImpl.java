package com.qri.tracker.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.qri.tracker.entity.AlarmRecord;
import com.qri.tracker.mapper.AlarmRecordMapper;
import com.qri.tracker.service.AlarmService;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

@Service
public class AlarmServiceImpl extends ServiceImpl<AlarmRecordMapper, AlarmRecord>
        implements AlarmService {

    @Override
    public boolean handle(Long id, String handler, String note) {
        AlarmRecord record = getById(id);
        if (record == null) return false;
        record.setStatus((byte) 1);
        record.setHandler(handler);
        record.setHandleNote(note);
        record.setHandleTime(LocalDateTime.now());
        return updateById(record);
    }
}
