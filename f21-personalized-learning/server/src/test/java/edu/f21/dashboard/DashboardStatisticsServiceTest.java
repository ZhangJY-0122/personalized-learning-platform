package edu.f21.dashboard;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import edu.f21.catalog.CourseAccess;
import java.time.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;

class DashboardStatisticsServiceTest {
 static final String COURSE="10000000-0000-4000-8000-000000000001",VERSION="java-g5-v1";
 private static DashboardStatisticsService service(JdbcTemplate db,CourseAccess access){return new DashboardStatisticsService(db,access,Clock.fixed(Instant.parse("2026-09-18T00:00:00Z"),ZoneOffset.UTC));}
 private static void version(JdbcTemplate db){when(db.queryForObject(startsWith("SELECT catalog_version"),eq(String.class),eq(COURSE))).thenReturn(VERSION);}
 @Test void weaknessEmptyDoesNotInventKnowledgeRows(){
  var db=mock(JdbcTemplate.class);var access=mock(CourseAccess.class);version(db);when(db.queryForMap(eq(DashboardStatisticsService.POPULATION_SQL),any(Object[].class))).thenReturn(Map.of("enrolledLearners",0,"learnersWithState",0,"learnersWithEvidence",0,"eligibleLearners",0));
  var value=service(db,access).weakness(mock(Jwt.class),COURSE);assertEquals("NO_ELIGIBLE_EVIDENCE",value.get("emptyReason"));assertEquals(List.of(),value.get("items"));verify(db,never()).queryForList(eq(DashboardStatisticsService.WEAKNESS_SQL),any(Object[].class));
 }
 @Test void weaknessUsesFrozenEvidenceAndRateDenominators(){
  var db=mock(JdbcTemplate.class);var access=mock(CourseAccess.class);version(db);when(db.queryForMap(eq(DashboardStatisticsService.POPULATION_SQL),any(Object[].class))).thenReturn(Map.of("enrolledLearners",4,"learnersWithState",3,"learnersWithEvidence",2,"eligibleLearners",2));
  when(db.queryForList(eq(DashboardStatisticsService.WEAKNESS_SQL),any(Object[].class))).thenReturn(List.of(new LinkedHashMap<>(Map.of("knowledgeId","k1","knowledgeName","循环","eligibleLearnerCount",2,"weakLearnerCount",1,"meanMastery",.55,"evidenceCount",7,"evidenceWeight",5d))));
  var value=service(db,access).weakness(mock(Jwt.class),COURSE);var item=(Map<?,?>)((List<?>)value.get("items")).get(0);assertEquals(.5,(Double)item.get("weakLearnerRate"));assertNull(value.get("emptyReason"));assertEquals("2026-09-18T00:00:00Z",value.get("generatedAt"));
 }
 @Test void recommendationMetricsBindExactPopulationFields(){
  var db=mock(JdbcTemplate.class);var access=mock(CourseAccess.class);version(db);
  when(db.queryForMap(eq(DashboardStatisticsService.RECOMMENDATION_SQL),any(Object[].class))).thenReturn(Map.of("generatedBatches",2,"distinctLearners",2,"recommendedItems",10,"availableRecommendedItems",9));
  when(db.queryForMap(eq(DashboardStatisticsService.FEEDBACK_SQL),any(Object[].class))).thenReturn(Map.of("exposures",8,"clicks",3,"trustedCompletions",2));
  when(db.queryForMap(eq(DashboardStatisticsService.OPINION_SQL),any(Object[].class))).thenReturn(Map.of("opinions",4,"helpfulOpinions",3));
  var value=service(db,access).recommendations(mock(Jwt.class),COURSE);var metrics=(List<?>)value.get("metrics");assertEquals(List.of(.9,.375,.75,.25),metrics.stream().map(x->((Map<?,?>)x).get("value")).toList());assertNull(value.get("emptyReason"));assertEquals("LOCAL_PRODUCT_EVENT_AGGREGATE",value.get("sourceType"));
 }
}
