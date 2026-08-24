package com.qri.tracker.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.qri.tracker.entity.SysOrgModuleAuth;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.List;
import java.util.Set;

@Mapper
public interface SysOrgModuleAuthMapper extends BaseMapper<SysOrgModuleAuth> {

    /** 查询某组织已开放的模块编码集合 */
    @Select("SELECT module_code FROM sys_org_module_auth WHERE org_id = #{orgId} AND is_enabled = 1")
    Set<String> findEnabledCodes(@Param("orgId") Long orgId);

    /**
     * 批量禁用：对一批组织的指定模块一次性置为 is_enabled=0
     * 用于级联禁用场景（orgIds 为子孙组织ID列表）
     */
    @Update("<script>" +
            "UPDATE sys_org_module_auth SET is_enabled = 0 " +
            "WHERE org_id IN " +
            "<foreach collection='orgIds' item='id' open='(' separator=',' close=')'>" +
            "#{id}" +
            "</foreach>" +
            " AND module_code IN " +
            "<foreach collection='codes' item='c' open='(' separator=',' close=')'>" +
            "#{c}" +
            "</foreach>" +
            "</script>")
    int cascadeDisable(@Param("orgIds") List<Long> orgIds, @Param("codes") List<String> codes);
}
