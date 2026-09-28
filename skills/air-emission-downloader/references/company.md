# Company mode

用于企业名称/简称/许可证号，或用户列出的多个企业。名称仅是检索线索，许可证和生产经营场所用于确认身份；集团不同厂址、同名公司和版本不能直接合并。

在skill根目录运行：

```powershell
python -X utf8 scripts/permit_air.py run --mode company --name "江苏常熟发电有限公司" --province "江苏省" --city "苏州市" --start 2023-11 --end 2024-02 --max-pages 0 --max-requests 40 --out "E:/permit_air/changshu_power_202311_202402"
```

`--max-pages 0`确保完成候选分页后才判定唯一身份。若没有唯一精确匹配，程序输出`company_candidates.json`后停止，不取第一条模糊结果。展示候选名称/许可证/场址给用户确认；不要把“未匹配全称”解释成企业不存在。

确认候选中的许可证后可在新目录执行：

```powershell
python -X utf8 scripts/permit_air.py run --mode company --permit "已核实的许可证编号" --years 2024 --months 1 2 3 --max-pages 0 --max-requests 40 --out "E:/permit_air/confirmed_company_2024q1"
```

以上日期仅为示例，必须替换为用户本次要求的时间；未给时段先问清。给定多家企业时串行逐家运行，每家独立目录，不并发请求同一站点；跨命令累计超过预算前仍需批准。改变企业或时间范围需新输出目录，不能把另一企业缓存复制过来填缺口。

完成后区分“目录检索到”“报告下载到”“请求月份实际量齐全”和“原报季/年合计可用”。0份适用月/季报告不代表排放为0；年度或季度总量不能均分。默认保留所有大气指标和全厂/排口等分组，不能仅挑NOx后声称所有大气字段均已获取。
