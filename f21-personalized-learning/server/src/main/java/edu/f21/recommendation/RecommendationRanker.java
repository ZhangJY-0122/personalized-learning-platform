package edu.f21.recommendation;

import java.time.Duration;
import java.time.Instant;
import java.util.*;

/** Pure, deterministic G3.1 engine. Callers must provide one authorized catalog/state snapshot. */
public final class RecommendationRanker {
    public static final String STRATEGY = "four-component-single-skill-v1";
    public enum Kind { RESOURCE, QUESTION }
    public enum Mode { WEAKNESS, DIAGNOSTIC, CONSOLIDATION }
    public record Skill(String id, String name, int order, double mastery, int evidenceCount,
                        double evidenceWeight, Double recentErrorRate) {
        public Skill {
            required(id); required(name); probability(mastery);
            if (evidenceCount < 0 || !Double.isFinite(evidenceWeight) || evidenceWeight < 0
                    || evidenceWeight > evidenceCount) throw invalid("Invalid evidence");
            if (recentErrorRate != null) probability(recentErrorRate);
            if (evidenceCount == 0 && (evidenceWeight != 0 || recentErrorRate != null))
                throw invalid("Evidence-free skill must not have error history");
            if (evidenceCount > 0 && recentErrorRate == null) throw invalid("Missing recent error history");
        }
        public boolean sufficient() { return evidenceCount >= 3 && evidenceWeight >= 2; }
        public boolean adequate() { return sufficient() && mastery >= .6; }
        public boolean weak() { return sufficient() && mastery < .6; }
    }
    public record Edge(String prerequisite, String target) {}
    public record Candidate(String id, String courseId, Kind kind, String skillId, String title,
                            boolean active, double difficulty, String resourceType, Instant completedAt) {
        public Candidate {
            required(id); required(courseId); required(title); Objects.requireNonNull(kind);
            probability(difficulty);
            if (kind == Kind.RESOURCE) required(resourceType);
        }
    }
    public record Input(String courseId, List<Skill> skills, List<Edge> edges, List<Candidate> candidates,
                        List<String> recentQuestionIds, Map<String, Double> resourcePreferences, Instant now) {
        public Input {
            required(courseId); Objects.requireNonNull(now);
            skills = List.copyOf(skills); edges = List.copyOf(edges); candidates = List.copyOf(candidates);
            recentQuestionIds = List.copyOf(recentQuestionIds);
            resourcePreferences = Map.copyOf(resourcePreferences);
            double total = 0;
            for (var entry : resourcePreferences.entrySet()) {
                required(entry.getKey()); probability(entry.getValue()); total += entry.getValue();
            }
            if (!resourcePreferences.isEmpty() && Math.abs(total - 1) > 1e-8)
                throw invalid("Preference shares must sum to one");
        }
    }
    public record Component(Double value, double rawWeight, double effectiveWeight, String source) {}
    public record Target(String skillId, double priority, String source) {}
    public record Ranked(Candidate candidate, double score, boolean review,
                         List<String> reasons, Map<String, Component> scoreDetails) {
        public Ranked { reasons = List.copyOf(reasons); scoreDetails = Map.copyOf(scoreDetails); }
    }
    public record Result(String strategyVersion, Mode mode, List<Target> targets, List<Ranked> items,
                         List<String> notices) {
        public Result { targets = List.copyOf(targets); items = List.copyOf(items); notices = List.copyOf(notices); }
    }
    private RecommendationRanker() {}
    private static IllegalArgumentException invalid(String message) { return new IllegalArgumentException(message); }
    private static void required(String value) { if (value == null || value.isBlank()) throw invalid("Missing identity or label"); }
    private static void probability(double value) {
        if (!Double.isFinite(value) || value < 0 || value > 1) throw invalid("Invalid probability");
    }

