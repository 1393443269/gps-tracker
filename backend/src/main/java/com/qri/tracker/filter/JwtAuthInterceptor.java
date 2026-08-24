package com.qri.tracker.filter;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.qri.tracker.common.R;
import com.qri.tracker.common.UserContext;
import com.qri.tracker.service.ModuleAuthService;
import com.qri.tracker.util.JwtUtil;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

import java.util.Map;

/**
 * JWT 鉴权拦截器（双职责）
 * 1. 解析 Token → 写入 UserContext → 请求结束后清理
 * 2. URL 前缀匹配 → 调用模块权限校验（C6 引擎）
 *
 * 兼容两种 Header：
 *   Authorization: Bearer <token>   (新标准)
 *   X-Admin-Token: <token>          (旧兼容)
 */
@Component
@RequiredArgsConstructor
public class JwtAuthInterceptor implements HandlerInterceptor {

    private final JwtUtil           jwtUtil;
    private final ObjectMapper      objectMapper;
    private final ModuleAuthService moduleAuthService;

    /**
     * URL 前缀 → 模块码映射表
     * 只有列出的路径才会做模块权限校验；其余已认证路径直接放行（保持原有兼容性）
     */
    private static final Map<String, String> URL_MODULE_MAP = Map.of(
        "/api/org",          "SYS_ORG",
        "/api/sys/users",    "SYS_USER",
        "/api/modules",      "SYS_MODULE_AUTH"
    );

    @Override
    public boolean preHandle(HttpServletRequest req, HttpServletResponse res, Object handler) throws Exception {
        String token = extractToken(req);
        if (token == null) {
            writeUnauthorized(res, "未登录或 Token 已过期");
            return false;
        }
        try {
            Claims claims   = jwtUtil.parse(token);
            Long   userId   = Long.parseLong(claims.getSubject());
            Long   orgId    = claims.get("orgId",    Long.class);
            Integer orgLevel = claims.get("orgLevel", Integer.class);
            Integer uType   = claims.get("userType", Integer.class);
            String  uname   = claims.get("username", String.class);
            UserContext.set(new UserContext.LoginUser(userId, orgId, orgLevel, uType, uname));
        } catch (JwtException | IllegalArgumentException e) {
            writeUnauthorized(res, "Token 无效，请重新登录");
            return false;
        }

        // 模块权限校验（超管在 checkModulePermission 内部直通，此处无需额外判断）
        String uri        = req.getRequestURI();
        Long   currentOrg = UserContext.getOrgId();
        for (Map.Entry<String, String> entry : URL_MODULE_MAP.entrySet()) {
            if (uri.startsWith(entry.getKey())) {
                if (currentOrg != null && !moduleAuthService.checkModulePermission(currentOrg, entry.getValue())) {
                    writeForbidden(res, "无访问权限：功能模块 [" + entry.getValue() + "] 未开放");
                    return false;
                }
                break;
            }
        }
        return true;
    }

    @Override
    public void afterCompletion(HttpServletRequest req, HttpServletResponse res, Object handler, Exception ex) {
        UserContext.clear();
    }

    private String extractToken(HttpServletRequest req) {
        String bearer = req.getHeader("Authorization");
        if (bearer != null && bearer.startsWith("Bearer ")) {
            return bearer.substring(7);
        }
        // 兼容旧 header
        String adminToken = req.getHeader("X-Admin-Token");
        if (adminToken != null && !adminToken.isBlank()) {
            return adminToken;
        }
        return null;
    }

    private void writeUnauthorized(HttpServletResponse res, String msg) throws Exception {
        res.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        res.setContentType("application/json;charset=UTF-8");
        res.getWriter().write(objectMapper.writeValueAsString(R.fail(401, msg)));
    }

    private void writeForbidden(HttpServletResponse res, String msg) throws Exception {
        res.setStatus(HttpServletResponse.SC_FORBIDDEN);
        res.setContentType("application/json;charset=UTF-8");
        res.getWriter().write(objectMapper.writeValueAsString(R.fail(403, msg)));
    }
}
