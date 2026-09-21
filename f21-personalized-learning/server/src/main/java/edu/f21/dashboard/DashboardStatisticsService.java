package edu.f21.dashboard;

import edu.f21.catalog.CourseAccess;
import edu.f21.practice.PracticeService;
import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;

@Service
public class DashboardStatisticsService {
 static final String POPULATION_SQL="""
  SELECT COUNT(DISTINCT e.user_id) enrolledLearners,
   COUNT(DISTINCT CASE WHEN m.knowledge_id IS NOT NULL THEN e.user_id END) learnersWithState,
   COUNT(DISTINCT CASE WHEN m.evidence_count>0 THEN e.user_id END) learnersWithEvidence,
   COUNT(DISTINCT CASE WHEN m.evidence_count>=3 AND m.evidence_weight>=2 THEN e.user_id END) eligibleLearners
  FROM course_enrollment e
  JOIN local_user u ON u.user_id=e.user_id AND u.role='STUDENT' AND u.status='ACTIVE'
  LEFT JOIN mastery_state m ON m.user_id=e.user_id AND m.course_id=e.course_id AND m.catalog_version=? AND m.model_version=?
  WHERE e.course_id=? AND e.status='ACTIVE'
  """;
 static final String WEAKNESS_SQL="""
  SELECT k.knowledge_id knowledgeId,k.name knowledgeName,COUNT(m.knowledge_id) eligibleLearnerCount,
   COALESCE(SUM(CASE WHEN m.mastery<0.6 THEN 1 ELSE 0 END),0) weakLearnerCount,
   AVG(m.mastery) meanMastery,COALESCE(SUM(m.evidence_count),0) evidenceCount,
   COALESCE(SUM(m.evidence_weight),0) evidenceWeight
  FROM knowledge_snapshot k
  LEFT JOIN (
   SELECT m.knowledge_id,m.mastery,m.evidence_count,m.evidence_weight
   FROM mastery_state m
   JOIN course_enrollment e ON e.user_id=m.user_id AND e.course_id=m.course_id AND e.status='ACTIVE'
   JOIN local_user u ON u.user_id=m.user_id AND u.role='STUDENT' AND u.status='ACTIVE'
   WHERE m.course_id=? AND m.catalog_version=? AND m.model_version=? AND m.evidence_count>=3 AND m.evidence_weight>=2
  ) m ON m.knowledge_id=k.knowledge_id
  WHERE k.course_id=? AND k.catalog_version=?
  GROUP BY k.knowledge_id,k.name,k.sort_order
  ORDER BY CASE WHEN COUNT(m.knowledge_id)=0 THEN 1 ELSE 0 END,
   (COALESCE(SUM(CASE WHEN m.mastery<0.6 THEN 1 ELSE 0 END),0)/NULLIF(COUNT(m.knowledge_id),0)) DESC,
   AVG(m.mastery),k.sort_order,k.knowledge_id
  """;
 static final String RECOMMENDATION_SQL="""
  SELECT COUNT(DISTINCT b.batch_id) generatedBatches,COUNT(DISTINCT b.user_id) distinctLearners,
   COUNT(DISTINCT i.recommendation_id) recommendedItems,
   COUNT(DISTINCT CASE WHEN
    (i.item_type='RESOURCE' AND EXISTS(SELECT 1 FROM resource r WHERE r.resource_id=i.item_id AND r.course_id=b.course_id AND r.status='ACTIVE')) OR
    (i.item_type='QUESTION' AND EXISTS(SELECT 1 FROM question q WHERE q.question_id=i.item_id AND q.course_id=b.course_id AND q.status='ACTIVE'))
    THEN i.recommendation_id END) availableRecommendedItems
  FROM recommendation_batch b LEFT JOIN recommendation_item i ON i.batch_id=b.batch_id
  WHERE b.course_id=? AND b.catalog_version=?
  """;
 static final String FEEDBACK_SQL="""
  SELECT COUNT(DISTINCT CASE WHEN rf.feedback_type='EXPOSED' THEN i.recommendation_id END) exposures,
   COUNT(DISTINCT CASE WHEN rf.feedback_type='CLICKED' AND EXISTS(
    SELECT 1 FROM recommendation_feedback x WHERE x.recommendation_id=i.recommendation_id AND x.feedback_type='EXPOSED'
   ) THEN i.recommendation_id END) clicks,
   COUNT(DISTINCT CASE WHEN rf.feedback_type='COMPLETED' AND rf.trusted=1 AND rf.source_event_id IS NOT NULL AND EXISTS(
    SELECT 1 FROM recommendation_feedback x WHERE x.recommendation_id=i.recommendation_id AND x.feedback_type='EXPOSED'
   ) THEN i.recommendation_id END) trustedCompletions
  FROM recommendation_batch b JOIN recommendation_item i ON i.batch_id=b.batch_id
  LEFT JOIN recommendation_feedback rf ON rf.recommendation_id=i.recommendation_id
  WHERE b.course_id=? AND b.catalog_version=?
  """;
 static final String OPINION_SQL="""
  SELECT COUNT(*) opinions,COALESCE(SUM(latest.feedback_type='HELPFUL'),0) helpfulOpinions
  FROM (
   SELECT rf.feedback_type,ROW_NUMBER() OVER(PARTITION BY rf.recommendation_id ORDER BY rf.feedback_seq DESC) rowNumber
   FROM recommendation_batch b JOIN recommendation_item i ON i.batch_id=b.batch_id
   JOIN recommendation_feedback rf ON rf.recommendation_id=i.recommendation_id AND rf.feedback_type IN ('HELPFUL','NOT_HELPFUL')
   WHERE b.course_id=? AND b.catalog_version=?
  ) latest WHERE latest.rowNumber=1
  """;

