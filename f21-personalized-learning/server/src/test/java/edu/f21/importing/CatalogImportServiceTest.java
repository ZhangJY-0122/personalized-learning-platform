package edu.f21.importing;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.ObjectMapper;
import edu.f21.common.BusinessException;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;

class CatalogImportServiceTest {
    private static final String COURSE = "7ab3489a-8a95-4d8c-85c8-71907a81c028";

    private static String body(String mode) {
        return """
            {"publishMode":"%s","course":{"courseId":"%s","catalogVersion":"demo-v1","title":"New course","description":"Synthetic"},"chapters":[],"knowledgePoints":[],"resources":[],"questions":[],"prerequisites":[]}
            """.formatted(mode, COURSE);
    }

    @Test
    void activeImportCanCreateANewCourseAtomically() throws Exception {
        var db = mock(JdbcTemplate.class);
        when(db.queryForList(startsWith("SELECT catalog_version FROM course"), anyString())).thenReturn(List.of());
        when(db.queryForList(startsWith("SELECT 1 FROM catalog_snapshot"), anyString(), anyString())).thenReturn(List.of());
        when(db.queryForList(startsWith("SELECT user_id FROM learner_course_state"), anyString())).thenReturn(List.of());
        var service = new CatalogImportService(db, new ObjectMapper(), mock(PlatformTransactionManager.class));
        var body = new ObjectMapper().readTree(body("ACTIVATE"));

        service.publish(null, body, "job-1");

        verify(db).update(
            "INSERT INTO course(course_id,title,description,catalog_version) VALUES (?,?,?,?)",
            COURSE, "New course", "Synthetic", "demo-v1");
    }

    @Test
    void archiveOnlyCannotCreateAnEmptyNewCourse() throws Exception {
        var db = mock(JdbcTemplate.class);
        when(db.queryForList(startsWith("SELECT catalog_version FROM course"), anyString())).thenReturn(List.of());
        var service = new CatalogImportService(db, new ObjectMapper(), mock(PlatformTransactionManager.class));

        assertThrows(BusinessException.class, () -> service.publish(null, new ObjectMapper().readTree(body("ARCHIVE_ONLY")), "job-2"));

        verify(db, never()).update(startsWith("INSERT INTO course"), any(), any(), any(), any());
    }
}
