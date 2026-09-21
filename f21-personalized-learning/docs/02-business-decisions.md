# 六项业务规则与状态冻结
这些规则覆盖实施歧义；详细定义以05 V2.0为依据。

## D1 数值
BKT mastery为潜在掌握估计；DKT为下一步技能答对概率，不互相覆盖。
confidence若使用只代表证据充足度，不叫统计置信概率。默认evidenceCount>=3且evidenceWeight>=2才允许判薄弱；mastery<0.6为薄弱。
tests/fixtures/bkt-vectors.json使用Fraction有理数独立计算作为参考；Java和Python均检查同一预期。多技能插值属于本项目启发式。

## D2 更新来源
只有服务端QUESTION_ANSWERED影响BKT。RESOURCE_VIEWED/COMPLETED、EXAM_SUBMITTED、反馈不直接更新BKT。
学生作答请求只含questionId、answer和catalogVersion（校验题目版本），服务端判分。事件导入只允许管理员；schema样例中的correct不意味着学生可提交它。

## D3 完成归因
反馈COMPLETED默认自报trusted=false。后端消费可信答题事件后才能派生可信完成；学生提供sourceEventId也必须查本地事件身份、类型、项目、课程，不能据此直接信任。
反馈和学习行为可关联同一sourceEventId，仅事件消费者更新BKT。
Idempotency-Key绑定用户+操作+请求哈希；同键同内容重放首次结果，同键不同内容409。

## D4 时效
用户课程状态stateRevision递增；推荐/路径/DKT结果携带相同revision和catalogVersion。
GET推荐只读；无结果items=[]。POST生成使用幂等键，提交前确认revision未变。
重复查看不增revision；有效学习和影响排序的偏好变化递增。
stale=true时仍可回看历史，但UI不称为当前结果。

## D5 事件/作业状态
PENDING→SUCCEEDED；事务失败→FAILED→重试；缺快照→PENDING_COMPENSATION→补齐后重建；5次失败→NEEDS_ATTENTION。
日志存在不等于已成功。事务A提交判分+事件；事务B原子提交交互+BKT+历史+消费成功。外部网络调用不进入事务B。
晚到触发REBUILD_REQUIRED→REBUILDING→READY；重建失败保留旧结果标stale。模拟回放测试仅验证算法等价性，不证明数据库事务已实现。

## D6 目录与路径
目录/modelVersion不复用；新版本校验后原子激活并重建BKT，不改变旧事件关联。
路径ACTIVE→COMPLETED或INVALIDATED/BLOCKED；复测最多自动2轮，随后NEEDS_REVIEW为业务原因，不额外发明顶层路径状态。
新版本继承仍有效的完成证据，不复制原始完成事件。资源完成不表示技能掌握。

## UI异常检查
web目录提供练习、掌握度、推荐、路径、实验五个页面选择，每个正常/空/处理中/失败四态。
以上是开发前历史状态稿说明；G1已替换真实页面，G2以以下规则实现真实学习操作。

## G2 实施细则（电脑网页版）

- G2只做作答、历史、画像摘要及BKT；不要求手机适配，不提前写入模拟掌握度。
- V4冻结现有目录、知识点和题目快照。题目详情返回catalogVersion；提交必须携带该版本。当前版本变化返回409，不用旧页面静默提交新题。题干、选项和判分均来自同一快照；题目下架仍即时阻止新提交。
- 答案支持单选字符串或多选字符串数组；按选项键校验、排序归一化，重复选项拒绝。G1内置题均为单选。暂不接收recommendationId、correct、userId、occurredAt等客户端字段。
- Idempotency-Key为UUID，绑定当前学生和作答操作。相同请求重放原submissionId/eventId及首次PENDING响应；实时状态由GET查询。同键改答案或版本409。重新练习必须显式创建新键；网络结果不明时保留原请求与键。
- 事务A锁用户及用户课程状态、产生服务器UTC时间和本地序号、保存判分和事件；事务B按(occurredAt,eventSeq,eventId)串行更新交互、画像、掌握度、历史与revision。所有展示只读取已提交派生状态。
- 同一用户课程的未完成队首事件阻止后续事件越过。失败按5/15/60/300/900秒退避，5次自动重试后NEEDS_ATTENTION。管理员可重试；乱序转REBUILD_REQUIRED，管理员按保存的可信事件重建，原作答不复制，旧历史按replayGeneration保留。
- G2仅本地单知识点题(weight=1)。BKT使用固定演示参数，结果为潜在掌握估计。无作答也返回8个知识点的先验0.2并标证据不足，不视为真实学习结论。证据>=3且权重>=2后才区分薄弱(<0.6)和非薄弱。
- 学生只能写本人作答、读取本人历史/画像；管理员可读取并维护；教师仍只浏览授权课程，个人结果接口不向教师开放，课程统计另做。
- 画像仅报告已处理作答的总量、正确率、UTC活跃天数。未采集用时或资源偏好，返回null而非编造。
- 目录导入/多版本切换尚未开放；不得直接修改快照。正式导入必须新建版本并实现跨版本重建后才开放。
