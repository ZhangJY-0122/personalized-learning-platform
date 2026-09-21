# G5 最终研究版收口说明

## 最终定位

本项目最终交付为：一个可独立部署的个性化学习平台原型，支持 Java、数据结构、计算机网络三门课程，通过 BKT 在线维护学生掌握状态，结合规则、内容、Item-CF 与知识追踪生成可解释推荐和动态学习路径，并提供教师课程统计与离线算法实验看板。

## 必交范围

- 学生：登录、课程、资源、练习、历史、画像、掌握度、薄弱点、推荐、反馈和动态学习路径。
- 教师：授权课程浏览、课程薄弱点统计、推荐效果统计、模型与实验结果看板。
- 管理员：版本化目录/事件导入、补偿、重建、失败查询与重试。
- 研究：ASSISTments 数据许可与来源记录、防泄漏顺序切分、Rule/BKT/DKT 对比、五种推荐基线与消融。
- 工程：Spring Boot、MySQL、Vue 和 Docker Compose 独立启动；M0 主链路不依赖 Python 在线服务。

## 最终实测摘要

| 检查 | 结果 |
|---|---:|
| G4 API/恢复场景 | 38/38 |
| G5 新空卷三课程端到端 HTTP 检查 | 70 |
| Python 单元测试 | 116/116 |
| Java 单元测试 | 49/49 |
| OpenAPI 响应/请求示例 | 44 |
| Vue 生产构建 | 通过 |

G5 空库验收逐门完成目录导入、资源可信完成、推荐、路径、真实答题、异步状态更新和掌握度查询；教师/管理员看板可读取，学生访问教师看板返回 403。测试容器和网络已移除，隔离数据卷保留供审计。

## 正式实验结果摘要

Rule、BKT 和 DKT 在同一组 71,937 个测试目标上比较。Rule 的 AUC/Accuracy/LogLoss 分别为 0.6936/0.6763/0.6152；BKT 为 0.7013/0.6887/0.5936；DKT 三个随机种子的均值为 0.7434/0.7119/0.5654。结果仅说明对公开日志中下一次答题正确率的预测表现，不证明真实教学提升。

推荐实验只评价公开作答日志中的题目推荐。HYBRID_KT 的 Precision@5、Recall@5、HitRate@5 和 NDCG@5 分别为 0.0791、0.0356、0.1816 和 0.0904；相关结果按冻结验证集权重原样报告，未使用测试集调权。三门课程中的资源与题目混合推荐属于 `SYNTHETIC_DEMO` 产品链路，不与公开数据实验混写。

## 明确不做的可选增强

在线 DKT 推理、SAKT/Transformer、复杂协同过滤、班级/组织管理、手机端专项交付、外部平台集成和多实例生产部署均不属于最终必交范围。后续不再为这些能力扩展代码。

## 复现与验收

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/validate_contract.py
mvn -f server/pom.xml test
npm --prefix web run build
python3 tests/g5_acceptance.py
python3 scripts/build_g5_final_manifest.py
```

最终机器清单为 `artifacts/g5-final-manifest.json`。原始 ASSISTments 文件、可逆用户映射、逐用户序列、正式预测文件和模型 checkpoint 不进入普通 Git 历史；仓库只保存许可、哈希、聚合指标和可复现脚本。
