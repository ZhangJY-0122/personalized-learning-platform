"""Static contract phase for the real G4 isolation runner."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
spec = json.loads((ROOT / "docs/openapi.json").read_text())
assert spec["info"]["version"] == "0.5.0"
for path in ("/students/{userId}/learning-path", "/students/{userId}/learning-path/generate", "/admin/catalog-imports", "/admin/event-imports", "/admin/import-jobs/{jobId}", "/admin/path-jobs/{jobId}/retry"):
    assert path in spec["paths"], path
status_schema = spec["components"]["schemas"]["Path"]["properties"]["status"]
assert status_schema["type"] == ["string", "null"]
assert status_schema["enum"] == ["ACTIVE", "COMPLETED", "INVALIDATED", "BLOCKED"]
assert spec["components"]["schemas"]["Health"]["properties"]["stage"]["enum"] == ["G4_PATHS"]
migration = (ROOT / "server/src/main/resources/db/migration/V6__Learning_paths_catalog_import_and_recovery.sql").read_text()
for table in ("learning_path", "learning_path_node", "path_replan_job", "import_job", "import_error", "rebuild_job", "catalog_knowledge_map"):
    assert f"CREATE TABLE {table}" in migration, table
for column in ("path_node_id", "import_job_id", "trace_id", "source_event_seq"):
    assert column in migration, column
assert (ROOT / "server/src/main/java/edu/f21/importing/CatalogImportService.java").exists()
assert (ROOT / "server/src/main/java/edu/f21/importing/EventImportService.java").exists()
v7 = (ROOT / "server/src/main/resources/db/migration/V7__G4_attribution_and_snapshot_order.sql").read_text()
for needle in ("source_resource_activity_id", "fk_learning_path_node_resource_activity", "question_snapshot", "sort_order"):
    assert needle in v7, needle
catalog = (ROOT / "server/src/main/java/edu/f21/importing/CatalogImportService.java").read_text()
assert "source_resource_activity_id" not in migration
assert "question_snapshot(question_id,course_id,catalog_version,knowledge_id,question_type,stem,options_json,answer_json,difficulty,sort_order)" in catalog
events = (ROOT / "server/src/main/java/edu/f21/importing/EventImportService.java").read_text()
assert 'jobType,"EVENT_JSON"' not in events and '"EVENT_CSV"' in events
planner = (ROOT / "server/src/main/java/edu/f21/learningpath/LearningPathPlanner.java").read_text()
assert "JdbcTemplate" not in planner and "@Service" not in planner and "Instant.now" not in planner
print("G4 static contract assertions passed")
