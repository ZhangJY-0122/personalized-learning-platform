package edu.f21.importing;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import edu.f21.common.BusinessException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** Validates and atomically publishes a versioned catalog package. */
@Service
public class CatalogImportService {
    private final JdbcTemplate db; private final ObjectMapper json; private final TransactionTemplate tx;
    public CatalogImportService(JdbcTemplate db, ObjectMapper json, PlatformTransactionManager tm) { this.db=db; this.json=json; this.tx=new TransactionTemplate(tm); }
    private static String id(){return UUID.randomUUID().toString();}
    private static void admin(Jwt jwt){if(!"ADMIN".equals(jwt.getClaimAsString("role")))throw BusinessException.forbidden();}
    private static BusinessException bad(String m){return new BusinessException(400,"INVALID_ARGUMENT",m);}
    private static BusinessException conflict(String m){return new BusinessException(409,"CONFLICT",m);}
    private String hash(String s){try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8)));}catch(Exception e){throw new IllegalStateException(e);}}
    private static String text(JsonNode n,String f){var v=n.get(f);if(v==null||!v.isTextual()||v.asText().isBlank())throw bad("缺少字段: "+f);return v.asText();}
    private static String uuid(JsonNode n,String f){String s=text(n,f);try{return UUID.fromString(s).toString();}catch(Exception e){throw bad("字段不是 UUID: "+f);}}
    private static double difficulty(JsonNode n,String f){var v=n.get(f);if(v==null||!v.isNumber()||v.asDouble()<0||v.asDouble()>1)throw bad("难度必须在0到1之间: "+f);return v.asDouble();}
    private static int integer(JsonNode n,String f){var v=n.get(f);if(v==null||!v.canConvertToInt())throw bad("字段必须是整数: "+f);return v.asInt();}
    private static List<JsonNode> array(JsonNode n,String f){var v=n.get(f);if(v==null||!v.isArray())throw bad("字段必须是数组: "+f);var out=new ArrayList<JsonNode>();v.forEach(out::add);return out;}
    private String canonical(JsonNode n){if(n.isObject()){var m=new TreeMap<String,Object>();n.fields().forEachRemaining(e->m.put(e.getKey(),json.valueToTree(canonicalNode(e.getValue()))));try{return json.writeValueAsString(m);}catch(Exception e){throw new IllegalStateException(e);}}return n.toString();}
    private Object canonicalNode(JsonNode n){if(n.isObject()){var m=new TreeMap<String,Object>();n.fields().forEachRemaining(e->m.put(e.getKey(),canonicalNode(e.getValue())));return m;}if(n.isArray()){var l=new ArrayList<Object>();n.forEach(v->l.add(canonicalNode(v)));return l;}return json.convertValue(n,Object.class);}

    public Object importCatalog(Jwt jwt,String key,JsonNode body){
        admin(jwt); if(body==null||body.toString().getBytes(StandardCharsets.UTF_8).length>10*1024*1024)throw bad("目录包不能超过10 MiB");
        String content=canonical(body), contentHash=hash(content);
        var previous=tx.execute(t->{
            var existing=db.queryForList("SELECT job_id,status FROM import_job WHERE job_type='CATALOG_JSON' AND content_hash=?",contentHash);
            if(!existing.isEmpty()) {
                String jobId=existing.get(0).get("job_id").toString();
                String status=existing.get(0).get("status").toString();
                // A successful (or currently running) content hash is idempotent.
                // A validation failure may be transient (for example an injected
                // database fault), so the same durable job is explicitly re-queued
                // instead of being permanently replay-blocked by the unique key.
                if ("SUCCEEDED".equals(status) || "VALIDATING".equals(status))
                    return Map.of("jobId",jobId,"status",status);
                db.update("DELETE FROM import_error WHERE job_id=?",jobId);
                db.update("UPDATE import_job SET status='VALIDATING',failure_count=0,error_code=NULL,completed_at=NULL WHERE job_id=?",jobId);
                return Map.of("jobId",jobId,"status","VALIDATING");
            }
            String job=id();db.update("INSERT INTO import_job(job_id,job_type,content_hash,status,request_json,total_count,created_by) VALUES (?,?,?,?,?,?,?)",job,"CATALOG_JSON",contentHash,"VALIDATING",body.toString(),1,jwt.getSubject());
            return Map.of("jobId",job,"status","VALIDATING");
        });
        if(!"VALIDATING".equals(previous.get("status"))) return previous;
        String job=previous.get("jobId").toString();
        try {
            validate(body);
            return tx.execute(t->{
                Map<String,Object> result=publish(jwt,body,job);
                db.update("UPDATE import_job SET status='SUCCEEDED',success_count=1,completed_at=UTC_TIMESTAMP(6) WHERE job_id=?",job);
                return result;
            });
        } catch(RuntimeException ex) {
            String code=ex instanceof BusinessException b?b.code:"VALIDATION_FAILED";
            String message=ex.getMessage()==null?"目录校验失败":ex.getMessage();
            tx.executeWithoutResult(t->{
                db.update("INSERT IGNORE INTO import_error(job_id,row_no,field_path,error_code,message) VALUES (?,?,?,?,?)",job,1,"$",code,message);
                db.update("UPDATE import_job SET status='VALIDATION_FAILED',failure_count=1,completed_at=UTC_TIMESTAMP(6),error_code=? WHERE job_id=?",code,job);
            });
            return Map.of("jobId",job,"status","VALIDATION_FAILED","errorCode",code,"message",message);
        }
    }

    private void validate(JsonNode body){
        if(body.path("schemaVersion").asInt(-1)!=1)throw bad("schemaVersion 必须为1");
        String mode=text(body,"publishMode");if(!Set.of("ACTIVATE","ARCHIVE_ONLY").contains(mode))throw bad("publishMode 无效");
        JsonNode c=body.path("course");if(!c.isObject())throw bad("缺少 course");String course=uuid(c,"courseId"),version=text(c,"catalogVersion");if(version.length()>64)throw bad("catalogVersion 过长");
        var chapters=array(body,"chapters");var knowledge=array(body,"knowledgePoints");var resources=array(body,"resources");var questions=array(body,"questions");var edges=array(body,"prerequisites");
        Set<String> chapterIds=new HashSet<>(),knowledgeIds=new HashSet<>();for(var x:chapters){if(!course.equals(uuid(x,"courseId")))throw bad("章节跨课程");if(!chapterIds.add(uuid(x,"chapterId")))throw bad("重复 chapterId");}
        Map<String,Integer> qCount=new HashMap<>();Set<String> resourceSkills=new HashSet<>();
        for(var x:knowledge){if(!course.equals(uuid(x,"courseId"))||!chapterIds.contains(uuid(x,"chapterId")))throw bad("知识点引用无效");String kid=uuid(x,"knowledgeId");if(!knowledgeIds.add(kid))throw bad("重复 knowledgeId");text(x,"name");text(x,"description");difficulty(x,"difficulty");integer(x,"sortOrder");}
        Set<String> resourceIds=new HashSet<>();for(var x:resources){if(!course.equals(uuid(x,"courseId"))||!knowledgeIds.contains(uuid(x,"knowledgeId")))throw bad("资源引用无效");if(!resourceIds.add(uuid(x,"resourceId")))throw bad("重复 resourceId");text(x,"title");text(x,"content");difficulty(x,"difficulty");integer(x,"sortOrder");resourceSkills.add(uuid(x,"knowledgeId"));}
        Set<String> questionIds=new HashSet<>();for(var x:questions){if(!course.equals(uuid(x,"courseId"))||!knowledgeIds.contains(uuid(x,"knowledgeId")))throw bad("题目引用无效");if(!questionIds.add(uuid(x,"questionId")))throw bad("重复 questionId");String type=text(x,"questionType");if(!Set.of("SINGLE","MULTIPLE","TRUE_FALSE").contains(type))throw bad("题型无效");difficulty(x,"difficulty");integer(x,"sortOrder");var options=array(x,"options");Set<String> keys=new HashSet<>();for(var o:options){String k=text(o,"key");if(!keys.add(k))throw bad("题目选项重复");text(o,"text");}var ans=array(x,"answer");if(ans.isEmpty()||("SINGLE".equals(type)||"TRUE_FALSE".equals(type))&&ans.size()!=1)throw bad("答案数量与题型不匹配");for(var a:ans)if(!a.isTextual()||!keys.contains(a.asText()))throw bad("答案键不存在");qCount.merge(uuid(x,"knowledgeId"),1,Integer::sum);}
        for(String kid:knowledgeIds)if(!resourceSkills.contains(kid)||qCount.getOrDefault(kid,0)<5)throw bad("每个知识点至少需要1个资源和5道题: "+kid);
        Map<String,Set<String>> parents=new HashMap<>();for(String k:knowledgeIds)parents.put(k,new HashSet<>());Set<String> edgeKeys=new HashSet<>();for(var x:edges){if(!course.equals(uuid(x,"courseId")))throw bad("先修关系跨课程");String a=uuid(x,"sourceKnowledgeId"),b=uuid(x,"targetKnowledgeId");if(!parents.containsKey(a)||!parents.containsKey(b)||a.equals(b)||!edgeKeys.add(a+"->"+b))throw bad("先修关系重复或悬空");parents.get(b).add(a);}for(String k:knowledgeIds)visit(k,parents,new HashSet<>(),new HashSet<>());
        if(body.has("knowledgeMappings")){Set<String> pairs=new HashSet<>();for(var x:array(body,"knowledgeMappings")){String fromV=text(x,"fromCatalogVersion"),toV=text(x,"toCatalogVersion"),fromK=uuid(x,"fromKnowledgeId"),toK=uuid(x,"toKnowledgeId");if(!"EXPLICIT".equals(x.path("mappingType").asText()))throw bad("映射类型必须为 EXPLICIT");if(!knowledgeIds.contains(toK))throw bad("映射目标知识点不在当前目录");if(!pairs.add(fromV+":"+fromK))throw bad("知识点映射一对多");if(toV.equals(fromV)&&toK.equals(fromK))throw bad("无意义的知识点映射");}}
    }
    private void visit(String k,Map<String,Set<String>> p,Set<String> seen,Set<String> stack){if(stack.contains(k))throw bad("先修关系存在环");if(seen.contains(k))return;stack.add(k);for(String x:p.get(k))visit(x,p,seen,stack);stack.remove(k);seen.add(k);}

    Map<String,Object> publish(Jwt jwt,JsonNode body,String job){
        JsonNode c=body.path("course");String course=uuid(c,"courseId"),version=text(c,"catalogVersion"),mode=text(body,"publishMode");
        var current=db.queryForList("SELECT catalog_version FROM course WHERE course_id=? FOR UPDATE",course);
        String previousVersion=current.isEmpty()?null:current.get(0).get("catalog_version").toString();
        if(previousVersion==null){
            if("ARCHIVE_ONLY".equals(mode))throw bad("新课程首次导入必须使用 ACTIVATE");
            db.update("INSERT INTO course(course_id,title,description,catalog_version) VALUES (?,?,?,?)",course,text(c,"title"),c.path("description").asText(""),version);
        }
        if(!db.queryForList("SELECT 1 FROM catalog_snapshot WHERE course_id=? AND catalog_version=?",course,version).isEmpty())throw conflict("目录版本不可复用");
        db.update("INSERT INTO catalog_snapshot(course_id,catalog_version,title,description,content_hash,snapshot_status,published_at) VALUES (?,?,?,?,?,?,?)",course,version,text(c,"title"),c.path("description").asText(""),hash(canonical(body)),"ACTIVATE".equals(mode)?"PUBLISHED":"ARCHIVED",Timestamp.from(Instant.now()));
        for(var x:array(body,"chapters"))db.update("INSERT INTO chapter_snapshot(course_id,catalog_version,chapter_id,title,sort_order) VALUES (?,?,?,?,?)",course,version,uuid(x,"chapterId"),text(x,"title"),integer(x,"sortOrder"));
        for(var x:array(body,"knowledgePoints"))db.update("INSERT INTO knowledge_snapshot(course_id,catalog_version,knowledge_id,name,sort_order,chapter_id,description,difficulty) VALUES (?,?,?,?,?,?,?,?)",course,version,uuid(x,"knowledgeId"),text(x,"name"),integer(x,"sortOrder"),uuid(x,"chapterId"),text(x,"description"),difficulty(x,"difficulty"));
        for(var x:array(body,"resources"))db.update("INSERT INTO resource_snapshot(resource_id,course_id,catalog_version,knowledge_id,title,content_text,resource_type,difficulty,sort_order) VALUES (?,?,?,?,?,?,?,?,?)",uuid(x,"resourceId"),course,version,uuid(x,"knowledgeId"),text(x,"title"),text(x,"content"),x.path("resourceType").asText("ARTICLE"),difficulty(x,"difficulty"),integer(x,"sortOrder"));
        for(var x:array(body,"questions"))db.update("INSERT INTO question_snapshot(question_id,course_id,catalog_version,knowledge_id,question_type,stem,options_json,answer_json,difficulty,sort_order) VALUES (?,?,?,?,?,?,?,?,?,?)",uuid(x,"questionId"),course,version,uuid(x,"knowledgeId"),text(x,"questionType"),text(x,"stem"),x.path("options").toString(),x.path("answer").toString(),difficulty(x,"difficulty"),integer(x,"sortOrder"));
        for(var x:array(body,"prerequisites"))db.update("INSERT INTO prerequisite_snapshot(course_id,catalog_version,source_knowledge_id,target_knowledge_id) VALUES (?,?,?,?)",course,version,uuid(x,"sourceKnowledgeId"),uuid(x,"targetKnowledgeId"));
        if(previousVersion!=null&&!version.equals(previousVersion)) for(var x:array(body,"knowledgePoints")) {
            String kid=uuid(x,"knowledgeId");
            if(!db.queryForList("SELECT 1 FROM knowledge_snapshot WHERE course_id=? AND catalog_version=? AND knowledge_id=?",course,previousVersion,kid).isEmpty())
                db.update("INSERT IGNORE INTO catalog_knowledge_map(course_id,from_catalog_version,from_knowledge_id,to_catalog_version,to_knowledge_id,mapping_type) VALUES (?,?,?,?,?,'SAME_UUID')",course,previousVersion,kid,version,kid);
        }
        if(body.has("knowledgeMappings")) for(var x:array(body,"knowledgeMappings"))
            db.update("INSERT IGNORE INTO catalog_knowledge_map(course_id,from_catalog_version,from_knowledge_id,to_catalog_version,to_knowledge_id,mapping_type) VALUES (?,?,?,?,?,?)",course,text(x,"fromCatalogVersion"),uuid(x,"fromKnowledgeId"),text(x,"toCatalogVersion"),uuid(x,"toKnowledgeId"),"EXPLICIT");
        if("ARCHIVE_ONLY".equals(mode))return Map.of("jobId",job,"status","SUCCEEDED","catalogVersion",version,"publishMode",mode);
        db.update("UPDATE course SET title=?,description=?,catalog_version=? WHERE course_id=?",text(c,"title"),c.path("description").asText(""),version,course);
        for(var x:array(body,"chapters"))db.update("INSERT INTO chapter(chapter_id,course_id,title,sort_order) VALUES (?,?,?,?) ON DUPLICATE KEY UPDATE title=VALUES(title),sort_order=VALUES(sort_order)",uuid(x,"chapterId"),course,text(x,"title"),integer(x,"sortOrder"));
        for(var x:array(body,"knowledgePoints"))db.update("INSERT INTO knowledge_point(knowledge_id,course_id,chapter_id,name,description,difficulty,sort_order) VALUES (?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE chapter_id=VALUES(chapter_id),name=VALUES(name),description=VALUES(description),difficulty=VALUES(difficulty),sort_order=VALUES(sort_order)",uuid(x,"knowledgeId"),course,uuid(x,"chapterId"),text(x,"name"),text(x,"description"),difficulty(x,"difficulty"),integer(x,"sortOrder"));
        for(var x:array(body,"resources"))db.update("INSERT INTO resource(resource_id,course_id,knowledge_id,title,content_text,resource_type,difficulty,sort_order,status) VALUES (?,?,?,?,?,?,?,?, 'ACTIVE') ON DUPLICATE KEY UPDATE knowledge_id=VALUES(knowledge_id),title=VALUES(title),content_text=VALUES(content_text),resource_type=VALUES(resource_type),difficulty=VALUES(difficulty),sort_order=VALUES(sort_order),status='ACTIVE'",uuid(x,"resourceId"),course,uuid(x,"knowledgeId"),text(x,"title"),text(x,"content"),x.path("resourceType").asText("ARTICLE"),difficulty(x,"difficulty"),integer(x,"sortOrder"));
        for(var x:array(body,"questions"))db.update("INSERT INTO question(question_id,course_id,knowledge_id,question_type,stem,options_json,answer_json,difficulty,sort_order,status) VALUES (?,?,?,?,?,?,?,?,?,'ACTIVE') ON DUPLICATE KEY UPDATE knowledge_id=VALUES(knowledge_id),question_type=VALUES(question_type),stem=VALUES(stem),options_json=VALUES(options_json),answer_json=VALUES(answer_json),difficulty=VALUES(difficulty),sort_order=VALUES(sort_order),status='ACTIVE'",uuid(x,"questionId"),course,uuid(x,"knowledgeId"),text(x,"questionType"),text(x,"stem"),x.path("options").toString(),x.path("answer").toString(),difficulty(x,"difficulty"),integer(x,"sortOrder"));
        db.update("DELETE FROM knowledge_prerequisite WHERE course_id=?",course);for(var x:array(body,"prerequisites"))db.update("INSERT INTO knowledge_prerequisite(course_id,source_knowledge_id,target_knowledge_id) VALUES (?,?,?)",course,uuid(x,"sourceKnowledgeId"),uuid(x,"targetKnowledgeId"));
        db.update("UPDATE learner_course_state SET status='REBUILD_REQUIRED' WHERE course_id=?",course);
        var states=db.queryForList("SELECT user_id FROM learner_course_state WHERE course_id=?",course);for(var s:states){String user=s.get("user_id").toString();db.update("UPDATE learning_path SET status='INVALIDATED',reason_code='CATALOG_CHANGED' WHERE user_id=? AND course_id=? AND status='ACTIVE'",user,course);db.update("INSERT INTO rebuild_job(job_id,user_id,course_id,from_catalog_version,to_catalog_version) SELECT ?,?,?,catalog_version,? FROM learner_course_state WHERE user_id=? AND course_id=?",id(),user,course,version,user,course);}
        return Map.of("jobId",job,"status","SUCCEEDED","catalogVersion",version,"publishMode",mode,"rebuildUsers",states.size());
    }
}
