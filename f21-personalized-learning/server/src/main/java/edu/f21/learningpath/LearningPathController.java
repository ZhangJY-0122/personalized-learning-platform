package edu.f21.learningpath;

import edu.f21.common.Api;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import java.util.UUID;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1")
public class LearningPathController {
    private final LearningPathService service;
    public LearningPathController(LearningPathService service) { this.service = service; }

    public record Generate(@NotNull UUID courseId) {}

    @GetMapping("/students/{userId}/learning-path")
    public Object get(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID userId,
                      @RequestParam UUID courseId, @RequestParam(required = false) Integer pathVersion) {
        if (pathVersion != null && pathVersion < 1) throw new edu.f21.common.BusinessException(400, "INVALID_ARGUMENT", "pathVersion 必须大于 0");
        return Api.ok(service.get(jwt, userId.toString(), courseId.toString(), pathVersion));
    }

    @PostMapping("/students/{userId}/learning-path/generate")
    public Object generate(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID userId,
                           @RequestHeader("Idempotency-Key") UUID key, @Valid @RequestBody Generate body) {
        return Api.ok(service.generate(jwt, userId.toString(), body.courseId().toString(), key));
    }
}
