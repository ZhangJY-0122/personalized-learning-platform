package edu.f21.dashboard;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class DashboardArtifactService {
    private static final Pattern SAFE_PATH = Pattern.compile("^artifacts/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\\.json$");
    private static final List<Spec> REQUIRED = List.of(
        new Spec("g5-kt-comparison","KT_COMPARISON","PUBLIC_DATASET","artifacts/g5-kt-comparison.json","g5-kt-comparison-v2","g5-04-report-validation",10),
        new Spec("g5-04-report-validation","KT_VALIDATION","PUBLIC_DATASET","artifacts/g5-04-report-validation.json","g5-04-report-validation-v1",null,20),
        new Spec("g5-recommendation-comparison","RECOMMENDATION_COMPARISON","PUBLIC_DATASET","artifacts/g5-recommendation-comparison.json","g5-recommendation-comparison-v1","g5-05-report-validation",30),
        new Spec("g5-05-report-validation","RECOMMENDATION_VALIDATION","PUBLIC_DATASET","artifacts/g5-05-report-validation.json","g5-05-report-validation-v1",null,40),
        new Spec("g5-demo-catalog-validation","DEMO_CATALOG_VALIDATION","SYNTHETIC_DEMO","artifacts/g5-demo-catalog-validation.json","g5-demo-catalog-validation-v1",null,50),
        new Spec("g5-demo-catalog-import","DEMO_CATALOG_IMPORT","SYNTHETIC_DEMO","artifacts/g5-demo-catalog-import.json","g5-demo-catalog-import-v1","g5-demo-catalog-validation",60)
    );
    private static final String PUBLIC_DATASET = "assistments-2009-2010-skill-builder-corrected";
    private final Map<String,Artifact> artifacts;

    private record Artifact(String id,String relativePath,String sha256,String schemaVersion,String validationId,JsonNode payload) {}
    private record Spec(String id,String type,String dataType,String path,String schema,String validationId,int order) {}

    @Autowired
    public DashboardArtifactService(ObjectMapper mapper,@Value("${app.dashboard.root}") String root) {
        this(mapper,Path.of(root));
    }

    DashboardArtifactService(ObjectMapper mapper,Path root) {
        try { this.artifacts=load(mapper,root); }
        catch(Exception ex) { throw new IllegalStateException("Dashboard artifact registry verification failed",ex); }
    }

    public Map<String,Object> models(int page,int pageSize) {
        JsonNode report=artifact("g5-kt-comparison").payload();
        JsonNode source=report.path("models");
        var items=new ArrayList<Map<String,Object>>();
        items.add(model("RULE_RECENT20_BETA11_V1","Rule","RULE_RECENT20_BETA11_V1",source.path("RULE_RECENT20_BETA11_V1"),List.of(),false));
        items.add(model("BKT_G5_FROZEN_SKILL_SPECIFIC_FALLBACK_V1","BKT","BKT_G5_FROZEN_SKILL_SPECIFIC_FALLBACK_V1",source.path("BKT_G5_FROZEN_SKILL_SPECIFIC_FALLBACK_V1"),List.of(),false));
        JsonNode dkt=source.path("DKT_LSTM_V1");
        List<Integer> seeds=new ArrayList<>();
        dkt.path("seeds").forEach(row->seeds.add(row.path("seed").asInt()));
        String config=report.path("tuning").path("selectedConfig").path("configId").asText();
        items.add(model("DKT_LSTM_V1","DKT","DKT_LSTM_V1:"+config,dkt.path("meanStd"),seeds,true));
        return page(items,page,pageSize);
    }

    public Map<String,Object> experiments(int page,int pageSize) {
        var items=new ArrayList<Map<String,Object>>();
        JsonNode kt=artifact("g5-kt-comparison").payload();
        JsonNode dkt=kt.path("models").path("DKT_LSTM_V1").path("meanStd");
        items.add(experiment(
            "g5-kt-comparison","G5 知识追踪模型比较","KT_COMPARISON","PUBLIC_DATASET",PUBLIC_DATASET,
            null,null,kt.path("targetSet").path("count").asInt(),
            List.of(metric("AUC",dkt.path("auc").path("mean").asDouble(),dkt.path("auc").path("std").asDouble(),"RATE"),
                metric("LogLoss",dkt.path("logLoss").path("mean").asDouble(),dkt.path("logLoss").path("std").asDouble(),"LOSS")),
            "g5-kt-comparison","g5-04-report-validation",kt.path("verifiedAt").asText(),strings(kt.path("limitations"))));

        JsonNode recommendation=artifact("g5-recommendation-comparison").payload();
        JsonNode recommendationMetrics=recommendation.path("testEvaluation").path("metrics").path("HYBRID_KT");
        items.add(experiment(
            "g5-recommendation-comparison","G5 推荐基线比较","RECOMMENDATION_COMPARISON","PUBLIC_DATASET",PUBLIC_DATASET,
            null,recommendation.path("strategyVersion").asText(),recommendationMetrics.path("validUsers").asInt(),
            List.of(metric("NDCG@5",recommendationMetrics.path("ndcgAt5").asDouble(),0d,"RATE"),
                metric("HitRate@5",recommendationMetrics.path("hitRateAt5").asDouble(),0d,"RATE"),
                metric("Coverage",recommendationMetrics.path("coverage").asDouble(),0d,"RATE")),
            "g5-recommendation-comparison","g5-05-report-validation",recommendation.path("verifiedAt").asText(),strings(recommendation.path("limitations"))));

        JsonNode catalog=artifact("g5-demo-catalog-validation").payload();
        JsonNode imported=artifact("g5-demo-catalog-import").payload();
        var versions=new ArrayList<String>();
        catalog.path("courses").forEach(course->versions.add(course.path("catalogVersion").asText()));
        JsonNode totals=catalog.path("totals");
        items.add(experiment(
            "g5-demo-catalog-import","G5 三门合成演示课程验证","DEMO_CATALOG_VALIDATION","SYNTHETIC_DEMO","g5-synthetic-demo-catalogs-v1",
            String.join(",",versions),null,totals.path("courses").asInt(),
            List.of(metric("KnowledgePoints",totals.path("knowledgePoints").asDouble(),null,"COUNT"),
                metric("Resources",totals.path("resources").asDouble(),null,"COUNT"),
                metric("Questions",totals.path("questions").asDouble(),null,"COUNT")),
            "g5-demo-catalog-import","g5-demo-catalog-validation",imported.path("verifiedAt").asText(),concat(strings(catalog.path("limitations")),strings(imported.path("limitations")))));
        return page(items,page,pageSize);
    }

    private Map<String,Object> model(String id,String name,String version,JsonNode values,List<Integer> seeds,boolean meanStd) {
        JsonNode report=artifact("g5-kt-comparison").payload();
        int samples=meanStd?report.path("targetSet").path("count").asInt():values.path("samples").asInt();
        var metrics=new ArrayList<Map<String,Object>>();
        for(var definition:List.of(new String[]{"AUC","auc","RATE"},new String[]{"Accuracy","accuracy","RATE"},new String[]{"RMSE","rmse","ERROR"},new String[]{"LogLoss","logLoss","LOSS"})) {
            JsonNode value=values.path(definition[1]);
            metrics.add(metric(definition[0],meanStd?value.path("mean").asDouble():value.asDouble(),meanStd?value.path("std").asDouble():null,definition[2]));
        }
        var item=new LinkedHashMap<String,Object>();
        item.put("modelId",id);item.put("modelName",name);item.put("modelVersion",version);item.put("experimentId","g5-kt-comparison");
        item.put("dataType","PUBLIC_DATASET");item.put("datasetVersion",PUBLIC_DATASET);item.put("targetDefinition","next-answer correctness before revelation");
        item.put("sampleCount",samples);item.put("seeds",seeds);item.put("metrics",metrics);item.put("artifact",reference("g5-kt-comparison"));item.put("status","VERIFIED");item.put("limitations",strings(report.path("limitations")));
        return item;
    }

    private Map<String,Object> experiment(String id,String title,String type,String dataType,String datasetVersion,String catalogVersion,String strategyVersion,int sampleCount,List<Map<String,Object>> metrics,String artifactId,String validationId,String verifiedAt,List<String> limitations) {
        var item=new LinkedHashMap<String,Object>();
        item.put("experimentId",id);item.put("title",title);item.put("experimentType",type);item.put("dataType",dataType);item.put("datasetVersion",datasetVersion);
        item.put("catalogVersion",catalogVersion);item.put("strategyVersion",strategyVersion);item.put("sampleCount",sampleCount);item.put("metrics",metrics);
        item.put("artifact",reference(artifactId));item.put("validationArtifact",reference(validationId));item.put("verifiedAt",verifiedAt);item.put("limitations",limitations);
        return item;
    }

    private static Map<String,Object> metric(String name,double value,Double stdDev,String unit) {
        var result=new LinkedHashMap<String,Object>();result.put("name",name);result.put("value",value);result.put("stdDev",stdDev);result.put("unit",unit);return result;
    }

    private Map<String,Object> reference(String id) {
        Artifact artifact=artifact(id);
        return Map.of("relativePath",artifact.relativePath(),"sha256",artifact.sha256(),"schemaVersion",artifact.schemaVersion(),"checksumVerified",true);
    }

    private Artifact artifact(String id) {
        Artifact artifact=artifacts.get(id);
        if(artifact==null)throw new IllegalStateException("Missing verified dashboard artifact: "+id);
        return artifact;
    }

    private static Map<String,Object> page(List<Map<String,Object>> all,int page,int pageSize) {
        long offset=(long)(page-1)*pageSize;
        List<Map<String,Object>> items=offset>=all.size()?List.of():all.subList((int)offset,Math.min(all.size(),(int)offset+pageSize));
        return Map.of("items",List.copyOf(items),"page",page,"pageSize",pageSize,"total",all.size());
    }

    private static List<String> strings(JsonNode values) {
        var result=new ArrayList<String>();if(values.isArray())values.forEach(value->result.add(value.asText()));return List.copyOf(result);
    }

    private static List<String> concat(List<String> first,List<String> second) {
        var values=new ArrayList<String>(first);values.addAll(second);return List.copyOf(values);
    }

    private static Map<String,Artifact> load(ObjectMapper mapper,Path configuredRoot) throws IOException {
        Path root=configuredRoot.toAbsolutePath().normalize(),rootReal=root.toRealPath();
        Path registryPath=root.resolve("data/manifests/g5-dashboard-artifact-registry.json").normalize();
        if(!registryPath.startsWith(root)||Files.isSymbolicLink(registryPath)||!Files.isRegularFile(registryPath))throw new IOException("Missing safe registry");
        if(!registryPath.toRealPath().startsWith(rootReal))throw new IOException("Registry escaped root");
        JsonNode registry=mapper.readTree(registryPath.toFile());
        if(!"g5-dashboard-artifact-registry-v1".equals(registry.path("schemaVersion").asText()))throw new IOException("Unexpected registry schema");
        JsonNode policy=registry.path("requestPolicy");
        if(policy.path("trainsDuringRequest").asBoolean(true)||policy.path("infersDuringRequest").asBoolean(true)||policy.path("recomputesFormalTestDuringRequest").asBoolean(true))throw new IOException("Unsafe request policy");
        var result=new LinkedHashMap<String,Artifact>();int index=0;Path artifactsRoot=root.resolve("artifacts").toRealPath();
        for(JsonNode entry:registry.path("artifacts")) {
            String id=entry.path("artifactId").asText(),relative=entry.path("relativePath").asText(),expected=entry.path("sha256").asText(),schema=entry.path("schemaVersion").asText();
            if(index>=REQUIRED.size())throw new IOException("Unexpected registry entry");Spec spec=REQUIRED.get(index++);
            String validation=entry.path("validationArtifactId").isNull()?null:entry.path("validationArtifactId").asText();
            if(!spec.id().equals(id)||!spec.type().equals(entry.path("artifactType").asText())||!spec.dataType().equals(entry.path("dataType").asText())||!spec.path().equals(relative)||!spec.schema().equals(schema)||!java.util.Objects.equals(spec.validationId(),validation)||spec.order()!=entry.path("displayOrder").asInt())throw new IOException("Registry allowlist mismatch");
            if(!SAFE_PATH.matcher(relative).matches()||!"VERIFIED".equals(entry.path("publishStatus").asText())||!expected.matches("[a-f0-9]{64}"))throw new IOException("Unsafe registry entry");
            Path path=root.resolve(relative).normalize();
            if(!path.startsWith(root.resolve("artifacts").normalize())||Files.isSymbolicLink(path)||!Files.isRegularFile(path)||!path.toRealPath().startsWith(artifactsRoot))throw new IOException("Unsafe artifact path");
            String actual=sha256(path);if(!MessageDigest.isEqual(actual.getBytes(),expected.getBytes()))throw new IOException("Artifact checksum mismatch");
            JsonNode payload=mapper.readTree(path.toFile());
            if(!payload.path("passed").asBoolean(false)||!schema.equals(payload.path("schemaVersion").asText()))throw new IOException("Unverified artifact payload");
            if(result.put(id,new Artifact(id,relative,expected,schema,validation,payload))!=null)throw new IOException("Duplicate artifact id");
        }
        if(index!=REQUIRED.size())throw new IOException("Registry allowlist mismatch");
        for(Artifact artifact:result.values())if(artifact.validationId()!=null&&!result.containsKey(artifact.validationId()))throw new IOException("Missing validation binding");
        return Map.copyOf(result);
    }

    private static String sha256(Path path) throws IOException {
        try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path)));}
        catch(java.security.NoSuchAlgorithmException ex){throw new IllegalStateException(ex);}
    }
}
