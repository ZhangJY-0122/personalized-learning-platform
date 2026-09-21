package edu.f21.recommendation;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.databind.node.ObjectNode;
import edu.f21.catalog.CourseAccess;
import edu.f21.common.BusinessException;
import edu.f21.learningpath.LearningPathService;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Timestamp;
import java.time.*;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;
import static edu.f21.recommendation.RecommendationRanker.*;

@Service
public class RecommendationService {
 private final JdbcTemplate db; private final CourseAccess access; private final ObjectMapper json; private final TransactionTemplate tx;
 private final LearningPathService paths;
 public RecommendationService(JdbcTemplate db,CourseAccess access,ObjectMapper json,PlatformTransactionManager tm,LearningPathService paths){this.db=db;this.access=access;this.json=json;this.paths=paths;tx=new TransactionTemplate(tm);tx.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);}
 private static BusinessException conflict(String m){return new BusinessException(409,"CONFLICT",m);}
 private static BusinessException invalid(String m){return new BusinessException(400,"INVALID_ARGUMENT",m);}
 private String id(){return UUID.randomUUID().toString();}
 private JsonNode parse(Object s){try{return json.readTree(s.toString());}catch(Exception e){throw new IllegalStateException(e);}}
 private Object sorted(JsonNode n){if(n.isObject()){var m=new TreeMap<String,Object>();n.fields().forEachRemaining(e->m.put(e.getKey(),sorted(e.getValue())));return m;}if(n.isArray()){var l=new ArrayList<Object>();n.forEach(v->l.add(sorted(v)));return l;}return json.convertValue(n,Object.class);}
 private String encode(Object o){try{return json.writeValueAsString(sorted(json.valueToTree(o)));}catch(Exception e){throw new IllegalStateException(e);}}
 private String hash(Object o){try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(encode(o).getBytes(StandardCharsets.UTF_8)));}catch(Exception e){throw new IllegalStateException(e);}}
 private static Instant time(Object o){return o instanceof Timestamp t?t.toInstant():((LocalDateTime)o).toInstant(ZoneOffset.UTC);}
 private static double number(Object o){return ((Number)o).doubleValue();}
 private static long integer(Object o){return ((Number)o).longValue();}
 private void student(Jwt jwt,String user){if(!"STUDENT".equals(jwt.getClaimAsString("role"))||!jwt.getSubject().equals(user))throw BusinessException.forbidden();}
 private void readAccess(Jwt jwt,String user,String course){if(!jwt.getSubject().equals(user)&&!"ADMIN".equals(jwt.getClaimAsString("role")))throw BusinessException.forbidden();access.require(jwt,course);}
 private void lockUser(String user){db.queryForObject("SELECT user_id FROM local_user WHERE user_id=? FOR UPDATE",String.class,user);}
 private Map<String,Object> state(String user,String course,String version){
  db.update("INSERT IGNORE INTO learner_course_state(user_id,course_id,catalog_version) VALUES (?,?,?)",user,course,version);
  var s=db.queryForMap("SELECT * FROM learner_course_state WHERE user_id=? AND course_id=? FOR UPDATE",user,course);
  if(!version.equals(s.get("catalog_version")))throw conflict("学习目录版本待迁移");return s;
 }
 private void ready(String user,String course,Map<String,Object>s){
  if(!"READY".equals(s.get("status"))||db.queryForObject("SELECT COUNT(*) FROM event_consume_log WHERE user_id=? AND course_id=? AND status<>'SUCCEEDED'",Integer.class,user,course)>0)
   throw conflict("学习状态正在处理或待修复，请稍后刷新再生成");
 }
 private JsonNode replay(String user,String op,String key,Object body){
  var rows=db.queryForList("SELECT * FROM recommendation_request WHERE user_id=? AND operation=? AND idempotency_key=?",user,op,key);
  if(rows.isEmpty())return null;var r=rows.get(0);if(!hash(body).equals(r.get("payload_hash")))throw conflict("同一请求编号不能用于不同内容");return parse(r.get("response_json"));
 }
 private Object remember(String user,String op,String key,Object body,Object result){db.update("INSERT INTO recommendation_request VALUES (?,?,?,?,?)",user,op,key,hash(body),encode(result));return result;}
 private record Catalog(String version,List<Map<String,Object>> skills,List<Map<String,Object>> edges,List<Map<String,Object>> resources,List<Map<String,Object>> questions){}
 private Catalog catalog(String course){
  String v=db.queryForObject("SELECT catalog_version FROM course WHERE course_id=? FOR SHARE",String.class,course);
  return new Catalog(v,
   db.queryForList("SELECT * FROM knowledge_snapshot WHERE course_id=? AND catalog_version=? ORDER BY knowledge_id FOR SHARE",course,v),
   db.queryForList("SELECT * FROM prerequisite_snapshot WHERE course_id=? AND catalog_version=? ORDER BY source_knowledge_id,target_knowledge_id FOR SHARE",course,v),
   db.queryForList("SELECT s.*,r.status FROM resource_snapshot s JOIN resource r ON r.resource_id=s.resource_id AND r.course_id=s.course_id WHERE s.course_id=? AND s.catalog_version=? ORDER BY s.resource_id FOR SHARE",course,v),
   db.queryForList("SELECT s.question_id,s.knowledge_id,s.stem,s.difficulty,q.status FROM question_snapshot s JOIN question q ON q.question_id=s.question_id AND q.course_id=s.course_id WHERE s.course_id=? AND s.catalog_version=? ORDER BY s.question_id FOR SHARE",course,v));
 }
 private Input input(String user,String course,Catalog c,Instant now){
  var skills=new ArrayList<Skill>();
  for(var k:c.skills()){
   String kid=k.get("knowledge_id").toString();
   var states=db.queryForList("SELECT * FROM mastery_state WHERE user_id=? AND course_id=? AND catalog_version=? AND knowledge_id=? AND model_version=?",user,course,c.version(),kid,"bkt-"+course+"-"+c.version()+"-param1");
   var recent=db.queryForList("SELECT i.correct,ik.normalized_weight FROM learning_interaction i JOIN interaction_knowledge ik ON ik.interaction_id=i.interaction_id WHERE i.user_id=? AND i.course_id=? AND i.catalog_version=? AND ik.knowledge_id=? ORDER BY i.occurred_at DESC,i.event_seq DESC LIMIT 20",user,course,c.version(),kid);
   double weight=0,errors=0;for(var r:recent){double w=number(r.get("normalized_weight"));weight+=w;if(!Boolean.TRUE.equals(r.get("correct"))&&!(r.get("correct") instanceof Number n&&n.intValue()!=0))errors+=w;}
   var m=states.isEmpty()?null:states.get(0);
   skills.add(new Skill(kid,k.get("name").toString(),((Number)k.get("sort_order")).intValue(),m==null?.2:number(m.get("mastery")),m==null?0:((Number)m.get("evidence_count")).intValue(),m==null?0:number(m.get("evidence_weight")),weight==0?null:errors/weight));
  }
  var edges=c.edges().stream().map(e->new Edge(e.get("source_knowledge_id").toString(),e.get("target_knowledge_id").toString())).toList();
  var candidates=new ArrayList<Candidate>();
  for(var r:c.resources()){
   var done=db.queryForList("SELECT MAX(occurred_at) completed FROM resource_activity WHERE user_id=? AND course_id=? AND catalog_version=? AND resource_id=? AND activity_type='COMPLETED'",user,course,c.version(),r.get("resource_id"));
   Object completed=done.get(0).get("completed");
   candidates.add(new Candidate(r.get("resource_id").toString(),course,Kind.RESOURCE,r.get("knowledge_id").toString(),r.get("title").toString(),"ACTIVE".equals(r.get("status")),number(r.get("difficulty")),r.get("resource_type").toString(),completed==null?null:time(completed)));
  }
  for(var q:c.questions())candidates.add(new Candidate(q.get("question_id").toString(),course,Kind.QUESTION,q.get("knowledge_id").toString(),q.get("stem").toString(),"ACTIVE".equals(q.get("status")),number(q.get("difficulty")),null,null));
  var recent=db.queryForList("SELECT question_id FROM learning_interaction WHERE user_id=? AND course_id=? AND catalog_version=? ORDER BY occurred_at DESC,event_seq DESC LIMIT 3",String.class,user,course,c.version());
  return new Input(course,skills,edges,candidates,recent,preferences(user,course,now),now);
 }
 public Map<String,Double> preferences(String user,String course,Instant now){
  var rows=db.queryForList("SELECT resource_type,COUNT(*) n FROM resource_activity WHERE user_id=? AND course_id=? AND occurred_at>? AND occurred_at<=? GROUP BY resource_type",user,course,Timestamp.from(now.minus(Duration.ofDays(30))),Timestamp.from(now));
  double sum=rows.stream().mapToDouble(r->number(r.get("n"))).sum();var p=new TreeMap<String,Double>();for(var r:rows)p.put(r.get("resource_type").toString(),number(r.get("n"))/sum);return p;
 }
 public Object generate(Jwt jwt,String user,String course,String key){
  student(jwt,user);access.require(jwt,course);
  return tx.execute(t->{lockUser(user);var body=Map.of("courseId",course);var previous=replay(user,"GENERATE",key,body);if(previous!=null)return previous;
   String version=db.queryForObject("SELECT catalog_version FROM course WHERE course_id=?",String.class,course);
   var s=state(user,course,version);ready(user,course,s);var c=catalog(course);if(!version.equals(c.version()))throw conflict("目录变化，请重试");
   Instant now=Instant.now().truncatedTo(ChronoUnit.MICROS);Input input=input(user,course,c,now);Result ranked;
   try{ranked=RecommendationRanker.rank(input);}catch(IllegalArgumentException e){throw conflict("推荐所需目录或学习证据不完整，请管理员检查");}
   String batch=id();var items=new ArrayList<Map<String,Object>>();
   for(var r:ranked.items()){var i=new LinkedHashMap<String,Object>();i.put("recommendationId",id());i.put("itemId",r.candidate().id());i.put("itemType",r.candidate().kind().name());i.put("title",r.candidate().title());i.put("knowledgeId",r.candidate().skillId());i.put("score",r.score());i.put("scoreDetails",r.scoreDetails());i.put("reasons",r.reasons());i.put("review",r.review());i.put("available",true);i.put("opinion",null);i.put("trustedCompleted",false);items.add(i);}
   var result=new LinkedHashMap<String,Object>();result.put("meta",Map.of("catalogVersion",version,"stateRevision",s.get("state_revision"),"asOf",now.toString(),"stale",false,"status","READY"));result.put("batchId",batch);result.put("strategyVersion",STRATEGY);result.put("items",items);result.put("emptyReason",items.isEmpty()?"暂无满足目标与先修条件的内容":null);result.put("expiresAt",now.plusSeconds(1800).toString());result.put("mode",ranked.mode().name());result.put("staleReasons",List.of());result.put("notices",ranked.notices());
   // Catalog rows and learner state remain locked until this transaction commits.
   if(!hash(c).equals(hash(catalog(course))))throw conflict("目录内容变化，请重试");
   db.update("INSERT INTO recommendation_batch(batch_id,user_id,course_id,catalog_version,state_revision,catalog_hash,strategy_version,created_at,expires_at,response_json,input_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",batch,user,course,version,s.get("state_revision"),hash(c),STRATEGY,Timestamp.from(now),Timestamp.from(now.plusSeconds(1800)),encode(result),encode(input));
   int order=0;for(var i:items)db.update("INSERT INTO recommendation_item VALUES (?,?,?,?,?,?)",i.get("recommendationId"),batch,i.get("itemType"),i.get("itemId"),i.get("knowledgeId"),order++);
   return remember(user,"GENERATE",key,body,result);
  });
 }
 public Object get(Jwt jwt,String user,String course){
  readAccess(jwt,user,course);return tx.execute(t->{
   // Hold the user's serialization lock for a consistent batch/state read; never create rows on GET.
   lockUser(user);
   db.queryForList("SELECT user_id FROM learner_course_state WHERE user_id=? AND course_id=? FOR SHARE",user,course);
   var batches=db.queryForList("SELECT * FROM recommendation_batch WHERE user_id=? AND course_id=? ORDER BY batch_seq DESC LIMIT 1",user,course);
   var c=catalog(course);
   if(batches.isEmpty()){
    var states=db.queryForList("SELECT * FROM learner_course_state WHERE user_id=? AND course_id=?",user,course);
    var state=states.isEmpty()?null:states.get(0);
    boolean pending=state!=null&&(!"READY".equals(state.get("status"))||!c.version().equals(state.get("catalog_version")))||db.queryForObject("SELECT COUNT(*) FROM event_consume_log WHERE user_id=? AND course_id=? AND status<>'SUCCEEDED'",Integer.class,user,course)>0;
    var r=new LinkedHashMap<String,Object>();r.put("meta",Map.of("catalogVersion",c.version(),"stateRevision",state==null?0:state.get("state_revision"),"asOf",Instant.now().toString(),"stale",pending,"status",pending?"PROCESSING":"INSUFFICIENT"));r.put("batchId",null);r.put("strategyVersion",STRATEGY);r.put("items",List.of());r.put("emptyReason",pending?"学习状态正在处理或待修复，请稍后生成推荐":"尚未生成推荐，请点击生成推荐");r.put("expiresAt",null);r.put("mode","NONE");r.put("notices",List.of());r.put("staleReasons",pending?List.of("学习状态处理中或待修复"):List.of());return r;
   }
   var b=batches.get(0);ObjectNode result=(ObjectNode)parse(b.get("response_json"));var reasons=new ArrayList<String>();
   var s=db.queryForMap("SELECT * FROM learner_course_state WHERE user_id=? AND course_id=?",user,course);
   if(integer(s.get("state_revision"))!=integer(b.get("state_revision")))reasons.add("学习或资源行为已更新");
   if(!hash(c).equals(b.get("catalog_hash"))||!c.version().equals(b.get("catalog_version")))reasons.add("课程内容或可用状态已变化");
   if(!STRATEGY.equals(b.get("strategy_version")))reasons.add("推荐策略已更新");
   if(!Instant.now().isBefore(time(b.get("expires_at"))))reasons.add("推荐已超过30分钟有效期");
   boolean pending=!"READY".equals(s.get("status"))||db.queryForObject("SELECT COUNT(*) FROM event_consume_log WHERE user_id=? AND course_id=? AND status<>'SUCCEEDED'",Integer.class,user,course)>0;
   if(pending)reasons.add("学习状态处理中或待修复");
   ((ObjectNode)result.get("meta")).put("stale",!reasons.isEmpty()).put("status",pending?"PROCESSING":"READY");result.set("staleReasons",json.valueToTree(reasons));
   var available=new HashSet<String>();for(var r:c.resources())if("ACTIVE".equals(r.get("status")))available.add("RESOURCE:"+r.get("resource_id"));for(var q:c.questions())if("ACTIVE".equals(q.get("status")))available.add("QUESTION:"+q.get("question_id"));
   for(var node:result.withArray("items")){var i=(ObjectNode)node;String rec=i.path("recommendationId").asText();i.put("available",c.version().equals(b.get("catalog_version"))&&available.contains(i.path("itemType").asText()+":"+i.path("itemId").asText()));
    var opinions=db.queryForList("SELECT feedback_type FROM recommendation_feedback WHERE recommendation_id=? AND feedback_type IN ('HELPFUL','NOT_HELPFUL') ORDER BY feedback_seq DESC LIMIT 1",String.class,rec);if(opinions.isEmpty())i.putNull("opinion");else i.put("opinion",opinions.get(0));
    i.put("trustedCompleted",db.queryForObject("SELECT COUNT(*) FROM recommendation_feedback WHERE recommendation_id=? AND trusted=1 AND feedback_type='COMPLETED'",Integer.class,rec)>0);
   }return result;
  });
 }
 public void validateAttribution(String user,String course,String item,String version,String kind,String rec){
  if(rec==null)return;var rows=db.queryForList("SELECT i.*,b.user_id,b.course_id,b.catalog_version FROM recommendation_item i JOIN recommendation_batch b ON b.batch_id=i.batch_id WHERE i.recommendation_id=?",rec);
  if(rows.isEmpty())throw BusinessException.missing();var r=rows.get(0);
  if(!user.equals(r.get("user_id"))||!course.equals(r.get("course_id")))throw BusinessException.forbidden();
  if(!item.equals(r.get("item_id"))||!kind.equals(r.get("item_type"))||!version.equals(r.get("catalog_version")))throw conflict("推荐与当前内容或版本不匹配");
 }
 /** Called only inside the successful practice/resource transaction, never from untrusted feedback. */
 public void trustedCompletion(String user,String rec,String event){
  if(rec==null)return;
  db.update("INSERT INTO recommendation_feedback(feedback_id,recommendation_id,user_id,feedback_type,trusted,source_event_id) VALUES (?,?,?,'COMPLETED',1,?) ON DUPLICATE KEY UPDATE source_event_id=VALUES(source_event_id)",id(),rec,user,event);
 }
 public Object feedback(Jwt jwt,String rec,String key,String type,String source){
  String user=jwt.getSubject();student(jwt,user);
  if(!List.of("EXPOSED","CLICKED","COMPLETED","HELPFUL","NOT_HELPFUL","IGNORED").contains(type))throw invalid("未知反馈类型");
  return tx.execute(t->{lockUser(user);var rows=db.queryForList("SELECT b.user_id,b.course_id FROM recommendation_item i JOIN recommendation_batch b ON b.batch_id=i.batch_id WHERE i.recommendation_id=?",rec);if(rows.isEmpty())throw BusinessException.missing();var r=rows.get(0);if(!user.equals(r.get("user_id")))throw BusinessException.forbidden();access.require(jwt,r.get("course_id").toString());
   var body=new TreeMap<String,Object>();body.put("recommendationId",rec);body.put("feedbackType",type);body.put("sourceEventId",source);var prior=replay(user,"FEEDBACK",key,body);if(prior!=null)return prior;
   String fid;boolean trusted=false;
   if(source!=null){
    if(!"COMPLETED".equals(type))throw invalid("来源事件只能用于完成反馈");
    // A source is usable only if the server already derived this exact user's exact attribution.
    var matches=db.queryForList("SELECT feedback_id FROM recommendation_feedback WHERE recommendation_id=? AND user_id=? AND source_event_id=? AND trusted=1 AND feedback_type='COMPLETED'",String.class,rec,user,source);
    if(matches.isEmpty())throw conflict("来源事件尚未成功处理或不属于本推荐");fid=matches.get(0);trusted=true;
   }else {fid=id();db.update("INSERT INTO recommendation_feedback(feedback_id,recommendation_id,user_id,feedback_type,trusted) VALUES (?,?,?,?,0)",fid,rec,user,type);}
   return remember(user,"FEEDBACK",key,body,Map.of("feedbackId",fid,"trusted",trusted,"updatedMastery",false));
  });
 }
 public Object resourceActivity(Jwt jwt,String resource,String rec,String pathNode,String key,String type){
  String user=jwt.getSubject();student(jwt,user);
  if(rec!=null&&pathNode!=null)throw invalid("推荐归因与路径归因不能同时提供");
  return tx.execute(t->{lockUser(user);
   var info=db.queryForList("SELECT r.course_id,c.catalog_version FROM resource r JOIN course c ON c.course_id=r.course_id WHERE r.resource_id=? AND r.status='ACTIVE'",resource);if(info.isEmpty())throw BusinessException.missing();String course=info.get(0).get("course_id").toString(),version=info.get(0).get("catalog_version").toString();access.require(jwt,course);
   var body=new TreeMap<String,Object>();body.put("resourceId",resource);body.put("recommendationId",rec);body.put("pathNodeId",pathNode);body.put("activityType",type);var previous=replay(user,"RESOURCE_ACTIVITY",key,body);if(previous!=null)return previous;
   var s=state(user,course,version);var c=catalog(course);if(!version.equals(c.version()))throw conflict("目录已变化");
   var row=c.resources().stream().filter(r->resource.equals(r.get("resource_id"))&&"ACTIVE".equals(r.get("status"))).findFirst().orElseThrow(BusinessException::missing);
   validateAttribution(user,course,resource,version,"RESOURCE",rec);
   paths.validatePathAttribution(user,course,resource,version,"RESOURCE",pathNode);
   if(pathNode!=null && db.queryForObject("SELECT COUNT(*) FROM resource_activity WHERE path_node_id=? AND activity_type=?",Integer.class,pathNode,type)>0)
    throw conflict("路径节点活动已经记录");
   if(pathNode!=null&&"VIEWED".equals(type))paths.markNodeStarted(pathNode);
   if("COMPLETED".equals(type)&&db.queryForObject("SELECT COUNT(*) FROM resource_activity WHERE user_id=? AND course_id=? AND resource_id=? AND catalog_version=? AND activity_type='VIEWED'",Integer.class,user,course,resource,version)==0)throw conflict("请先打开并阅读资源，再记录完成");
   String event=id();db.update("INSERT INTO resource_activity(event_id,user_id,course_id,resource_id,catalog_version,resource_type,activity_type,recommendation_id,path_node_id,occurred_at) VALUES (?,?,?,?,?,?,?,?,?,?)",event,user,course,resource,version,row.get("resource_type"),type,rec,pathNode,Timestamp.from(Instant.now()));
   long newRevision=integer(s.get("state_revision"))+1;
   db.update("UPDATE learner_course_state SET state_revision=state_revision+1,updated_at=UTC_TIMESTAMP(6) WHERE user_id=? AND course_id=?",user,course);
   paths.completeResourceNode(user,course,version,pathNode,event,newRevision,"COMPLETED".equals(type));
   if("COMPLETED".equals(type))trustedCompletion(user,rec,event);
   return remember(user,"RESOURCE_ACTIVITY",key,body,Map.of("sourceEventId",event,"activityType",type,"updatedMastery",false,"stateRevision",integer(s.get("state_revision"))+1));
  });
 }
}
