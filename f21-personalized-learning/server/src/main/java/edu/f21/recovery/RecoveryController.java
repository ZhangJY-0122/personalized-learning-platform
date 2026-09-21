package edu.f21.recovery;

import edu.f21.common.Api;
import edu.f21.common.BusinessException;
import edu.f21.learningpath.LearningPathService;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

/** Read-only admin recovery views plus durable path-job retry. */
@RestController
@RequestMapping("/api/v1/admin")
public class RecoveryController {
    private final JdbcTemplate db;
    private final LearningPathService paths;

    public RecoveryController(JdbcTemplate db, LearningPathService paths) { this.db = db; this.paths = paths; }
    private static void admin(Jwt jwt) { if (!"ADMIN".equals(jwt.getClaimAsString("role"))) throw BusinessException.forbidden(); }

    @GetMapping("/events")
    public Object events(@AuthenticationPrincipal Jwt jwt, @RequestParam(required = false) String status,
                         @RequestParam(defaultValue = "1") int page, @RequestParam(defaultValue = "20") int pageSize) {
        admin(jwt); if (page < 1 || pageSize < 1 || pageSize > 100) throw new BusinessException(400, "INVALID_ARGUMENT", "分页参数超出范围");
        String filter = status == null || status.isBlank() ? "" : " AND status=?";
        var args = new ArrayList<Object>(); if (!filter.isEmpty()) args.add(status);
        int total = db.queryForObject("SELECT COUNT(*) FROM event_consume_log WHERE 1=1" + filter, Integer.class, args.toArray());
        args.add(pageSize); args.add((page - 1) * pageSize);
        var rows = db.queryForList("SELECT event_id AS eventId,user_id AS userId,course_id AS courseId,status,retry_count AS retryCount,error_code AS errorCode,trace_id AS traceId,occurred_at AS occurredAt FROM event_consume_log WHERE 1=1" + filter + " ORDER BY occurred_at,event_seq,event_id LIMIT ? OFFSET ?", args.toArray());
        return Api.ok(Map.of("items", rows, "page", page, "pageSize", pageSize, "total", total));
    }

    @GetMapping("/path-jobs/{jobId}")
    public Object pathJob(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID jobId) { return Api.ok(paths.pathJob(jwt, jobId.toString())); }

    @PostMapping("/path-jobs/{jobId}/retry")
    public Object retryPathJob(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID jobId) { return Api.ok(paths.retryPathJob(jwt, jobId.toString())); }

    @GetMapping("/rebuild-jobs/{jobId}")
    public Object rebuildJob(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID jobId) {
        admin(jwt); var rows = db.queryForList("SELECT * FROM rebuild_job WHERE job_id=?", jobId.toString());
        if (rows.isEmpty()) throw BusinessException.missing(); return Api.ok(rows.get(0));
    }

    @GetMapping("/import-jobs/{jobId}")
    public Object importJob(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID jobId) {
        admin(jwt); var rows = db.queryForList("SELECT * FROM import_job WHERE job_id=?", jobId.toString());
        if (rows.isEmpty()) throw BusinessException.missing();
        var errors = db.queryForList("SELECT row_no AS rowNo,field_path AS fieldPath,error_code AS errorCode,message FROM import_error WHERE job_id=? ORDER BY row_no,field_path", jobId.toString());
        var result = new LinkedHashMap<String,Object>(rows.get(0)); result.put("errors", errors); return Api.ok(result);
    }
}
