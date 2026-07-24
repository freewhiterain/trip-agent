# 怎么启用 CrossEncoder 重排

## 这是什么

`app/rag/reranker.py` 里的 `CrossEncoderReranker` 用一个训练过的小模型判断
"查询和候选文档语义上有多相关"，比默认的 `RelevanceReranker`（词频重叠计数）
更能识别同义改写、说法不同但意思一样的情况。默认关闭，因为它需要在本地
下载一个模型文件并占用推理时间。

## 怎么打开

在 `.env` 里加：

```
ENABLE_CROSS_ENCODER_RERANK=true
CROSS_ENCODER_MODEL=BAAI/bge-reranker-base
```

`CROSS_ENCODER_MODEL` 不设置时默认就是 `BAAI/bge-reranker-base`。

## 候选模型

- `BAAI/bge-reranker-base`（默认）：中文效果和模型体积比较均衡，约
  1.1GB，首次加载需要从 HuggingFace 下载。
- `BAAI/bge-reranker-large`：精度更高，体积约 2.2GB，推理速度更慢，适合
  对排序质量要求更高、能接受更高延迟的场景。
- `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`：体积更小（约 470MB）、
  速度更快，但主要面向英文/多语言语料训练，中文效果弱于 bge 系列。

## 资源需求

- 首次调用会从 HuggingFace 下载模型到本地缓存（默认
  `~/.cache/huggingface`），需要网络访问；之后复用本地缓存，不会重复下载。
- 模型加载到内存后，单次查询的重排推理耗时通常在几十到几百毫秒量级
  （取决于候选文档数量和机器算力），比默认的词频重叠打分慢，但比调用
  远程 LLM API 快很多。

## 怎么验证生效

1. 打开开关后跑：`RUN_CROSS_ENCODER_TESTS=1 python -m pytest tests/test_cross_encoder_real_model.py -v`，
   确认模型能正常下载并给出合理的排序结果。
2. 或者直接跑一次真实查询，对比开关打开前后同一个查询返回的
   `Evidence`/chunk 顺序是否发生变化——`RelevanceReranker.rerank` 和
   `CrossEncoderReranker.rerank` 返回的 chunk 都带 `metadata["rerank_score"]`，
   可以直接比较分数分布。
