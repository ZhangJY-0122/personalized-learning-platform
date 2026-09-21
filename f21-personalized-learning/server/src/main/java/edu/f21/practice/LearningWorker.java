package edu.f21.practice;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.*;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.slf4j.LoggerFactory;

@Configuration
@EnableScheduling
@ConditionalOnProperty(name="app.worker.enabled",havingValue="true",matchIfMissing=true)
public class LearningWorker {
 private final PracticeService service;
 private final JdbcTemplate db;
 public LearningWorker(PracticeService service,JdbcTemplate db){this.service=service;this.db=db;}
 @EventListener(ApplicationReadyEvent.class)
 public void recoverInterruptedRebuild(){
  db.update("UPDATE learner_course_state SET status='REBUILD_REQUIRED' WHERE status='REBUILDING'");
 }
 @Scheduled(fixedDelayString="${app.worker.delay-ms:250}",initialDelay=2000)
 public void tick(){
  try{service.drain();}catch(Exception ex){LoggerFactory.getLogger(LearningWorker.class).warn("Learning worker unavailable: {}",ex.getClass().getSimpleName());}
 }
}
