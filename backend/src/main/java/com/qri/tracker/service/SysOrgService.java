package com.qri.tracker.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.qri.tracker.entity.SysOrg;

import java.util.List;

public interface SysOrgService extends IService<SysOrg> {

    /** 新建组织（自动计算 org_level 和 org_path） */
    SysOrg createOrg(SysOrg org);

    /** 查询直属下级列表 */
    List<SysOrg> listDirectChildren(Long parentId);

    /** 查询某组织及其所有子孙的 ID 集合（数据权限用） */
    List<Long> findSubtreeIds(Long orgId);

    /** 查询某组织的所有子孙 ID（不含自身，级联禁用用） */
    List<Long> findDescendantIds(Long orgId);

    /** 以 rootOrgId 为根构建完整嵌套树（前端组织管理页用） */
    List<SysOrg> buildTree(Long rootOrgId);
}
