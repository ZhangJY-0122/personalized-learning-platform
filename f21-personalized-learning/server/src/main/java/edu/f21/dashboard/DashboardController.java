package edu.f21.dashboard;

import edu.f21.common.Api;
import edu.f21.common.BusinessException;
import java.util.Set;
import java.util.UUID;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
public class DashboardController {
 private final DashboardArtifactService artifacts;private final DashboardStatisticsService statistics;
 public DashboardController(DashboardArtifactService artifacts,DashboardStatisticsService statistics){this.artifacts=artifacts;this.statistics=statistics;}
 @GetMapping("/models")
 public Object models(@AuthenticationPrincipal Jwt jwt,@RequestParam(defaultValue="1") int page,@RequestParam(defaultValue="20") int pageSize){requireStaff(jwt);pagination(page,pageSize);return Api.ok(artifacts.models(page,pageSize));}
 @GetMapping("/experiments")
 public Object experiments(@AuthenticationPrincipal Jwt jwt,@RequestParam(defaultValue="1") int page,@RequestParam(defaultValue="20") int pageSize){requireStaff(jwt);pagination(page,pageSize);return Api.ok(artifacts.experiments(page,pageSize));}
 @GetMapping("/courses/{courseId}/weakness-statistics")
 public Object weakness(@AuthenticationPrincipal Jwt jwt,@PathVariable UUID courseId){return Api.ok(statistics.weakness(jwt,courseId.toString()));}
 @GetMapping("/courses/{courseId}/recommendation-metrics")
 public Object recommendations(@AuthenticationPrincipal Jwt jwt,@PathVariable UUID courseId){return Api.ok(statistics.recommendations(jwt,courseId.toString()));}
 private static void requireStaff(Jwt jwt){if(jwt==null||!Set.of("TEACHER","ADMIN").contains(jwt.getClaimAsString("role")))throw BusinessException.forbidden();}
 private static void pagination(int page,int pageSize){if(page<1||pageSize<1||pageSize>100)throw new BusinessException(400,"INVALID_ARGUMENT","分页参数超出范围");}
}
