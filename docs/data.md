# 数据说明

## 本机完整输入

`data/official-benchmark/html`包含1038份公告，`file`包含970个公告级附件ZIP。公告UUID由原文件名确定，同名HTML与ZIP配对；68条公告没有同名附件包，不直接视为文件丢失。

完整输入约3.4GiB，保留在本机并从Git提交范围排除。输出标准保存在`data/official-benchmark/format`，三表字段定义保存在`indexes/output-schema.json`。

数据准备命令：

```powershell
.\demo\procurement\.venv\Scripts\python.exe .\scripts\prepare_official_data.py --source-dir "C:\path\to\competition-files"
```

原始压缩包不被删除或改写。脚本按提供的数据命名读取外层文件，只整理输入和索引，不调用模型。

## 随源码提供的样本

`demo/procurement/data/demo`包含20条公告、历史模型响应、结构化结果及清单。首次启动将结果加载到`official-reference`数据集；在数据接入页可下载公告ZIP并重新回放。

样本仅覆盖公告正文，不能作为完整附件演示。历史响应缓存用于复现已有提取结果，不代表对新文件的推理能力。

## 参考标注与验证

`data/reference/announcements.jsonl`覆盖79条标准商品行；`relationships.jsonl`覆盖20公告、26采购包、123条投标事实。它们由程序生成并核对来源，尚未完成独立人工双人复核，不能作为官方评分答案。

参考集曾用于调试，原有`diagnostic/holdout`字段只是历史分组，不能据此宣称独立留出测试。`gold.example.jsonl`是评测格式示例，不是模型效果证明。

正文回放产生138条商品，其中59条额外细表未纳入79行参考。含附件回放产生216条商品，两种结果对应不同输入范围；不能将商品数量增加直接解释为准确率提高。

## 同一表格行中的多件货物

HTML 和 Word 表格同时包含货物名称、品牌、型号、数量、单价五列表头，且五列分号分段严格对应时，解析文本按位置拆成多条货物。序号和供应商等共享单元格原样复制；每条定位保留 `TABLE:n ROW:n ITEM:n`，用于原文核验、去重和评测。其余普通表格行保持原定位格式。

字段段数不一致、有空段或共享字段含分号时，保留完整原行并标记待核验。PDF/OCR 等非单元格输入由提示词要求按位置拆分；无法对应时留空并告警。未拆开的并列数量或金额不会静默取第一个数字，总价仍只保存原文披露值。

同一原文行上的同名同型号货物若品牌、数量或金额冲突，保留独立记录并标记待核验。解析预检同样展示无法拆分的警告。明确的交付期、质保期等完整时间备注不作为第二个数值；真正的并列数值在标的、项目、采购包及投标金额中都会置空并报告告警。HTML 保留原有词间空格和段落边界，仅拼接明确的连字符断行。

解析版本为 `parse-v3.6`，处理版本为 `package-v2.10`，处理指纹和解析缓存随版本更新。新抽取与历史回放统一使用当前规则，不回退旧提示词。旧缓存不匹配时明确提示使用模型抽取重新生成；回放不自动联网。已有漏项记录需要重新抽取，不会仅刷新页面就自动补回，当前附带的旧响应也不代表新版规则的结果。指引字段与显式评测口径见[说明](field-evaluation.md)。

## 本地缓存和数据库

- `.cache/model-responses`：历史模型响应，保留以减少重复请求。
- `.cache/parsed`：已解析文本，可重建。
- `demo/procurement/data/entity_mining.db`：当前业务数据库。
- `demo/procurement/data/uploads`：上传原件及重试所需输入。
- `results/offline-release`、`offline-direct`、`offline-attachments-final`：保留的三个有效验证结果集。

上述运行数据默认不提交Git。完整公告及附件的来源、原始哈希和异常索引保留在本机。
