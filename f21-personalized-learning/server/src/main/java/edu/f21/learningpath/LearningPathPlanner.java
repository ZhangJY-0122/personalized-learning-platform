package edu.f21.learningpath;

import java.util.*;

/**
 * Pure G4 path planner.  The service layer supplies one already-authorized,
 * immutable catalog/state snapshot; this class never reads a database or clock.
 */
public final class LearningPathPlanner {
    public static final String VERSION = "dag-path-v1";
    public enum ItemType { RESOURCE, QUESTION }
    public enum Phase { RESOURCE, BASIC_PRACTICE, ADVANCED_PRACTICE, REMEDIATION, RETEST }
    public enum Status { ACTIVE, COMPLETED, INVALIDATED, BLOCKED }
    public enum NodeStatus { PENDING, IN_PROGRESS, COMPLETED, SKIPPED }

    public record Skill(String id, String name, int order, double mastery,
                        int evidenceCount, double evidenceWeight, Double recentErrorRate) {
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

    public record Edge(String prerequisite, String target) {
        public Edge { required(prerequisite); required(target); }
    }

    public record Candidate(String id, String courseId, ItemType type, String skillId,
                            String title, boolean active, double difficulty, int sortOrder) {
        public Candidate {
            required(id); required(courseId); required(skillId); required(title);
            Objects.requireNonNull(type); probability(difficulty);
        }
    }

    public record ExistingNode(String nodeId, String skillId, ItemType type, String itemId,
                               Phase phase, int roundNo, NodeStatus status) {
        public ExistingNode {
            required(nodeId); required(skillId); required(itemId); Objects.requireNonNull(type);
            Objects.requireNonNull(phase); Objects.requireNonNull(status);
            if (roundNo < 1 || roundNo > 2) throw invalid("Invalid path round");
        }
    }

    /** nextRoundBySkill contains only explicit second-round retry skills. */
    public record Input(String courseId, String catalogVersion, List<Skill> skills,
                        List<Edge> edges, List<Candidate> candidates,
                        List<ExistingNode> existingNodes, Map<String, Integer> nextRoundBySkill,
                        Set<String> usedItemIds) {
        public Input {
            required(courseId); required(catalogVersion);
            skills = List.copyOf(Objects.requireNonNull(skills));
            edges = List.copyOf(Objects.requireNonNull(edges));
            candidates = List.copyOf(Objects.requireNonNull(candidates));
            existingNodes = List.copyOf(Objects.requireNonNull(existingNodes));
            nextRoundBySkill = Map.copyOf(Objects.requireNonNull(nextRoundBySkill));
            usedItemIds = Set.copyOf(Objects.requireNonNull(usedItemIds));
            nextRoundBySkill.forEach((id, round) -> {
                required(id);
                if (round != 2) throw invalid("Only round two retries are supported");
            });
        }
        public Input(String courseId, String catalogVersion, List<Skill> skills, List<Edge> edges,
                     List<Candidate> candidates) {
            this(courseId, catalogVersion, skills, edges, candidates, List.of(), Map.of(), Set.of());
        }
    }

    public record PlanNode(String skillId, ItemType itemType, String itemId, String title,
                           Phase phase, int roundNo, NodeStatus status, String carriedFromNodeId) {}
    public record Missing(String skillId, String requirement, int required, int available) {}
    public record Result(Status status, String reasonCode, String reason, List<PlanNode> nodes,
                         List<Missing> missing, List<String> notices) {
        public Result {
            Objects.requireNonNull(status); nodes = List.copyOf(nodes); missing = List.copyOf(missing);
            notices = List.copyOf(notices);
        }
    }

    private LearningPathPlanner() {}
    private static IllegalArgumentException invalid(String message) { return new IllegalArgumentException(message); }
    private static void required(String s) { if (s == null || s.isBlank()) throw invalid("Missing identity or label"); }
    private static void probability(double x) { if (!Double.isFinite(x) || x < 0 || x > 1) throw invalid("Invalid probability"); }

    public static Result plan(Input input) {
        var skills = new TreeMap<String, Skill>();
        for (var skill : input.skills()) if (skills.put(skill.id(), skill) != null) throw invalid("Duplicate skill");
        var parents = new TreeMap<String, Set<String>>();
        skills.keySet().forEach(id -> parents.put(id, new TreeSet<>()));
        var edgeKeys = new HashSet<String>();
        for (var edge : input.edges()) {
            if (!skills.containsKey(edge.prerequisite()) || !skills.containsKey(edge.target())
                    || edge.prerequisite().equals(edge.target())
                    || !edgeKeys.add(edge.prerequisite() + "->" + edge.target()))
                throw invalid("Invalid prerequisite edge");
            parents.get(edge.target()).add(edge.prerequisite());
        }
        var ancestors = new TreeMap<String, Set<String>>();
        for (String id : skills.keySet()) ancestors(id, parents, ancestors, new HashSet<>());
        var topo = topological(skills, parents);

        var weak = skills.values().stream().filter(Skill::weak).sorted(Comparator
                .comparingDouble((Skill s) -> weakness(s, ancestors, skills)).reversed()
                .thenComparingInt(Skill::order).thenComparing(Skill::id)).limit(3).toList();
        var targetIds = new TreeSet<String>();
        String mode;
        if (!weak.isEmpty()) {
            mode = "WEAKNESS";
            for (var skill : weak) { targetIds.add(skill.id()); targetIds.addAll(ancestors.get(skill.id())); }
        } else {
            var diagnostic = skills.values().stream().anyMatch(s -> !s.adequate());
            if (!diagnostic) return new Result(Status.COMPLETED, null, null, List.of(), List.of(), List.of());
            mode = "DIAGNOSTIC";
            for (var id : topo) {
                var skill = skills.get(id);
                if (!skill.adequate() && eligible(id, ancestors, skills)) { targetIds.add(id); break; }
            }
            if (targetIds.isEmpty()) return blocked("NO_ELIGIBLE_TARGET", "暂无满足先修条件的目标知识点", List.of());
        }
        // A retry request explicitly names the skill(s) whose second round must be planned.
        if (!input.nextRoundBySkill().isEmpty()) {
            targetIds.clear();
            for (String id : input.nextRoundBySkill().keySet()) {
                if (!skills.containsKey(id)) throw invalid("Retry skill does not exist");
                targetIds.add(id); targetIds.addAll(ancestors.get(id));
            }
        }

        var carried = new HashMap<String, ExistingNode>();
        for (var n : input.existingNodes()) {
            if (n.status() == NodeStatus.COMPLETED || n.status() == NodeStatus.SKIPPED)
                carried.put(n.type() + ":" + n.itemId(), n);
        }
        var used = new HashSet<>(input.usedItemIds());
        var nodes = new ArrayList<PlanNode>();
        // A retry creates a new path version but keeps already completed
        // evidence as SKIPPED nodes. The old items stay out of candidate
        // selection through Input.usedItemIds, while the audit trail remains.
        if (!input.nextRoundBySkill().isEmpty()) {
            for (String skillId : topo) {
                for (var old : input.existingNodes()) {
                    if (old.skillId().equals(skillId)
                            && (old.status() == NodeStatus.COMPLETED || old.status() == NodeStatus.SKIPPED))
                        nodes.add(new PlanNode(old.skillId(), old.type(), old.itemId(), old.itemId(), old.phase(),
                                old.roundNo(), NodeStatus.SKIPPED, old.nodeId()));
                }
            }
        }
        var missing = new ArrayList<Missing>();
        for (String skillId : topo) {
            if (!targetIds.contains(skillId) || skills.get(skillId).adequate() && !input.nextRoundBySkill().containsKey(skillId)) continue;
            int round = input.nextRoundBySkill().getOrDefault(skillId, 1);
            var byType = input.candidates().stream().filter(c -> c.active() && c.courseId().equals(input.courseId())
                    && c.skillId().equals(skillId) && !used.contains(c.id())).toList();
            var resources = byType.stream().filter(c -> c.type() == ItemType.RESOURCE).sorted(resourceOrder(skills.get(skillId))).toList();
            var questions = new ArrayList<>(byType.stream().filter(c -> c.type() == ItemType.QUESTION)
                    .sorted(Comparator.comparingInt(Candidate::sortOrder).thenComparing(Candidate::id)).toList());
            if (round == 1) {
                if (resources.isEmpty()) missing.add(new Missing(skillId, "RESOURCE", 1, 0));
                if (questions.size() < 3) missing.add(new Missing(skillId, "QUESTION_ROUND_1", 3, questions.size()));
                if (resources.isEmpty() || questions.size() < 3) continue;
                var resource = resources.get(0); used.add(resource.id());
                nodes.add(node(skillId, resource, Phase.RESOURCE, 1, carried));
                var basic = questions.remove(0); used.add(basic.id());
                nodes.add(node(skillId, basic, Phase.BASIC_PRACTICE, 1, carried));
                var advanced = questions.remove(questions.size() - 1); used.add(advanced.id());
                nodes.add(node(skillId, advanced, Phase.ADVANCED_PRACTICE, 1, carried));
                var retest = closest(questions, .60); used.add(retest.id());
                nodes.add(node(skillId, retest, Phase.RETEST, 1, carried));
            } else {
                if (questions.size() < 2) missing.add(new Missing(skillId, "QUESTION_ROUND_2", 2, questions.size()));
                if (questions.size() < 2) continue;
                var remediation = closest(questions, skills.get(skillId).mastery()); questions.remove(remediation); used.add(remediation.id());
                nodes.add(node(skillId, remediation, Phase.REMEDIATION, 2, carried));
                var retest = closest(questions, .60); used.add(retest.id());
                nodes.add(node(skillId, retest, Phase.RETEST, 2, carried));
            }
        }
        if (!missing.isEmpty()) return blocked("NO_CONTENT", "路径内容不足，无法安全安排复测", missing);
        if (nodes.isEmpty()) return blocked("NO_ELIGIBLE_TARGET", "暂无可进入的路径内容", List.of());
        var notices = new ArrayList<String>();
        if ("DIAGNOSTIC".equals(mode)) notices.add("当前无足够证据判定薄弱，按稳定先修顺序安排诊断。");
        return new Result(Status.ACTIVE, null, null, nodes, List.of(), notices);
    }

    private static Result blocked(String code, String reason, List<Missing> missing) {
        return new Result(Status.BLOCKED, code, reason, List.of(), missing, List.of());
    }
    private static PlanNode node(String skillId, Candidate c, Phase phase, int round,
                                 Map<String, ExistingNode> carried) {
        var old = carried.get(c.type() + ":" + c.id());
        return new PlanNode(skillId, c.type(), c.id(), c.title(), phase, round,
                old == null ? NodeStatus.PENDING : NodeStatus.SKIPPED, old == null ? null : old.nodeId());
    }
    private static Comparator<Candidate> resourceOrder(Skill s) {
        double target = Math.max(.2, Math.min(.7, s.mastery() + .1));
        return Comparator.comparingDouble((Candidate c) -> Math.abs(c.difficulty() - target))
                .thenComparingInt(Candidate::sortOrder).thenComparing(Candidate::id);
    }
    private static Candidate closest(List<Candidate> values, double target) {
        if (values.isEmpty()) throw invalid("Not enough question candidates");
        return values.stream().min(Comparator.comparingDouble((Candidate c) -> Math.abs(c.difficulty() - target))
                .thenComparingInt(Candidate::sortOrder).thenComparing(Candidate::id)).orElseThrow();
    }
    private static double weakness(Skill s, Map<String, Set<String>> ancestors, Map<String, Skill> skills) {
        long descendants = ancestors.values().stream().filter(set -> set.contains(s.id())).count();
        double impact = (double) descendants / Math.max(1, skills.size() - 1);
        return .6 * (1 - s.mastery()) + .2 * s.recentErrorRate() + .1 * .5 + .1 * impact;
    }
    private static boolean eligible(String id, Map<String, Set<String>> ancestors, Map<String, Skill> skills) {
        return ancestors.get(id).stream().allMatch(parent -> skills.get(parent).adequate());
    }
    private static Set<String> ancestors(String id, Map<String, Set<String>> parents,
                                         Map<String, Set<String>> cache, Set<String> visiting) {
        if (cache.containsKey(id)) return cache.get(id);
        if (!visiting.add(id)) throw invalid("Cyclic prerequisite graph");
        var result = new TreeSet<String>();
        for (String parent : parents.get(id)) { result.add(parent); result.addAll(ancestors(parent, parents, cache, visiting)); }
        visiting.remove(id); cache.put(id, Set.copyOf(result)); return cache.get(id);
    }
    private static List<String> topological(Map<String, Skill> skills, Map<String, Set<String>> parents) {
        var indegree = new HashMap<String, Integer>();
        var children = new HashMap<String, Set<String>>();
        for (String id : skills.keySet()) { indegree.put(id, parents.get(id).size()); children.put(id, new TreeSet<>()); }
        for (var e : parents.entrySet()) for (String p : e.getValue()) children.get(p).add(e.getKey());
        var ready = new PriorityQueue<String>(Comparator.comparingInt((String id) -> skills.get(id).order()).thenComparing(String::compareTo));
        indegree.forEach((id, n) -> { if (n == 0) ready.add(id); });
        var result = new ArrayList<String>();
        while (!ready.isEmpty()) { String id = ready.remove(); result.add(id); for (String child : children.get(id)) if (indegree.merge(child, -1, Integer::sum) == 0) ready.add(child); }
        if (result.size() != skills.size()) throw invalid("Cyclic prerequisite graph");
        return result;
    }
}
