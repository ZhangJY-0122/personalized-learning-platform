# 个性化学习平台

一个可本地部署的个性化学习平台研究原型，支持 Java、数据结构和计算机网络三门课程，包含在线 BKT 掌握度、可解释推荐、动态学习路径、教师统计和离线实验看板。

## 项目目录

- [`f21-personalized-learning/`](f21-personalized-learning/)：应用源码、数据库、测试和启动脚本
- [`docs/`](docs/)：项目设计与阶段文档
- [`deliverables/`](deliverables/)：最终课程设计报告、答辩 PPT 和演讲稿

## 快速启动

需要 Docker Desktop。在应用目录执行：

```bash
cd f21-personalized-learning
bash scripts/start.sh
```

- 学习平台：http://127.0.0.1:15173
- 健康检查：http://127.0.0.1:18083/api/v1/health

本地演示账号：`student01`、`teacher01`、`admin01`，密码均为 `Learn@12345`。

更完整的运行、测试和范围说明见 [`f21-personalized-learning/README.md`](f21-personalized-learning/README.md)。
