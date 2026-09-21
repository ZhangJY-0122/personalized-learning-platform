package edu.f21.catalog;
import edu.f21.common.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1")
public class CatalogController {
 private final JdbcTemplate jdbc;private final CourseAccess access;private final ObjectMapper mapper;
 public CatalogController(JdbcTemplate jdbc,CourseAccess access,ObjectMapper mapper){this.jdbc=jdbc;this.access=access;this.mapper=mapper;}
 @GetMapping("/courses")
 public Object courses(@AuthenticationPrincipal Jwt jwt,@RequestParam(defaultValue="1") int page,@RequestParam(defaultValue="20") int pageSize){
  if(page<1||page>100000||pageSize<1||pageSize>100)throw new BusinessException(400,"INVALID_ARGUMENT","分页参数超出范围");
  String filter="c.status='ACTIVE'"; var args=new ArrayList<Object>();
  String role=jwt.getClaimAsString("role");
  if(!"ADMIN".equals(role)){
   String table="TEACHER".equals(role)?"teacher_course_scope":"course_enrollment";
   filter+=" AND EXISTS(SELECT 1 FROM "+table+" s WHERE s.course_id=c.course_id AND s.user_id=? AND s.status='ACTIVE')";
   args.add(jwt.getSubject());
  }
  var total=jdbc.queryForObject("SELECT COUNT(*) FROM course c WHERE "+filter,Integer.class,args.toArray());
  args.add(pageSize);args.add((page-1)*pageSize);
  var items=jdbc.queryForList("SELECT c.course_id AS courseId,c.title,c.description,c.catalog_version AS catalogVersion FROM course c WHERE "+filter+" ORDER BY c.course_id LIMIT ? OFFSET ?",args.toArray());
  return Api.ok(Map.of("items",items,"page",page,"pageSize",pageSize,"total",total));
 }
 @GetMapping("/courses/{courseId}/structure")
 public Object structure(@PathVariable UUID courseId,@AuthenticationPrincipal Jwt jwt){
  String id=courseId.toString();access.require(jwt,id);
  var c=jdbc.queryForList("SELECT course_id AS courseId,title,description,catalog_version AS catalogVersion FROM course WHERE course_id=? AND status='ACTIVE'",id);
  if(c.isEmpty())throw BusinessException.missing();
  return Api.ok(Map.of("course",c.get(0),"catalogVersion",c.get(0).get("catalogVersion"),
   "chapters",jdbc.queryForList("SELECT chapter_id AS chapterId,title,sort_order AS sortOrder FROM chapter WHERE course_id=? ORDER BY sort_order",id),
   "knowledgePoints",jdbc.queryForList("SELECT knowledge_id AS knowledgeId,chapter_id AS chapterId,name,description,difficulty FROM knowledge_point WHERE course_id=? ORDER BY sort_order",id),
   "prerequisites",jdbc.queryForList("SELECT p.source_knowledge_id AS sourceKnowledgeId,p.target_knowledge_id AS targetKnowledgeId FROM prerequisite_snapshot p JOIN course c ON c.course_id=p.course_id AND c.catalog_version=p.catalog_version WHERE p.course_id=? ORDER BY p.source_knowledge_id,p.target_knowledge_id",id)));
 }
 @GetMapping("/courses/{courseId}/resources")
 public Object resources(@PathVariable UUID courseId,@AuthenticationPrincipal Jwt jwt){
  String id=courseId.toString();access.require(jwt,id);
  return Api.ok(Map.of("items",jdbc.queryForList("SELECT s.resource_id AS resourceId,s.title,s.resource_type AS resourceType,s.difficulty,s.knowledge_id AS knowledgeId FROM resource r JOIN course c ON c.course_id=r.course_id JOIN resource_snapshot s ON s.resource_id=r.resource_id AND s.catalog_version=c.catalog_version WHERE r.course_id=? AND r.status='ACTIVE' ORDER BY s.sort_order",id)));
 }
 @GetMapping("/courses/{courseId}/questions")
 public Object questions(@PathVariable UUID courseId,@AuthenticationPrincipal Jwt jwt){
  String id=courseId.toString();access.require(jwt,id);
  return Api.ok(Map.of("items",jdbc.queryForList("SELECT s.question_id AS questionId,s.stem,s.question_type AS questionType,s.difficulty,s.knowledge_id AS knowledgeId FROM question q JOIN course c ON c.course_id=q.course_id JOIN question_snapshot s ON s.question_id=q.question_id AND s.catalog_version=c.catalog_version WHERE q.course_id=? AND q.status='ACTIVE' ORDER BY q.sort_order",id)));
 }
 @GetMapping("/resources/{resourceId}")
 public Object resource(@PathVariable UUID resourceId,@AuthenticationPrincipal Jwt jwt){
  var items=jdbc.queryForList("SELECT s.resource_id AS resourceId,s.course_id AS courseId,s.knowledge_id AS knowledgeId,s.title,s.content_text AS content,s.resource_type AS resourceType,s.difficulty FROM resource r JOIN course c ON c.course_id=r.course_id JOIN resource_snapshot s ON s.resource_id=r.resource_id AND s.catalog_version=c.catalog_version WHERE r.resource_id=? AND r.status='ACTIVE'",resourceId.toString());
  if(items.isEmpty())throw BusinessException.missing();
  var item=items.get(0);access.require(jwt,(String)item.get("courseId"));return Api.ok(item);
 }
 @GetMapping("/questions/{questionId}")
 public Object question(@PathVariable UUID questionId,@AuthenticationPrincipal Jwt jwt) throws Exception{
  // Explicit projection deliberately excludes answer_json, even for administrators.
  var items=jdbc.queryForList("SELECT s.question_id AS questionId,s.course_id AS courseId,s.catalog_version AS catalogVersion,s.knowledge_id AS knowledgeId,s.stem,s.question_type AS questionType,s.options_json AS optionsJson,s.difficulty FROM question q JOIN course c ON c.course_id=q.course_id JOIN question_snapshot s ON s.question_id=q.question_id AND s.catalog_version=c.catalog_version WHERE q.question_id=? AND q.status='ACTIVE'",questionId.toString());
  if(items.isEmpty())throw BusinessException.missing();
  var item=items.get(0);access.require(jwt,(String)item.get("courseId"));
  Object options=item.remove("optionsJson");
  item.put("options",mapper.readTree(options.toString()));
  return Api.ok(item);
 }
}
