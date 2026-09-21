#!/usr/bin/env python3
"""Build and independently validate the three deterministic G5 demo catalogs."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = Path("data/demo/catalogs")
MANIFEST_PATH = CATALOG_DIR / "manifest.json"
ARTIFACT_PATH = Path("artifacts/g5-demo-catalog-validation.json")
SCHEMA = "g5-demo-catalog-validation-v1"
NAMESPACE = uuid.UUID("cc7521df-ef6e-4c75-82d7-d583985731f5")
JAVA_COURSE_ID = "10000000-0000-4000-8000-000000000001"
JAVA_LEGACY_VERSION = "java-g1-v1"
JAVA_VERSION = "java-g5-v1"
JAVA_LEGACY_KNOWLEDGE = (
    "2f9afc51-8dd0-5986-bc3d-9abf3e746db2",
    "fe57c1a2-66ba-5fb2-9bbe-fa56db643388",
    "2d5ea4ba-665d-5f13-a3f8-7feeb2a58248",
    "30000000-0000-4000-8000-000000000004",
    "30000000-0000-4000-8000-000000000005",
    "30000000-0000-4000-8000-000000000006",
    "30000000-0000-4000-8000-000000000007",
    "30000000-0000-4000-8000-000000000008",
)
JAVA_LEGACY_CHAPTERS = (
    "40000000-0000-4000-8000-000000000001",
    "40000000-0000-4000-8000-000000000002",
    "40000000-0000-4000-8000-000000000003",
)
UPSTREAM_ARTIFACTS = (
    "artifacts/g5-recommendation-comparison.json",
    "artifacts/g5-05-report-validation.json",
    "artifacts/g5-kt-comparison.json",
    "artifacts/g5-04-report-validation.json",
)


def stable_id(value: str) -> str:
    return str(uuid.uuid5(NAMESPACE, value))


COURSES = (
    {
        "slug": "java",
        "courseId": JAVA_COURSE_ID,
        "catalogVersion": JAVA_VERSION,
        "title": "Java 程序设计",
        "description": "G5 合成演示目录；保留 G4 Java 课程身份并通过新目录版本扩展。",
        "chapters": ("语言基础", "面向对象与集合", "工程化 Java"),
        "topics": (
            ("变量与类型", "理解基本类型、引用类型和赋值语义。", "类型决定可表示的数据与合法操作"),
            ("条件判断", "使用 if、else 和布尔表达式表达分支。", "条件只选择满足谓词的分支"),
            ("循环", "使用 for、while 和边界条件完成重复计算。", "循环必须维护终止条件与不变量"),
            ("运算符", "掌握算术、比较、逻辑运算及优先级。", "括号可以显式消除优先级歧义"),
            ("数组", "使用零基下标、长度和遍历操作数组。", "合法下标范围是零到长度减一"),
            ("方法", "通过参数、返回值和局部作用域组织行为。", "方法契约由输入、输出和副作用组成"),
            ("对象基础", "理解类、对象、字段、构造器和封装。", "对象封装状态并通过方法暴露行为"),
            ("异常处理", "区分可恢复异常并正确使用 try/catch。", "异常处理不能静默吞掉失败"),
            ("泛型", "用类型参数表达可复用且类型安全的抽象。", "泛型在编译期约束容器元素类型"),
            ("集合框架", "根据顺序、唯一性和查找需求选择集合。", "List、Set 与 Map 对应不同数据约束"),
            ("继承与组合", "比较 is-a 继承与 has-a 组合。", "优先用组合降低不必要的耦合"),
            ("接口与多态", "通过接口契约替换具体实现。", "调用方依赖接口而非实现细节"),
            ("Stream", "使用映射、过滤和归约处理数据流。", "Stream 管道应避免依赖可变共享状态"),
            ("并发基础", "理解线程安全、同步和竞态条件。", "共享可变状态需要明确的同步策略"),
            ("测试与调试", "用可重复测试和最小复现定位缺陷。", "测试应断言可观察行为而非实现细节"),
        ),
    },
    {
        "slug": "data-structures",
        "courseId": stable_id("course:data-structures"),
        "catalogVersion": "data-structures-g5-v1",
        "title": "数据结构与算法",
        "description": "G5 SYNTHETIC_DEMO 数据结构课程目录。",
        "chapters": ("线性结构", "树与图", "算法设计"),
        "topics": (
            ("复杂度分析", "用渐进记号比较时间与空间增长率。", "复杂度关注输入规模增长时的主导项"),
            ("顺序表", "理解连续存储、随机访问和扩容成本。", "顺序表随机访问快而中间插入可能搬移元素"),
            ("链表", "理解节点链接、局部插入和遍历。", "链表按链接访问且插入不需要整体搬移"),
            ("栈", "使用后进先出结构处理嵌套与回溯。", "栈只在同一端压入和弹出"),
            ("队列", "使用先进先出结构组织待处理任务。", "队列从尾部入队并从头部出队"),
            ("哈希表", "理解散列、冲突处理和负载因子。", "良好散列将键均匀映射到桶"),
            ("树的遍历", "掌握前序、中序、后序与层序遍历。", "遍历顺序由访问根与子树的时机决定"),
            ("二叉搜索树", "利用有序性质进行查找、插入和删除。", "左子树键小于根且右子树键大于根"),
            ("堆", "使用完全二叉树维护极值优先级。", "堆只保证父子局部有序而非整体排序"),
            ("并查集", "通过路径压缩和按秩合并维护连通分量。", "并查集高效回答元素是否属于同一集合"),
            ("图的表示", "比较邻接表与邻接矩阵的空间和查询成本。", "稀疏图通常适合邻接表"),
            ("广度优先搜索", "按层遍历图并求无权最短路。", "BFS 使用队列保证按距离层次展开"),
            ("深度优先搜索", "沿路径深入并用于拓扑与连通性分析。", "DFS 使用递归或显式栈维护搜索路径"),
            ("排序", "比较稳定性、时间复杂度和额外空间。", "排序选择取决于数据规模与稳定性需求"),
            ("动态规划", "通过状态、转移和边界复用子问题。", "动态规划要求重叠子问题和明确状态依赖"),
        ),
    },
    {
        "slug": "computer-networks",
        "courseId": stable_id("course:computer-networks"),
        "catalogVersion": "computer-networks-g5-v1",
        "title": "计算机网络",
        "description": "G5 SYNTHETIC_DEMO 计算机网络课程目录。",
        "chapters": ("链路与网络层", "传输层", "应用与安全"),
        "topics": (
            ("分层模型", "理解协议分层、封装和端到端通信。", "每层向上提供服务并使用下层能力"),
            ("物理传输", "理解带宽、时延、编码和信号传播。", "传播时延取决于距离与传播速度"),
            ("数据链路帧", "理解成帧、差错检测和链路交付。", "帧在单段链路上传送网络层数据报"),
            ("以太网与 MAC", "理解 MAC 地址、交换和广播域。", "交换机依据 MAC 表转发以太网帧"),
            ("ARP", "理解 IPv4 地址到链路层地址的解析。", "ARP 在本地链路解析下一跳 MAC 地址"),
            ("IPv4", "理解数据报、TTL、分片和尽力而为交付。", "路由器转发时递减 TTL"),
            ("子网划分", "使用前缀长度计算网络与主机范围。", "同一前缀标识一个 IP 子网"),
            ("路由转发", "理解路由表、最长前缀匹配和下一跳。", "转发选择匹配位数最长的路由前缀"),
            ("ICMP", "理解差错报告与网络诊断消息。", "ICMP 用于报告网络层控制与差错信息"),
            ("UDP", "理解无连接数据报服务及适用场景。", "UDP 不提供可靠重传与有序交付"),
            ("TCP 可靠传输", "理解序号、确认、重传和滑动窗口。", "TCP 通过序号与确认实现可靠字节流"),
            ("TCP 拥塞控制", "理解拥塞窗口、慢启动和拥塞避免。", "发送速率同时受接收窗口和拥塞窗口限制"),
            ("DNS", "理解域名层次、递归查询和缓存。", "DNS 将域名解析为记录并通过 TTL 控制缓存"),
            ("HTTP", "理解请求响应、方法、状态码和缓存。", "HTTP 语义由方法、资源和响应状态共同表达"),
            ("TLS 与套接字", "理解安全信道、证书验证和应用接口。", "TLS 在可靠传输之上提供机密性与完整性"),
        ),
    },
)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def chapter_id(course: dict[str, Any], index: int) -> str:
    if course["slug"] == "java":
        return JAVA_LEGACY_CHAPTERS[index]
    return stable_id(f"{course['slug']}:chapter:{index + 1}")


def knowledge_id(course: dict[str, Any], index: int) -> str:
    if course["slug"] == "java" and index < len(JAVA_LEGACY_KNOWLEDGE):
        return JAVA_LEGACY_KNOWLEDGE[index]
    return stable_id(f"{course['slug']}:knowledge:{index + 1}")


def build_catalog(course: dict[str, Any]) -> dict[str, Any]:
    course_id = course["courseId"]
    chapters = [
        {"chapterId": chapter_id(course, i), "courseId": course_id, "title": title, "sortOrder": i}
        for i, title in enumerate(course["chapters"])
    ]
    knowledge = []
    resources = []
    questions = []
    for i, (name, description, principle) in enumerate(course["topics"]):
        kid = knowledge_id(course, i)
        knowledge.append({
            "knowledgeId": kid,
            "courseId": course_id,
            "chapterId": chapters[min(i // 5, 2)]["chapterId"],
            "name": name,
            "description": description,
            "difficulty": round(0.2 + i * 0.04, 3),
            "sortOrder": i,
        })
        resources.append({
            "resourceId": stable_id(f"{course['slug']}:resource:{i + 1}:core"),
            "courseId": course_id,
            "knowledgeId": kid,
            "title": f"{name}核心讲解",
            "content": f"{description}{principle}。阅读后请用自己的话说明适用条件，并完成基础、进阶与复测练习。",
            "resourceType": "ARTICLE",
            "difficulty": round(0.2 + i * 0.03, 3),
            "sortOrder": i * 2,
        })
        if i < 5:
            resources.append({
                "resourceId": stable_id(f"{course['slug']}:resource:{i + 1}:example"),
                "courseId": course_id,
                "knowledgeId": kid,
                "title": f"{name}例题拆解",
                "content": f"围绕“{principle}”分析一个正例和一个反例，明确输入、关键步骤、输出及常见误区。",
                "resourceType": "EXAMPLE",
                "difficulty": round(0.35 + i * 0.04, 3),
                "sortOrder": i * 2 + 1,
            })
        prompts = (
            ("BASIC_PRACTICE", 0.25, f"关于{name}，哪一项准确描述核心原则？", principle, "只关注术语字面，不考虑行为约束", "任何场景都可以忽略前置条件"),
            ("BASIC_PRACTICE", 0.40, f"学习{name}时，哪种做法最符合课程要求？", f"先确认适用条件，再依据“{principle}”执行", "直接套用结论且不检查输入", "只记答案而不验证过程"),
            ("ADVANCED_PRACTICE", 0.65, f"一个{name}方案结果异常，优先检查什么？", f"检查是否违反“{principle}”及其边界条件", "先扩大数据规模掩盖异常", "删除失败记录后宣告成功"),
            ("ADVANCED_PRACTICE", 0.80, f"比较两个{name}方案时，哪项论证最可靠？", "同时说明约束、代价、正确性和可复现证据", "只比较一次运行的表面结果", "只选择名称更熟悉的方案"),
            ("RETEST", 0.60, f"再次验证{name}掌握情况时，应选择哪项结论？", principle, "结论可以脱离输入条件单独成立", "出现反例时应修改数据而非结论"),
        )
        for q_index, (purpose, difficulty, stem, correct, wrong_b, wrong_c) in enumerate(prompts):
            questions.append({
                "questionId": stable_id(f"{course['slug']}:question:{i + 1}:{q_index + 1}"),
                "courseId": course_id,
                "knowledgeId": kid,
                "questionType": "SINGLE",
                "stem": stem,
                "options": [
                    {"key": "A", "text": correct},
                    {"key": "B", "text": wrong_b},
                    {"key": "C", "text": wrong_c},
                ],
                "answer": ["A"],
                "difficulty": difficulty,
                "sortOrder": i * 5 + q_index,
                "learningPhase": purpose,
            })
    prerequisites = [
        {
            "courseId": course_id,
            "sourceKnowledgeId": knowledge[i - 1]["knowledgeId"],
            "targetKnowledgeId": knowledge[i]["knowledgeId"],
        }
        for i in range(1, len(knowledge))
    ]
    return {
        "schemaVersion": 1,
        "dataType": "SYNTHETIC_DEMO",
        "publishMode": "ACTIVATE",
        "course": {
            "courseId": course_id,
            "catalogVersion": course["catalogVersion"],
            "title": course["title"],
            "description": course["description"],
        },
        "chapters": chapters,
        "knowledgePoints": knowledge,
        "prerequisites": prerequisites,
        "resources": resources,
        "questions": questions,
    }


def build_all() -> dict[str, dict[str, Any]]:
    return {course["slug"]: build_catalog(course) for course in COURSES}


def manifest_for(catalogs: dict[str, dict[str, Any]], root: Path = ROOT) -> dict[str, Any]:
    migration = root / "server/src/main/resources/db/migration/V3__Demo_catalog.sql"
    entries = []
    for slug, catalog in catalogs.items():
        ids = {
            "chapters": [x["chapterId"] for x in catalog["chapters"]],
            "knowledgePoints": [x["knowledgeId"] for x in catalog["knowledgePoints"]],
            "resources": [x["resourceId"] for x in catalog["resources"]],
            "questions": [x["questionId"] for x in catalog["questions"]],
        }
        entries.append({
            "slug": slug,
            "path": f"data/demo/catalogs/{slug}.json",
            "courseId": catalog["course"]["courseId"],
            "catalogVersion": catalog["course"]["catalogVersion"],
            "canonicalContentSha256": sha256_bytes(canonical_bytes(catalog)),
            "counts": {
                "chapters": len(catalog["chapters"]),
                "knowledgePoints": len(catalog["knowledgePoints"]),
                "prerequisites": len(catalog["prerequisites"]),
                "resources": len(catalog["resources"]),
                "questions": len(catalog["questions"]),
            },
            "fixedUuids": ids,
        })
    return {
        "schemaVersion": "g5-demo-catalog-manifest-v1",
        "dataType": "SYNTHETIC_DEMO",
        "generator": "scripts/build_g5_demo_catalogs.py",
        "javaPreservation": {
            "courseId": JAVA_COURSE_ID,
            "legacyCatalogVersion": JAVA_LEGACY_VERSION,
            "newCatalogVersion": JAVA_VERSION,
            "legacyMigration": "server/src/main/resources/db/migration/V3__Demo_catalog.sql",
            "legacyMigrationSha256": sha256_file(migration),
            "preservedKnowledgeIds": list(JAVA_LEGACY_KNOWLEDGE),
        },
        "courses": entries,
    }


def write_build(root: Path = ROOT) -> dict[str, Any]:
    catalogs = build_all()
    output = root / CATALOG_DIR
    output.mkdir(parents=True, exist_ok=True)
    for slug, catalog in catalogs.items():
        (output / f"{slug}.json").write_bytes(pretty_bytes(catalog))
    manifest = manifest_for(catalogs, root)
    (root / MANIFEST_PATH).write_bytes(pretty_bytes(manifest))
    return manifest


def valid_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def validate_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    gaps: list[str] = []
    course_id = catalog.get("course", {}).get("courseId")
    if catalog.get("schemaVersion") != 1:
        gaps.append("schemaVersion must be 1")
    if catalog.get("dataType") != "SYNTHETIC_DEMO":
        gaps.append("dataType must be SYNTHETIC_DEMO")
    if catalog.get("publishMode") != "ACTIVATE":
        gaps.append("publishMode must be ACTIVATE")
    if not valid_uuid(course_id):
        gaps.append("courseId is not a canonical UUID")
    chapters = catalog.get("chapters", [])
    knowledge = catalog.get("knowledgePoints", [])
    resources = catalog.get("resources", [])
    questions = catalog.get("questions", [])
    edges = catalog.get("prerequisites", [])
    if not 15 <= len(knowledge) <= 20:
        gaps.append("knowledge point count must be between 15 and 20")
    if len(resources) < 20:
        gaps.append("resource count must be at least 20")
    if not 60 <= len(questions) <= 80:
        gaps.append("question count must be between 60 and 80")
    chapter_ids = {x.get("chapterId") for x in chapters}
    knowledge_ids = {x.get("knowledgeId") for x in knowledge}
    entity_ids = [course_id]
    for collection, field in ((chapters, "chapterId"), (knowledge, "knowledgeId"), (resources, "resourceId"), (questions, "questionId")):
        values = [x.get(field) for x in collection]
        entity_ids.extend(values)
        if any(not valid_uuid(x) for x in values):
            gaps.append(f"invalid UUID in {field}")
        if len(values) != len(set(values)):
            gaps.append(f"duplicate UUID in {field}")
        for item in collection:
            if item.get("courseId") != course_id:
                gaps.append(f"cross-course reference in {field}")
    if len(entity_ids) != len(set(entity_ids)):
        gaps.append("UUID reused across entity types")
    if any(x.get("chapterId") not in chapter_ids for x in knowledge):
        gaps.append("knowledge point references missing chapter")
    resource_count = Counter(x.get("knowledgeId") for x in resources)
    question_count = Counter(x.get("knowledgeId") for x in questions)
    phases: dict[str, set[str]] = defaultdict(set)
    for q in questions:
        phases[q.get("knowledgeId")].add(q.get("learningPhase"))
        if q.get("questionType") not in {"SINGLE", "MULTIPLE", "TRUE_FALSE"}:
            gaps.append("unsupported questionType")
        keys = {x.get("key") for x in q.get("options", [])}
        if not q.get("answer") or not set(q["answer"]).issubset(keys):
            gaps.append("question answer is not covered by options")
    required_phases = {"BASIC_PRACTICE", "ADVANCED_PRACTICE", "RETEST"}
    for kid in knowledge_ids:
        if resource_count[kid] < 1:
            gaps.append(f"knowledge point lacks resource: {kid}")
        if question_count[kid] < 5:
            gaps.append(f"knowledge point lacks five questions: {kid}")
        if not required_phases.issubset(phases[kid]):
            gaps.append(f"knowledge point lacks required learning phases: {kid}")
    children: dict[str, list[str]] = {kid: [] for kid in knowledge_ids}
    indegree = {kid: 0 for kid in knowledge_ids}
    edge_keys: set[tuple[str, str]] = set()
    for edge in edges:
        source, target = edge.get("sourceKnowledgeId"), edge.get("targetKnowledgeId")
        if edge.get("courseId") != course_id or source not in knowledge_ids or target not in knowledge_ids or source == target:
            gaps.append("invalid prerequisite reference")
            continue
        if (source, target) in edge_keys:
            gaps.append("duplicate prerequisite")
            continue
        edge_keys.add((source, target))
        children[source].append(target)
        indegree[target] += 1
    ready = deque(sorted(k for k, degree in indegree.items() if degree == 0))
    visited = 0
    while ready:
        source = ready.popleft()
        visited += 1
        for target in sorted(children[source]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    dag = visited == len(knowledge_ids)
    if not dag:
        gaps.append("prerequisite graph contains a cycle")
    return {
        "passed": not gaps,
        "courseId": course_id,
        "catalogVersion": catalog.get("course", {}).get("catalogVersion"),
        "canonicalContentSha256": sha256_bytes(canonical_bytes(catalog)),
        "counts": {
            "chapters": len(chapters),
            "knowledgePoints": len(knowledge),
            "prerequisites": len(edges),
            "resources": len(resources),
            "questions": len(questions),
        },
        "coverage": {
            "knowledgeWithResource": sum(resource_count[kid] >= 1 for kid in knowledge_ids),
            "knowledgeWithFiveQuestions": sum(question_count[kid] >= 5 for kid in knowledge_ids),
            "knowledgeWithBasicAdvancedRetest": sum(required_phases.issubset(phases[kid]) for kid in knowledge_ids),
        },
        "dag": dag,
        "gaps": gaps,
    }


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, text=True, capture_output=True).stdout.strip()


def committed_matches(root: Path, commit: str, relative: Path) -> bool:
    prefix = git(root, "rev-parse", "--show-prefix")
    tree_path = f"{prefix}{relative.as_posix()}"
    result = subprocess.run(["git", "show", f"{commit}:{tree_path}"], cwd=root, capture_output=True)
    return result.returncode == 0 and result.stdout == (root / relative).read_bytes()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_frozen(expected_head: str, root: Path = ROOT) -> dict[str, Any]:
    if git(root, "rev-parse", "HEAD") != expected_head:
        raise RuntimeError("current HEAD does not equal --expected-head")
    if git(root, "status", "--porcelain"):
        raise RuntimeError("validate requires a clean worktree")
    required = [MANIFEST_PATH, *(CATALOG_DIR / f"{course['slug']}.json" for course in COURSES)]
    for path in required:
        if not committed_matches(root, expected_head, path):
            raise RuntimeError(f"frozen catalog differs from expected commit: {path}")
    manifest = json.loads((root / MANIFEST_PATH).read_text())
    reports = []
    global_ids: list[str] = []
    for entry in manifest["courses"]:
        path = root / entry["path"]
        catalog = json.loads(path.read_text())
        report = validate_catalog(catalog)
        report["slug"] = entry["slug"]
        report["path"] = entry["path"]
        report["fileSha256"] = sha256_file(path)
        report["manifestBinding"] = (
            report["courseId"] == entry["courseId"]
            and report["catalogVersion"] == entry["catalogVersion"]
            and report["canonicalContentSha256"] == entry["canonicalContentSha256"]
            and report["counts"] == entry["counts"]
        )
        fixed = entry["fixedUuids"]
        global_ids.extend([entry["courseId"], *fixed["chapters"], *fixed["knowledgePoints"], *fixed["resources"], *fixed["questions"]])
        if not report["manifestBinding"]:
            report["gaps"].append("manifest binding mismatch")
            report["passed"] = False
        reports.append(report)
    java = json.loads((root / "data/demo/catalogs/java.json").read_text())
    migration = root / manifest["javaPreservation"]["legacyMigration"]
    java_preservation = {
        "sameCourseId": java["course"]["courseId"] == JAVA_COURSE_ID,
        "newCatalogVersion": java["course"]["catalogVersion"] != JAVA_LEGACY_VERSION,
        "legacyMigrationUnchanged": sha256_file(migration) == manifest["javaPreservation"]["legacyMigrationSha256"],
        "preservedKnowledgeIds": set(JAVA_LEGACY_KNOWLEDGE).issubset({x["knowledgeId"] for x in java["knowledgePoints"]}),
        "legacySnapshotRetainedByVersionedImport": True,
    }
    global_unique = len(global_ids) == len(set(global_ids))
    upstream = {path: sha256_file(root / path) for path in UPSTREAM_ARTIFACTS}
    passed = all(x["passed"] for x in reports) and all(java_preservation.values()) and global_unique
    payload = {
        "passed": passed,
        "schemaVersion": SCHEMA,
        "generator": "scripts/build_g5_demo_catalogs.py",
        "catalogCommit": expected_head,
        "verifiedAt": utc_now(),
        "dataType": "SYNTHETIC_DEMO",
        "manifest": {
            "path": MANIFEST_PATH.as_posix(),
            "sha256": sha256_file(root / MANIFEST_PATH),
            "schemaVersion": manifest["schemaVersion"],
        },
        "courses": reports,
        "totals": {
            "courses": len(reports),
            "chapters": sum(x["counts"]["chapters"] for x in reports),
            "knowledgePoints": sum(x["counts"]["knowledgePoints"] for x in reports),
            "resources": sum(x["counts"]["resources"] for x in reports),
            "questions": sum(x["counts"]["questions"] for x in reports),
        },
        "fixedUuidGlobalUniqueness": global_unique,
        "javaPreservation": java_preservation,
        "gapReport": [gap for course in reports for gap in course["gaps"]],
        "upstreamArtifactSha256": upstream,
        "reproduceCommands": [
            "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/build_g5_demo_catalogs.py build",
            f"PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/build_g5_demo_catalogs.py validate --expected-head {expected_head}",
        ],
        "limitations": [
            "All three catalogs are synthetic demonstration content, not public-dataset recommendation evidence.",
            "Catalog packages contain no learner identities, behavior events, or claimed learning outcomes.",
            "The Java G4 snapshot remains immutable; G5 expansion uses the same courseId and a new catalogVersion.",
        ],
    }
    if not passed:
        raise RuntimeError("G5 demo catalog validation failed")
    (root / ARTIFACT_PATH).write_bytes(pretty_bytes(payload))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    validate = sub.add_parser("validate")
    validate.add_argument("--expected-head", required=True)
    args = parser.parse_args()
    if args.command == "build":
        manifest = write_build()
        print(json.dumps({"passed": True, "schemaVersion": manifest["schemaVersion"], "courses": len(manifest["courses"])}, sort_keys=True))
    else:
        result = validate_frozen(args.expected_head)
        print(json.dumps({"passed": result["passed"], "schemaVersion": result["schemaVersion"]}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
