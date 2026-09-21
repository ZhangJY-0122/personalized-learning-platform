package edu.f21.auth;
import edu.f21.common.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1")
public class AuthController {
 private final JdbcTemplate jdbc;
 private final PasswordEncoder passwords;
 private final JwtEncoder encoder;
 private final String dummyHash;
 public AuthController(JdbcTemplate jdbc,PasswordEncoder passwords,JwtEncoder encoder) {
  this.jdbc=jdbc;this.passwords=passwords;this.encoder=encoder;
  this.dummyHash=passwords.encode(UUID.randomUUID().toString());
 }
 public record Login(@NotBlank @Size(max=64) String username,@NotBlank @Size(max=72) String password) {}
 @PostMapping("/auth/login")
 public Object login(@Valid @RequestBody Login body) {
  if(body.password().getBytes(StandardCharsets.UTF_8).length>72)
   throw new BusinessException(400,"INVALID_ARGUMENT","密码长度超出限制");
  var users=jdbc.queryForList("SELECT user_id,username,display_name,role,password_hash,status FROM local_user WHERE username=?",body.username().trim());
  String hash=users.isEmpty()?dummyHash:(String)users.get(0).get("password_hash");
  boolean matches=passwords.matches(body.password(),hash);
  if(users.isEmpty() || !matches || !"ACTIVE".equals(users.get(0).get("status")))
   throw new BusinessException(401,"UNAUTHORIZED","用户名或密码错误");
  var user=users.get(0); Instant now=Instant.now();
  var claims=JwtClaimsSet.builder().issuer(SecurityConfig.ISSUER).subject((String)user.get("user_id"))
    .audience(List.of(SecurityConfig.AUDIENCE)).issuedAt(now).expiresAt(now.plusSeconds(3600))
    .id(UUID.randomUUID().toString()).claim("role",user.get("role")).build();
  var token=encoder.encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(),claims));
  return Api.ok(Map.of("accessToken",token.getTokenValue(),"tokenType","Bearer","expiresIn",3600,
     "userId",user.get("user_id"),"role",user.get("role")));
 }
 @GetMapping("/users/me")
 public Object me(@AuthenticationPrincipal Jwt jwt) { return Api.ok(user(jwt.getSubject())); }
 @GetMapping("/students/{userId}/enrollments")
 public Object enrollments(@PathVariable UUID userId,@AuthenticationPrincipal Jwt jwt) {
  if(!jwt.getSubject().equals(userId.toString()) && !"ADMIN".equals(jwt.getClaimAsString("role"))) throw BusinessException.forbidden();
  return Api.ok(Map.of("items",jdbc.queryForList(
    "SELECT c.course_id AS courseId,c.title,c.catalog_version AS catalogVersion FROM course_enrollment e JOIN course c ON c.course_id=e.course_id WHERE e.user_id=? AND e.status='ACTIVE' AND c.status='ACTIVE'",userId.toString())));
 }
 @PostMapping("/auth/logout")
 public Object logout(@AuthenticationPrincipal Jwt jwt) {
  jdbc.update("INSERT IGNORE INTO revoked_token(jti,expires_at) VALUES (?,?)",jwt.getId(),Timestamp.from(jwt.getExpiresAt()));
  return Api.ok(Map.of("loggedOut",true));
 }
 public Map<String,Object> user(String id) {
  var users=jdbc.queryForList("SELECT user_id AS userId,username,display_name AS displayName,role FROM local_user WHERE user_id=? AND status='ACTIVE'",id);
  if(users.isEmpty()) throw new BusinessException(401,"UNAUTHORIZED","账号不可用");
  return users.get(0);
 }
}
