package edu.f21.dashboard;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import edu.f21.common.BusinessException;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.security.oauth2.jwt.Jwt;

class DashboardControllerTest {
 private static Jwt jwt(String role){var jwt=mock(Jwt.class);when(jwt.getClaimAsString("role")).thenReturn(role);return jwt;}
 @Test void studentCannotReadFrozenExperimentArtifacts(){var artifacts=mock(DashboardArtifactService.class);var controller=new DashboardController(artifacts,mock(DashboardStatisticsService.class));var ex=assertThrows(BusinessException.class,()->controller.models(jwt("STUDENT"),1,20));assertEquals(403,ex.status);verifyNoInteractions(artifacts);}
 @Test void invalidPaginationIsRejectedBeforeArtifactRead(){var artifacts=mock(DashboardArtifactService.class);var controller=new DashboardController(artifacts,mock(DashboardStatisticsService.class));var ex=assertThrows(BusinessException.class,()->controller.experiments(jwt("TEACHER"),1,101));assertEquals(400,ex.status);verifyNoInteractions(artifacts);}
 @Test void teacherReadsVerifiedSummaries(){var artifacts=mock(DashboardArtifactService.class);when(artifacts.models(1,20)).thenReturn(Map.of("items",java.util.List.of(),"page",1,"pageSize",20,"total",0));var result=new DashboardController(artifacts,mock(DashboardStatisticsService.class)).models(jwt("TEACHER"),1,20);assertNotNull(result);verify(artifacts).models(1,20);}
}
