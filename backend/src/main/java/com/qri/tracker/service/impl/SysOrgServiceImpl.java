package com.qri.tracker.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.qri.tracker.entity.SysOrg;
import com.qri.tracker.mapper.SysOrgMapper;
import com.qri.tracker.service.SysOrgService;
import org.springframework.stereotype.Service;

import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
public class SysOrgServiceImpl extends ServiceImpl<SysOrgMapper, SysOrg> implements SysOrgService {

    @Override
    @Transactional(rollbackFor = Exception.class)
    public SysOrg createOrg(SysOrg org) {
        if (org.getParentId() == null) {
            // 根节点（L1总部）
            org.setOrgLevel(1);
            save(org);
            // 物化路径写入自身 /id/
            org.setOrgPath("/" + org.getId() + "/");
        } else {
            SysOrg parent = getById(org.getParentId());
            if (parent == null) throw new IllegalArgumentException("父组织不存在");
            org.setOrgLevel(parent.getOrgLevel() + 1);
            if (org.getOrgLevel() > 5) throw new IllegalArgumentException("组织层级不能超过5级");
            save(org);
            org.setOrgPath(parent.getOrgPath() + org.getId() + "/");
        }
        updateById(org);
        return org;
    }

    @Override
    public List<SysOrg> listDirectChildren(Long parentId) {
        return baseMapper.findDirectChildren(parentId);
    }

    @Override
    public List<Long> findSubtreeIds(Long orgId) {
        SysOrg org = getById(orgId);
        if (org == null || org.getOrgPath() == null) return List.of(orgId);
        List<SysOrg> subtree = baseMapper.findSubtreeByPath(org.getOrgPath());
        List<Long> ids = new ArrayList<>();
        for (SysOrg o : subtree) ids.add(o.getId());
        return ids;
    }

    @Override
    public List<Long> findDescendantIds(Long orgId) {
        SysOrg org = getById(orgId);
        if (org == null || org.getOrgPath() == null) return List.of();
        return baseMapper.findDescendantIds(org.getOrgPath(), orgId);
    }

    @Override
    public List<SysOrg> buildTree(Long rootOrgId) {
        SysOrg root = getById(rootOrgId);
        if (root == null || root.getOrgPath() == null) return List.of();

        // 查出以 root 为根的全部子孙节点（含 root 本身）
        List<SysOrg> all = baseMapper.findSubtreeByPath(root.getOrgPath());

        // 按 parentId 分组，然后递归挂载 children
        Map<Long, List<SysOrg>> byParent = all.stream()
                .filter(o -> o.getParentId() != null)
                .collect(Collectors.groupingBy(SysOrg::getParentId));

        // 将 children 挂到每个节点上
        for (SysOrg org : all) {
            List<SysOrg> children = byParent.getOrDefault(org.getId(), List.of());
            org.setChildren(children);
        }

        // 只返回第一层（root 本身），children 已递归挂好
        return all.stream()
                .filter(o -> o.getId().equals(rootOrgId))
                .collect(Collectors.toList());
    }
}
