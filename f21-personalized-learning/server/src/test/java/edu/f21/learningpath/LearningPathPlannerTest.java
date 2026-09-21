package edu.f21.learningpath;

import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import static edu.f21.learningpath.LearningPathPlanner.*;

class LearningPathPlannerTest {
    private Skill skill(String id, int order, double mastery, int count) {
        return new Skill(id, id, order, mastery, count, count, count == 0 ? null : .5);
    }
    private Candidate q(String id, String skill, double difficulty, int order) {
        return new Candidate(id, "c", ItemType.QUESTION, skill, id, true, difficulty, order);
    }
    private Candidate r(String id, String skill, double difficulty, int order) {
        return new Candidate(id, "c", ItemType.RESOURCE, skill, id, true, difficulty, order);
    }
    private Input input(List<Skill> skills, List<Edge> edges, List<Candidate> candidates) {
        return new Input("c", "v1", skills, edges, candidates);
    }
    private List<Candidate> five(String skill) {
        return List.of(r("r-" + skill, skill, .3, 0), q("q1-" + skill, skill, .2, 0),
                q("q2-" + skill, skill, .4, 1), q("q3-" + skill, skill, .6, 2),
                q("q4-" + skill, skill, .8, 3));
    }

    @Test void topologicalOrderIsStableForChainAndMerge() {
        var skills = List.of(skill("c", 2, .2, 0), skill("a", 0, .2, 0), skill("b", 1, .2, 0));
        var result = LearningPathPlanner.plan(input(skills, List.of(new Edge("a", "b"), new Edge("b", "c")), List.of()));
        assertEquals(Status.BLOCKED, result.status());
        assertEquals(List.of(), result.nodes());
        assertEquals("NO_CONTENT", result.reasonCode());
    }

    @Test void weakTargetIncludesUnmetPrerequisiteInTopoOrder() {
        var skills = List.of(skill("a", 0, .2, 3), skill("b", 1, .2, 3));
        var candidates = new ArrayList<>(five("a")); candidates.addAll(five("b"));
        var result = LearningPathPlanner.plan(input(skills, List.of(new Edge("a", "b")), candidates));
        assertEquals(List.of("a", "b"), result.nodes().stream().map(PlanNode::skillId).distinct().toList());
        assertEquals(List.of(Phase.RESOURCE, Phase.BASIC_PRACTICE, Phase.ADVANCED_PRACTICE, Phase.RETEST),
                result.nodes().stream().limit(4).map(PlanNode::phase).toList());
    }

    @Test void allAdequateReturnsCompletedEmptyPath() {
        var result = LearningPathPlanner.plan(input(List.of(skill("a", 0, .6, 3)), List.of(), List.of()));
        assertEquals(Status.COMPLETED, result.status());
        assertTrue(result.nodes().isEmpty());
    }

    @Test void diagnosticsChooseOnlyEligibleRoot() {
        var skills = List.of(skill("a", 0, .2, 0), skill("b", 1, .2, 0));
        var candidates = new ArrayList<>(five("a")); candidates.addAll(five("b"));
        var result = LearningPathPlanner.plan(input(skills, List.of(new Edge("a", "b")), candidates));
        assertEquals(List.of("a"), result.nodes().stream().map(PlanNode::skillId).distinct().toList());
        assertTrue(result.notices().get(0).contains("诊断"));
    }

    @Test void secondRoundUsesTwoUnusedQuestionsOnly() {
        var candidates = new ArrayList<>(five("a"));
        candidates.add(q("q5-a", "a", .55, 4));
        candidates.add(q("q6-a", "a", .7, 5));
        var first = LearningPathPlanner.plan(input(List.of(skill("a", 0, .2, 3)), List.of(), candidates));
        var used = first.nodes().stream().map(PlanNode::itemId).collect(java.util.stream.Collectors.toSet());
        var retry = LearningPathPlanner.plan(new Input("c", "v1", List.of(skill("a", 0, .2, 3)), List.of(), candidates,
                first.nodes().stream().map(n -> new ExistingNode("old-" + n.itemId(), n.skillId(), n.itemType(), n.itemId(), n.phase(), n.roundNo(), n.status())).toList(),
                Map.of("a", 2), used));
        assertEquals(Status.ACTIVE, retry.status());
        assertEquals(List.of(Phase.REMEDIATION, Phase.RETEST), retry.nodes().stream().map(PlanNode::phase).toList());
        assertEquals(2, retry.nodes().stream().map(PlanNode::itemId).distinct().count());
    }

    @Test void carryCompletedNodeAsSkippedWithoutCopyingEvent() {
        var candidates = five("a");
        var old = new ExistingNode("old-r", "a", ItemType.RESOURCE, "r-a", Phase.RESOURCE, 1, NodeStatus.COMPLETED);
        var result = LearningPathPlanner.plan(new Input("c", "v1", List.of(skill("a", 0, .2, 3)), List.of(), candidates,
                List.of(old), Map.of(), Set.of()));
        assertEquals(NodeStatus.SKIPPED, result.nodes().get(0).status());
        assertEquals("old-r", result.nodes().get(0).carriedFromNodeId());
    }

    @Test void rejectsCycleSelfEdgeDanglingAndDuplicateEdge() {
        var skills = List.of(skill("a", 0, .2, 0), skill("b", 1, .2, 0));
        assertThrows(IllegalArgumentException.class, () -> LearningPathPlanner.plan(input(skills, List.of(new Edge("a", "a")), List.of())));
        assertThrows(IllegalArgumentException.class, () -> LearningPathPlanner.plan(input(skills, List.of(new Edge("a", "b"), new Edge("b", "a")), List.of())));
        assertThrows(IllegalArgumentException.class, () -> LearningPathPlanner.plan(input(skills, List.of(new Edge("z", "a")), List.of())));
        assertThrows(IllegalArgumentException.class, () -> LearningPathPlanner.plan(input(skills, List.of(new Edge("a", "b"), new Edge("a", "b")), List.of())));
    }

    @Test void contentShortageNamesExactRequirement() {
        var result = LearningPathPlanner.plan(input(List.of(skill("a", 0, .2, 0)), List.of(),
                List.of(r("r", "a", .2, 0), q("q1", "a", .2, 0), q("q2", "a", .4, 1))));
        assertEquals(Status.BLOCKED, result.status());
        assertEquals("QUESTION_ROUND_1", result.missing().get(0).requirement());
        assertEquals(3, result.missing().get(0).required());
    }

    @Test void invalidNumbersAreRejected() {
        assertThrows(IllegalArgumentException.class, () -> skill("a", 0, Double.NaN, 0));
        assertThrows(IllegalArgumentException.class, () -> q("q", "a", Double.POSITIVE_INFINITY, 0));
        assertThrows(IllegalArgumentException.class, () -> new Input("c", "v1", List.of(skill("a", 0, .2, 0)), List.of(), List.of(), List.of(), Map.of("a", 1), Set.of()));
    }

    @Test void deterministicUnderInputPermutation() {
        var skills = new ArrayList<>(List.of(skill("a", 0, .2, 3), skill("b", 1, .2, 3)));
        var candidates = new ArrayList<Candidate>(); candidates.addAll(five("a")); candidates.addAll(five("b"));
        var expected = LearningPathPlanner.plan(input(skills, List.of(new Edge("a", "b")), candidates));
        Collections.shuffle(skills, new Random(1)); Collections.shuffle(candidates, new Random(2));
        var actual = LearningPathPlanner.plan(input(skills, List.of(new Edge("a", "b")), candidates));
        assertEquals(expected, actual);
    }
}
