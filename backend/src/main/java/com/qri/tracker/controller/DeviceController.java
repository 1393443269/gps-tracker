package com.qri.tracker.controller;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.qri.tracker.common.R;
import com.qri.tracker.entity.Device;
import com.qri.tracker.service.DeviceService;
import com.qri.tracker.session.SessionManager;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

/**
 * 设备管理 API
 */
@RestController
@RequestMapping("/api/devices")
@CrossOrigin(origins = "*")
public class DeviceController {

    @Autowired private DeviceService  deviceService;
    @Autowired private SessionManager sessionManager;

    /** 设备列表（分页） */
    @GetMapping
    public R<?> list(
            @RequestParam(defaultValue = "1")   long   page,
            @RequestParam(defaultValue = "20")  long   size,
            @RequestParam(required = false)     String keyword) {
        Page<Device> pg = new Page<>(page, size);
        if (keyword != null && !keyword.isBlank()) {
            deviceService.lambdaQuery()
                    .like(Device::getPhone,  keyword)
                    .or().like(Device::getName,    keyword)
                    .or().like(Device::getPlateNo, keyword)
                    .page(pg);
        } else {
            deviceService.page(pg);
        }
        return R.ok(pg);
    }

    /** 设备详情 */
    @GetMapping("/{id}")
    public R<Device> get(@PathVariable Long id) {
        Device device = deviceService.getById(id);
        if (device == null) return R.fail(404, "设备不存在");
        return R.ok(device);
    }

    /** 更新设备别名 */
    @PutMapping("/{id}")
    public R<?> update(@PathVariable Long id, @RequestBody Device req) {
        Device device = deviceService.getById(id);
        if (device == null) return R.fail(404, "设备不存在");
        device.setName(req.getName());
        device.setPlateNo(req.getPlateNo());
        deviceService.updateById(device);
        return R.ok();
    }

    /** 在线统计概览 */
    @GetMapping("/summary")
    public R<Map<String, Object>> summary() {
        Map<String, Object> map = new HashMap<>();
        map.put("total",   deviceService.count());
        map.put("online",  sessionManager.onlineCount());
        map.put("offline", deviceService.count() - sessionManager.onlineCount());
        map.put("alarm",   deviceService.lambdaQuery().eq(Device::getStatus, 2).count());
        return R.ok(map);
    }
}