    public static Result rank(Input input) {
        var skills = new TreeMap<String, Skill>();
        for (var skill : input.skills()) if (skills.put(skill.id(), skill) != null) throw invalid("Duplicate skill");
        var parents = new TreeMap<String, Set<String>>();
        skills.keySet().forEach(id -> parents.put(id, new TreeSet<>()));
        for (var edge : input.edges()) {
            if (!skills.containsKey(edge.prerequisite()) || !skills.containsKey(edge.target())
                    || edge.prerequisite().equals(edge.target())) throw invalid("Invalid prerequisite edge");
            parents.get(edge.target()).add(edge.prerequisite());
        }
        var ancestors = new TreeMap<String, Set<String>>();
        for (String id : skills.keySet()) ancestors(id, parents, ancestors, new HashSet<>());
        var weakness = new TreeMap<String, Double>();
        for (var skill : skills.values()) if (skill.weak()) {
            long descendants = ancestors.values().stream().filter(set -> set.contains(skill.id())).count();
            double impact = (double) descendants / Math.max(1, skills.size() - 1);
            weakness.put(skill.id(), .6 * (1 - skill.mastery()) + .2 * skill.recentErrorRate() + .1 * .5 + .1 * impact);
        }
        var targets = new TreeMap<String, Target>();
        Mode mode;
        if (!weakness.isEmpty()) {
            mode = Mode.WEAKNESS;
            var weakIds = weakness.keySet().stream().sorted(Comparator
                    .<String>comparingDouble(weakness::get).reversed().thenComparing(id -> id)).limit(5).toList();
            for (String id : weakIds) targets.put(id, new Target(id, weakness.get(id), "EVIDENCED_WEAKNESS"));
            for (String id : weakIds) for (String parent : ancestors.get(id)) if (!skills.get(parent).adequate()) {
                // A selected weak skill keeps its own measured weakness, not an inherited score.
                if (weakIds.contains(parent)) continue;
                double priority = Math.max(weakness.get(id), targets.containsKey(parent) ? targets.get(parent).priority() : 0);
                targets.put(parent, new Target(parent, priority, "UNMET_PREREQUISITE"));
            }
        } else {
            boolean diagnostic = skills.values().stream().anyMatch(s -> !s.adequate());
            mode = diagnostic ? Mode.DIAGNOSTIC : Mode.CONSOLIDATION;
            skills.values().stream().filter(s -> !diagnostic || !s.adequate())
                    .filter(s -> eligible(s.id(), ancestors, skills))
                    .sorted(Comparator.comparingInt(Skill::order).thenComparing(Skill::id)).limit(5)
                    .forEach(s -> targets.put(s.id(), new Target(s.id(), 1, diagnostic ? "DIAGNOSTIC" : "CONSOLIDATION")));
        }
        double norm = Math.sqrt(targets.values().stream().mapToDouble(t -> t.priority() * t.priority()).sum());
        var recent = new HashSet<>(input.recentQuestionIds().stream().limit(3).toList());
        var fresh = new ArrayList<Ranked>();
        var repeats = new ArrayList<Ranked>();
        var identities = new HashSet<String>();
        for (var candidate : input.candidates()) {
            if (!identities.add(candidate.kind() + ":" + candidate.id())) throw invalid("Duplicate candidate");
            if (!candidate.active() || !input.courseId().equals(candidate.courseId()) || candidate.skillId() == null
                    || !skills.containsKey(candidate.skillId()) || !targets.containsKey(candidate.skillId())
                    || !eligible(candidate.skillId(), ancestors, skills)) continue;
            var skill = skills.get(candidate.skillId());
            boolean review = candidate.kind() == Kind.QUESTION && recent.contains(candidate.id());
            if (candidate.kind() == Kind.RESOURCE && candidate.completedAt() != null) {
                if (Duration.between(candidate.completedAt(), input.now()).compareTo(Duration.ofDays(7)) < 0 || !skill.weak()) continue;
                review = true;
            }
            var ranked = score(candidate, skill, targets.get(skill.id()), mode, norm, review, input.resourcePreferences());
            if (candidate.kind() == Kind.QUESTION && recent.contains(candidate.id())) repeats.add(ranked);
            else fresh.add(ranked);
        }
        Comparator<Ranked> order = Comparator.comparingDouble(Ranked::score).reversed()
                .thenComparingDouble(r -> Math.abs(r.candidate().difficulty() - .65))
                .thenComparing(r -> r.candidate().id()).thenComparing(r -> r.candidate().kind());
        fresh.sort(order); repeats.sort(order);
        var notices = new ArrayList<String>();
        // Repeat questions only fill otherwise empty slots, never displace a fresh item.
        if (fresh.size() < 5 && !repeats.isEmpty()) {
            fresh.addAll(repeats.subList(0, Math.min(5 - fresh.size(), repeats.size())));
            notices.add("合规新候选不足，补充近期题目复习。");
        }
        var pool = fresh.subList(0, Math.min(20, fresh.size()));
        var chosen = new ArrayList<Ranked>();
        var counts = new HashMap<String, Integer>();
        if (!pool.isEmpty()) {
            add(pool.get(0), chosen, counts);
            Kind firstKind = pool.get(0).candidate().kind();
            pool.stream().filter(r -> r.candidate().kind() != firstKind).findFirst().ifPresent(r -> add(r, chosen, counts));
        }
        for (var item : pool) if (chosen.size() < 5 && !chosen.contains(item)
                && counts.getOrDefault(item.candidate().skillId(), 0) < 2) add(item, chosen, counts);
        if (chosen.size() < Math.min(5, pool.size())) {
            notices.add("可用知识点不足，已放宽同知识点最多2项的多样性限制；未放宽先修或可用性过滤。");
            for (var item : pool) if (chosen.size() < 5 && !chosen.contains(item)) add(item, chosen, counts);
        }
        if (chosen.size() < 5) notices.add("通过课程、先修、状态及复习过滤的候选不足5项，返回实际可用列表。");
        var orderedTargets = targets.values().stream().sorted(Comparator.comparingDouble(Target::priority).reversed()
                .thenComparing(Target::skillId)).toList();
        return new Result(STRATEGY, mode, orderedTargets, chosen, notices);
    }

