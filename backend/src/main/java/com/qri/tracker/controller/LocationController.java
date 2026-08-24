package com.qri.tracker.controller;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.qri.tracker.common.R;
import com.qri.tracker.entity.LocationRecord;
import com.qri.tracker.service.LocationService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

/**
 * 位置数据 API
 */
@RestController
@RequestMapping("/api/locations")
@CrossOrigin(origins = "*")
public class LocationController {

    @Autowired private LocationService locationService;

    /** 最新位置 */
    @GetMapping("/{phone}/latest")
    public R<LocationRecord> latest(@PathVariable String phone) {
        return R.ok(locationService.getLatest(phone));
    }

    /** 历史轨迹（分页） */
    @GetMapping("/{phone}/history")
    public R<?> history(
            @PathVariable String phone,
            @RequestParam(defaultValue = "1")   long page,
            @RequestParam(defaultValue = "100") long size,
            @RequestParam(required = false) String start,
            @RequestParam(required = false) String end) {

        Page<LocationRecord> pg = new Page<>(page, size);

        if (start != null && end != null) {
            locationService.lambdaQuery()
                    .eq(LocationRecord::getPhone, phone)
                    .between(LocationRecord::getGpsTime, start, end)
                    .orderByAsc(LocationRecord::getGpsTime)
                    .page(pg);
        } else {
            locationService.lambdaQuery()
                    .eq(LocationRecord::getPhone, phone)
                    .orderByDesc(LocationRecord::getGpsTime)
                    .page(pg);
        }
        return R.ok(pg);
    }
}
