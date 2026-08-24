package com.qri.tracker.util;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;

/**
 * JWT 工具类（JJWT 0.12.x API）
 */
@Component
public class JwtUtil {

    @Value("${jwt.secret}")
    private String secret;

    @Value("${jwt.expire:86400000}")
    private long expire;

    /** 生成 HMAC-SHA256 密钥（密钥长度需 ≥ 256 bit） */
    private SecretKey getKey() {
        return Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * 签发 JWT Token
     *
     * @param userId   用户ID
     * @param orgId    所属组织ID
     * @param orgLevel 组织层级
     * @param userType 用户类型（1=普通 9=超管）
     * @param username 用户名
     */
    public String generate(Long userId, Long orgId, Integer orgLevel, Integer userType, String username) {
        return Jwts.builder()
                .subject(userId.toString())
                .claim("orgId",    orgId)
                .claim("orgLevel", orgLevel)
                .claim("userType", userType)
                .claim("username", username)
                .issuedAt(new Date())
                .expiration(new Date(System.currentTimeMillis() + expire))
                .signWith(getKey())
                .compact();
    }

    /**
     * 解析 Token，返回 Claims；失败抛 JwtException
     */
    public Claims parse(String token) {
        return Jwts.parser()
                .verifyWith(getKey())
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }

    /** 快速验证 Token 是否合法（不抛异常） */
    public boolean isValid(String token) {
        try {
            parse(token);
            return true;
        } catch (JwtException | IllegalArgumentException e) {
            return false;
        }
    }
}