    private static void add(Ranked item, List<Ranked> chosen, Map<String, Integer> counts) {
        chosen.add(item); counts.merge(item.candidate().skillId(), 1, Integer::sum);
    }
    private static Set<String> ancestors(String id, Map<String, Set<String>> parents,
                                         Map<String, Set<String>> cache, Set<String> visiting) {
        if (cache.containsKey(id)) return cache.get(id);
        if (!visiting.add(id)) throw invalid("Cyclic prerequisite graph");
        var result = new TreeSet<String>();
        for (String parent : parents.get(id)) { result.add(parent); result.addAll(ancestors(parent, parents, cache, visiting)); }
        visiting.remove(id); cache.put(id, Set.copyOf(result)); return result;
    }
    private static boolean eligible(String id, Map<String, Set<String>> ancestors, Map<String, Skill> skills) {
        return ancestors.get(id).stream().allMatch(parent -> skills.get(parent).adequate());
    }
    private static Ranked score(Candidate candidate, Skill skill, Target target, Mode mode,
                                double norm, boolean review, Map<String, Double> preferences) {
        boolean question = candidate.kind() == Kind.QUESTION;
        double prediction = .9 * skill.mastery() + .2 * (1 - skill.mastery());
        double difficulty = question ? 1 - Math.min(1, Math.abs(prediction - .65) / .65)
                : Math.max(0, 1 - Math.abs(candidate.difficulty() - skill.mastery()));
        Double alignment = mode == Mode.WEAKNESS ? target.priority() : null;
        Double cosine = norm == 0 ? null : target.priority() / norm;
        Double preference = question || preferences.isEmpty() ? null : preferences.getOrDefault(candidate.resourceType(), 0.0);
        Double[] values = {alignment, difficulty, cosine, preference};
        double[] weights = {.45, .25, .20, .10};
        String[] names = {"weaknessMatch", "difficultyMatch", "contentMatch", "preferenceMatch"};
        String[] sources = {alignment == null ? "DISABLED_NO_EVIDENCED_WEAKNESS" : target.source(),
                question ? "BKT_SKILL_PREDICTION_G0.2_S0.1" : "RESOURCE_DIFFICULTY_BKT_HEURISTIC",
                "SINGLE_SKILL_TARGET_COSINE", preference == null ? "DISABLED_NO_APPLICABLE_PREFERENCE" : "TRUSTED_RESOURCE_TYPE_SHARE_30D"};
        double sum = 0;
        for (int i = 0; i < values.length; i++) if (values[i] != null) sum += weights[i];
        var details = new LinkedHashMap<String, Component>();
        double total = 0;
        for (int i = 0; i < values.length; i++) {
            double effective = values[i] == null ? 0 : weights[i] / sum;
            details.put(names[i], new Component(values[i], weights[i], effective, sources[i]));
            if (values[i] != null) total += effective * values[i];
        }
        var reasons = new ArrayList<String>();
        reasons.add(switch (target.source()) {
            case "EVIDENCED_WEAKNESS" -> "关联知识点“" + skill.name() + "”已有" + skill.evidenceCount() + "次有效证据，BKT低于0.6。";
            case "UNMET_PREREQUISITE" -> "关联知识点“" + skill.name() + "”是薄弱目标尚未达标的先修知识。";
            case "DIAGNOSTIC" -> "关联知识点“" + skill.name() + "”证据不足，按课程顺序安排诊断，不判为薄弱。";
            default -> "关联知识点“" + skill.name() + "”已达标，本次用于巩固。";
        });
        reasons.add(question ? "难度匹配来自技能级BKT答对概率，不是该题的精确预测。"
                : "难度匹配来自资源标注难度与BKT的差距，是启发式评分。");
        if (mode == Mode.WEAKNESS) reasons.add("目标优先级使用近期加权错误率、先修后继比例及默认重要度0.5。");
        if (review) reasons.add(question ? "近期已作答过此题，候选不足时作为复习补充。" : "此资源完成已满7日，关联知识点仍薄弱，安排复习。");
        return new Ranked(candidate, total, review, reasons, details);
    }
}