 private final JdbcTemplate db; private final CourseAccess access; private final Clock clock;
 @Autowired public DashboardStatisticsService(JdbcTemplate db,CourseAccess access){this(db,access,Clock.systemUTC());}
 DashboardStatisticsService(JdbcTemplate db,CourseAccess access,Clock clock){this.db=db;this.access=access;this.clock=clock;}

 public Map<String,Object> weakness(Jwt jwt,String course){
  String version=activeVersion(jwt,course),model=PracticeService.model(course,version);
  Map<String,Object> row=db.queryForMap(POPULATION_SQL,version,model,course);
  var population=new LinkedHashMap<String,Object>();
  for(String field:List.of("enrolledLearners","learnersWithState","learnersWithEvidence","eligibleLearners"))population.put(field,integer(row.get(field)));
  var items=new ArrayList<Map<String,Object>>();
  if(integer(population.get("eligibleLearners"))>0)for(Map<String,Object> value:db.queryForList(WEAKNESS_SQL,course,version,model,course,version)){
   int eligible=integer(value.get("eligibleLearnerCount")),weak=integer(value.get("weakLearnerCount"));
   var item=new LinkedHashMap<String,Object>();item.put("knowledgeId",text(value.get("knowledgeId")));item.put("knowledgeName",text(value.get("knowledgeName")));
   item.put("eligibleLearnerCount",eligible);item.put("weakLearnerCount",weak);item.put("weakLearnerRate",rate(weak,eligible));item.put("meanMastery",number(value.get("meanMastery")));
   item.put("evidenceCount",integer(value.get("evidenceCount")));item.put("evidenceWeight",number(value.get("evidenceWeight")));items.add(item);
  }
  String now=Instant.now(clock).toString();var result=base(course,version,now);result.put("population",population);
  result.put("threshold",Map.of("minimumEvidenceCount",3,"minimumEvidenceWeight",2,"weaknessMasteryBelow",.6));result.put("items",items);result.put("emptyReason",items.isEmpty()?"NO_ELIGIBLE_EVIDENCE":null);return result;
 }

 public Map<String,Object> recommendations(Jwt jwt,String course){
  String version=activeVersion(jwt,course);
  Map<String,Object> batches=db.queryForMap(RECOMMENDATION_SQL,course,version),feedback=db.queryForMap(FEEDBACK_SQL,course,version),opinions=db.queryForMap(OPINION_SQL,course,version);
  var population=new LinkedHashMap<String,Object>();
  population.put("generatedBatches",integer(batches.get("generatedBatches")));population.put("distinctLearners",integer(batches.get("distinctLearners")));
  population.put("recommendedItems",integer(batches.get("recommendedItems")));population.put("availableRecommendedItems",integer(batches.get("availableRecommendedItems")));
  population.put("exposures",integer(feedback.get("exposures")));population.put("clicks",integer(feedback.get("clicks")));
  population.put("opinions",integer(opinions.get("opinions")));population.put("helpfulOpinions",integer(opinions.get("helpfulOpinions")));population.put("trustedCompletions",integer(feedback.get("trustedCompletions")));
  int recommended=integer(population.get("recommendedItems")),exposures=integer(population.get("exposures")),opinionCount=integer(population.get("opinions"));
  var metrics=List.of(metric("availableItemRate",integer(population.get("availableRecommendedItems")),recommended),metric("clickThroughRate",integer(population.get("clicks")),exposures),metric("helpfulRate",integer(population.get("helpfulOpinions")),opinionCount),metric("trustedCompletionRate",integer(population.get("trustedCompletions")),exposures));
  String now=Instant.now(clock).toString();var result=base(course,version,now);result.put("sourceType","LOCAL_PRODUCT_EVENT_AGGREGATE");result.put("population",population);result.put("metrics",metrics);result.put("emptyReason",integer(population.get("generatedBatches"))==0?"NO_PRODUCT_EVENT_DATA":null);return result;
 }

 private String activeVersion(Jwt jwt,String course){access.requireDashboard(jwt,course);String version=db.queryForObject("SELECT catalog_version FROM course WHERE course_id=? AND status='ACTIVE'",String.class,course);if(version==null)throw edu.f21.common.BusinessException.missing();return version;}
 private static LinkedHashMap<String,Object> base(String course,String version,String now){var result=new LinkedHashMap<String,Object>();result.put("courseId",course);result.put("catalogVersion",version);result.put("dataType","SYNTHETIC_DEMO");result.put("generatedAt",now);var window=new LinkedHashMap<String,Object>();window.put("kind","ALL_TIME");window.put("start",null);window.put("end",now);window.put("timezone","UTC");result.put("window",window);return result;}
 private static Map<String,Object> metric(String name,int numerator,int denominator){var value=new LinkedHashMap<String,Object>();value.put("name",name);value.put("numerator",numerator);value.put("denominator",denominator);value.put("value",rate(numerator,denominator));value.put("unit","RATE");return value;}
 private static Double rate(int numerator,int denominator){return denominator==0?null:(double)numerator/denominator;}
 private static int integer(Object value){return value==null?0:((Number)value).intValue();}
 private static Double number(Object value){return value==null?null:((Number)value).doubleValue();}
 private static String text(Object value){return value==null?"":value.toString();}
}
