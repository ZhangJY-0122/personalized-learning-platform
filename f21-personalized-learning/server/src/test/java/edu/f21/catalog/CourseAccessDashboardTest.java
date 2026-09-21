package edu.f21.catalog;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import edu.f21.common.BusinessException;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;

class CourseAccessDashboardTest {
 private static Jwt jwt(String role){var jwt=mock(Jwt.class);when(jwt.getClaimAsString("role")).thenReturn(role);when(jwt.getSubject()).thenReturn("user");return jwt;}
 @Test void studentIsAlwaysForbidden(){var db=mock(JdbcTemplate.class);var ex=assertThrows(BusinessException.class,()->new CourseAccess(db).requireDashboard(jwt("STUDENT"),"course"));assertEquals(403,ex.status);verifyNoInteractions(db);}
 @Test void unscopedTeacherGetsForbiddenBeforeCourseLookup(){var db=mock(JdbcTemplate.class);when(db.queryForObject(startsWith("SELECT COUNT(*) FROM teacher_course_scope"),eq(Integer.class),any(),any())).thenReturn(0);var ex=assertThrows(BusinessException.class,()->new CourseAccess(db).requireDashboard(jwt("TEACHER"),"missing"));assertEquals(403,ex.status);verify(db,never()).queryForObject(startsWith("SELECT COUNT(*) FROM course"),eq(Integer.class),any());}
 @Test void adminMissingCourseGetsNotFound(){var db=mock(JdbcTemplate.class);when(db.queryForObject(startsWith("SELECT COUNT(*) FROM course"),eq(Integer.class),any())).thenReturn(0);var ex=assertThrows(BusinessException.class,()->new CourseAccess(db).requireDashboard(jwt("ADMIN"),"missing"));assertEquals(404,ex.status);}
 @Test void scopedTeacherCanReadActiveCourse(){var db=mock(JdbcTemplate.class);when(db.queryForObject(startsWith("SELECT COUNT(*) FROM teacher_course_scope"),eq(Integer.class),any(),any())).thenReturn(1);when(db.queryForObject(startsWith("SELECT COUNT(*) FROM course"),eq(Integer.class),any())).thenReturn(1);assertDoesNotThrow(()->new CourseAccess(db).requireDashboard(jwt("TEACHER"),"course"));}
}
