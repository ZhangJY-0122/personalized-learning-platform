package edu.f21.dashboard;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.HexFormat;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class DashboardArtifactServiceTest {
 @TempDir Path root;
 private final ObjectMapper json=new ObjectMapper();
 @Test void verifiedRegistryBuildsOnlySafeSummaries() throws Exception {
  prepare();var service=new DashboardArtifactService(json,root);
  var models=service.models(1,20);assertEquals(3,models.get("total"));
  var model=(Map<?,?>)((List<?>)models.get("items")).get(2);assertEquals(List.of(11,22,33),model.get("seeds"));assertFalse(model.toString().contains("userIds"));
  var experiments=service.experiments(1,20);assertEquals(3,experiments.get("total"));assertTrue(service.models(2,20).get("items") instanceof List<?> list&&list.isEmpty());
 }
 @Test void changedArtifactFailsClosed() throws Exception {
  prepare();Files.writeString(root.resolve("artifacts/g5-kt-comparison.json"),"{}",StandardOpenOption.TRUNCATE_EXISTING);
  assertThrows(IllegalStateException.class,()->new DashboardArtifactService(json,root));
 }
 private void prepare() throws Exception {
  Files.createDirectories(root.resolve("artifacts"));Files.createDirectories(root.resolve("data/manifests"));
  var payloads=new LinkedHashMap<String,String>();
  payloads.put("g5-kt-comparison","""
   {"passed":true,"schemaVersion":"g5-kt-comparison-v2","verifiedAt":"2026-09-18T00:00:00Z","limitations":[],"targetSet":{"count":9},"tuning":{"selectedConfig":{"configId":"h64"}},"models":{"RULE_RECENT20_BETA11_V1":{"samples":9,"auc":0.6,"accuracy":0.6,"rmse":0.4,"logLoss":0.5},"BKT_G5_FROZEN_SKILL_SPECIFIC_FALLBACK_V1":{"samples":9,"auc":0.7,"accuracy":0.7,"rmse":0.3,"logLoss":0.4},"DKT_LSTM_V1":{"seeds":[{"seed":11},{"seed":22},{"seed":33}],"meanStd":{"auc":{"mean":0.8,"std":0.01},"accuracy":{"mean":0.8,"std":0.01},"rmse":{"mean":0.2,"std":0.01},"logLoss":{"mean":0.3,"std":0.01}}}}}
   """);
  payloads.put("g5-04-report-validation","{\"passed\":true,\"schemaVersion\":\"g5-04-report-validation-v1\"}");
  payloads.put("g5-recommendation-comparison","""
   {"passed":true,"schemaVersion":"g5-recommendation-comparison-v1","verifiedAt":"2026-09-18T00:00:00Z","strategyVersion":"v1","limitations":[],"testEvaluation":{"metrics":{"HYBRID_KT":{"validUsers":4,"ndcgAt5":0.2,"hitRateAt5":0.3,"coverage":0.4}}}}
   """);
  payloads.put("g5-05-report-validation","{\"passed\":true,\"schemaVersion\":\"g5-05-report-validation-v1\"}");
  payloads.put("g5-demo-catalog-validation","""
   {"passed":true,"schemaVersion":"g5-demo-catalog-validation-v1","limitations":[],"totals":{"courses":3,"knowledgePoints":45,"resources":60,"questions":225},"courses":[{"catalogVersion":"a"},{"catalogVersion":"b"},{"catalogVersion":"c"}]}
   """);
  payloads.put("g5-demo-catalog-import","{\"passed\":true,\"schemaVersion\":\"g5-demo-catalog-import-v1\",\"verifiedAt\":\"2026-09-18T00:00:00Z\",\"limitations\":[]}");
  String[][] specs={{"g5-kt-comparison","KT_COMPARISON","PUBLIC_DATASET","g5-kt-comparison-v2","g5-04-report-validation"},{"g5-04-report-validation","KT_VALIDATION","PUBLIC_DATASET","g5-04-report-validation-v1",null},{"g5-recommendation-comparison","RECOMMENDATION_COMPARISON","PUBLIC_DATASET","g5-recommendation-comparison-v1","g5-05-report-validation"},{"g5-05-report-validation","RECOMMENDATION_VALIDATION","PUBLIC_DATASET","g5-05-report-validation-v1",null},{"g5-demo-catalog-validation","DEMO_CATALOG_VALIDATION","SYNTHETIC_DEMO","g5-demo-catalog-validation-v1",null},{"g5-demo-catalog-import","DEMO_CATALOG_IMPORT","SYNTHETIC_DEMO","g5-demo-catalog-import-v1","g5-demo-catalog-validation"}};
  var entries=new ArrayList<Map<String,Object>>();int order=10;
  for(String[] spec:specs){Path path=root.resolve("artifacts/"+spec[0]+".json");Files.writeString(path,payloads.get(spec[0]));var entry=new LinkedHashMap<String,Object>();entry.put("artifactId",spec[0]);entry.put("artifactType",spec[1]);entry.put("dataType",spec[2]);entry.put("displayOrder",order);entry.put("publishStatus","VERIFIED");entry.put("relativePath","artifacts/"+spec[0]+".json");entry.put("schemaVersion",spec[3]);entry.put("sha256",sha(path));entry.put("validationArtifactId",spec[4]);entries.add(entry);order+=10;}
  var registry=new LinkedHashMap<String,Object>();registry.put("schemaVersion","g5-dashboard-artifact-registry-v1");registry.put("requestPolicy",Map.of("trainsDuringRequest",false,"infersDuringRequest",false,"recomputesFormalTestDuringRequest",false));registry.put("artifacts",entries);json.writeValue(root.resolve("data/manifests/g5-dashboard-artifact-registry.json").toFile(),registry);
 }
 private static String sha(Path path)throws Exception{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path)));}
}
