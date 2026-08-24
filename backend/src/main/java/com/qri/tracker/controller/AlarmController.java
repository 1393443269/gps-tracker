package com.qri.tracker.controller;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.qri.tracker.common.R;
import com.qri.tracker.entity.AlarmRecord;
import com.qri.tracker.service.AlarmService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 报警记录 API
 */
@RestController
@RequestMapping("/api/alarms")
@CrossOrigin(origins = "*")
public class AlarmController {

    @Autowired private AlarmService alarmService;

    /** 报警列表（分页，可按状态/设备筛选） */
    @GetMapping
    public R<?> list(
            @RequestParam(defaultValue = "1")  long   page,
            @RequestParam(defaultValue = "20") long   size,
            @RequestParam(required = false)    Integer status,
            @RequestParam(required = false)    String phone) {

        Page<AlarmRecord> pg = new Page<>(page, size);
        alarmService.lambdaQuery()
                .eq(status != null, AlarmRecord::getStatus, status)
                .eq(phone  != null, AlarmRecord::getPhone, phone)
                .orderByDesc(AlarmRecord::getAlarmTime)
                .page(pg);
        return R.ok(pg);
    }

    /** 处理报警 */
    @PutMapping("/{id}/handle")
    public R<?> handle(@PathVariable Long id, @RequestBody Map<String, String> req) {
        boolean ok = alarmService.handle(id, req.get("handler"), req.get("note"));
        return ok ? R.ok() : R.fail("报警记录不存在");
    }
}
