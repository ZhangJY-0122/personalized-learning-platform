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
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.transaction.PlatformTransactionManager;

/** Minimal JSON batch adapter. Each event is isolated and durably queued. */
@Service
public class EventImportService {
    private final JdbcTemplate db; private final ObjectMapper json; private final TransactionTemplate tx;
    public EventImportService(JdbcTemplate db, ObjectMapper json, PlatformTransactionManager tm) { this.db=db; this.json=json; this.tx=new TransactionTemplate(tm); }
    private static String id() { return UUID.randomUUID().toString(); }
    private Object canonicalNode(JsonNode n){if(n.isObject()){var m=new TreeMap<String,Object>();n.fields().forEachRemaining(e->m.put(e.getKey(),canonicalNode(e.getValue())));return m;}if(n.isArray()){var l=new ArrayList<Object>();n.forEach(v->l.add(canonicalNode(v)));return l;}return json.convertValue(n,Object.class);}
    private String canonical(JsonNode n){try{return json.writeValueAsString(canonicalNode(n));}catch(Exception e){throw new IllegalStateException(e);}}
    private String hash(String value) { try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); } catch (Exception e) { throw new IllegalStateException(e); } }
    private static void admin(Jwt jwt) { if (!"ADMIN".equals(jwt.getClaimAsString("role"))) throw BusinessException.forbidden(); }
    private String text(JsonNode n, String field) { var v=n.get(field); if (v==null||v.isNull()||!v.isTextual()||v.asText().isBlank()) throw new BusinessException(400,"INVALID_ARGUMENT","缺少字段: "+field); return v.asText(); }

    public Object batch(Jwt jwt, String key, JsonNode body) {
        return batch(jwt,key,body,"EVENT_JSON");
    }

    private Object batch(Jwt jwt, String key, JsonNode body, String jobType) {
        admin(jwt); if (body==null||!body.isArray()||body.size()>500) throw new BusinessException(400,"INVALID_ARGUMENT","事件批次最多500条");
        String raw=canonical(body), contentHash=hash(key+"|"+raw);
        return tx.execute(t -> {
            var prior=db.queryForList("SELECT job_id,status FROM import_job WHERE job_type=? AND content_hash=?",jobType,contentHash);
            if(!prior.isEmpty()) return Map.of("jobId",prior.get(0).get("job_id"),"status",prior.get(0).get("status"));
            String job=id(); db.update("INSERT INTO import_job(job_id,job_type,content_hash,status,request_json,total_count,created_by) VALUES (?,?,?,?,?,?,?)",job,jobType,contentHash,"VALIDATING",raw,body.size(),jwt.getSubject());
            var accepted=new ArrayList<String>(); var duplicates=new ArrayList<String>(); var failed=new ArrayList<Map<String,Object>>(); int rowNo=0;
            for(JsonNode event:body){ rowNo++; try {
                String eventId=text(event,"eventId"), user=text(event,"userId"), course=text(event,"courseId"), version=text(event,"catalogVersion"), type=text(event,"eventType"), source=text(event,"sourceService"), occurred=text(event,"occurredAt");
                if("local-practice".equals(source)) throw new BusinessException(400,"INVALID_ARGUMENT","导入事件不得冒用 local-practice");
                if(!Set.of("QUESTION_ANSWERED","RESOURCE_VIEWED","RESOURCE_COMPLETED","COURSE_ENROLLED","EXAM_SUBMITTED").contains(type)||type.startsWith("RECOMMENDATION")) throw new BusinessException(400,"INVALID_ARGUMENT","不支持的导入事件类型");
                Instant.parse(occurred); String encoded=canonical(event), payloadHash=hash(encoded);
                var old=db.queryForList("SELECT payload_hash FROM event_consume_log WHERE event_id=?",eventId);
                if(!old.isEmpty()){ if(payloadHash.equals(old.get(0).get("payload_hash"))) duplicates.add(eventId); else failed.add(Map.of("eventId",eventId,"errorCode","CONFLICT")); continue; }
                String current=db.queryForObject("SELECT catalog_version FROM course WHERE course_id=?",String.class,course);
                db.update("INSERT IGNORE INTO learner_course_state(user_id,course_id,catalog_version) VALUES (?,?,?)",user,course,current);
                String state=db.queryForObject("SELECT catalog_version FROM learner_course_state WHERE user_id=? AND course_id=?",String.class,user,course);
                boolean snapshot=!db.queryForList("SELECT 1 FROM catalog_snapshot WHERE course_id=? AND catalog_version=?",course,version).isEmpty();
                String objectId=event.path("objectId").asText(null);
                if(snapshot && "QUESTION_ANSWERED".equals(type)) snapshot=objectId!=null&&!db.queryForList("SELECT 1 FROM question_snapshot WHERE question_id=? AND course_id=? AND catalog_version=?",objectId,course,version).isEmpty();
                if(snapshot && ("RESOURCE_VIEWED".equals(type)||"RESOURCE_COMPLETED".equals(type))) snapshot=objectId!=null&&!db.queryForList("SELECT 1 FROM resource_snapshot WHERE resource_id=? AND course_id=? AND catalog_version=?",objectId,course,version).isEmpty();
                String status=version.equals(state)&&snapshot?"PENDING":"PENDING_COMPENSATION";
                Object sourceSeq=event.has("eventSeq")&&event.get("eventSeq").canConvertToLong()?event.get("eventSeq").asLong():null;
                db.update("INSERT INTO event_consume_log(event_id,submission_id,user_id,course_id,catalog_version,event_type,source_service,payload_json,payload_hash,occurred_at,status,source_event_seq,import_job_id,trace_id) VALUES (?,?,?,?,?,?,?,?,?,?,?, ?,?,?)",eventId,null,user,course,version,type,source,encoded,payloadHash,Timestamp.from(Instant.parse(occurred)),status,sourceSeq,job,event.path("traceId").asText(null));
                accepted.add(eventId);
            } catch(Exception ex){ failed.add(Map.of("row",rowNo,"errorCode",ex instanceof BusinessException b?b.code:"INVALID_ARGUMENT","message",ex.getMessage()==null?"invalid event":ex.getMessage())); db.update("INSERT IGNORE INTO import_error(job_id,row_no,field_path,error_code,message) VALUES (?,?,?,?,?)",job,rowNo,"$","INVALID_ARGUMENT",ex.getMessage()==null?"invalid event":ex.getMessage()); } }
            String finalStatus=failed.isEmpty()?"SUCCEEDED":"SUCCEEDED_WITH_WARNINGS";
            db.update("UPDATE import_job SET status=?,success_count=?,failure_count=?,completed_at=UTC_TIMESTAMP(6) WHERE job_id=?",finalStatus,accepted.size()+duplicates.size(),failed.size(),job);
            return Map.of("jobId",job,"status",finalStatus,"acceptedEventIds",accepted,"duplicateEventIds",duplicates,"failedEvents",failed);
        });
    }

    public Object csv(Jwt jwt, String key, String csv) {
        admin(jwt); if (csv == null || csv.getBytes(StandardCharsets.UTF_8).length > 10*1024*1024) throw new BusinessException(400,"INVALID_ARGUMENT","CSV 不能超过10 MiB");
        var rows=parseCsv(csv); if(rows.isEmpty()||rows.size()>10001)throw new BusinessException(400,"INVALID_ARGUMENT","CSV 最多10000行");
        var headers=rows.get(0); if(headers.stream().anyMatch(String::isBlank)||new HashSet<>(headers).size()!=headers.size())throw new BusinessException(400,"INVALID_ARGUMENT","CSV 表头不能为空或重复");
        var array=json.createArrayNode();
        for(int i=1;i<rows.size();i++){var values=rows.get(i);if(values.stream().allMatch(String::isBlank))continue;var row=json.createObjectNode();for(int j=0;j<headers.size();j++)row.put(headers.get(j),j<values.size()?values.get(j):"");if(values.size()!=headers.size())row.put("_csvError","字段数量与表头不一致");array.add(row);}
        if(array.size()<=500)return batch(jwt,key+"|csv",array,"EVENT_CSV");
        var accepted=new ArrayList<String>();var duplicates=new ArrayList<String>();var failed=new ArrayList<Object>();var jobs=new ArrayList<Object>();
        for(int offset=0;offset<array.size();offset+=500){var part=json.createArrayNode();for(int i=offset;i<Math.min(offset+500,array.size());i++)part.add(array.get(i));var result=(Map<?,?>)batch(jwt,key+"|csv|"+offset,part,"EVENT_CSV");jobs.add(result.get("jobId"));if(result.get("acceptedEventIds") instanceof List<?> l)for(var x:l)accepted.add(x.toString());if(result.get("duplicateEventIds") instanceof List<?> l)for(var x:l)duplicates.add(x.toString());if(result.get("failedEvents") instanceof List<?> l)failed.addAll(l);}
        return Map.of("jobIds",jobs,"status",failed.isEmpty()?"SUCCEEDED":"SUCCEEDED_WITH_WARNINGS","acceptedEventIds",accepted,"duplicateEventIds",duplicates,"failedEvents",failed);
    }

    private List<List<String>> parseCsv(String csv) {
        var rows=new ArrayList<List<String>>(); var row=new ArrayList<String>(); var cell=new StringBuilder(); boolean quoted=false;
        for(int i=0;i<csv.length();i++) { char c=csv.charAt(i);
            if(quoted) { if(c=='"') { if(i+1<csv.length()&&csv.charAt(i+1)=='"'){cell.append('"');i++;} else quoted=false; } else cell.append(c); }
            else if(c=='"'&&cell.isEmpty()) quoted=true;
            else if(c==','){row.add(cell.toString().trim());cell.setLength(0);}
            else if(c=='\n'||c=='\r'){ if(c=='\r'&&i+1<csv.length()&&csv.charAt(i+1)=='\n')i++; row.add(cell.toString().trim());cell.setLength(0);rows.add(row);row=new ArrayList<>(); }
            else cell.append(c);
        }
        if(quoted)throw new BusinessException(400,"INVALID_ARGUMENT","CSV 引号未闭合");
        if(!row.isEmpty()||cell.length()>0||csv.endsWith(",")){row.add(cell.toString().trim());rows.add(row);}
        return rows;
    }
}
