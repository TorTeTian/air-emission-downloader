# Region mode

用于用户明确要求某地理区域内企业的情况。需要官方省、市、县区名称、年份、输出目录；县区并非服务端可直接筛选，须先查地市目录，再核验企业实际场址。

在skill根目录运行小样本：

```powershell
python -X utf8 scripts/permit_air.py run --mode region --province "江苏省" --city "苏州市" --county "常熟市" --years 2023 2024 --max-pages 1 --max-details 6 --max-requests 40 --out "E:/permit_air/changshu_pilot"
```

这是地市目录首页前6个详情的**流程试跑**，不是代表性抽样，也不是常熟企业总数。首页可能不包含目标县区企业；不得为了凑样本改成名称关键词过滤。`region`不接受`--name/--permit`窄化。

先看`discovery.json`的服务端页数、已读页数、检查详情数和县区命中数，估算全量请求。经用户批准后才可使用下例（预算仅示例，需按实际估算调整）：

```powershell
python -X utf8 scripts/permit_air.py run --mode region --province "江苏省" --city "苏州市" --county "常熟市" --years 2023 2024 --max-pages 0 --max-details 0 --max-requests 20000 --bulk-approved --delay 1.25 --out "E:/permit_air/changshu_full"
```

`--max-pages 0`和`--max-details 0`表示遍历所有页/详情，但仍受请求预算限制。全量必须所有页和详情完成、无分页重复或未解决异常、没有企业样本截断。官方仅提供页数而无精确记录总数时要披露。

若要求“2023/2024当时全部持证企业，包括后来注销者”，停止宣称此适配器能够直接满足；需另扩展历史档案。当前许可发证日期不能简单作为排除历史报告的依据，因为企业可能延续/变更许可证。
