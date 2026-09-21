package edu.f21.auth;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.nimbusds.jose.jwk.source.ImmutableSecret;
import edu.f21.common.Api;
import java.nio.charset.StandardCharsets;
import java.util.List;
import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.core.*;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.security.web.SecurityFilterChain;

@Configuration
public class SecurityConfig {
 public static final String ISSUER="f21-local";
 public static final String AUDIENCE="f21-services";
 @Bean SecretKey signingKey(@Value("${app.jwt.secret}") String secret) {
  if(secret.getBytes(StandardCharsets.UTF_8).length<32) throw new IllegalStateException("JWT_SECRET must have at least 32 bytes");
  return new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8),"HmacSHA256");
 }
 @Bean PasswordEncoder passwordEncoder() { return new BCryptPasswordEncoder(12); }
 @Bean JwtEncoder jwtEncoder(SecretKey key) { return new NimbusJwtEncoder(new ImmutableSecret<>(key)); }
 @Bean JwtDecoder jwtDecoder(SecretKey key,JdbcTemplate jdbc) {
  var decoder=NimbusJwtDecoder.withSecretKey(key).macAlgorithm(MacAlgorithm.HS256).build();
  OAuth2TokenValidator<Jwt> local=jwt->{
   String role=jwt.getClaimAsString("role");
   if(jwt.getId()==null || jwt.getSubject()==null || jwt.getExpiresAt()==null || jwt.getIssuedAt()==null
      || !jwt.getAudience().contains(AUDIENCE) || !List.of("STUDENT","TEACHER","ADMIN").contains(role==null?"":role))
    return failure();
   Integer active=jdbc.queryForObject("SELECT COUNT(*) FROM local_user WHERE user_id=? AND role=? AND status='ACTIVE'",
      Integer.class,jwt.getSubject(),role);
   Integer revoked=jdbc.queryForObject("SELECT COUNT(*) FROM revoked_token WHERE jti=?",Integer.class,jwt.getId());
   return active!=null && active==1 && revoked!=null && revoked==0 ? OAuth2TokenValidatorResult.success():failure();
  };
  decoder.setJwtValidator(new DelegatingOAuth2TokenValidator<>(JwtValidators.createDefaultWithIssuer(ISSUER),local));
  return decoder;
 }
 private static OAuth2TokenValidatorResult failure() {
  return OAuth2TokenValidatorResult.failure(new OAuth2Error("invalid_token","Invalid local session",null));
 }
 @Bean SecurityFilterChain security(HttpSecurity http,ObjectMapper mapper) throws Exception {
  var entry=(org.springframework.security.web.AuthenticationEntryPoint)(req,res,ex)->{
   res.setStatus(401); res.setContentType("application/json;charset=UTF-8");
   mapper.writeValue(res.getOutputStream(),Api.error("UNAUTHORIZED","请登录或重新登录",String.valueOf(req.getAttribute("traceId"))));
  };
  var denied=(org.springframework.security.web.access.AccessDeniedHandler)(req,res,ex)->{
   res.setStatus(403); res.setContentType("application/json;charset=UTF-8");
   mapper.writeValue(res.getOutputStream(),Api.error("FORBIDDEN","无权访问",String.valueOf(req.getAttribute("traceId"))));
  };
  return http.csrf(c->c.disable()) // Bearer header only: no cookie-based authentication.
    .sessionManagement(c->c.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
    .authorizeHttpRequests(c->c.requestMatchers("/api/v1/health","/api/v1/auth/login","/error").permitAll().anyRequest().authenticated())
    .exceptionHandling(c->c.authenticationEntryPoint(entry).accessDeniedHandler(denied))
    .oauth2ResourceServer(c->c.jwt(j->{}).authenticationEntryPoint(entry).accessDeniedHandler(denied))
    .build();
 }
}
