# 数据与实验准备
## 固定样例
scripts/preflight.py生成UUID5固定标识、9条循环作答、离线资源、客观题及先修DAG。结果保存在artifacts/preflight-result.json，文件哈希绑定输入。
固定示例输出只用于计算验证。它不满足正式目录每个知识点的资源/题库覆盖数量，正式G1阶段需要扩充。

## 训练烟测
training/smoke_dkt.py使用独立能力演化生成器生成36名学生×60次行为，共2160条，dataType=SYNTHETIC_DEMO。
每学生索引0:42训练、42:48验证、48:60测试。目标t只使用0:t已揭示行为。
3个种子11/22/33，CPU小模型hidden16，12轮。保存每种子的检查点，再加载检查相同输入输出完全相等。
规则和BKT使用相同432条测试目标；BKT目前固定演示参数，未拟合。窗口为完整60步烟测，正式研究按05文档配置。
artifacts/training-smoke.json是真实运行结果，不能改名成ASSISTments、最终模型效果或真实学习收益。
训练通过仅说明输入/切分/预测/评估/保存/加载管线可行。

## 正式研究准入
真实数据下载后：验证文件类型和哈希→字段清单→缺失率→每学生长度→多技能重复处理→按时间或顺序切分→训练拟合BKT→最终DKT与基线。
公开数据获取未通过时，核心开发仍继续，但公开实验未完成项必须保留。

## 本次公开数据抽样检查
已从官方修正版下载前1MiB，原始文件放data/raw并从Git排除；scripts/inspect_public_sample.py生成artifacts/public-data-inspection.json。
已确认user_id、problem_id、skill_id、correct、order_id、ms_first_response字段存在。该文件没有真实时间戳，使用order_id排序，报告应写“按顺序切分”。skill_id可能为1_13这样的合并技能，不能按重复作答拆行。
本次只证明下载通路和首段字段可用；全量清洗、分布审查、许可与正式公开数据训练尚未完成。
