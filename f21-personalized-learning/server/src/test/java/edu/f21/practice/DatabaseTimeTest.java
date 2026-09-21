package edu.f21.practice;
import static org.junit.jupiter.api.Assertions.*;
import java.time.*;
import java.sql.Timestamp;
import org.junit.jupiter.api.Test;
public class DatabaseTimeTest {
 @Test void handlesBothJdbcDatetimeRepresentations(){
  var time=LocalDateTime.of(2026,9,10,1,2,3);
  assertEquals("2026-09-10T01:02:03Z",PracticeService.time(time));
  assertEquals(PracticeService.time(time),PracticeService.time(Timestamp.from(time.toInstant(ZoneOffset.UTC))));
 }
}
