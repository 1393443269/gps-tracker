package com.qri.tracker.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.qri.tracker.entity.SysOrg;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

@Mapper
public interface SysOrgMapper extends BaseMapper<SysOrg> {

    /**
     * 利用物化路径查询某组织的所有子孙（含自身）
     * 例：orgPath=/1/3/，则查 LIKE '/1/3/%'
     */
    @Select("SELECT * FROM sys_org WHERE org_path LIKE CONCAT(#{orgPath}, '%') AND deleted = 0 AND is_active = 1")
    List<SysOrg> findSubtreeByPath(@Param("orgPath") String orgPath);

    /**
     * 利用物化路径查询子孙 ID（不含自身）
     * 用于级联禁用、数据权限范围
     */
    @Select("SELECT id FROM sys_org WHERE org_path LIKE CONCAT(#{orgPath}, '%') AND id != #{selfId} AND deleted = 0")
    List<Long> findDescendantIds(@Param("orgPath") String orgPath, @Param("selfId") Long selfId);

    /**
     * 查询直属下级列表
     */
    @Select("SELECT * FROM sys_org WHERE parent_id = #{parentId} AND deleted = 0 AND is_active = 1 ORDER BY sort_order")
    List<SysOrg> findDirectChildren(@Param("parentId") Long parentId);
}
