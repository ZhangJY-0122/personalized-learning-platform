package edu.f21.learningpath;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import edu.f21.catalog.CourseAccess;
import edu.f21.common.BusinessException;
import edu.f21.recommendation.RecommendationRanker;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** Persistence boundary for the pure G4 planner. No planner method may perform SQL. */
@Service
public class LearningPathService {
    private final JdbcTemplate db;
    private final CourseAccess access;
    private final ObjectMapper json;
    private final TransactionTemplate tx;

    public LearningPathService(JdbcTemplate db, CourseAccess access, ObjectMapper json,
                               PlatformTransactionManager tm) {
        this.db = db; this.access = access; this.json = json; this.tx = new TransactionTemplate(tm);
    }

    private static BusinessException conflict(String m) { return new BusinessException(409, "CONFLICT", m); }
    private static BusinessException invalid(String m) { return new BusinessException(400, "INVALID_ARGUMENT", m); }
    private static String id() { return UUID.randomUUID().toString(); }
    private static Instant time(Object value) {
        if (value instanceof Timestamp t) return t.toInstant();
        if (value instanceof java.time.LocalDateTime t) return t.toInstant(java.time.ZoneOffset.UTC);
        if (value instanceof Instant t) return t;
        throw new IllegalStateException("Unsupported database time value");
    }
    private static long number(Object value) { return ((Number) value).longValue(); }
    private static double decimal(Object value) { return ((Number) value).doubleValue(); }
    private static String hash(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
    private static String str(Object value) { return value == null ? null : value.toString(); }

    public Object generate(Jwt jwt, String user, String course, UUID key) {
        if (!"STUDENT".equals(jwt.getClaimAsString("role")) || !jwt.getSubject().equals(user))
            throw BusinessException.forbidden();
        access.require(jwt, course);
        return tx.execute(status -> {
            lockUser(user);
            String version = db.queryForObject("SELECT catalog_version FROM course WHERE course_id=? AND status='ACTIVE'", String.class, course);
            ensureState(user, course, version);
            var state = lockState(user, course);
            if (!version.equals(str(state.get("catalog_version")))) throw conflict("学习目录版本待迁移");
            ready(user, course, state);
            var prior = replay(user, key.toString(), Map.of("courseId", course));
            if (prior != null) return prior;

            var input = input(user, course, version, state);
            LearningPathPlanner.Result planned;
            try { planned = LearningPathPlanner.plan(input); }
            catch (IllegalArgumentException ex) { throw conflict("路径所需目录或学习证据不完整，请管理员检查"); }
            String previous = latestPathId(user, course);
            int pathVersion = db.queryForObject("SELECT COALESCE(MAX(path_version),0)+1 FROM learning_path WHERE user_id=? AND course_id=?", Integer.class, user, course);
            String path = id();
            Instant now = Instant.now().truncatedTo(ChronoUnit.MICROS);
            var reasonJson = encodeReason(planned);
            db.update("INSERT INTO learning_path(path_id,user_id,course_id,catalog_version,path_version,origin_state_revision,state_revision,planner_version,catalog_hash,status,reason_code,reason_json,previous_path_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    path, user, course, version, pathVersion, number(state.get("state_revision")), number(state.get("state_revision")),
                    LearningPathPlanner.VERSION, hash(inputHash(input)), planned.status().name(), planned.reasonCode(), reasonJson,
                    previous, Timestamp.from(now), Timestamp.from(now));
            db.update("UPDATE learning_path SET status='INVALIDATED',reason_code='STATE_CHANGED',updated_at=UTC_TIMESTAMP(6) WHERE user_id=? AND course_id=? AND status='ACTIVE' AND path_id<>?",
                    user, course, path);
            int sequence = 1;
            for (var node : planned.nodes()) {
                db.update("INSERT INTO learning_path_node(node_id,path_id,sequence_no,knowledge_id,item_type,item_id,phase,round_no,status,source_event_id,source_resource_activity_id,carried_from_node_id,reason_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        id(), path, sequence++, node.skillId(), node.itemType().name(), node.itemId(), node.phase().name(), node.roundNo(),
                        node.status().name(), null, null, node.carriedFromNodeId(), "{}", Timestamp.from(now));
            }
            var result = responseFromRow(latestPath(user, course), version, number(state.get("state_revision")), false, false);
            return remember(user, key.toString(), Map.of("courseId", course), result);
        });
    }

    public Object get(Jwt jwt, String user, String course, Integer requestedVersion) {
        readAccess(jwt, user, course);
        return tx.execute(status -> {
            lockUser(user);
            String currentVersion = db.queryForObject("SELECT catalog_version FROM course WHERE course_id=? AND status='ACTIVE'", String.class, course);
            var states = db.queryForList("SELECT * FROM learner_course_state WHERE user_id=? AND course_id=?", user, course);
            long currentRevision = states.isEmpty() ? 0 : number(states.get(0).get("state_revision"));
            Map<String, Object> path;
            if (requestedVersion == null)
                path = latestPath(user, course);
            else {
                var rows = db.queryForList("SELECT * FROM learning_path WHERE user_id=? AND course_id=? AND path_version=?", user, course, requestedVersion);
                path = rows.isEmpty() ? null : rows.get(0);
            }
            if (path == null) return empty(currentVersion, currentRevision);
            boolean pending = !states.isEmpty() && (!"READY".equals(str(states.get(0).get("status")))
                    || db.queryForObject("SELECT COUNT(*) FROM event_consume_log WHERE user_id=? AND course_id=? AND status<>'SUCCEEDED'", Integer.class, user, course) > 0);
            boolean stale = pending || !currentVersion.equals(str(path.get("catalog_version")))
                    || currentRevision != number(path.get("state_revision")) || "INVALIDATED".equals(str(path.get("status")));
            return responseFromRow(path, currentVersion, currentRevision, stale, pending);
        });
    }

    /** Validates an attribution inside the caller's existing transaction. */
    public void validatePathAttribution(String user, String course, String item, String version,
                                        String kind, String nodeId) {
        if (nodeId == null) return;
        var rows = db.queryForList("SELECT n.*,p.user_id,p.course_id,p.catalog_version,p.status AS path_status FROM learning_path_node n JOIN learning_path p ON p.path_id=n.path_id WHERE n.node_id=? FOR UPDATE", nodeId);
        if (rows.isEmpty()) throw BusinessException.missing();
        var n = rows.get(0);
        if (!user.equals(str(n.get("user_id"))) || !course.equals(str(n.get("course_id")))) throw BusinessException.forbidden();
        if (!"ACTIVE".equals(str(n.get("path_status")))) throw conflict("学习路径已失效，请生成新路径");
        if (!version.equals(str(n.get("catalog_version"))) || !item.equals(str(n.get("item_id"))) || !kind.equals(str(n.get("item_type"))))
            throw conflict("路径节点与当前内容或版本不匹配");
        if (Set.of("COMPLETED", "SKIPPED").contains(str(n.get("status")))) throw conflict("路径节点已经完成");
        int blocked = db.queryForObject("SELECT COUNT(*) FROM learning_path_node WHERE path_id=? AND sequence_no<? AND status NOT IN ('COMPLETED','SKIPPED')", Integer.class, n.get("path_id"), n.get("sequence_no"));
        if (blocked != 0) throw conflict("请先完成路径中的前置节点");
        String current = db.queryForObject("SELECT catalog_version FROM course WHERE course_id=?", String.class, course);
        if (!version.equals(current)) throw conflict("课程目录已变化，请生成新路径");
    }

    public void markNodeStarted(String nodeId) {
        if (nodeId == null) return;
        db.update("UPDATE learning_path_node SET status='IN_PROGRESS',started_at=COALESCE(started_at,UTC_TIMESTAMP(6)) WHERE node_id=? AND status='PENDING'", nodeId);
    }

    /** Called only from LIVE event consumption after BKT has been written. */
    public void completeQuestionNode(String user, String course, String version, String nodeId,
                                     String eventId, long newRevision) {
        if (nodeId == null) { invalidateForExternalActivity(user, course, version, newRevision, "NON_PATH_ACTIVITY"); return; }
        var rows = db.queryForList("SELECT n.*,p.status AS path_status FROM learning_path_node n JOIN learning_path p ON p.path_id=n.path_id WHERE n.node_id=? AND p.user_id=? AND p.course_id=? FOR UPDATE", nodeId, user, course);
        if (rows.isEmpty()) throw BusinessException.missing();
        var n = rows.get(0); if (!"ACTIVE".equals(str(n.get("path_status")))) throw conflict("学习路径已失效，请生成新路径");
        if (!"QUESTION".equals(str(n.get("item_type")))) throw conflict("路径节点不是题目");
        if (Set.of("COMPLETED", "SKIPPED").contains(str(n.get("status")))) {
            if (eventId.equals(str(n.get("source_event_id")))) return;
            throw conflict("路径节点已经完成");
        }
        db.update("UPDATE learning_path_node SET status='COMPLETED',source_event_id=?,completed_at=UTC_TIMESTAMP(6) WHERE node_id=?", eventId, nodeId);
        String path = str(n.get("path_id"));
        var state = db.queryForList("SELECT mastery,evidence_count,evidence_weight FROM mastery_state WHERE user_id=? AND course_id=? AND catalog_version=? AND knowledge_id=? AND model_version=?", user, course, version, str(n.get("knowledge_id")), "bkt-" + course + "-" + version + "-param1");
        boolean adequate = !state.isEmpty() && ((Number) state.get(0).get("evidence_count")).intValue() >= 3 && decimal(state.get(0).get("evidence_weight")) >= 2 && decimal(state.get(0).get("mastery")) >= .6;
        if ("RETEST".equals(str(n.get("phase"))) && !adequate) {
            int round = ((Number) n.get("round_no")).intValue();
            if (round >= 2) {
                db.update("UPDATE learning_path SET status='BLOCKED',reason_code='NEEDS_REVIEW',reason_json=?,state_revision=?,updated_at=UTC_TIMESTAMP(6) WHERE path_id=?", "{\"reason\":\"第二轮复测后仍未达标\"}", newRevision, path);
            } else {
                db.update("UPDATE learning_path SET status='INVALIDATED',reason_code='RETEST_FAILED',state_revision=?,updated_at=UTC_TIMESTAMP(6) WHERE path_id=?", newRevision, path);
                queueReplan(user, course, version, newRevision, "RETEST_FAILED");
            }
        } else {
            db.update("UPDATE learning_path SET state_revision=?,updated_at=UTC_TIMESTAMP(6) WHERE path_id=?", newRevision, path);
            int open = db.queryForObject("SELECT COUNT(*) FROM learning_path_node WHERE path_id=? AND status NOT IN ('COMPLETED','SKIPPED')", Integer.class, path);
            if (open == 0) db.update("UPDATE learning_path SET status='COMPLETED',updated_at=UTC_TIMESTAMP(6) WHERE path_id=? AND status='ACTIVE'", path);
        }
    }

    /** Called only inside the successful resource VIEWED/COMPLETED transaction. */
    public void completeResourceNode(String user, String course, String version, String nodeId,
                                     String eventId, long newRevision, boolean completed) {
        if (nodeId == null) { invalidateForExternalActivity(user, course, version, newRevision, "NON_PATH_ACTIVITY"); return; }
        var rows = db.queryForList("SELECT n.*,p.status AS path_status FROM learning_path_node n JOIN learning_path p ON p.path_id=n.path_id WHERE n.node_id=? AND p.user_id=? AND p.course_id=? FOR UPDATE", nodeId, user, course);
        if (rows.isEmpty()) throw BusinessException.missing();
        var n = rows.get(0); if (!"ACTIVE".equals(str(n.get("path_status")))) throw conflict("学习路径已失效，请生成新路径");
        if (!"RESOURCE".equals(str(n.get("item_type")))) throw conflict("路径节点不是资源");
        if (completed) {
            if (!"IN_PROGRESS".equals(str(n.get("status")))) throw conflict("请先打开并阅读路径资源");
            db.update("UPDATE learning_path_node SET status='COMPLETED',source_resource_activity_id=?,completed_at=UTC_TIMESTAMP(6) WHERE node_id=? AND status='IN_PROGRESS'", eventId, nodeId);
        }
        else db.update("UPDATE learning_path_node SET status='IN_PROGRESS',started_at=COALESCE(started_at,UTC_TIMESTAMP(6)) WHERE node_id=? AND status='PENDING'", nodeId);
        db.update("UPDATE learning_path SET state_revision=?,updated_at=UTC_TIMESTAMP(6) WHERE path_id=?", newRevision, n.get("path_id"));
    }

    public void invalidateForExternalActivity(String user, String course, String version, long revision, String trigger) {
        var active = db.queryForList("SELECT path_id FROM learning_path WHERE user_id=? AND course_id=? AND status='ACTIVE' ORDER BY path_version DESC LIMIT 1", user, course);
        if (active.isEmpty()) return;
        String path = str(active.get(0).get("path_id"));
        db.update("UPDATE learning_path SET status='INVALIDATED',reason_code='STATE_CHANGED',state_revision=?,updated_at=UTC_TIMESTAMP(6) WHERE path_id=? AND status='ACTIVE'", revision, path);
        queueReplan(user, course, version, revision, trigger);
    }

    private void queueReplan(String user, String course, String version, long revision, String trigger) {
        String dedupe = hash(user + "|" + course + "|" + version + "|" + revision + "|" + trigger);
        db.update("INSERT IGNORE INTO path_replan_job(job_id,user_id,course_id,catalog_version,trigger_state_revision,trigger_type,dedupe_key) VALUES (?,?,?,?,?,?,?)", id(), user, course, version, revision, trigger, dedupe);
    }

    /** Drains path replan jobs independently from the learning-event worker. */
    public void drainReplans() {
        var jobs = db.queryForList("SELECT job_id,next_retry_at FROM path_replan_job WHERE status IN ('PENDING','FAILED') ORDER BY created_at LIMIT 20");
        for (var job : jobs) {
            if (job.get("next_retry_at") != null && time(job.get("next_retry_at")).isAfter(Instant.now())) continue;
            String jobId = str(job.get("job_id"));
            try { tx.executeWithoutResult(s -> processReplan(jobId)); }
            catch (RuntimeException ex) { markReplanFailure(jobId, ex); }
        }
    }

    public Map<String, Object> pathJob(Jwt jwt, String jobId) {
        if (!"ADMIN".equals(jwt.getClaimAsString("role"))) throw BusinessException.forbidden();
        var rows = db.queryForList("SELECT * FROM path_replan_job WHERE job_id=?", jobId);
        if (rows.isEmpty()) throw BusinessException.missing();
        return rows.get(0);
    }

    public Map<String, Object> retryPathJob(Jwt jwt, String jobId) {
        if (!"ADMIN".equals(jwt.getClaimAsString("role"))) throw BusinessException.forbidden();
        return tx.execute(t -> {
            var rows = db.queryForList("SELECT * FROM path_replan_job WHERE job_id=? FOR UPDATE", jobId);
            if (rows.isEmpty()) throw BusinessException.missing();
            var row = rows.get(0);
            if ("SUCCEEDED".equals(str(row.get("status")))) throw conflict("路径作业已经成功");
            db.update("UPDATE path_replan_job SET status='PENDING',attempt_count=0,next_retry_at=NULL,error_code=NULL,updated_at=UTC_TIMESTAMP(6) WHERE job_id=?", jobId);
            return Map.of("jobId", jobId, "status", "QUEUED");
        });
    }

    private void processReplan(String jobId) {
        var jobs = db.queryForList("SELECT * FROM path_replan_job WHERE job_id=? FOR UPDATE", jobId);
        if (jobs.isEmpty()) return;
        var job = jobs.get(0);
        if (!("PENDING".equals(str(job.get("status"))) || "FAILED".equals(str(job.get("status"))))) return;
        String user = str(job.get("user_id")), course = str(job.get("course_id")), version = str(job.get("catalog_version"));
        lockUser(user);
        var state = lockState(user, course);
        if (!version.equals(str(state.get("catalog_version")))) throw conflict("路径重规划等待目录迁移完成");
        ready(user, course, state);
        var base = input(user, course, version, state);
        Map<String, Integer> retry = Map.of();
        Set<String> used = Set.of();
        if ("RETEST_FAILED".equals(str(job.get("trigger_type")))) {
            var retrySkills = db.queryForList("SELECT n.knowledge_id FROM learning_path_node n JOIN learning_path p ON p.path_id=n.path_id WHERE p.user_id=? AND p.course_id=? AND p.catalog_version=? AND p.state_revision=? AND p.status='INVALIDATED' AND p.reason_code='RETEST_FAILED' AND n.phase='RETEST' AND n.round_no=1 AND n.status='COMPLETED'", user, course, version, job.get("trigger_state_revision"));
            var map = new LinkedHashMap<String, Integer>();
            var skillById = new HashMap<String, LearningPathPlanner.Skill>();
            for (var skill : base.skills()) skillById.put(skill.id(), skill);
            for (var row : retrySkills) {
                var skill = skillById.get(str(row.get("knowledge_id")));
                if (skill != null && !skill.adequate()) map.put(skill.id(), 2);
            }
            retry = map;
            var oldIds = new LinkedHashSet<String>();
            for (var node : base.existingNodes()) oldIds.add(node.itemId());
            used = oldIds;
        }
        var planned = LearningPathPlanner.plan(new LearningPathPlanner.Input(course, version, base.skills(), base.edges(), base.candidates(), base.existingNodes(), retry, used));
        String previous = latestPathId(user, course);
        int pathVersion = db.queryForObject("SELECT COALESCE(MAX(path_version),0)+1 FROM learning_path WHERE user_id=? AND course_id=?", Integer.class, user, course);
        String path = id(); Instant now = Instant.now().truncatedTo(ChronoUnit.MICROS);
        db.update("INSERT INTO learning_path(path_id,user_id,course_id,catalog_version,path_version,origin_state_revision,state_revision,planner_version,catalog_hash,status,reason_code,reason_json,previous_path_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                path, user, course, version, pathVersion, number(state.get("state_revision")), number(state.get("state_revision")), LearningPathPlanner.VERSION,
                hash(inputHash(base)), planned.status().name(), planned.reasonCode(), encodeReason(planned), previous, Timestamp.from(now), Timestamp.from(now));
        int sequence = 1;
        for (var node : planned.nodes()) {
            db.update("INSERT INTO learning_path_node(node_id,path_id,sequence_no,knowledge_id,item_type,item_id,phase,round_no,status,source_event_id,source_resource_activity_id,carried_from_node_id,reason_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    id(), path, sequence++, node.skillId(), node.itemType().name(), node.itemId(), node.phase().name(), node.roundNo(), node.status().name(), null,
                    null, node.carriedFromNodeId(), "{}", Timestamp.from(now));
        }
        db.update("UPDATE learning_path SET status='INVALIDATED',reason_code='STATE_CHANGED',updated_at=UTC_TIMESTAMP(6) WHERE user_id=? AND course_id=? AND status='ACTIVE' AND path_id<>?", user, course, path);
        db.update("UPDATE path_replan_job SET status='SUCCEEDED',result_path_id=?,error_code=NULL,next_retry_at=NULL,updated_at=UTC_TIMESTAMP(6) WHERE job_id=?", path, jobId);
    }

    private void markReplanFailure(String jobId, RuntimeException ex) {
        try {
            tx.executeWithoutResult(s -> {
                var rows = db.queryForList("SELECT attempt_count FROM path_replan_job WHERE job_id=? FOR UPDATE", jobId);
                if (rows.isEmpty()) return;
                int attempt = ((Number) rows.get(0).get("attempt_count")).intValue() + 1;
                int[] delays = {5, 15, 60, 300, 900};
                boolean terminal = attempt >= 5;
                db.update("UPDATE path_replan_job SET status=?,attempt_count=?,next_retry_at=?,error_code=?,updated_at=UTC_TIMESTAMP(6) WHERE job_id=?",
                        terminal ? "NEEDS_ATTENTION" : "FAILED", attempt, terminal ? null : Timestamp.from(Instant.now().plusSeconds(delays[Math.min(attempt - 1, delays.length - 1)])),
                        ex.getClass().getSimpleName(), jobId);
            });
        } catch (RuntimeException ignored) { /* leave the job for the next worker pass */ }
    }

    private Map<String, Object> empty(String currentVersion, long revision) {
        var meta = new LinkedHashMap<String, Object>();
        meta.put("catalogVersion", currentVersion); meta.put("stateRevision", revision); meta.put("asOf", Instant.now().toString());
        meta.put("stale", false); meta.put("status", "INSUFFICIENT");
        var result = new LinkedHashMap<String, Object>(); result.put("meta", meta); result.put("generated", false);
        result.put("pathId", null); result.put("pathVersion", 0); result.put("plannerVersion", LearningPathPlanner.VERSION);
        result.put("originStateRevision", revision); result.put("status", null); result.put("reasonCode", "NOT_GENERATED");
        result.put("nodes", List.of()); result.put("reason", "尚未生成学习路径"); return result;
    }

    private Map<String, Object> response(String user, String course, String version, Map<String, Object> state,
                                         String path, int pathVersion, LearningPathPlanner.Result planned,
                                         Instant now, boolean stale) {
        var meta = new LinkedHashMap<String, Object>(); meta.put("catalogVersion", version);
        meta.put("stateRevision", number(state.get("state_revision"))); meta.put("asOf", now.toString());
        meta.put("stale", stale); meta.put("status", "READY");
        var result = new LinkedHashMap<String, Object>(); result.put("meta", meta); result.put("generated", true);
        result.put("pathId", path); result.put("pathVersion", pathVersion); result.put("plannerVersion", LearningPathPlanner.VERSION);
        result.put("originStateRevision", number(state.get("state_revision"))); result.put("status", planned.status().name());
        result.put("reasonCode", planned.reasonCode()); result.put("reason", planned.reason());
        result.put("nodes", planned.nodes().stream().map(n -> {
            var item = new LinkedHashMap<String, Object>(); item.put("nodeId", null); item.put("sequenceNo", null);
            item.put("knowledgeId", n.skillId()); item.put("itemType", n.itemType().name()); item.put("itemId", n.itemId());
            item.put("title", n.title()); item.put("phase", n.phase().name()); item.put("roundNo", n.roundNo());
            item.put("status", n.status().name()); item.put("actionable", false); item.put("available", true);
            item.put("sourceEventId", null); item.put("carriedFromNodeId", n.carriedFromNodeId()); item.put("reason", null); return item;
        }).toList());
        return result;
    }

    private Map<String, Object> responseFromRow(Map<String, Object> path, String currentVersion,
                                                long currentRevision, boolean stale, boolean pending) {
        var meta = new LinkedHashMap<String, Object>(); meta.put("catalogVersion", currentVersion);
        meta.put("stateRevision", currentRevision); meta.put("asOf", time(path.get("updated_at")).toString());
        meta.put("stale", stale); meta.put("status", pending ? "PROCESSING" : stale ? "FAILED" : "READY");
        var result = new LinkedHashMap<String, Object>(); result.put("meta", meta); result.put("generated", true);
        result.put("pathId", path.get("path_id")); result.put("pathVersion", number(path.get("path_version")));
        result.put("plannerVersion", path.get("planner_version")); result.put("originStateRevision", number(path.get("origin_state_revision")));
        result.put("status", path.get("status")); result.put("reasonCode", path.get("reason_code"));
        result.put("reason", reasonText(path));
        var nodes = db.queryForList("SELECT * FROM learning_path_node WHERE path_id=? ORDER BY sequence_no", path.get("path_id"));
        int firstOpen = -1;
        for (int i = 0; i < nodes.size(); i++) if (!Set.of("COMPLETED", "SKIPPED").contains(str(nodes.get(i).get("status")))) { firstOpen = i; break; }
        var output = new ArrayList<Map<String, Object>>();
        for (int i = 0; i < nodes.size(); i++) {
            var n = nodes.get(i); String type = str(n.get("item_type")), itemId = str(n.get("item_id"));
            var item = new LinkedHashMap<String, Object>(); item.put("nodeId", n.get("node_id")); item.put("sequenceNo", number(n.get("sequence_no")));
            item.put("knowledgeId", n.get("knowledge_id")); item.put("itemType", type); item.put("itemId", itemId);
            item.put("title", title(type, itemId, str(path.get("catalog_version")))); item.put("phase", n.get("phase")); item.put("roundNo", number(n.get("round_no")));
            item.put("status", n.get("status")); item.put("actionable", !stale && i == firstOpen); item.put("available", available(type, itemId, currentVersion));
            item.put("sourceEventId", "RESOURCE".equals(type) ? n.get("source_resource_activity_id") : n.get("source_event_id"));
            item.put("carriedFromNodeId", n.get("carried_from_node_id")); item.put("reason", reasonNode(n)); output.add(item);
        }
        result.put("nodes", output); return result;
    }

    private String reasonText(Map<String, Object> path) {
        String code = str(path.get("reason_code")); if (code == null) return null;
        return switch (code) { case "NO_CONTENT" -> "路径内容不足，无法安全安排复测"; case "NEEDS_REVIEW" -> "两轮复测后仍未达到掌握与证据门槛，请人工复核";
            case "STATE_CHANGED" -> "学习状态已变化，路径已失效"; case "CATALOG_CHANGED" -> "课程目录已变化，路径已失效"; default -> code; };
    }
    private String reasonNode(Map<String, Object> node) { return node.get("reason_json") == null ? null : str(node.get("reason_json")); }
    private String title(String type, String item, String version) {
        var rows = "RESOURCE".equals(type)
                ? db.queryForList("SELECT title FROM resource_snapshot WHERE resource_id=? AND catalog_version=?", item, version)
                : db.queryForList("SELECT stem FROM question_snapshot WHERE question_id=? AND catalog_version=?", item, version);
        return rows.isEmpty() ? item : str(rows.get(0).values().iterator().next());
    }
    private boolean available(String type, String item, String currentVersion) {
        var rows = "RESOURCE".equals(type)
                ? db.queryForList("SELECT 1 FROM resource r JOIN course c ON c.course_id=r.course_id WHERE r.resource_id=? AND r.status='ACTIVE' AND c.catalog_version=?", item, currentVersion)
                : db.queryForList("SELECT 1 FROM question q JOIN course c ON c.course_id=q.course_id WHERE q.question_id=? AND q.status='ACTIVE' AND c.catalog_version=?", item, currentVersion);
        return !rows.isEmpty();
    }

    private LearningPathPlanner.Input input(String user, String course, String version, Map<String, Object> state) {
        return input(user, course, version, state, Map.of(), Set.of());
    }

    private LearningPathPlanner.Input input(String user, String course, String version, Map<String, Object> state,
                                            Map<String, Integer> nextRoundBySkill, Set<String> usedItemIds) {
        var skills = new ArrayList<LearningPathPlanner.Skill>();
        var rows = db.queryForList("SELECT * FROM knowledge_snapshot WHERE course_id=? AND catalog_version=? ORDER BY sort_order,knowledge_id", course, version);
        for (var k : rows) {
            String kid = str(k.get("knowledge_id")); String model = "bkt-" + course + "-" + version + "-param1";
            var mastery = db.queryForList("SELECT mastery,evidence_count,evidence_weight FROM mastery_state WHERE user_id=? AND course_id=? AND knowledge_id=? AND catalog_version=? AND model_version=?", user, course, kid, version, model);
            var recent = db.queryForList("SELECT i.correct,ik.normalized_weight FROM learning_interaction i JOIN interaction_knowledge ik ON ik.interaction_id=i.interaction_id WHERE i.user_id=? AND i.course_id=? AND i.catalog_version=? AND ik.knowledge_id=? ORDER BY i.occurred_at DESC,i.event_seq DESC LIMIT 20", user, course, version, kid);
            double weight = 0, errors = 0; for (var r : recent) { double w = decimal(r.get("normalized_weight")); weight += w; if (!truth(r.get("correct"))) errors += w; }
            var m = mastery.isEmpty() ? null : mastery.get(0);
            skills.add(new LearningPathPlanner.Skill(kid, str(k.get("name")), ((Number) k.get("sort_order")).intValue(),
                    m == null ? .2 : decimal(m.get("mastery")), m == null ? 0 : ((Number) m.get("evidence_count")).intValue(),
                    m == null ? 0 : decimal(m.get("evidence_weight")), weight == 0 ? null : errors / weight));
        }
        var edges = db.queryForList("SELECT source_knowledge_id,target_knowledge_id FROM prerequisite_snapshot WHERE course_id=? AND catalog_version=? ORDER BY source_knowledge_id,target_knowledge_id", course, version)
                .stream().map(e -> new LearningPathPlanner.Edge(str(e.get("source_knowledge_id")), str(e.get("target_knowledge_id")))).toList();
        var candidates = new ArrayList<LearningPathPlanner.Candidate>();
        for (var r : db.queryForList("SELECT s.resource_id,s.knowledge_id,s.title,s.difficulty,s.sort_order,r.status FROM resource_snapshot s JOIN resource r ON r.resource_id=s.resource_id AND r.course_id=s.course_id JOIN course c ON c.course_id=s.course_id AND c.catalog_version=s.catalog_version WHERE s.course_id=? AND s.catalog_version=? ORDER BY s.sort_order,s.resource_id", course, version))
            candidates.add(new LearningPathPlanner.Candidate(str(r.get("resource_id")), course, LearningPathPlanner.ItemType.RESOURCE, str(r.get("knowledge_id")), str(r.get("title")), "ACTIVE".equals(str(r.get("status"))), decimal(r.get("difficulty")), ((Number) r.get("sort_order")).intValue()));
        for (var q : db.queryForList("SELECT s.question_id,s.knowledge_id,s.stem,s.difficulty,s.sort_order,q.status FROM question_snapshot s JOIN question q ON q.question_id=s.question_id AND q.course_id=s.course_id JOIN course c ON c.course_id=s.course_id AND c.catalog_version=s.catalog_version WHERE s.course_id=? AND s.catalog_version=? ORDER BY s.sort_order,s.question_id", course, version))
            candidates.add(new LearningPathPlanner.Candidate(str(q.get("question_id")), course, LearningPathPlanner.ItemType.QUESTION, str(q.get("knowledge_id")), str(q.get("stem")), "ACTIVE".equals(str(q.get("status"))), decimal(q.get("difficulty")), ((Number) q.get("sort_order")).intValue()));
        var existing = new ArrayList<LearningPathPlanner.ExistingNode>();
        String latest = latestPathId(user, course);
        if (latest != null) for (var n : db.queryForList("SELECT node_id,knowledge_id,item_type,item_id,phase,round_no,status FROM learning_path_node WHERE path_id=?", latest))
            existing.add(new LearningPathPlanner.ExistingNode(str(n.get("node_id")), str(n.get("knowledge_id")), LearningPathPlanner.ItemType.valueOf(str(n.get("item_type"))), str(n.get("item_id")), LearningPathPlanner.Phase.valueOf(str(n.get("phase"))), ((Number) n.get("round_no")).intValue(), LearningPathPlanner.NodeStatus.valueOf(str(n.get("status")))));
        return new LearningPathPlanner.Input(course, version, skills, edges, candidates, existing, nextRoundBySkill, usedItemIds);
    }

    private String inputHash(LearningPathPlanner.Input input) {
        return input.courseId() + "|" + input.catalogVersion() + "|" + input.skills().stream().map(s -> s.id() + ":" + s.mastery() + ":" + s.evidenceCount()).sorted().toList()
                + "|" + input.edges().stream().map(e -> e.prerequisite() + "->" + e.target()).sorted().toList()
                + "|" + input.candidates().stream().map(c -> c.type() + ":" + c.id() + ":" + c.active()).sorted().toList();
    }
    private String encodeReason(LearningPathPlanner.Result planned) {
        try { return json.writeValueAsString(Map.of("reason", planned.reason() == null ? "" : planned.reason(), "missing", planned.missing())); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
    private JsonNode parse(Object value) { try { return json.readTree(str(value)); } catch (Exception e) { throw new IllegalStateException(e); } }
    private String encode(Object value) { try { return json.writeValueAsString(value); } catch (Exception e) { throw new IllegalStateException(e); } }
    private Object replay(String user, String key, Object body) {
        var rows = db.queryForList("SELECT response_json,payload_hash FROM learning_path_request WHERE user_id=? AND operation='GENERATE' AND idempotency_key=?", user, key);
        if (rows.isEmpty()) return null;
        if (!hash(encode(body)).equals(str(rows.get(0).get("payload_hash")))) throw conflict("同一请求编号不能用于不同课程");
        return parse(rows.get(0).get("response_json"));
    }
    private Object remember(String user, String key, Object body, Object result) {
        db.update("INSERT INTO learning_path_request(user_id,operation,idempotency_key,payload_hash,response_json) VALUES (?,?,?, ?,?)", user, "GENERATE", key, hash(encode(body)), encode(result));
        return result;
    }
    private void lockUser(String user) { db.queryForObject("SELECT user_id FROM local_user WHERE user_id=? FOR UPDATE", String.class, user); }
    private Map<String, Object> lockState(String user, String course) { return db.queryForMap("SELECT * FROM learner_course_state WHERE user_id=? AND course_id=? FOR UPDATE", user, course); }
    private void ensureState(String user, String course, String version) { db.update("INSERT IGNORE INTO learner_course_state(user_id,course_id,catalog_version) VALUES (?,?,?)", user, course, version); }
    private void ready(String user, String course, Map<String, Object> state) {
        if (!"READY".equals(str(state.get("status"))) || db.queryForObject("SELECT COUNT(*) FROM event_consume_log WHERE user_id=? AND course_id=? AND status<>'SUCCEEDED'", Integer.class, user, course) > 0)
            throw conflict("学习状态正在处理或待修复，请稍后刷新再生成路径");
    }
    private String latestPathId(String user, String course) {
        var rows = db.queryForList("SELECT path_id FROM learning_path WHERE user_id=? AND course_id=? ORDER BY path_version DESC LIMIT 1", user, course);
        return rows.isEmpty() ? null : str(rows.get(0).get("path_id"));
    }
    private Map<String, Object> latestPath(String user, String course) {
        var rows = db.queryForList("SELECT * FROM learning_path WHERE user_id=? AND course_id=? ORDER BY path_version DESC LIMIT 1", user, course);
        return rows.isEmpty() ? null : rows.get(0);
    }
    private void readAccess(Jwt jwt, String user, String course) {
        if (!jwt.getSubject().equals(user) && !"ADMIN".equals(jwt.getClaimAsString("role"))) throw BusinessException.forbidden();
        access.require(jwt, course);
    }
    private static boolean truth(Object value) { return value instanceof Boolean b ? b : ((Number) value).intValue() != 0; }
}
