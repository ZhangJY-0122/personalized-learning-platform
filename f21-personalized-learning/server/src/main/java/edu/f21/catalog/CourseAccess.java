package edu.f21.catalog;
import edu.f21.common.BusinessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;

@Service
public class CourseAccess {
 private final JdbcTemplate jdbc;
 public CourseAccess(JdbcTemplate jdbc){this.jdbc=jdbc;}
 public void require(Jwt jwt,String courseId) {
  if("ADMIN".equals(jwt.getClaimAsString("role"))) { requireActive(courseId); return; }
  String table="TEACHER".equals(jwt.getClaimAsString("role"))?"teacher_course_scope":"course_enrollment";
  Integer count=jdbc.queryForObject("SELECT COUNT(*) FROM "+table+" WHERE user_id=? AND course_id=? AND status='ACTIVE'",
      Integer.class,jwt.getSubject(),courseId);
  if(count==null || count==0)throw BusinessException.forbidden();
  requireActive(courseId);
 }
 public void requireDashboard(Jwt jwt,String courseId) {
  String role=jwt.getClaimAsString("role");
  if("ADMIN".equals(role)){requireActive(courseId);return;}
  if(!"TEACHER".equals(role))throw BusinessException.forbidden();
  Integer count=jdbc.queryForObject("SELECT COUNT(*) FROM teacher_course_scope WHERE user_id=? AND course_id=? AND status='ACTIVE'",
      Integer.class,jwt.getSubject(),courseId);
  // Check scope first so a teacher cannot enumerate missing or inactive courses.
  if(count==null||count==0)throw BusinessException.forbidden();
  requireActive(courseId);
 }
 private void requireActive(String courseId) {
  if(jdbc.queryForObject("SELECT COUNT(*) FROM course WHERE course_id=? AND status='ACTIVE'",Integer.class,courseId)==0)throw BusinessException.missing();
 }
}
