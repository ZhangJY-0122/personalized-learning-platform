package edu.f21.practice;
import com.fasterxml.jackson.databind.JsonNode;
import edu.f21.common.Api;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.util.UUID;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1")
public class PracticeController {
 private final PracticeService service;
 public PracticeController(PracticeService service){this.service=service;}
 public record Submission(@NotNull UUID questionId,@NotNull JsonNode answer,@NotBlank @Size(max=64) String catalogVersion,UUID recommendationId,UUID pathNodeId){}
 @PostMapping("/practice/submissions")
 public Object submit(@AuthenticationPrincipal Jwt jwt,@RequestHeader("Idempotency-Key") UUID key,@Valid @RequestBody Submission body){
  return Api.ok(service.submit(jwt,key,body.questionId(),body.answer(),body.catalogVersion(),body.recommendationId(),body.pathNodeId()));
 }
 @GetMapping("/practice/submissions/{submissionId}")
 public Object result(@AuthenticationPrincipal Jwt jwt,@PathVariable UUID submissionId){return Api.ok(service.submission(jwt,submissionId));}
 @GetMapping("/students/{userId}/practice-history")
 public Object history(@AuthenticationPrincipal Jwt jwt,@PathVariable UUID userId,@RequestParam UUID courseId,@RequestParam(defaultValue="1") int page,@RequestParam(defaultValue="20") int pageSize){
  return Api.ok(service.history(jwt,userId.toString(),courseId.toString(),page,pageSize));
 }
 @GetMapping("/students/{userId}/mastery")
 public Object mastery(@AuthenticationPrincipal Jwt jwt,@PathVariable UUID userId,@RequestParam UUID courseId){return Api.ok(service.mastery(jwt,userId.toString(),courseId.toString()));}
 @GetMapping("/students/{userId}/profile")
 public Object profile(@AuthenticationPrincipal Jwt jwt,@PathVariable UUID userId,@RequestParam UUID courseId){return Api.ok(service.profile(jwt,userId.toString(),courseId.toString()));}
 @PostMapping("/admin/events/{eventId}/retry")
 public Object retry(@AuthenticationPrincipal Jwt jwt,@PathVariable UUID eventId){return Api.ok(service.retry(jwt,eventId.toString()));}
 @PostMapping("/admin/students/{userId}/rebuild")
 public Object rebuild(@AuthenticationPrincipal Jwt jwt,@PathVariable UUID userId,@RequestParam UUID courseId){return Api.ok(service.rebuild(jwt,userId.toString(),courseId.toString()));}
}
