package edu.f21;
import java.util.Map;
import edu.f21.common.Api;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;
@SpringBootApplication
@RestController
public class Application {
 private final JdbcTemplate jdbc;
 public Application(JdbcTemplate jdbc) { this.jdbc=jdbc; }
 public static void main(String[] args) { SpringApplication.run(Application.class,args); }
 @GetMapping("/api/v1/health")
 public Map<String,Object> health() {
  int rows=jdbc.queryForObject("SELECT COUNT(*) FROM course",Integer.class);
  return Api.ok(Map.of("status","UP","database","UP","stage","G4_PATHS","catalogRows",rows));
 }
}
