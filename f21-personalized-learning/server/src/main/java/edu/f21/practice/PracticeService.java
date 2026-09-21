package edu.f21.practice;

import com.fasterxml.jackson.databind.*;
import edu.f21.Bkt;
import edu.f21.RuleBaseline;
import edu.f21.recommendation.RecommendationService;
import edu.f21.learningpath.LearningPathService;
import edu.f21.catalog.CourseAccess;
import edu.f21.common.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Timestamp;
import java.time.*;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.transaction.PlatformTransactionManager;

@Service
public class PracticeService {
 private final JdbcTemplate db;
 private final TransactionTemplate tx;
 private final CourseAccess access;
 private final ObjectMapper json;
 private final RecommendationService recommendations;
 private final LearningPathService paths;
 public PracticeService(JdbcTemplate db,PlatformTransactionManager tm,CourseAccess access,ObjectMapper json,RecommendationService recommendations,LearningPathService paths) {
  this.db=db;this.tx=new TransactionTemplate(tm);this.access=access;this.json=json;this.recommendations=recommendations;this.paths=paths;
 }
 static BusinessException invalid(String message){return new BusinessException(400,"INVALID_ARGUMENT",message);}
 static BusinessException conflict(String message){return new BusinessException(409,"CONFLICT",message);}
 static class MissingSnapshot extends RuntimeException {}
 static String uuid(){return UUID.randomUUID().toString();}
 public static String model(String course,String catalog){return "bkt-"+course+"-"+catalog+"-param1";}
 JsonNode parse(Object value){try{return json.readTree(value.toString());}catch(Exception ex){throw new IllegalStateException("Invalid persisted JSON",ex);}}
 Object sorted(JsonNode node){
  if(node.isObject()){var map=new TreeMap<String,Object>();node.fields().forEachRemaining(e->map.put(e.getKey(),sorted(e.getValue())));return map;}
  if(node.isArray()){var list=new ArrayList<Object>();node.forEach(n->list.add(sorted(n)));return list;}
  return json.convertValue(node,Object.class);
 }
 String canonical(Object value){try{return json.writeValueAsString(sorted(json.valueToTree(value)));}catch(Exception ex){throw new IllegalStateException(ex);}}
 String hash(String value){try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}catch(Exception ex){throw new IllegalStateException(ex);}}
 List<String> answers(JsonNode node) {
  var values=new TreeSet<String>();
  if(node==null || (!node.isTextual()&&!node.isArray()))throw invalid("答案必须是选项键或选项键数组");
  var items=new ArrayList<JsonNode>();if(node.isTextual())items.add(node);else node.forEach(items::add);
  if(items.isEmpty()||items.size()>20)throw invalid("请选择有效答案");
  for(var item:items)if(!item.isTextual()||!item.asText().matches("[A-Za-z0-9_-]{1,16}")||!values.add(item.asText()))throw invalid("选项格式错误或重复");
  return new ArrayList<>(values);
 }
 static boolean truth(Object v){return v instanceof Boolean b?b:((Number)v).intValue()!=0;}
 static Instant instant(Object v){
  if(v instanceof Timestamp t)return t.toInstant();
  if(v instanceof LocalDateTime t)return t.toInstant(ZoneOffset.UTC);
  if(v instanceof Instant t)return t;
  throw new IllegalStateException("Unsupported database time value");
 }
 static String time(Object v){return v==null?Instant.EPOCH.toString():instant(v).toString();}
 Map<String,Object> result(Map<String,Object> row,String status){
  return Map.of("submissionId",row.get("submission_id"),"eventId",row.get("event_id"),"correct",truth(row.get("correct")),"processingStatus",status);
 }
 public Object submit(Jwt jwt,UUID key,UUID questionId,JsonNode answer,String catalog,UUID recommendationId,UUID pathNodeId) {
  if(!"STUDENT".equals(jwt.getClaimAsString("role")))throw BusinessException.forbidden();
  if(recommendationId!=null&&pathNodeId!=null)throw invalid("推荐归因与路径归因不能同时提供");
  List<String> choices=answers(answer);
  var request=new TreeMap<String,Object>();request.put("questionId",questionId.toString());request.put("answer",choices);request.put("catalogVersion",catalog);
  if(recommendationId!=null)request.put("recommendationId",recommendationId.toString());
  if(pathNodeId!=null)request.put("pathNodeId",pathNodeId.toString());
  String bodyHash=hash(canonical(request));
  return tx.execute(status->{
   // Serialize identical idempotency keys, including concurrent first requests.
   db.queryForObject("SELECT user_id FROM local_user WHERE user_id=? FOR UPDATE",String.class,jwt.getSubject());
   var existing=db.queryForList("SELECT s.*,e.event_id FROM practice_submission s JOIN event_consume_log e ON e.submission_id=s.submission_id WHERE s.user_id=? AND s.idempotency_key=?",jwt.getSubject(),key.toString());
   if(!existing.isEmpty()){
    var row=existing.get(0);access.require(jwt,row.get("course_id").toString());
    if(!bodyHash.equals(row.get("payload_hash")))throw conflict("同一幂等键不能用于不同答案或题目版本");
    return result(row,"PENDING");
   }
   var rows=db.queryForList("SELECT q.course_id,c.catalog_version FROM question q JOIN course c ON c.course_id=q.course_id WHERE q.question_id=? AND q.status='ACTIVE'",questionId.toString());
   if(rows.isEmpty())throw BusinessException.missing();
   String course=rows.get(0).get("course_id").toString();access.require(jwt,course);
   if(!catalog.equals(rows.get(0).get("catalog_version")))throw conflict("题目版本已变化，请重新打开题目");
   var snapshots=db.queryForList("SELECT * FROM question_snapshot WHERE question_id=? AND course_id=? AND catalog_version=?",questionId.toString(),course,catalog);
   if(snapshots.isEmpty())throw conflict("题目快照尚未发布，不能作答");
   var q=snapshots.get(0);var valid=new HashSet<String>();parse(q.get("options_json")).forEach(n->valid.add(n.get("key").asText()));
   if(!valid.containsAll(choices)||(!"MULTIPLE".equals(q.get("question_type"))&&choices.size()!=1))throw invalid("答案不符合题目选项或题型");
   boolean correct=choices.equals(answers(parse(q.get("answer_json"))));
   db.update("INSERT IGNORE INTO learner_course_state(user_id,course_id,catalog_version) VALUES (?,?,?)",jwt.getSubject(),course,catalog);
   var state=lockState(jwt.getSubject(),course);
   if(!catalog.equals(state.get("catalog_version")))throw conflict("课程学习版本尚未完成迁移");
   recommendations.validateAttribution(jwt.getSubject(),course,questionId.toString(),catalog,"QUESTION",recommendationId==null?null:recommendationId.toString());
   paths.validatePathAttribution(jwt.getSubject(),course,questionId.toString(),catalog,"QUESTION",pathNodeId==null?null:pathNodeId.toString());
   paths.markNodeStarted(pathNodeId==null?null:pathNodeId.toString());
   Instant now=Instant.now().truncatedTo(ChronoUnit.MICROS);
   Timestamp last=db.queryForObject("SELECT MAX(occurred_at) FROM event_consume_log WHERE user_id=? AND course_id=?",Timestamp.class,jwt.getSubject(),course);
   if(last!=null&&!now.isAfter(last.toInstant()))now=last.toInstant().plusNanos(1000);
   String submission=uuid(),event=uuid();
   db.update("INSERT INTO practice_submission(submission_id,user_id,course_id,question_id,catalog_version,answer_json,correct,idempotency_key,payload_hash,path_node_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
     submission,jwt.getSubject(),course,questionId.toString(),catalog,canonical(choices),correct,key.toString(),bodyHash,pathNodeId==null?null:pathNodeId.toString(),Timestamp.from(now));
   if(recommendationId!=null)db.update("UPDATE practice_submission SET recommendation_id=? WHERE submission_id=?",recommendationId.toString(),submission);
   var payload=new TreeMap<String,Object>();
   payload.put("schemaVersion",1);payload.put("eventId",event);payload.put("eventType","QUESTION_ANSWERED");
   payload.put("userId",jwt.getSubject());payload.put("courseId",course);payload.put("objectType","QUESTION");payload.put("objectId",questionId.toString());
   payload.put("catalogVersion",catalog);payload.put("correct",correct);payload.put("occurredAt",now.toString());payload.put("sourceService","local-practice");payload.put("traceId",Api.trace());
   payload.put("knowledgeItems",List.of(Map.of("knowledgeId",q.get("knowledge_id"),"weight",1)));
   String encoded=canonical(payload);
   db.update("INSERT INTO event_consume_log(event_id,submission_id,user_id,course_id,catalog_version,event_type,source_service,payload_json,payload_hash,occurred_at) VALUES (?,?,?,?,?,'QUESTION_ANSWERED','local-practice',?,?,?)",
     event,submission,jwt.getSubject(),course,catalog,encoded,hash(encoded),Timestamp.from(now));
   payload.put("eventSeq",db.queryForObject("SELECT event_seq FROM event_consume_log WHERE event_id=?",Long.class,event));
   encoded=canonical(payload);
   db.update("UPDATE event_consume_log SET payload_json=?,payload_hash=? WHERE event_id=?",encoded,hash(encoded),event);
   return Map.of("submissionId",submission,"eventId",event,"correct",correct,"processingStatus","PENDING");
  });
 }
 Map<String,Object> lockState(String user,String course){
  // Feedback's user FK participates in consumption/replay: always lock user before course state.
  db.queryForObject("SELECT user_id FROM local_user WHERE user_id=? FOR UPDATE",String.class,user);
  return db.queryForMap("SELECT * FROM learner_course_state WHERE user_id=? AND course_id=? FOR UPDATE",user,course);
 }
 void readAccess(Jwt jwt,String user,String course){
  if(!user.equals(jwt.getSubject())&&!"ADMIN".equals(jwt.getClaimAsString("role")))throw BusinessException.forbidden();
  access.require(jwt,course);
 }
 public Object submission(Jwt jwt,UUID id){
  var rows=db.queryForList("SELECT s.*,e.event_id,e.status FROM practice_submission s JOIN event_consume_log e ON e.submission_id=s.submission_id WHERE s.submission_id=?",id.toString());
  if(rows.isEmpty())throw BusinessException.missing();var row=rows.get(0);
  readAccess(jwt,row.get("user_id").toString(),row.get("course_id").toString());
  return result(row,row.get("status").toString());
 }
 public Object history(Jwt jwt,String user,String course,int page,int size){
  readAccess(jwt,user,course);if(page<1||page>100000||size<1||size>100)throw invalid("分页参数超出范围");
  return tx.execute(s->{
   int total=db.queryForObject("SELECT COUNT(*) FROM practice_submission WHERE user_id=? AND course_id=?",Integer.class,user,course);
   var rows=db.queryForList("SELECT s.*,e.event_id,e.status,q.stem FROM practice_submission s JOIN event_consume_log e ON e.submission_id=s.submission_id JOIN question_snapshot q ON q.question_id=s.question_id AND q.catalog_version=s.catalog_version WHERE s.user_id=? AND s.course_id=? ORDER BY s.created_at DESC,s.submission_id DESC LIMIT ? OFFSET ?",user,course,size,(page-1)*size);
   var items=new ArrayList<Object>();
   for(var row:rows){var item=new LinkedHashMap<String,Object>(result(row,row.get("status").toString()));item.put("questionId",row.get("question_id"));item.put("catalogVersion",row.get("catalog_version"));item.put("stem",row.get("stem"));item.put("answer",parse(row.get("answer_json")));item.put("createdAt",time(row.get("created_at")));items.add(item);}
   return Map.of("items",items,"page",page,"pageSize",size,"total",total);
  });
 }
 Map<String,Object> meta(String user,String course){
  String catalog=db.queryForObject("SELECT catalog_version FROM course WHERE course_id=?",String.class,course);
  var states=db.queryForList("SELECT * FROM learner_course_state WHERE user_id=? AND course_id=?",user,course);
  long revision=states.isEmpty()?0:((Number)states.get(0).get("state_revision")).longValue();
  String state=states.isEmpty()?"READY":states.get(0).get("status").toString();
  var pending=db.queryForList("SELECT status FROM event_consume_log WHERE user_id=? AND course_id=? AND status<>'SUCCEEDED' ORDER BY occurred_at,event_seq,event_id LIMIT 1",user,course);
  String status=revision==0?"INSUFFICIENT":"READY";boolean stale=false;
  if(!states.isEmpty()&&!catalog.equals(states.get(0).get("catalog_version"))){status="FAILED";stale=true;}
  else if(!"READY".equals(state)){status=state.equals("REBUILDING")?"REBUILDING":"FAILED";stale=true;}
  else if(!pending.isEmpty()){status="PENDING".equals(pending.get(0).get("status"))?"PROCESSING":"FAILED";stale=true;}
  return Map.of("catalogVersion",catalog,"stateRevision",revision,"asOf",states.isEmpty()?Instant.now().toString():time(states.get(0).get("updated_at")),"stale",stale,"status",status);
 }
 public Object mastery(Jwt jwt,String user,String course){
  readAccess(jwt,user,course);
  return tx.execute(s->{
   var meta=meta(user,course);String catalog=meta.get("catalogVersion").toString(),model=model(course,catalog);
   var rows=db.queryForList("SELECT k.knowledge_id,COALESCE(m.mastery,0.2) mastery,COALESCE(m.evidence_count,0) evidence_count,COALESCE(m.evidence_weight,0) evidence_weight FROM knowledge_snapshot k LEFT JOIN mastery_state m ON m.user_id=? AND m.course_id=k.course_id AND m.knowledge_id=k.knowledge_id AND m.catalog_version=k.catalog_version AND m.model_version=? WHERE k.course_id=? AND k.catalog_version=? ORDER BY k.sort_order",user,model,course,catalog);
   var items=new ArrayList<Object>();
   for(var row:rows){
    var item=new LinkedHashMap<String,Object>(Map.of("knowledgeId",row.get("knowledge_id"),"mastery",row.get("mastery"),"evidenceCount",row.get("evidence_count"),"evidenceWeight",row.get("evidence_weight"),"evidenceStatus",((Number)row.get("evidence_count")).intValue()>=3&&((Number)row.get("evidence_weight")).doubleValue()>=2?"SUFFICIENT":"INSUFFICIENT","modelName","BKT","modelVersion",model));
    var recent=db.queryForList("SELECT i.correct,k.normalized_weight FROM learning_interaction i JOIN interaction_knowledge k ON k.interaction_id=i.interaction_id WHERE i.user_id=? AND i.course_id=? AND i.catalog_version=? AND k.knowledge_id=? ORDER BY i.occurred_at DESC,i.event_seq DESC,i.source_event_id DESC LIMIT 20",user,course,catalog,row.get("knowledge_id"));
    var observations=recent.stream().map(r->new RuleBaseline.Observation(truth(r.get("correct")),((Number)r.get("normalized_weight")).doubleValue())).toList();
    item.put("ruleScore",RuleBaseline.estimate(observations));item.put("ruleEvidenceCount",recent.size());items.add(item);
   }
   return Map.of("meta",meta,"items",items);
  });
 }
 public Object profile(Jwt jwt,String user,String course){
  readAccess(jwt,user,course);
  return tx.execute(s->{
   var m=meta(user,course);var rows=db.queryForList("SELECT * FROM student_profile WHERE user_id=? AND course_id=?",user,course);
   int total=rows.isEmpty()?0:((Number)rows.get(0).get("total_answers")).intValue(),correct=rows.isEmpty()?0:((Number)rows.get(0).get("correct_answers")).intValue();
   var result=new LinkedHashMap<String,Object>();result.put("meta",m);result.put("totalAnswers",total);result.put("correctAnswers",correct);result.put("accuracy",total==0?null:(double)correct/total);result.put("activityDays",db.queryForObject("SELECT COUNT(DISTINCT DATE(occurred_at)) FROM learning_interaction WHERE user_id=? AND course_id=? AND occurred_at>=UTC_DATE()-INTERVAL 6 DAY AND occurred_at<UTC_DATE()+INTERVAL 1 DAY",Integer.class,user,course));result.put("medianDurationMs",null);var preferences=recommendations.preferences(user,course,Instant.now());result.put("resourcePreference",preferences.isEmpty()?null:preferences);return result;
  });
 }
 // Each course is serialized. A failed head event cannot be overtaken.
 public void drain(){
  var courses=db.queryForList("SELECT DISTINCT user_id,course_id FROM event_consume_log WHERE status IN ('PENDING','FAILED') LIMIT 100");
  for(var row:courses){
   String user=row.get("user_id").toString(),course=row.get("course_id").toString();
   try{tx.executeWithoutResult(s->consumeHead(user,course));}
   catch(Exception ex){recordFailure(user,course,ex);}
  }
 }
 void consumeHead(String user,String course){
  var state=lockState(user,course);if(!"READY".equals(state.get("status")))return;
  if(!state.get("catalog_version").equals(db.queryForObject("SELECT catalog_version FROM course WHERE course_id=?",String.class,course))){
   db.update("UPDATE learner_course_state SET status='REBUILD_REQUIRED' WHERE user_id=? AND course_id=?",user,course);return;
  }
  var heads=db.queryForList("SELECT * FROM event_consume_log WHERE user_id=? AND course_id=? AND status<>'SUCCEEDED' ORDER BY occurred_at,COALESCE(source_event_seq,event_seq),event_seq,event_id LIMIT 1 FOR UPDATE",user,course);
  if(heads.isEmpty())return;var e=heads.get(0);
  if(!state.get("catalog_version").equals(e.get("catalog_version"))) {
   // Imported historical events may legitimately target an ARCHIVE_ONLY
   // snapshot.  Local practice must still be pinned to the learner's active
   // catalog, while an imported event is allowed through once its snapshot
   // exists; applyImported keeps its mastery in that historical generation.
   if (e.get("import_job_id") == null) throw new MissingSnapshot();
   String eventCatalog = e.get("catalog_version").toString();
   if (db.queryForList("SELECT 1 FROM catalog_snapshot WHERE course_id=? AND catalog_version=?",course,eventCatalog).isEmpty())
    throw new MissingSnapshot();
  }
  if(!List.of("PENDING","FAILED").contains(e.get("status")))return;
  if(e.get("next_retry_at")!=null&&instant(e.get("next_retry_at")).isAfter(Instant.now()))return;
  if(state.get("last_occurred_at")!=null && compare(e,state)<0){
   db.update("UPDATE learner_course_state SET status='REBUILD_REQUIRED' WHERE user_id=? AND course_id=?",user,course);return;
  }
  apply(e,((Number)state.get("replay_generation")).intValue(),true,((Number)state.get("state_revision")).longValue()+1);
  updateState(user,course,e,true);
 }
 int compare(Map<String,Object> event,Map<String,Object> state){
  int c=instant(event.get("occurred_at")).compareTo(instant(state.get("last_occurred_at")));if(c!=0)return c;
  c=Long.compare(((Number)event.get("event_seq")).longValue(),((Number)state.get("last_event_seq")).longValue());return c!=0?c:event.get("event_id").toString().compareTo(state.get("last_event_id").toString());
 }
 void apply(Map<String,Object> e,int generation){ apply(e,generation,true,0); }
 void apply(Map<String,Object> e,int generation,boolean live,long newRevision){
  String user=e.get("user_id").toString(),course=e.get("course_id").toString(),catalog=e.get("catalog_version").toString(),event=e.get("event_id").toString();
  if(!hash(canonical(parse(e.get("payload_json")))).equals(e.get("payload_hash")))throw new IllegalStateException("EVENT_HASH_MISMATCH");
  boolean local="local-practice".equals(e.get("source_service"));
  boolean imported=e.get("import_job_id")!=null&&!local;
  if(!local&&!imported)throw new IllegalStateException("UNTRUSTED_EVENT");
  if(imported){ applyImported(e,generation,live,newRevision); return; }
  var rows=db.queryForList("SELECT s.*,q.knowledge_id FROM practice_submission s JOIN question_snapshot q ON q.question_id=s.question_id AND q.catalog_version=s.catalog_version WHERE s.submission_id=?",e.get("submission_id"));
  if(rows.isEmpty())throw new MissingSnapshot();var row=rows.get(0);
  if(!user.equals(row.get("user_id"))||!course.equals(row.get("course_id"))||!catalog.equals(row.get("catalog_version")))throw new IllegalStateException("EVENT_IDENTITY_MISMATCH");
  JsonNode payload=parse(e.get("payload_json"));
  if(!payload.path("correct").isBoolean()||!"QUESTION_ANSWERED".equals(payload.path("eventType").asText())||!"local-practice".equals(payload.path("sourceService").asText())||payload.path("eventSeq").asLong()!=((Number)e.get("event_seq")).longValue()||!Instant.parse(payload.path("occurredAt").asText()).equals(instant(e.get("occurred_at"))))throw new IllegalStateException("EVENT_ORDER_MISMATCH");
  var skills=payload.path("knowledgeItems");
  if(!skills.isArray()||skills.size()!=1||!row.get("knowledge_id").equals(skills.get(0).path("knowledgeId").asText())||skills.get(0).path("weight").asDouble()!=1)throw new IllegalStateException("EVENT_KNOWLEDGE_MISMATCH");
  if(!event.equals(payload.path("eventId").asText())||!user.equals(payload.path("userId").asText())||!course.equals(payload.path("courseId").asText())||!catalog.equals(payload.path("catalogVersion").asText())||!row.get("question_id").equals(payload.path("objectId").asText())||truth(row.get("correct"))!=payload.path("correct").asBoolean())throw new IllegalStateException("EVENT_CONTENT_MISMATCH");
  String knowledge=row.get("knowledge_id").toString(),model=model(course,catalog);boolean correct=truth(row.get("correct"));
  var previous=db.queryForList("SELECT * FROM mastery_state WHERE user_id=? AND course_id=? AND knowledge_id=? AND model_version=?",user,course,knowledge,model);
  double before=previous.isEmpty()?.2:((Number)previous.get(0).get("mastery")).doubleValue(),after=Bkt.update(before,correct,1);
  int count=previous.isEmpty()?0:((Number)previous.get(0).get("evidence_count")).intValue();
  db.update("INSERT IGNORE INTO learning_interaction(interaction_id,source_event_id,user_id,course_id,question_id,catalog_version,correct,occurred_at,event_seq) VALUES (?,?,?,?,?,?,?,?,?)",event,event,user,course,row.get("question_id"),catalog,correct,e.get("occurred_at"),e.get("event_seq"));
  db.update("INSERT IGNORE INTO interaction_knowledge(interaction_id,knowledge_id,normalized_weight) VALUES (?,?,1)",event,knowledge);
  db.update("INSERT INTO mastery_state(user_id,course_id,catalog_version,knowledge_id,model_version,mastery,evidence_count,evidence_weight) VALUES (?,?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE mastery=VALUES(mastery),evidence_count=VALUES(evidence_count),evidence_weight=VALUES(evidence_weight)",user,course,catalog,knowledge,model,after,count+1,count+1);
  db.update("INSERT INTO mastery_history(event_id,knowledge_id,model_version,replay_generation,before_mastery,after_mastery,weight) VALUES (?,?,?,?,?,?,1)",event,knowledge,model,generation,before,after);
  db.update("INSERT INTO student_profile(user_id,course_id,total_answers,correct_answers,activity_days,last_activity_at) SELECT user_id,course_id,COUNT(*),SUM(correct),COUNT(DISTINCT CASE WHEN occurred_at>=UTC_DATE()-INTERVAL 6 DAY AND occurred_at<UTC_DATE()+INTERVAL 1 DAY THEN DATE(occurred_at) END),MAX(occurred_at) FROM learning_interaction WHERE user_id=? AND course_id=? GROUP BY user_id,course_id ON DUPLICATE KEY UPDATE total_answers=VALUES(total_answers),correct_answers=VALUES(correct_answers),activity_days=VALUES(activity_days),last_activity_at=VALUES(last_activity_at)",user,course);
  if(live) paths.completeQuestionNode(user,course,catalog,row.get("path_node_id")==null?null:row.get("path_node_id").toString(),event,newRevision);
  db.update("UPDATE event_consume_log SET status='SUCCEEDED',consumed_at=UTC_TIMESTAMP(6),error_code=NULL,next_retry_at=NULL WHERE event_id=?",event);
  recommendations.trustedCompletion(user,row.get("recommendation_id")==null?null:row.get("recommendation_id").toString(),event);
 }
 void applyImported(Map<String,Object> e,int generation,boolean live,long newRevision){
  String user=e.get("user_id").toString(),course=e.get("course_id").toString(),catalog=e.get("catalog_version").toString(),event=e.get("event_id").toString(),type=e.get("event_type").toString();
  JsonNode payload=parse(e.get("payload_json"));String objectId=payload.path("objectId").asText(null);
  if("QUESTION_ANSWERED".equals(type)){
   if(!payload.path("correct").isBoolean()||objectId==null)throw new IllegalStateException("IMPORTED_QUESTION_INVALID");
   var q=db.queryForList("SELECT knowledge_id FROM question_snapshot WHERE question_id=? AND course_id=? AND catalog_version=?",objectId,course,catalog);if(q.isEmpty())throw new MissingSnapshot();
   var items=payload.path("knowledgeItems");if(!items.isArray()||items.size()!=1||!q.get(0).get("knowledge_id").toString().equals(items.get(0).path("knowledgeId").asText())||items.get(0).path("weight").asDouble()!=1)throw new IllegalStateException("IMPORTED_KNOWLEDGE_INVALID");
   String knowledge=q.get(0).get("knowledge_id").toString(),model=model(course,catalog);var previous=db.queryForList("SELECT * FROM mastery_state WHERE user_id=? AND course_id=? AND knowledge_id=? AND model_version=?",user,course,knowledge,model);double before=previous.isEmpty()?.2:((Number)previous.get(0).get("mastery")).doubleValue(),after=Bkt.update(before,payload.path("correct").asBoolean(),1);int count=previous.isEmpty()?0:((Number)previous.get(0).get("evidence_count")).intValue();
   db.update("INSERT IGNORE INTO learning_interaction(interaction_id,source_event_id,user_id,course_id,question_id,catalog_version,correct,occurred_at,event_seq,source_service,event_type) VALUES (?,?,?,?,?,?,?,?,?,?,?)",event,event,user,course,objectId,catalog,payload.path("correct").asBoolean(),e.get("occurred_at"),e.get("event_seq"),e.get("source_service"),type);db.update("INSERT IGNORE INTO interaction_knowledge(interaction_id,knowledge_id,normalized_weight) VALUES (?,?,1)",event,knowledge);db.update("INSERT INTO mastery_state(user_id,course_id,catalog_version,knowledge_id,model_version,mastery,evidence_count,evidence_weight) VALUES (?,?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE mastery=VALUES(mastery),evidence_count=VALUES(evidence_count),evidence_weight=VALUES(evidence_weight)",user,course,catalog,knowledge,model,after,count+1,count+1);db.update("INSERT INTO mastery_history(event_id,knowledge_id,model_version,replay_generation,before_mastery,after_mastery,weight) VALUES (?,?,?,?,?,?,1)",event,knowledge,model,generation,before,after);
  } else if("RESOURCE_VIEWED".equals(type)||"RESOURCE_COMPLETED".equals(type)){
   if(objectId==null||db.queryForList("SELECT 1 FROM resource_snapshot WHERE resource_id=? AND course_id=? AND catalog_version=?",objectId,course,catalog).isEmpty())throw new MissingSnapshot();var r=db.queryForMap("SELECT resource_type FROM resource_snapshot WHERE resource_id=? AND course_id=? AND catalog_version=?",objectId,course,catalog);db.update("INSERT IGNORE INTO resource_activity(event_id,user_id,course_id,resource_id,catalog_version,resource_type,activity_type,occurred_at) VALUES (?,?,?,?,?,?,?,?)",event,user,course,objectId,catalog,r.get("resource_type"),type.substring("RESOURCE_".length()),e.get("occurred_at"));
  }
  if(live) paths.completeQuestionNode(user,course,catalog,null,event,newRevision);
  db.update("UPDATE event_consume_log SET status='SUCCEEDED',consumed_at=UTC_TIMESTAMP(6),error_code=NULL,next_retry_at=NULL WHERE event_id=?",event);
 }
 void updateState(String user,String course,Map<String,Object> e,boolean increment){
  db.update("UPDATE learner_course_state SET state_revision=state_revision+?,last_occurred_at=?,last_event_seq=?,last_event_id=?,updated_at=UTC_TIMESTAMP(6),status='READY' WHERE user_id=? AND course_id=?",increment?1:0,e.get("occurred_at"),e.get("event_seq"),e.get("event_id"),user,course);
 }
 void recordFailure(String user,String course,Exception ex){
  tx.executeWithoutResult(s->{
   lockState(user,course);
   var heads=db.queryForList("SELECT * FROM event_consume_log WHERE user_id=? AND course_id=? AND status<>'SUCCEEDED' ORDER BY occurred_at,COALESCE(source_event_seq,event_seq),event_seq,event_id LIMIT 1 FOR UPDATE",user,course);
   if(heads.isEmpty())return;var e=heads.get(0);int attempt=((Number)e.get("retry_count")).intValue()+1;
   if(ex instanceof MissingSnapshot){db.update("UPDATE event_consume_log SET status='PENDING_COMPENSATION',error_code='MISSING_SNAPSHOT',next_retry_at=NULL WHERE event_id=?",e.get("event_id"));return;}
   int[] delays={5,15,60,300,900};boolean terminal=attempt>=5;
   db.update("UPDATE event_consume_log SET status=?,retry_count=?,next_retry_at=?,error_code=? WHERE event_id=?",terminal?"NEEDS_ATTENTION":"FAILED",attempt,terminal?null:Timestamp.from(Instant.now().plusSeconds(delays[attempt-1])),ex.getClass().getSimpleName(),e.get("event_id"));
  });
 }
 public Object retry(Jwt jwt,String event){
  if(!"ADMIN".equals(jwt.getClaimAsString("role")))throw BusinessException.forbidden();
  return tx.execute(s->{
   var rows=db.queryForList("SELECT user_id,course_id FROM event_consume_log WHERE event_id=?",event);if(rows.isEmpty())throw BusinessException.missing();
   lockState(rows.get(0).get("user_id").toString(),rows.get(0).get("course_id").toString());
   db.update("UPDATE event_consume_log SET status='PENDING',retry_count=0,next_retry_at=NULL,error_code=NULL WHERE event_id=? AND status<>'SUCCEEDED'",event);
   return Map.of("status","QUEUED");
  });
 }
 public Object rebuild(Jwt jwt,String user,String course){
  if(!"ADMIN".equals(jwt.getClaimAsString("role")))throw BusinessException.forbidden();
  access.require(jwt,course);
  tx.executeWithoutResult(s->{
   db.queryForObject("SELECT user_id FROM local_user WHERE user_id=? FOR UPDATE",String.class,user);
   var rows=db.queryForList("SELECT * FROM learner_course_state WHERE user_id=? AND course_id=? FOR UPDATE",user,course);if(rows.isEmpty())throw BusinessException.missing();
   if("REBUILDING".equals(rows.get(0).get("status")))throw conflict("已有重建进行中");
   db.update("UPDATE learner_course_state SET status='REBUILDING' WHERE user_id=? AND course_id=?",user,course);
  });
  try{return tx.execute(s->{
   var state=lockState(user,course);String catalog=state.get("catalog_version").toString();
   String targetCatalog=db.queryForObject("SELECT catalog_version FROM course WHERE course_id=?",String.class,course);
   db.update("UPDATE rebuild_job SET status='REBUILDING',attempt_count=attempt_count+1 WHERE user_id=? AND course_id=? AND to_catalog_version=? AND status='PENDING'",user,course,targetCatalog);
   if(!catalog.equals(targetCatalog)) return crossCatalogRebuild(user,course,catalog,targetCatalog,state);
   var events=db.queryForList("SELECT * FROM event_consume_log WHERE user_id=? AND course_id=? AND status IN ('SUCCEEDED','PENDING','FAILED') ORDER BY occurred_at,COALESCE(source_event_seq,event_seq),event_id FOR UPDATE",user,course);
   if(events.size()>10000)throw conflict("单次重建最多10000条事件");
   if(events.stream().anyMatch(e->!catalog.equals(e.get("catalog_version"))))throw conflict("跨目录重建尚未开放");
   int generation=((Number)state.get("replay_generation")).intValue()+1;
   db.update("DELETE FROM mastery_state WHERE user_id=? AND course_id=?",user,course);
   for(var event:events)apply(event,generation,false,0);
   if(!events.isEmpty())updateState(user,course,events.get(events.size()-1),true);
   db.update("UPDATE learner_course_state SET replay_generation=?,status='READY' WHERE user_id=? AND course_id=?",generation,user,course);
   db.update("UPDATE rebuild_job SET status='SUCCEEDED',processed_events=?,mapped_events=?,unmapped_events=0,completed_at=UTC_TIMESTAMP(6) WHERE user_id=? AND course_id=? AND status='REBUILDING'",events.size(),events.size(),user,course);
   return Map.of("status","READY");
  });}catch(RuntimeException ex){
   tx.executeWithoutResult(s->db.update("UPDATE learner_course_state SET status='NEEDS_ATTENTION' WHERE user_id=? AND course_id=?",user,course));throw ex;
  }
 }

 private Map<String,Object> crossCatalogRebuild(String user,String course,String fromCatalog,String targetCatalog,Map<String,Object> state){
  var events=db.queryForList("SELECT * FROM event_consume_log WHERE user_id=? AND course_id=? AND status IN ('SUCCEEDED','PENDING','FAILED') ORDER BY occurred_at,COALESCE(source_event_seq,event_seq),event_id FOR UPDATE",user,course);
  if(events.size()>10000)throw conflict("单次重建最多10000条事件");
  int generation=((Number)state.get("replay_generation")).intValue()+1;String targetModel=model(course,targetCatalog);int mapped=0,unmapped=0;
  var mastery=new LinkedHashMap<String,double[]>();
  for(var e:events){
   if(!"QUESTION_ANSWERED".equals(e.get("event_type"))||e.get("submission_id")==null)continue;
   var old=db.queryForList("SELECT s.question_id,q.knowledge_id FROM practice_submission s JOIN question_snapshot q ON q.question_id=s.question_id AND q.catalog_version=s.catalog_version WHERE s.submission_id=? AND s.catalog_version=?",e.get("submission_id"),fromCatalog);
   if(old.isEmpty()){unmapped++;continue;}
   String fromSkill=old.get(0).get("knowledge_id").toString();String toSkill=fromSkill;
   if(db.queryForList("SELECT 1 FROM knowledge_snapshot WHERE course_id=? AND catalog_version=? AND knowledge_id=?",course,targetCatalog,fromSkill).isEmpty()){
    var map=db.queryForList("SELECT to_knowledge_id FROM catalog_knowledge_map WHERE course_id=? AND from_catalog_version=? AND from_knowledge_id=? AND to_catalog_version=?",course,fromCatalog,fromSkill,targetCatalog);
    if(map.isEmpty()){unmapped++;continue;} toSkill=map.get(0).get("to_knowledge_id").toString();
   }
   var value=mastery.computeIfAbsent(toSkill,k->new double[]{.2,0,0});boolean correct=truth(db.queryForObject("SELECT correct FROM practice_submission WHERE submission_id=?",Object.class,e.get("submission_id")));value[0]=Bkt.update(value[0],correct,1);value[1]++;value[2]++;mapped++;
  }
  for(var entry:mastery.entrySet())db.update("INSERT INTO mastery_state(user_id,course_id,catalog_version,knowledge_id,model_version,mastery,evidence_count,evidence_weight) VALUES (?,?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE mastery=VALUES(mastery),evidence_count=VALUES(evidence_count),evidence_weight=VALUES(evidence_weight)",user,course,targetCatalog,entry.getKey(),targetModel,entry.getValue()[0],(int)entry.getValue()[1],entry.getValue()[2]);
  long revision=((Number)state.get("state_revision")).longValue()+1;
  db.update("UPDATE learner_course_state SET catalog_version=?,state_revision=?,replay_generation=?,status='READY',updated_at=UTC_TIMESTAMP(6) WHERE user_id=? AND course_id=?",targetCatalog,revision,generation,user,course);
  String dedupe=hash(user+"|"+course+"|"+targetCatalog+"|"+revision+"|CATALOG_MIGRATED");db.update("INSERT IGNORE INTO path_replan_job(job_id,user_id,course_id,catalog_version,trigger_state_revision,trigger_type,dedupe_key) VALUES (?,?,?,?,?,?,?)",uuid(),user,course,targetCatalog,revision,"CATALOG_MIGRATED",dedupe);
  db.update("UPDATE rebuild_job SET status=?,processed_events=?,mapped_events=?,unmapped_events=?,completed_at=UTC_TIMESTAMP(6) WHERE user_id=? AND course_id=? AND status='REBUILDING'",unmapped==0?"SUCCEEDED":"SUCCEEDED_WITH_WARNINGS",events.size(),mapped,unmapped,user,course);
  return Map.of("status",unmapped==0?"SUCCEEDED":"SUCCEEDED_WITH_WARNINGS","processedEvents",events.size(),"mappedEvents",mapped,"unmappedEvents",unmapped,"catalogVersion",targetCatalog);
 }
}
