import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from scripts.build_g5_demo_catalogs import (
    COURSES,
    JAVA_COURSE_ID,
    JAVA_LEGACY_KNOWLEDGE,
    JAVA_LEGACY_VERSION,
    ROOT,
    build_all,
    manifest_for,
    pretty_bytes,
    validate_catalog,
    write_build,
)


class G5DemoCatalogTest(unittest.TestCase):
    def test_generation_is_deterministic(self):
        first = build_all()
        second = build_all()
        self.assertEqual(pretty_bytes(first), pretty_bytes(second))

    def test_three_course_scope_and_counts(self):
        catalogs = build_all()
        self.assertEqual({"java", "data-structures", "computer-networks"}, set(catalogs))
        for catalog in catalogs.values():
            self.assertEqual("SYNTHETIC_DEMO", catalog["dataType"])
            self.assertEqual(15, len(catalog["knowledgePoints"]))
            self.assertEqual(20, len(catalog["resources"]))
            self.assertEqual(75, len(catalog["questions"]))

    def test_every_knowledge_has_resource_and_all_learning_phases(self):
        for catalog in build_all().values():
            report = validate_catalog(catalog)
            self.assertTrue(report["passed"], report["gaps"])
            self.assertEqual(15, report["coverage"]["knowledgeWithResource"])
            self.assertEqual(15, report["coverage"]["knowledgeWithFiveQuestions"])
            self.assertEqual(15, report["coverage"]["knowledgeWithBasicAdvancedRetest"])

    def test_missing_content_and_cycle_are_rejected(self):
        catalog = deepcopy(build_all()["data-structures"])
        first = catalog["knowledgePoints"][0]["knowledgeId"]
        catalog["resources"] = [x for x in catalog["resources"] if x["knowledgeId"] != first]
        report = validate_catalog(catalog)
        self.assertFalse(report["passed"])
        self.assertTrue(any("lacks resource" in gap for gap in report["gaps"]))

        cyclic = deepcopy(build_all()["computer-networks"])
        cyclic["prerequisites"].append({
            "courseId": cyclic["course"]["courseId"],
            "sourceKnowledgeId": cyclic["knowledgePoints"][-1]["knowledgeId"],
            "targetKnowledgeId": cyclic["knowledgePoints"][0]["knowledgeId"],
        })
        report = validate_catalog(cyclic)
        self.assertFalse(report["passed"])
        self.assertIn("prerequisite graph contains a cycle", report["gaps"])

    def test_uuid_reuse_across_entity_types_is_rejected(self):
        catalog = deepcopy(build_all()["java"])
        catalog["resources"][0]["resourceId"] = catalog["questions"][0]["questionId"]
        report = validate_catalog(catalog)
        self.assertFalse(report["passed"])
        self.assertIn("UUID reused across entity types", report["gaps"])

    def test_dags_and_global_fixed_uuids(self):
        manifest = manifest_for(build_all())
        values = []
        for course in manifest["courses"]:
            values.append(course["courseId"])
            for ids in course["fixedUuids"].values():
                values.extend(ids)
        self.assertEqual(len(values), len(set(values)))
        self.assertTrue(all(validate_catalog(catalog)["dag"] for catalog in build_all().values()))

    def test_java_expands_same_course_with_new_version(self):
        java = build_all()["java"]
        self.assertEqual(JAVA_COURSE_ID, java["course"]["courseId"])
        self.assertNotEqual(JAVA_LEGACY_VERSION, java["course"]["catalogVersion"])
        actual = {x["knowledgeId"] for x in java["knowledgePoints"]}
        self.assertTrue(set(JAVA_LEGACY_KNOWLEDGE).issubset(actual))

    def test_committed_catalogs_match_generator_and_manifest(self):
        generated = build_all()
        for slug, catalog in generated.items():
            path = ROOT / f"data/demo/catalogs/{slug}.json"
            self.assertEqual(pretty_bytes(catalog), path.read_bytes())
        self.assertEqual(pretty_bytes(manifest_for(generated)), (ROOT / "data/demo/catalogs/manifest.json").read_bytes())

    def test_build_can_write_complete_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            migration = root / "server/src/main/resources/db/migration/V3__Demo_catalog.sql"
            migration.parent.mkdir(parents=True)
            migration.write_text("legacy-java-snapshot\n")
            manifest = write_build(root)
            self.assertEqual(3, len(manifest["courses"]))
            for course in COURSES:
                self.assertTrue((root / f"data/demo/catalogs/{course['slug']}.json").exists())


if __name__ == "__main__":
    unittest.main()
