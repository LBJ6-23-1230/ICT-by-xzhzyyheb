# wf-exercise 试运行证据

| 项 | 值 |
|---|---|
| 工作流 | `wf-exercise`（练习辅导） |
| 工具 | `zhixue-learning-tools / create_grounded_exercise` |
| 资料 | `kb-demo / 二叉树讲义.pdf / 第3章 > 3.2 二叉树遍历基础` |
| 模型 | DeepSeek（平台侧）+ 服务器侧出题模型（DashScope） |

> 出题类能力的验收点不是"能出题"，而是**每道题都能追到资料片段**。
> 因此这里既要有成功样本（E1），也要有"没资料就拒绝"的样本（E3）。

---

## E1 按资料出题（工具级）

**请求**

```bash
curl -sk -X POST https://<MCP地址>:8443/mcp \
  -H "Content-Type: application/json" -H "X-API-Key: <Key>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
    "name":"create_grounded_exercise",
    "arguments":{"sessionRef":"sref-demo-0000000000000001",
                 "knowledgePointId":"binary-tree-traversal","count":2}}}'
```

**输出（节选）**

```json
{"ok": true, "data": {
  "knowledgePointId": "binary-tree-traversal",
  "exercises": [
    {"exerciseId": "llm-00100dc5fa-...-00",
     "stem": "根据资料，后序遍历的访问顺序是什么？",
     "options": ["A. 左右根", "B. 左根右", "C. 根左右", "D. 根右左"],
     "difficulty": "medium", "source": "materials"},
    {"exerciseId": "llm-00100dc5fa-...-01",
     "stem": "在二叉树遍历基础中，哪种遍历方式常用于二叉搜索树的有序输出？",
     "options": ["A. 前序遍历", "B. 中序遍历", "C. 后序遍历", "D. 层序遍历"],
     "difficulty": "medium", "source": "materials"}
  ],
  "sources": [
    {"chunkId": "chunk-demo-1", "documentId": "doc-demo-1", "kbId": "kb-demo",
     "fileName": "二叉树讲义.pdf",
     "headingPath": "第3章 > 3.2 二叉树遍历基础（binary-tree-traversal）",
     "chunkIndex": 0,
     "contentHash": "cf3914589027f0fea6d812633d20ec3f3db45d9c629420bc504306cfcf211da7",
     "parsedAt": "2026-11-10T00:00:00+00:00", "extractionVersion": 1}
  ]
}}
```

**结论**

1. 两道题的题干都能在资料片段中找到依据（后序遍历顺序、中序遍历用于二叉搜索树有序输出）。
2. 返回值带 `source="materials"`，与"通用生成"可区分；每道题都关联到具体 `chunkId`。
3. `sources` 带 `contentHash` 与 `extractionVersion`，可校验"出题时用的资料版本"是否仍然有效。
4. 题量按请求返回 2 道（`count` 生效），题目不重复。

---

## E3 无资料时拒绝出题（负样本）

**请求**：同一工具，但用户的资料库为空（或知识点在资料中不存在）

**输出**

```json
{"ok": false, "errorCode": "INSUFFICIENT_MATERIALS",
 "message": "该知识点没有可用资料，拒绝无依据出题",
 "details": {"knowledgePointId": "binary-tree-traversal", "reason": "INSUFFICIENT_MATERIALS"}}
```

**结论**

1. 没有资料时**不出题**，而不是退回通用题库冒充"依据资料"。
2. `details.reason` 区分了三种失败：`NOT_FOUND`（没有资料库）、`INSUFFICIENT_MATERIALS`（有库但没命中切片）、空（未命中但库正常），便于排查。
3. 前端/工作流据此应提示"请先上传讲义"，而不是给用户一份无出处的题。

---

## E1-工作流级：三题 + 逐题来源

**输入**：`query`、`sessionRef=sref-demo-0000000000000001`、`knowledgePointId=binary-tree-traversal`

**输出（工作流整理后的用户可见结果）**

```text
练习题（知识点：二叉树遍历）
第 1 题 根据资料，后序遍历（Post-order Traversal）的访问顺序是什么？
        A. 根左右  B. 左根右  C. 左右根  D. 右左根
        难度：中等
        来源：二叉树讲义.pdf — 第3章 > 3.2 二叉树遍历基础
第 2 题 在二叉搜索树中，哪种遍历方式常用于实现有序输出？
        A. 前序  B. 中序  C. 后序  D. 层序
        来源：同上
第 3 题 关于前序遍历（Pre-order Traversal），下列描述符合资料定义的是？
        A. 左→右→根  B. 根→左→右  C. 左→根→右  D. 右→左→根
        来源：同上
共 3 道题，均基于上述讲义章节生成。
```

**结论**

1. 三道题都能对应到同一资料片段的原文（后序左右根、中序用于 BST 有序输出、前序根左右）。
2. **每题都标了来源**（文件名 + 中文章节），评委可当场核对，这是"知识接入"最直接的可视化证据。
3. **没有泄露答案**：只给四个选项，不标正确项——题目可用于真实练习。
4. 工作流只做"整理与排版"，题目本体与来源都来自工具返回，模型未自行造题。

## 已知坑（很关键，踩过两次）

| 现象 | 根因 | 处理 |
|---|---|---|
| 改了 `repository.json`，工具却仍读旧数据 | `JsonRepository` **启动时全量读入内存**，之后不重读 | 外部改文件后**必须重启** `systemctl restart zhixue-mcp` |
| 报 `No module named 'flask'` | 出题链路经 `grounded_exercises` 反向导入了 `app.api`（题库） | 服务器安装 `flask flask-cors openai` |
| 一直 `INSUFFICIENT_MATERIALS` | 切片文本/章节里没有知识点名或 id | 章节写全 `二叉树遍历基础（binary-tree-traversal）` |
| 判分报 `NOT_FOUND` | 出题生成的题目**以前不落库**，判分按 ID 查不到 `answerKey` | 已修：`create_grounded_exercise` 现在会把题目连标准答案写入 `exercises` 集合 |

> 只有通过 MCP 工具自身写入（`save_learning_plan` / `update_mastery`）才不需要重启——因为写和读在同一个进程。

## 复现步骤

1. 服务器确保 `knowledge_bases` / `documents(status=ready)` / `chunks` 三张表都有数据
2. `systemctl restart zhixue-mcp`
3. 按 E1 的 curl 调用，检查 `exercises[].source == "materials"` 且 `sources[]` 非空
4. 把某个知识点改成一个资料里不存在的名字，应返回 `INSUFFICIENT_MATERIALS`
