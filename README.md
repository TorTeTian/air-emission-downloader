# Air Emission Downloader

一个skill，自动按用户指令选择地区或企业模式，并按本次要求的时间范围获取中国生态环境部公开排污许可大气执行报告。无默认年份或季节。

|用户要求|选择|
|---|---|
|指定地区内所有持证企业在指定时间段的记录|按地区：官方地市目录 → 生产经营场所县区核验|
|指定企业在指定时间段的记录|按企业：名称/许可证检索 → 精确身份核验|

## 安装与调用

将`skills/air-emission-downloader`复制到你的skill目录，例如Codex的`~/.codex/skills/air-emission-downloader`，新会话调用`$air-emission-downloader`。其他harness也可显式读取该文件夹的`SKILL.md`。

```text
使用 $air-emission-downloader，获取【省】【市】【县/区】在【起始时间】至【结束时间】的大气排污记录。
先做首页前6个企业详情的小样本，最多40次请求，输出到E:/permit_air/region_pilot。
不启动全量，核验来源、单位、缺失和覆盖范围。
```

```text
使用 $air-emission-downloader，获取【企业全称或许可证号】在【起始时间】至【结束时间】的大气排污记录。
输出到E:/permit_air/company_records，最多40次请求。核对许可证与场址，
保留全部大气指标以及全厂/排口口径，完成后运行来源回读核验。
```

时间参数三选一（不是默认值）：

```shell
--start 2023-11 --end 2024-02
--years 2024 --months 1 2 3
--periods 2023-12 2024-02
```

仅给`--years 2024`表示全年；没有时间参数则在联网前停止。起止边界也可写`YYYY-MM-DD`，但公开月报不能支持的日区间不分摊、不生成假精确总量。

## 环境

Python 3.10+；在skill目录运行：

```shell
python -m pip install -r scripts/requirements.txt
```

harness需要Python/shell执行、文件读写、HTTPS GET/POST、长任务轮询。无需GPU、多模态、API密钥或企业账号。脚本处理分页、下载和算术，模型负责范围、身份消歧和异常。未对特定廉价模型的成本/成功率做基准测试。

## 验证

```shell
python -B -X utf8 -m unittest discover -s skills/air-emission-downloader/scripts -p "test_*.py" -v
```

测试不联网。可选的历史缓存回读通过`MEE_AIR_TEST_MANIFEST`指定已有样本manifest；非夏季和年报回读用`MEE_AIR_TEST_PERIOD_ARCHIVE`指定包含`*/public_report_data.json`的本地档案。未提供时跳过相应可选测试，其余测试自包含。

旧版两入口曾完成2个真实案例的独立下载复跑：8份大气JSON正文、283条逐月字段一致。v2将时间筛选和聚合推广为用户指定时段；新时间逻辑通过离线边界测试与本地真实缓存回读验证，不把旧版小样本测试宣称为新版任意时段全量联网验证。完整原始企业数据、HTTP缓存和本地报告不放入此仓库。

## 口径与边界

- 按用户指定月份、全年或跨年时段下载；以报告所属期而非提交日期筛选，缺少时间先询问。
- `air_monthly_rows`只含目标月；`period_summary`为该月份集合的逐月合计；`native_period_summary`保留原报季度/年度总量。二者分开，不重复相加、不分摊。
- 指定日区间只能获取相交的月/季/年报，不能凭月数据声称得到精确日排放；缺月、无效值和冲突会阻止相应完整时段合计。
- 保留污染物原代码、单位、来源、原始字段、缺失和冲突；全厂与排口分开，不重复求和。
- 下载结果是官方匿名查看器的大气JSON投影，不是原始PDF；旧版PDF未自动解析。
- 当前公开许可目录不等于所有历史曾持证企业；注销/撤销历史全集未覆盖。仅登记管理企业不算许可企业。
- 默认串行限速；超过100次请求或1万条记录的计划先获用户批准；遇访问限制或结构变化停止，不绕过。

仓库保存可复用技能代码、协议及测试，不包含账号凭据或研究数据集。

升级后使用新输出目录；旧版结果与本次不同时间范围不得混用。
